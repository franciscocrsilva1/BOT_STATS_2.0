# ===============================================================================
# 🏆 BOT DE ESTATÍSTICAS DE CONFRONTO V2.4.0 - VERSÃO ATUALIZADA (IDÊNTICA)
# ===============================================================================

import gspread
from oauth2client.service_account import ServiceAccountCredentials
import requests
import os 
import tempfile
import asyncio
import logging
from datetime import datetime, timedelta, timezone
import nest_asyncio
import sys 
from threading import Thread # Para rodar o Flask em paralelo (Render Free)
from flask import Flask # Para evitar o erro de porta no Render Free

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes, JobQueue 
from telegram.error import BadRequest
from gspread.exceptions import WorksheetNotFound

# Configuração de Logging
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
nest_asyncio.apply()

# --- MINI SERVIDOR FLASK (PARA PLANO GRATUITO RENDER) ---
flask_app = Flask(__name__)
@flask_app.route('/')
def health_check():
    return "Bot is running!", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host='0.0.0.0', port=port)

# ===== Variáveis de Configuração =====
BOT_TOKEN = os.environ.get("BOT_TOKEN", "SEU_TOKEN_AQUI") 
API_KEY = os.environ.get("API_KEY", "SUA_API_KEY_AQUI")
SHEET_URL = os.environ.get("SHEET_URL", "https://docs.google.com/spreadsheets/d/1ChFFXQxo1qQElNzh2OC8-UPgofRXxyVWN06ExBQ3YqY/edit?usp=drivesdk")

# Mapeamento de Ligas ATUALIZADO (Inclusão das 6 novas ligas)
LIGAS_MAP = {
    "BSA": {"sheet_past": "BSA", "sheet_future": "BSA_FJ"},
    "BL1": {"sheet_past": "BL1", "sheet_future": "BL1_FJ"},
    "PL": {"sheet_past": "PL", "sheet_future": "PL_FJ"},
    "DED": {"sheet_past": "DED", "sheet_future": "DED_FJ"},
    "CL": {"sheet_past": "CL", "sheet_future": "CL_FJ"},
    "PD": {"sheet_past": "PD", "sheet_future": "PD_FJ"},
    "FL1": {"sheet_past": "FL1", "sheet_future": "FL1_FJ"},
    "ELC": {"sheet_past": "ELC", "sheet_future": "ELC_FJ"},
    "PPL": {"sheet_past": "PPL", "sheet_future": "PPL_FJ"},
    "SA": {"sheet_past": "SA", "sheet_future": "SA_FJ"},
}
ABAS_PASSADO = list(LIGAS_MAP.keys())

ULTIMOS = 10
SHEET_CACHE = {}
CACHE_DURATION_SECONDS = 3600
MAX_GAMES_LISTED = 30

# Filtros reutilizáveis ATUALIZADOS (Inclusão de "TODOS OS JOGOS")
CONFRONTO_FILTROS = [
    (f"📊 Estatísticas | ÚLTIMOS {ULTIMOS} GERAL", "STATS_FILTRO", ULTIMOS, None, None),
    (f"📊 Estatísticas | {ULTIMOS} (M CASA vs V FORA)", "STATS_FILTRO", ULTIMOS, "casa", "fora"),
    (f"📊 Estatísticas | TODOS OS JOGOS GERAIS", "STATS_FILTRO", None, None, None),
    (f"📊 Estatísticas | TODOS MANDANTE CASA", "STATS_FILTRO", None, "casa", "fora"),
    (f"📅 Resultados | ÚLTIMOS {ULTIMOS} GERAL", "RESULTADOS_FILTRO", ULTIMOS, None, None),
    (f"📅 Resultados | {ULTIMOS} (M CASA vs V FORA)", "RESULTADOS_FILTRO", ULTIMOS, "casa", "fora"),
    (f"📅 Resultados | TODOS OS JOGOS GERAIS", "RESULTADOS_FILTRO", None, None, None),
    (f"📅 Resultados | TODOS VISITANTE FORA", "RESULTADOS_FILTRO", None, "casa", "fora"),
]

LIVE_STATUSES = ["IN_PLAY", "HALF_TIME", "PAUSED"]

# --- [AQUI SEGUE TODA A LÓGICA DE CONEXÃO GSHEETS E FUNÇÕES IGUAL AO ORIGINAL] ---
CREDS_JSON = os.environ.get("GSPREAD_CREDS_JSON")
client = None

if not CREDS_JSON:
    logging.error("❌ ERRO DE AUTORIZAÇÃO GSHEET.")
else:
    try:
        with tempfile.NamedTemporaryFile(mode="w", delete=False, encoding='utf-8') as tmp_file:
            tmp_file.write(CREDS_JSON); tmp_file_path = tmp_file.name
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name(tmp_file_path, scope)
        client = gspread.authorize(creds); os.remove(tmp_file_path)
    except Exception as e:
        logging.error(f"❌ ERRO: {e}"); client = None

# Funções auxiliares (safe_int, pct, media, escape_markdown) IDÊNTICAS
def safe_int(v):
    try: return int(v)
    except: return 0
def pct(part, total): return f"{(part/total)*100:.1f}%" if total>0 else "—"
def media(part, total): return f"{(part/total):.2f}" if total>0 else "—"
def escape_markdown(text): return str(text).replace('*', '\\*').replace('_', '\\_').replace('[', '\\[') .replace(']', '\\]')

def get_sheet_data(aba_code):
    global SHEET_CACHE; agora = datetime.now(); aba_name = LIGAS_MAP[aba_code]['sheet_past']
    if aba_name in SHEET_CACHE:
        if (agora - SHEET_CACHE[aba_name]['timestamp']).total_seconds() < CACHE_DURATION_SECONDS: return SHEET_CACHE[aba_name]['data']
    if not client: raise Exception("GSheets off.")
    try:
        sh = client.open_by_url(SHEET_URL); linhas = sh.worksheet(aba_name).get_all_records()
        SHEET_CACHE[aba_name] = { 'data': linhas, 'timestamp': agora }; return linhas
    except Exception as e:
        if aba_name in SHEET_CACHE: return SHEET_CACHE[aba_name]['data']
        raise e

def get_sheet_data_future(aba_code):
    aba_name = LIGAS_MAP[aba_code]['sheet_future']
    if not client: return []
    try:
        sh = client.open_by_url(SHEET_URL); linhas_raw = sh.worksheet(aba_name).get_all_values()
        if not linhas_raw or len(linhas_raw) <= 1: return []
        return [{"Mandante_Nome": r[0], "Visitante_Nome": r[1], "Data_Hora": r[2], "Matchday": safe_int(r[3])} for r in linhas_raw[1:] if len(r) >= 4]
    except: return []

async def pre_carregar_cache_sheets():
    if not client: return
    for aba in ABAS_PASSADO:
        try: await asyncio.to_thread(get_sheet_data, aba); await asyncio.sleep(1)
        except: continue

# --- Lógica de API e Atualização IDÊNTICA ---
def buscar_jogos(league_code, status_filter):
    try:
        url = f"https://api.football-data.org/v4/competitions/{league_code}/matches"
        params = {"status": status_filter} if status_filter != "ALL" else {}
        if league_code == "BSA": params["season"] = "2026"
        r = requests.get(url, headers={"X-Auth-Token": API_KEY}, params=params, timeout=10); r.raise_for_status()
        all_matches = r.json().get("matches", [])
        if status_filter == "ALL": return [m for m in all_matches if m.get('status') in ['SCHEDULED', 'TIMED']]
        jogos = []
        for m in all_matches:
            if m.get('status') == "FINISHED":
                try:
                    ft = m.get("score", {}).get("fullTime", {}); ht = m.get("score", {}).get("halfTime", {})
                    if ft.get("home") is None: continue
                    gm, gv = ft.get("home",0), ft.get("away",0)
                    gm1, gv1 = ht.get("home",0), ht.get("away",0)
                    jogos.append({"Mandante": m["homeTeam"]["name"], "Visitante": m["awayTeam"]["name"], "Gols Mandante": gm, "Gols Visitante": gv, "Gols Mandante 1T": gm1, "Gols Visitante 1T": gv1, "Gols Mandante 2T": gm-gm1, "Gols Visitante 2T": gv-gv1, "Data": datetime.strptime(m['utcDate'][:10], "%Y-%m-%d").strftime("%d/%m/%Y")})
                except: continue
        return sorted(jogos, key=lambda x: datetime.strptime(x['Data'], "%d/%m/%Y"))
    except: return []

def buscar_jogos_live(league_code):
    try:
        hoje = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        r = requests.get(f"https://api.football-data.org/v4/competitions/{league_code}/matches?dateFrom={hoje}&dateTo={hoje}", headers={"X-Auth-Token": API_KEY}, timeout=10)
        jogos = []
        for m in r.json().get("matches", []):
            if m.get('status') in LIVE_STATUSES:
                score = m.get("score", {}).get("fullTime", {})
                minuto = m.get("minute", "Em Jogo")
                if m['status'] == 'HALF_TIME': minuto = "Intervalo"
                jogos.append({"Mandante_Nome": m["homeTeam"]["name"], "Visitante_Nome": m["awayTeam"]["name"], "Placar_Mandante": score.get("home", 0), "Placar_Visitante": score.get("away", 0), "Tempo_Jogo": minuto, "Matchday": m.get("matchday", 0)})
        return jogos
    except: return []

async def atualizar_planilhas(context: ContextTypes.DEFAULT_TYPE):
    if not client: return
    try:
        sh = client.open_by_url(SHEET_URL)
        for aba_code, cfg in LIGAS_MAP.items():
            # Passado
            ws_p = sh.worksheet(cfg['sheet_past']); j_f = buscar_jogos(aba_code, "FINISHED"); await asyncio.sleep(2)
            if j_f:
                exist = ws_p.get_all_records(); keys = {(r['Mandante'], r['Visitante'], r['Data']) for r in exist}
                novos = [[j["Mandante"], j["Visitante"], j["Gols Mandante"], j["Gols Visitante"], j["Gols Mandante 1T"], j["Gols Visitante 1T"], j["Gols Mandante 2T"], j["Gols Visitante 2T"], j["Data"]] for j in j_f if (j["Mandante"], j["Visitante"], j["Data"]) not in keys]
                if novos: ws_p.append_rows(novos)
            # Futuro
            ws_f = sh.worksheet(cfg['sheet_future']); j_fut = buscar_jogos(aba_code, "ALL"); await asyncio.sleep(2)
            ws_f.clear(); ws_f.update(values=[['Mandante', 'Visitante', 'Data/Hora', 'Matchday']], range_name='A1:D1')
            if j_fut:
                linhas = [[m["homeTeam"]["name"], m["awayTeam"]["name"], m["utcDate"], m.get("matchday", "")] for m in j_fut]
                ws_f.append_rows(linhas, value_input_option='USER_ENTERED')
    except: pass

# --- Funções de Cálculo (calcular_estatisticas_time, formatar_estatisticas, listar_ultimos_jogos) IDÊNTICAS ---
def calcular_estatisticas_time(time, aba, ultimos=None, casa_fora=None):
    d = {"time":time,"jogos_time":0,"jogos_casa":0,"jogos_fora":0,"over15":0,"over15_casa":0,"over15_fora":0,"over25":0,"over25_casa":0,"over25_fora":0,"btts":0,"btts_casa":0,"btts_fora":0,"g_a_t":0,"g_a_t_casa":0,"g_a_t_fora":0,"over05_1T":0,"over05_1T_casa":0,"over05_1T_fora":0,"over05_2T":0,"over05_2T_casa":0,"over05_2T_fora":0,"over15_2T":0,"over15_2T_casa":0,"over15_2T_fora":0,"gols_marcados":0,"gols_sofridos":0,"gols_marcados_casa":0,"gols_sofridos_casa":0,"gols_marcados_fora":0,"gols_sofridos_fora":0,"total_gols":0,"total_gols_casa":0,"total_gols_fora":0,"gols_marcados_1T":0,"gols_sofridos_1T":0,"gols_marcados_2T":0,"gols_sofridos_2T":0,"marcou_2_mais":0,"marcou_2_mais_casa":0,"marcou_2_mais_fora":0,"sofreu_2_mais":0,"sofreu_2_mais_casa":0,"sofreu_2_mais_fora":0,"marcou_ambos_tempos":0,"marcou_ambos_tempos_casa":0,"marcou_ambos_tempos_fora":0,"sofreu_ambos_tempos":0,"sofreu_ambos_tempos_casa":0,"sofreu_ambos_tempos_fora":0}
    try: linhas = get_sheet_data(aba)
    except: return {"time":time, "jogos_time":0}
    if casa_fora == "casa": linhas = [l for l in linhas if l['Mandante'] == time]
    elif casa_fora == "fora": linhas = [l for l in linhas if l['Visitante'] == time]
    else: linhas = [l for l in linhas if l['Mandante'] == time or l['Visitante'] == time]
    if ultimos: linhas = linhas[-ultimos:]
    for l in linhas:
        em_casa = (time == l['Mandante']); gm, gv = safe_int(l['Gols Mandante']), safe_int(l['Gols Visitante'])
        gm1, gv1 = safe_int(l['Gols Mandante 1T']), safe_int(l['Gols Visitante 1T'])
        gm2, gv2 = gm-gm1, gv-gv1; total, t1, t2 = gm+gv, gm1+gv1, gm2+gv2
        d["jogos_time"] += 1
        if em_casa: d["jogos_casa"] += 1; m, s, m1, s1, m2, s2 = gm, gv, gm1, gv1, gm2, gv2; d["gols_marcados_casa"] += m; d["gols_sofridos_casa"] += s; d["total_gols_casa"] += total
        else: d["jogos_fora"] += 1; m, s, m1, s1, m2, s2 = gv, gm, gv1, gm1, gv2, gm2; d["gols_marcados_fora"] += m; d["gols_sofridos_fora"] += s; d["total_gols_fora"] += total
        d["gols_marcados"] += m; d["gols_sofridos"] += s; d["total_gols"] += total; d["over15"] += (1 if total>1.5 else 0); d["over25"] += (1 if total>2.5 else 0); d["btts"] += (1 if gm>0 and gv>0 else 0); d["over05_1T"] += (1 if t1>0.5 else 0); d["over05_2T"] += (1 if t2>0.5 else 0); d["over15_2T"] += (1 if t2>1.5 else 0)
        if t1>0 and t2>0: d["g_a_t"] += 1; d["g_a_t_casa" if em_casa else "g_a_t_fora"] += 1
        if m>=2: d["marcou_2_mais"] += 1; d["marcou_2_mais_casa" if em_casa else "marcou_2_mais_fora"] += 1
        if s>=2: d["sofreu_2_mais"] += 1; d["sofreu_2_mais_casa" if em_casa else "sofreu_2_mais_fora"] += 1
        if m1>0 and m2>0: d["marcou_ambos_tempos"] += 1; d["marcou_ambos_tempos_casa" if em_casa else "marcou_ambos_tempos_fora"] += 1
        if s1>0 and s2>0: d["sofreu_ambos_tempos"] += 1; d["sofreu_ambos_tempos_casa" if em_casa else "sofreu_ambos_tempos_fora"] += 1
        d["over15_casa" if em_casa else "over15_fora"] += (1 if total>1.5 else 0); d["over25_casa" if em_casa else "over25_fora"] += (1 if total>2.5 else 0); d["btts_casa" if em_casa else "btts_fora"] += (1 if gm>0 and gv>0 else 0); d["over05_1T_casa" if em_casa else "over05_1T_fora"] += (1 if t1>0.5 else 0); d["over05_2T_casa" if em_casa else "over05_2T_fora"] += (1 if t2>0.5 else 0); d["over15_2T_casa" if em_casa else "over15_2T_fora"] += (1 if t2>1.5 else 0); d["gols_marcados_1T"] += m1; d["gols_sofridos_1T"] += s1; d["gols_marcados_2T"] += m2; d["gols_sofridos_2T"] += s2
    return d

def formatar_estatisticas(d):
    jt, jc, jf = d["jogos_time"], d.get("jogos_casa", 0), d.get("jogos_fora", 0)
    if jt == 0: return f"⚠️ Sem jogos."
    return (f"📊 **Estatísticas - {escape_markdown(d['time'])}**\n📅 Jogos: {jt} (C: {jc} | F: {jf})\n\n⚽ Over 1.5: **{pct(d['over15'], jt)}**\n⚽ Over 2.5: **{pct(d['over25'], jt)}**\n🔁 BTTS: **{pct(d['btts'], jt)}**\n🥅 G.A.T.: {pct(d['g_a_t'], jt)}\n📈 Marcou 2+: **{pct(d['marcou_2_mais'], jt)}**\n📉 Sofreu 2+: **{pct(d['sofreu_2_mais'], jt)}**\n⚽ M.A.T.: **{pct(d['marcou_ambos_tempos'], jt)}**\n⏱️ 1ºT > 0.5: {pct(d['over05_1T'], jt)} | 2ºT > 0.5: {pct(d['over05_2T'], jt)}\n🔢 **Média Total:** {media(d['total_gols'], jt)}")

def listar_ultimos_jogos(time, aba, ultimos=None, casa_fora=None):
    try:
        linhas = get_sheet_data(aba)
        if casa_fora == "casa": linhas = [l for l in linhas if l['Mandante'] == time]
        elif casa_fora == "fora": linhas = [l for l in linhas if l['Visitante'] == time]
        else: linhas = [l for l in linhas if l['Mandante'] == time or l['Visitante'] == time]
        if ultimos: linhas = linhas[-ultimos:]
        if not linhas: return "Nenhum jogo."
        res = ""
        for l in linhas:
            gm, gv = safe_int(l['Gols Mandante']), safe_int(l['Gols Visitante'])
            cor = "🟢" if (l['Mandante']==time and gm>gv) or (l['Visitante']==time and gv>gm) else ("🟡" if gm==gv else "🔴")
            res += f"{cor} {l['Data']}: {l['Mandante']} {gm}x{gv} {l['Visitante']}\n"
        return res
    except: return "Erro."

# --- Handlers do Bot (start, listar_competicoes, etc) IDÊNTICOS ---
async def start_command(update, context): await update.message.reply_text("👋 Use **/stats**.", parse_mode='Markdown')
async def listar_competicoes(update, context):
    kb = [[InlineKeyboardButton(aba, callback_data=f"c|{aba}") for aba in ABAS_PASSADO[i:i+3]] for i in range(0, len(ABAS_PASSADO), 3)]
    await (update.message.reply_text if update.message else update.callback_query.edit_message_text)("🏆 **Escolha a Liga:**", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

async def mostrar_menu_status_jogo(update, context, aba_code):
    kb = [[InlineKeyboardButton("🔴 AO VIVO", callback_data=f"STATUS|LIVE|{aba_code}")], [InlineKeyboardButton("📅 PRÓXIMOS", callback_data=f"STATUS|FUTURE|{aba_code}")], [InlineKeyboardButton("⬅️ Voltar", callback_data="VOLTAR_LIGA")]]
    await update.callback_query.edit_message_text(f"🎮 **{aba_code}**:", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

async def listar_jogos(update, context, aba_code, status):
    jogos = get_sheet_data_future(aba_code) if status == "FUTURE" else buscar_jogos_live(aba_code)
    if not jogos: await update.callback_query.edit_message_text("⚠️ Sem jogos."); return
    context.chat_data[f"{aba_code}_{status.lower()}"] = jogos
    kb = [[InlineKeyboardButton(f"{j.get('Tempo_Jogo', j['Data_Hora'][11:16])} | {j['Mandante_Nome']} x {j['Visitante_Nome']}", callback_data=f"JOGO|{aba_code}|{status}|{i}")] for i, j in enumerate(jogos)]
    kb.append([InlineKeyboardButton("⬅️ Voltar", callback_data=f"c|{aba_code}")])
    await update.callback_query.edit_message_text("⚽ **Escolha o Jogo:**", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

async def callback_query_handler(update, context):
    q = update.callback_query; d = q.data
    try:
        if d.startswith("c|"): await mostrar_menu_status_jogo(update, context, d.split('|')[1])
        elif d.startswith("STATUS|"): await listar_jogos(update, context, d.split('|')[2], d.split('|')[1])
        elif d.startswith("JOGO|"):
            _, aba, st, idx = d.split('|'); jogo = context.chat_data[f"{aba}_{st.lower()}"][int(idx)]
            context.chat_data.update({'m': jogo['Mandante_Nome'], 'v': jogo['Visitante_Nome'], 'a': aba})
            kb = [[InlineKeyboardButton(f[0], callback_data=f"{f[1]}|{i}")] for i, f in enumerate(CONFRONTO_FILTROS)]
            kb.append([InlineKeyboardButton("⬅️ Voltar", callback_data=f"STATUS|{st}|{aba}")])
            await q.edit_message_text(f"🎯 **{jogo['Mandante_Nome']} x {jogo['Visitante_Nome']}**", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
        elif d.startswith("STATS_FILTRO|"):
            f = CONFRONTO_FILTROS[int(d.split('|')[1])]; m = context.chat_data['m']; v = context.chat_data['v']; a = context.chat_data['a']
            await q.message.reply_text(f"{formatar_estatisticas(calcular_estatisticas_time(m, a, f[2], f[3]))}\n\n{formatar_estatisticas(calcular_estatisticas_time(v, a, f[2], f[4]))}", parse_mode='Markdown')
        elif d.startswith("RESULTADOS_FILTRO|"):
            f = CONFRONTO_FILTROS[int(d.split('|')[1])]; m = context.chat_data['m']; v = context.chat_data['v']; a = context.chat_data['a']
            await q.message.reply_text(f"📅 **{m}**\n{listar_ultimos_jogos(m, a, f[2], f[3])}\n\n📅 **{v}**\n{listar_ultimos_jogos(v, a, f[2], f[4])}", parse_mode='Markdown')
        elif d == "VOLTAR_LIGA": await listar_competicoes(update, context)
    except: pass

def main():
    # Inicia Flask em paralelo
    Thread(target=run_flask, daemon=True).start()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command)); app.add_handler(CommandHandler("stats", listar_competicoes)); app.add_handler(CallbackQueryHandler(callback_query_handler))
    if client: app.job_queue.run_repeating(atualizar_planilhas, interval=3600, first=0)
    app.run_polling()

if __name__ == "__main__":
    main()
