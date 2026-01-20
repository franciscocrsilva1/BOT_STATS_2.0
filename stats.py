# ===============================================================================
# 🏆 BOT DE ESTATÍSTICAS DE CONFRONTO V2.4.0 - LIGA SELECIONADA (BSA)
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

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes, JobQueue 
from telegram.error import BadRequest
from gspread.exceptions import WorksheetNotFound

# Configuração de Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
nest_asyncio.apply()

# ===== Variáveis de Configuração =====
BOT_TOKEN = os.environ.get("BOT_TOKEN", "SEU_TOKEN_AQUI") 
API_KEY = os.environ.get("API_KEY", "SUA_API_KEY_AQUI")
SHEET_URL = os.environ.get("SHEET_URL", "https://docs.google.com/spreadsheets/d/1ChFFXQxo1qQElNzh2OC8-UPgofRXxyVWN06ExBQ3YqY/edit?usp=drivesdk")

LIGAS_MAP = {"BSA": {"sheet_past": "BSA", "sheet_future": "BSA_FJ"}}
ABAS_PASSADO = list(LIGAS_MAP.keys())

SHEET_CACHE = {}
CACHE_DURATION_SECONDS = 3600 
MAX_GAMES_LISTED = 30

# ✅ NOVOS FILTROS CONFORME SOLICITADO (GERAIS E ÚLTIMOS 10)
CONFRONTO_FILTROS = [
    # ESTATÍSTICAS
    ("📊 Stats | GERAIS (Todos)", "STATS_FILTRO", None, None, None),
    ("📊 Stats | GERAIS (M Casa vs V Fora)", "STATS_FILTRO", None, "casa", "fora"),
    ("📊 Stats | ÚLTIMOS 10 GERAIS", "STATS_FILTRO", 10, None, None),
    ("📊 Stats | ÚLTIMOS 10 (M Casa vs V Fora)", "STATS_FILTRO", 10, "casa", "fora"),
    
    # RESULTADOS
    ("📅 Resultados | GERAIS (Todos)", "RESULTADOS_FILTRO", None, None, None),
    ("📅 Resultados | GERAIS (M Casa vs V Fora)", "RESULTADOS_FILTRO", None, "casa", "fora"),
    ("📅 Resultados | ÚLTIMOS 10 GERAIS", "RESULTADOS_FILTRO", 10, None, None),
    ("📅 Resultados | ÚLTIMOS 10 (M Casa vs V Fora)", "RESULTADOS_FILTRO", 10, "casa", "fora"),
]

LIVE_STATUSES = ["IN_PLAY", "HALF_TIME", "PAUSED"]

# =================================================================================
# ✅ CONEXÃO GSHEETS
# =================================================================================
CREDS_JSON = os.environ.get("GSPREAD_CREDS_JSON")
client = None

if not CREDS_JSON:
    logging.error("❌ ERRO: GSPREAD_CREDS_JSON não configurada.")
else:
    try:
        with tempfile.NamedTemporaryFile(mode="w", delete=False, encoding='utf-8') as tmp_file:
            tmp_file.write(CREDS_JSON)
            tmp_file_path = tmp_file.name
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name(tmp_file_path, scope)
        client = gspread.authorize(creds)
        os.remove(tmp_file_path)
    except Exception as e:
        logging.error(f"❌ ERRO GSHEET: {e}")

# =================================================================================
# 💾 FUNÇÕES DE SUPORTE
# =================================================================================
def safe_int(v):
    try: return int(v)
    except: return 0

def pct(part, total):
    return f"{(part/total)*100:.1f}%" if total>0 else "—"

def media(part, total):
    return f"{(part/total):.2f}" if total>0 else "—"

def escape_markdown(text):
    return str(text).replace('*', '\\*').replace('_', '\\_').replace('[', '\\[') .replace(']', '\\]')

def get_sheet_data(aba_code):
    global SHEET_CACHE
    agora = datetime.now()
    aba_name = LIGAS_MAP[aba_code]['sheet_past']

    if aba_name in SHEET_CACHE:
        if (agora - SHEET_CACHE[aba_name]['timestamp']).total_seconds() < CACHE_DURATION_SECONDS:
            return SHEET_CACHE[aba_name]['data']

    try:
        sh = client.open_by_url(SHEET_URL)
        linhas = sh.worksheet(aba_name).get_all_records()
        SHEET_CACHE[aba_name] = {'data': linhas, 'timestamp': agora}
        return linhas
    except Exception as e:
        return SHEET_CACHE[aba_name]['data'] if aba_name in SHEET_CACHE else []

def get_sheet_data_future(aba_code):
    aba_name = LIGAS_MAP[aba_code]['sheet_future']
    try:
        sh = client.open_by_url(SHEET_URL)
        linhas_raw = sh.worksheet(aba_name).get_all_values()
        if not linhas_raw or len(linhas_raw) <= 1: return []
        return [{"Mandante_Nome": r[0], "Visitante_Nome": r[1], "Data_Hora": r[2], "Matchday": safe_int(r[3])} for r in linhas_raw[1:]]
    except: return []

# =================================================================================
# 🎯 API E SINCRONIZAÇÃO
# =================================================================================
def buscar_jogos(league_code, status_filter):
    try:
        url = f"https://api.football-data.org/v4/competitions/{league_code}/matches"
        params = {"status": status_filter} if status_filter != "ALL" else {}
        if league_code == "BSA": params["season"] = "2026"
        r = requests.get(url, headers={"X-Auth-Token": API_KEY}, params=params, timeout=10)
        all_matches = r.json().get("matches", [])
        
        if status_filter == "ALL":
            return [m for m in all_matches if m.get('status') in ['SCHEDULED', 'TIMED']]
        
        jogos = []
        for m in all_matches:
            if m.get('status') == "FINISHED":
                ft = m.get("score", {}).get("fullTime", {})
                ht = m.get("score", {}).get("halfTime", {})
                if ft.get("home") is None: continue
                gm, gv = ft.get("home"), ft.get("away")
                gm1, gv1 = ht.get("home", 0), ht.get("away", 0)
                jogos.append({
                    "Mandante": m["homeTeam"]["name"], "Visitante": m["awayTeam"]["name"],
                    "Gols Mandante": gm, "Gols Visitante": gv, "Gols Mandante 1T": gm1, "Gols Visitante 1T": gv1,
                    "Gols Mandante 2T": gm-gm1, "Gols Visitante 2T": gv-gv1, "Data": datetime.strptime(m['utcDate'][:10], "%Y-%m-%d").strftime("%d/%m/%Y")
                })
        return jogos
    except: return []

def buscar_jogos_live(league_code):
    hoje = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    try:
        url = f"https://api.football-data.org/v4/competitions/{league_code}/matches?dateFrom={hoje}&dateTo={hoje}"
        r = requests.get(url, headers={"X-Auth-Token": API_KEY}, timeout=10)
        return [{
            "Mandante_Nome": m["homeTeam"]["name"], "Visitante_Nome": m["awayTeam"]["name"],
            "Placar_Mandante": m["score"]["fullTime"]["home"], "Placar_Visitante": m["score"]["fullTime"]["away"],
            "Tempo_Jogo": m.get("minute", "Live"), "Matchday": m.get("matchday", 0)
        } for m in r.json().get("matches", []) if m.get('status') in LIVE_STATUSES]
    except: return []

async def atualizar_planilhas(context: ContextTypes.DEFAULT_TYPE):
    if not client: return
    sh = client.open_by_url(SHEET_URL)
    for aba_code, config in LIGAS_MAP.items():
        # Histórico
        jogos_fin = buscar_jogos(aba_code, "FINISHED")
        if jogos_fin:
            ws = sh.worksheet(config['sheet_past'])
            exist = ws.get_all_records()
            keys = {(r['Mandante'], r['Visitante'], r['Data']) for r in exist}
            novos = [[j["Mandante"], j["Visitante"], j["Gols Mandante"], j["Gols Visitante"], j["Gols Mandante 1T"], j["Gols Visitante 1T"], j["Gols Mandante 2T"], j["Gols Visitante 2T"], j["Data"]] for j in jogos_fin if (j["Mandante"], j["Visitante"], j["Data"]) not in keys]
            if novos: ws.append_rows(novos)
        # Futuros
        jogos_fut = buscar_jogos(aba_code, "ALL")
        ws_f = sh.worksheet(config['sheet_future'])
        ws_f.clear()
        ws_f.update(values=[['Mandante', 'Visitante', 'Data/Hora', 'Matchday']], range_name='A1:D1')
        if jogos_fut:
            ws_f.append_rows([[m["homeTeam"]["name"], m["awayTeam"]["name"], m["utcDate"], m["matchday"]] for m in jogos_fut], value_input_option='USER_ENTERED')

# =================================================================================
# 📈 CÁLCULOS E FORMATAÇÃO
# =================================================================================
def calcular_estatisticas_time(time, aba, ultimos=None, casa_fora=None):
    d = {"time":time,"jogos_time":0,"jogos_casa":0,"jogos_fora":0,"over15":0,"over15_casa":0,"over15_fora":0,"over25":0,"over25_casa":0,"over25_fora":0,"btts":0,"btts_casa":0,"btts_fora":0,"g_a_t":0,"g_a_t_casa":0,"g_a_t_fora":0,"over05_1T":0,"over05_1T_casa":0,"over05_1T_fora":0,"over05_2T":0,"over05_2T_casa":0,"over05_2T_fora":0,"over15_2T":0,"over15_2T_casa":0,"over15_2T_fora":0,"gols_marcados":0,"gols_sofridos":0,"gols_marcados_casa":0,"gols_sofridos_casa":0,"gols_marcados_fora":0,"gols_sofridos_fora":0,"total_gols":0,"total_gols_casa":0,"total_gols_fora":0,"gols_marcados_1T":0,"gols_sofridos_1T":0,"gols_marcados_2T":0,"gols_sofridos_2T":0,"marcou_2_mais":0,"marcou_2_mais_casa":0,"marcou_2_mais_fora":0,"sofreu_2_mais":0,"sofreu_2_mais_casa":0,"sofreu_2_mais_fora":0,"marcou_ambos_tempos":0,"marcou_ambos_tempos_casa":0,"marcou_ambos_tempos_fora":0,"sofreu_ambos_tempos":0,"sofreu_ambos_tempos_casa":0,"sofreu_ambos_tempos_fora":0}
    
    linhas = get_sheet_data(aba)
    if casa_fora == "casa": 
        linhas = [l for l in linhas if l['Mandante'] == time]
    elif casa_fora == "fora": 
        linhas = [l for l in linhas if l['Visitante'] == time]
    else: 
        linhas = [l for l in linhas if l['Mandante'] == time or l['Visitante'] == time]

    linhas.sort(key=lambda x: datetime.strptime(x['Data'], "%d/%m/%Y"))
    if ultimos: linhas = linhas[-ultimos:]

    for l in linhas:
        em_casa = (time == l['Mandante'])
        gm, gv = safe_int(l['Gols Mandante']), safe_int(l['Gols Visitante'])
        gm1, gv1 = safe_int(l['Gols Mandante 1T']), safe_int(l['Gols Visitante 1T'])
        gm2, gv2, tot, tot1, tot2 = gm-gm1, gv-gv1, gm+gv, gm1+gv1, gm2+gv2
        
        d["jogos_time"] += 1
        m_ft, s_ft = (gm, gv) if em_casa else (gv, gm)
        m_1t, s_1t = (gm1, gv1) if em_casa else (gv1, gm1)
        m_2t, s_2t = (gm2, gv2) if em_casa else (gv2, gm2)

        if em_casa: d["jogos_casa"] += 1; d["total_gols_casa"] += tot; d["gols_marcados_casa"] += m_ft; d["gols_sofridos_casa"] += s_ft
        else: d["jogos_fora"] += 1; d["total_gols_fora"] += tot; d["gols_marcados_fora"] += m_ft; d["gols_sofridos_fora"] += s_ft

        d["total_gols"] += tot; d["gols_marcados"] += m_ft; d["gols_sofridos"] += s_ft
        d["over15"] += (1 if tot > 1.5 else 0); d["over25"] += (1 if tot > 2.5 else 0); d["btts"] += (1 if gm > 0 and gv > 0 else 0)
        d["over05_1T"] += (1 if tot1 > 0.5 else 0); d["over05_2T"] += (1 if tot2 > 0.5 else 0); d["over15_2T"] += (1 if tot2 > 1.5 else 0)
        
        if tot1 > 0 and tot2 > 0: d["g_a_t"] += 1; d["g_a_t_casa" if em_casa else "g_a_t_fora"] += 1
        if m_ft >= 2: d["marcou_2_mais"] += 1; d["marcou_2_mais_casa" if em_casa else "marcou_2_mais_fora"] += 1
        if s_ft >= 2: d["sofreu_2_mais"] += 1; d["sofreu_2_mais_casa" if em_casa else "sofreu_2_mais_fora"] += 1
        if m_1t > 0 and m_2t > 0: d["marcou_ambos_tempos"] += 1; d["marcou_ambos_tempos_casa" if em_casa else "marcou_ambos_tempos_fora"] += 1
        
        # Estatísticas por local (C/F) para o resumo
        if em_casa:
            d["over15_casa"] += (1 if tot > 1.5 else 0); d["over25_casa"] += (1 if tot > 2.5 else 0); d["btts_casa"] += (1 if gm > 0 and gv > 0 else 0)
            d["over05_1T_casa"] += (1 if tot1 > 0.5 else 0); d["over05_2T_casa"] += (1 if tot2 > 0.5 else 0); d["over15_2T_casa"] += (1 if tot2 > 1.5 else 0)
        else:
            d["over15_fora"] += (1 if tot > 1.5 else 0); d["over25_fora"] += (1 if tot > 2.5 else 0); d["btts_fora"] += (1 if gm > 0 and gv > 0 else 0)
            d["over05_1T_fora"] += (1 if tot1 > 0.5 else 0); d["over05_2T_fora"] += (1 if tot2 > 0.5 else 0); d["over15_2T_fora"] += (1 if tot2 > 1.5 else 0)

    return d

def formatar_estatisticas(d):
    jt, jc, jf = d["jogos_time"], d["jogos_casa"], d["jogos_fora"]
    if jt == 0: return f"⚠️ Sem dados para **{escape_markdown(d['time'])}**."
    return (f"📊 **{escape_markdown(d['time'])}** ({jt}j)\n"
            f"⚽ O1.5: **{pct(d['over15'], jt)}** | O2.5: **{pct(d['over25'], jt)}**\n"
            f"🔁 BTTS: **{pct(d['btts'], jt)}** | GAT: **{pct(d['g_a_t'], jt)}**\n"
            f"📈 Marcou 2+: {pct(d['marcou_2_mais'], jt)} | MAT: {pct(d['marcou_ambos_tempos'], jt)}\n"
            f"🔢 Média Gols: {media(d['total_gols'], jt)} (Marcados: {media(d['gols_marcados'], jt)})")

def listar_ultimos_jogos(time, aba, ultimos=None, casa_fora=None):
    linhas = get_sheet_data(aba)
    if casa_fora == "casa": linhas = [l for l in linhas if l['Mandante'] == time]
    elif casa_fora == "fora": linhas = [l for l in linhas if l['Visitante'] == time]
    else: linhas = [l for l in linhas if l['Mandante'] == time or l['Visitante'] == time]
    
    linhas.sort(key=lambda x: datetime.strptime(x['Data'], "%d/%m/%Y"))
    if ultimos: linhas = linhas[-ultimos:]
    if not linhas: return "Nenhum jogo encontrado."

    res = ""
    for l in linhas:
        gm, gv = safe_int(l['Gols Mandante']), safe_int(l['Gols Visitante'])
        is_m = (l['Mandante'] == time)
        win = (gm > gv if is_m else gv > gm)
        draw = (gm == gv)
        emoji = "🟢" if win else ("🟡" if draw else "🔴")
        res += f"{emoji} {l['Data']}: {escape_markdown(l['Mandante'])} {gm}x{gv} {escape_markdown(l['Visitante'])}\n"
    return res

# =================================================================================
# 🤖 HANDLERS
# =================================================================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⚽ **Bot BSA V2.4**\nUse **/stats** para analisar um jogo.", parse_mode='Markdown')

async def listar_competicoes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton(aba, callback_data=f"c|{aba}")] for aba in LIGAS_MAP.keys()]
    await update.effective_message.reply_text("Escolha a Competição:", reply_markup=InlineKeyboardMarkup(kb))

async def mostrar_menu_status_jogo(update: Update, context: ContextTypes.DEFAULT_TYPE, aba_code: str):
    kb = [[InlineKeyboardButton("🔴 AO VIVO", callback_data=f"STATUS|LIVE|{aba_code}")],
          [InlineKeyboardButton("📅 PRÓXIMOS JOGOS", callback_data=f"STATUS|FUTURE|{aba_code}")],
          [InlineKeyboardButton("⬅️ Voltar", callback_data="VOLTAR_LIGA")]]
    await update.callback_query.edit_message_text(f"**{aba_code}** - Tipo de Partida:", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

async def listar_jogos(update: Update, context: ContextTypes.DEFAULT_TYPE, aba_code: str, status: str):
    if status == "FUTURE":
        jogos = get_sheet_data_future(aba_code)
        kb = [[InlineKeyboardButton(f"{j['Mandante_Nome']} x {j['Visitante_Nome']}", callback_data=f"JOGO|{aba_code}|FUTURE|{i}")] for i, j in enumerate(jogos[:20])]
    else:
        jogos = buscar_jogos_live(aba_code)
        kb = [[InlineKeyboardButton(f"🔴 {j['Mandante_Nome']} {j['Placar_Mandante']}x{j['Placar_Visitante']} {j['Visitante_Nome']}", callback_data=f"JOGO|{aba_code}|LIVE|{i}")] for i, j in enumerate(jogos)]
    
    context.chat_data[f"{aba_code}_jogos_{status.lower()}"] = jogos
    kb.append([InlineKeyboardButton("⬅️ Voltar", callback_data=f"c|{aba_code}")])
    await update.callback_query.edit_message_text("Selecione a Partida:", reply_markup=InlineKeyboardMarkup(kb))

async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    
    if data.startswith("c|"):
        await mostrar_menu_status_jogo(update, context, data.split('|')[1])
    elif data.startswith("STATUS|"):
        _, status, aba = data.split('|'); await listar_jogos(update, context, aba, status)
    elif data.startswith("JOGO|"):
        _, aba, status, idx = data.split('|')
        jogo = context.chat_data[f"{aba}_jogos_{status.lower()}"][int(idx)]
        context.chat_data.update({'m': jogo['Mandante_Nome'], 'v': jogo['Visitante_Nome'], 'aba': aba})
        kb = [[InlineKeyboardButton(f[0], callback_data=f"{f[1]}|{i}")] for i, f in enumerate(CONFRONTO_FILTROS)]
        kb.append([InlineKeyboardButton("⬅️ Voltar", callback_data=f"STATUS|{status}|{aba}")])
        await query.edit_message_text(f"Analisando: **{jogo['Mandante_Nome']} x {jogo['Visitante_Nome']}**", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
    elif data.startswith("STATS_FILTRO|"):
        idx = int(data.split('|')[1]); f = CONFRONTO_FILTROS[idx]
        dm = calcular_estatisticas_time(context.chat_data['m'], context.chat_data['aba'], f[2], f[3])
        dv = calcular_estatisticas_time(context.chat_data['v'], context.chat_data['aba'], f[2], f[4])
        await query.message.reply_text(f"🏆 **{f[0]}**\n\n{formatar_estatisticas(dm)}\n\n{formatar_estatisticas(dv)}", parse_mode='Markdown')
    elif data.startswith("RESULTADOS_FILTRO|"):
        idx = int(data.split('|')[1]); f = CONFRONTO_FILTROS[idx]
        rm = listar_ultimos_jogos(context.chat_data['m'], context.chat_data['aba'], f[2], f[3])
        rv = listar_ultimos_jogos(context.chat_data['v'], context.chat_data['aba'], f[2], f[4])
        await query.message.reply_text(f"📅 **{f[0]}**\n\n**{context.chat_data['m']}:**\n{rm}\n\n**{context.chat_data['v']}:**\n{rv}", parse_mode='Markdown')
    elif data == "VOLTAR_LIGA": await listar_competicoes(update, context)

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("stats", listar_competicoes))
    app.add_handler(CallbackQueryHandler(callback_query_handler))
    
    if client:
        app.job_queue.run_repeating(atualizar_planilhas, interval=3600, first=10)
    
    app.run_polling()

if __name__ == "__main__":
    main()
