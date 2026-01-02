# ===============================================================================
# 🏆 BOT DE ESTATÍSTICAS DE CONFRONTO V2.4.0 - TEMPORADA 2026 (SEM CL/ELC)
# ===============================================================================

# ===== Importações Essenciais =====
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

# Mapeamento de Ligas Atualizado (Removido CL e ELC | Temporadas Configuradas)
LIGAS_MAP = {
    "BSA": {"sheet_past": "BSA", "sheet_future": "BSA_FJ", "season": "2026"}, # Brasileirão 2026
    "BL1": {"sheet_past": "BL1", "sheet_future": "BL1_FJ", "season": "2025"},
    "PL": {"sheet_past": "PL", "sheet_future": "PL_FJ", "season": "2025"},
    "DED": {"sheet_past": "DED", "sheet_future": "DED_FJ", "season": "2025"},
    "PD": {"sheet_past": "PD", "sheet_future": "PD_FJ", "season": "2025"},
    "PPL": {"sheet_past": "PPL", "sheet_future": "PPL_FJ", "season": "2025"},
    "SA": {"sheet_past": "SA", "sheet_future": "SA_FJ", "season": "2025"},
    "FL1": {"sheet_past": "FL1", "sheet_future": "FL1_FJ", "season": "2025"},
}
ABAS_PASSADO = list(LIGAS_MAP.keys())

ULTIMOS = 10
SHEET_CACHE = {}
CACHE_DURATION_SECONDS = 3600
MAX_GAMES_LISTED = 30

CONFRONTO_FILTROS = [
    (f"📊 Estatísticas | ÚLTIMOS {ULTIMOS} GERAL", "STATS_FILTRO", ULTIMOS, None, None),
    (f"📊 Estatísticas | {ULTIMOS} (M CASA vs V FORA)", "STATS_FILTRO", ULTIMOS, "casa", "fora"),
    (f"📅 Resultados | ÚLTIMOS {ULTIMOS} GERAL", "RESULTADOS_FILTRO", ULTIMOS, None, None),
    (f"📅 Resultados | {ULTIMOS} (M CASA vs V FORA)", "RESULTADOS_FILTRO", ULTIMOS, "casa", "fora"),
]

LIVE_STATUSES = ["IN_PLAY", "HALF_TIME", "PAUSED"]

# =================================================================================
# ✅ CONEXÃO GSHEETS
# =================================================================================
CREDS_JSON = os.environ.get("GSPREAD_CREDS_JSON")
client = None

if CREDS_JSON:
    try:
        with tempfile.NamedTemporaryFile(mode="w", delete=False, encoding='utf-8') as tmp_file:
            tmp_file.write(CREDS_JSON)
            tmp_file_path = tmp_file.name
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name(tmp_file_path, scope)
        client = gspread.authorize(creds)
        os.remove(tmp_file_path)
    except Exception as e:
        logging.error(f"Erro GSheets: {e}")

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
    if not client: return []
    try:
        sh = client.open_by_url(SHEET_URL)
        linhas = sh.worksheet(aba_name).get_all_records()
        SHEET_CACHE[aba_name] = { 'data': linhas, 'timestamp': agora }
        return linhas
    except: return []

def get_sheet_data_future(aba_code):
    aba_name = LIGAS_MAP[aba_code]['sheet_future']
    if not client: return []
    try:
        sh = client.open_by_url(SHEET_URL)
        linhas_raw = sh.worksheet(aba_name).get_all_values()
        if not linhas_raw or len(linhas_raw) <= 1: return []
        return [{"Mandante_Nome": r[0], "Visitante_Nome": r[1], "Data_Hora": r[2], "Matchday": safe_int(r[3])} for r in linhas_raw[1:]]
    except: return []

# =================================================================================
# 🎯 FUNÇÕES DE API E ATUALIZAÇÃO (Ajustadas para Temporada)
# =================================================================================
def buscar_jogos(league_code, status_filter):
    """Busca jogos na API forçando a temporada configurada."""
    season = LIGAS_MAP[league_code].get("season", "2025")
    try:
        url = f"https://api.football-data.org/v4/competitions/{league_code}/matches?season={season}"
        if status_filter != "ALL": url += f"&status={status_filter}"

        r = requests.get(url, headers={"X-Auth-Token": API_KEY}, timeout=10)
        r.raise_for_status()
        all_matches = r.json().get("matches", [])
    except Exception as e:
        logging.error(f"Erro API {league_code}: {e}")
        return []

    if status_filter == "ALL":
        return [m for m in all_matches if m.get('status') in ['SCHEDULED', 'TIMED']]
    else:
        jogos = []
        for m in all_matches:
            if m.get('status') == "FINISHED":
                try:
                    ft = m.get("score", {}).get("fullTime", {}); ht = m.get("score", {}).get("halfTime", {})
                    if ft.get("home") is None: continue
                    gm, gv = ft.get("home",0), ft.get("away",0)
                    gm1, gv1 = ht.get("home",0), ht.get("away",0)
                    jogos.append({
                        "Mandante": m.get("homeTeam", {}).get("name", ""),
                        "Visitante": m.get("awayTeam", {}).get("name", ""),
                        "Gols Mandante": gm, "Gols Visitante": gv,
                        "Gols Mandante 1T": gm1, "Gols Visitante 1T": gv1,
                        "Gols Mandante 2T": gm-gm1, "Gols Visitante 2T": gv-gv1,
                        "Data": datetime.strptime(m['utcDate'][:10], "%Y-%m-%d").strftime("%d/%m/%Y")
                    })
                except: continue
        return sorted(jogos, key=lambda x: datetime.strptime(x['Data'], "%d/%m/%Y"))

def buscar_jogos_live(league_code):
    hoje_utc = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    try:
        url = f"https://api.football-data.org/v4/competitions/{league_code}/matches?dateFrom={hoje_utc}&dateTo={hoje_utc}"
        r = requests.get(url, headers={"X-Auth-Token": API_KEY}, timeout=10)
        r.raise_for_status()
        all_matches = r.json().get("matches", [])
        jogos = []
        for m in all_matches:
            if m.get('status') in LIVE_STATUSES:
                ft = m.get("score", {}).get("fullTime", {})
                jogos.append({
                    "Mandante_Nome": m.get("homeTeam", {}).get("name", ""),
                    "Visitante_Nome": m.get("awayTeam", {}).get("name", ""),
                    "Placar_Mandante": ft.get("home",0), "Placar_Visitante": ft.get("away",0),
                    "Tempo_Jogo": m.get("minute", "LIVE"), "Matchday": safe_int(m.get("matchday", 0))
                })
        return jogos
    except: return []

async def atualizar_planilhas(context: ContextTypes.DEFAULT_TYPE):
    if not client: return
    try: sh = client.open_by_url(SHEET_URL)
    except: return

    for aba_code, cfg in LIGAS_MAP.items():
        # Histórico
        try:
            ws_past = sh.worksheet(cfg['sheet_past'])
            jogos_fin = buscar_jogos(aba_code, "FINISHED")
            await asyncio.sleep(6) 
            if jogos_fin:
                exist = ws_past.get_all_records()
                keys = {(r['Mandante'], r['Visitante'], r['Data']) for r in exist}
                novos = [[j["Mandante"], j["Visitante"], j["Gols Mandante"], j["Gols Visitante"], j["Gols Mandante 1T"], j["Gols Visitante 1T"], j["Gols Mandante 2T"], j["Gols Visitante 2T"], j["Data"]] 
                         for j in jogos_fin if (j["Mandante"], j["Visitante"], j["Data"]) not in keys]
                if novos: ws_past.append_rows(novos)
        except: pass

        # Futuros (Filtro 2026 para BSA)
        try:
            ws_fut = sh.worksheet(cfg['sheet_future'])
            jogos_fut = buscar_jogos(aba_code, "ALL")
            await asyncio.sleep(6)
            ws_fut.clear()
            ws_fut.update(values=[['Mandante', 'Visitante', 'Data/Hora', 'Matchday']], range_name='A1:D1')
            linhas_f = []
            for m in jogos_fut:
                utc_date = m.get('utcDate', '')
                if utc_date:
                    dt = datetime.strptime(utc_date[:10], '%Y-%m-%d')
                    # Trava para garantir apenas 2026 no Brasileirão
                    if aba_code == "BSA" and dt.year < 2026: continue
                    if dt < datetime.now() + timedelta(days=90):
                        linhas_f.append([m["homeTeam"]["name"], m["awayTeam"]["name"], utc_date, m.get("matchday", "")])
            if linhas_f: ws_fut.append_rows(linhas_f, value_input_option='USER_ENTERED')
        except: pass

# =================================================================================
# (O Restante das funções de cálculo e handlers permanecem idênticos ao seu original)
# =================================================================================

def calcular_estatisticas_time(time, aba, ultimos=None, casa_fora=None):
    # Logica de cálculo original da v2.3.0
    d = {"time":time,"jogos_time":0,"jogos_casa":0,"jogos_fora":0,"over15":0,"over15_casa":0,"over15_fora":0,"over25":0,"over25_casa":0,"over25_fora":0,"btts":0,"btts_casa":0,"btts_fora":0,"g_a_t":0,"g_a_t_casa":0,"g_a_t_fora":0,"over05_1T":0,"over05_1T_casa":0,"over05_1T_fora":0,"over05_2T":0,"over05_2T_casa":0,"over05_2T_fora":0,"over15_2T":0,"over15_2T_casa":0,"over15_2T_fora":0,"gols_marcados":0,"gols_sofridos":0,"gols_marcados_casa":0,"gols_sofridos_casa":0,"gols_marcados_fora":0,"gols_sofridos_fora":0,"total_gols":0,"total_gols_casa":0,"total_gols_fora":0,"gols_marcados_1T":0,"gols_sofridos_1T":0,"gols_marcados_2T":0,"gols_sofridos_2T":0,"marcou_2_mais":0,"marcou_2_mais_casa":0,"marcou_2_mais_fora":0,"sofreu_2_mais":0,"sofreu_2_mais_casa":0,"sofreu_2_mais_fora":0,"marcou_ambos_tempos":0,"marcou_ambos_tempos_casa":0,"marcou_ambos_tempos_fora":0,"sofreu_ambos_tempos":0,"sofreu_ambos_tempos_casa":0,"sofreu_ambos_tempos_fora":0}
    linhas = get_sheet_data(aba)
    if not linhas: return d
    if casa_fora=="casa": linhas = [l for l in linhas if l['Mandante']==time]
    elif casa_fora=="fora": linhas = [l for l in linhas if l['Visitante']==time]
    else: linhas = [l for l in linhas if l['Mandante']==time or l['Visitante']==time]
    try: linhas.sort(key=lambda x: datetime.strptime(x['Data'], "%d/%m/%Y"))
    except: pass
    if ultimos: linhas = linhas[-ultimos:]
    for l in linhas:
        ec = (time == l['Mandante']); gm, gv = safe_int(l['Gols Mandante']), safe_int(l['Gols Visitante'])
        gm1, gv1 = safe_int(l['Gols Mandante 1T']), safe_int(l['Gols Visitante 1T'])
        gm2, gv2 = gm-gm1, gv-gv1; tot, tot1, tot2 = gm+gv, gm1+gv1, gm2+gv2
        d["jogos_time"] += 1
        if ec: d["jogos_casa"] += 1; marc, sofr = gm, gv; m1, s1 = gm1, gv1; m2, s2 = gm2, gv2
        else: d["jogos_fora"] += 1; marc, sofr = gv, gm; m1, s1 = gv1, gm1; m2, s2 = gv2, gm2
        d["gols_marcados"] += marc; d["gols_sofridos"] += sofr
        d["total_gols"] += tot
        if tot>1.5: d["over15"] += 1; d["over15_casa" if ec else "over15_fora"] += 1
        if tot>2.5: d["over25"] += 1; d["over25_casa" if ec else "over25_fora"] += 1
        if gm>0 and gv>0: d["btts"] += 1; d["btts_casa" if ec else "btts_fora"] += 1
        if tot1>0.5: d["over05_1T"] += 1; d["over05_1T_casa" if ec else "over05_1T_fora"] += 1
        if tot2>0.5: d["over05_2T"] += 1; d["over05_2T_casa" if ec else "over05_2T_fora"] += 1
        if m1>0 and m2>0: d["marcou_ambos_tempos"] += 1
    return d

def formatar_estatisticas(d):
    # Formatação original v2.3.0
    jt = d["jogos_time"]
    if jt == 0: return "⚠️ Sem dados."
    return f"📊 **{escape_markdown(d['time'])}**\nJogos: {jt}\nOver 1.5: {pct(d['over15'], jt)}\nOver 2.5: {pct(d['over25'], jt)}\nBTTS: {pct(d['btts'], jt)}\nMedia Gols: {media(d['total_gols'], jt)}"

def listar_ultimos_jogos(time, aba, ultimos=None, casa_fora=None):
    linhas = get_sheet_data(aba)
    if not linhas: return "⚠️ Erro."
    if casa_fora=="casa": linhas = [l for l in linhas if l['Mandante']==time]
    elif casa_fora=="fora": linhas = [l for l in linhas if l['Visitante']==time]
    else: linhas = [l for l in linhas if l['Mandante']==time or l['Visitante']==time]
    if ultimos: linhas = linhas[-ultimos:]
    txt = ""
    for l in linhas:
        gm, gv = l['Gols Mandante'], l['Gols Visitante']
        txt += f"• {l['Data']}: {l['Mandante']} {gm} x {gv} {l['Visitante']}\n"
    return txt

async def start_command(u, c): await u.message.reply_text("📊 Bot 2026 Ativo! Use /stats.")

async def listar_competicoes(u, c):
    kb = []
    abas = list(LIGAS_MAP.keys())
    for i in range(0, len(abas), 3):
        kb.append([InlineKeyboardButton(a, callback_data=f"c|{a}") for a in abas[i:i+3]])
    m = u.message if u.message else u.callback_query.message
    await m.reply_text("Escolha a Liga:", reply_markup=InlineKeyboardMarkup(kb))

async def mostrar_menu_status_jogo(u, c, aba_code):
    kb = [[InlineKeyboardButton("🔴 LIVE", callback_data=f"STATUS|LIVE|{aba_code}")],
          [InlineKeyboardButton("📅 PRÓXIMOS", callback_data=f"STATUS|FUTURE|{aba_code}")],
          [InlineKeyboardButton("⬅️ Voltar", callback_data="VOLTAR_LIGA")]]
    await u.callback_query.edit_message_text(f"Liga: {aba_code}", reply_markup=InlineKeyboardMarkup(kb))

async def listar_jogos(u, c, aba_code, status):
    if status == "FUTURE": jogos = get_sheet_data_future(aba_code)
    else: jogos = buscar_jogos_live(aba_code)
    if not jogos: await u.callback_query.edit_message_text("Sem jogos."); return
    c.chat_data['jogos_cache'] = jogos
    kb = []
    for i, j in enumerate(jogos[:20]):
        label = f"{j['Mandante_Nome']} x {j['Visitante_Nome']}"
        kb.append([InlineKeyboardButton(label, callback_data=f"JOGO|{aba_code}|{status}|{i}")])
    kb.append([InlineKeyboardButton("⬅️ Voltar", callback_data=f"c|{aba_code}")])
    await u.callback_query.edit_message_text("Escolha a partida:", reply_markup=InlineKeyboardMarkup(kb))

async def mostrar_menu_acoes(u, c, aba, m, v):
    kb = []
    for i, (label, _, _, _, _) in enumerate(CONFRONTO_FILTROS):
        kb.append([InlineKeyboardButton(label, callback_data=f"FILTRO|{i}")])
    await u.effective_message.reply_text(f"Jogo: {m} x {v}", reply_markup=InlineKeyboardMarkup(kb))

async def callback_query_handler(u, c):
    q = u.callback_query; d = q.data
    if d == "VOLTAR_LIGA": await listar_competicoes(u, c)
    elif d.startswith("c|"): await mostrar_menu_status_jogo(u, c, d.split('|')[1])
    elif d.startswith("STATUS|"): await listar_jogos(u, c, d.split('|')[2], d.split('|')[1])
    elif d.startswith("JOGO|"):
        _, aba, status, idx = d.split('|')
        jogo = c.chat_data['jogos_cache'][int(idx)]
        c.chat_data.update({'m': jogo['Mandante_Nome'], 'v': jogo['Visitante_Nome'], 'aba': aba})
        await mostrar_menu_acoes(u, c, aba, jogo['Mandante_Nome'], jogo['Visitante_Nome'])
    elif d.startswith("FILTRO|"):
        idx = int(d.split('|')[1]); label, tipo, ult, cm, cv = CONFRONTO_FILTROS[idx]
        m, v, aba = c.chat_data['m'], c.chat_data['v'], c.chat_data['aba']
        if "STATS" in label:
            res = formatar_estatisticas(calcular_estatisticas_time(m, aba, ult, cm)) + "\n\n" + formatar_estatisticas(calcular_estatisticas_time(v, aba, ult, cv))
        else:
            res = listar_ultimos_jogos(m, aba, ult, cm) + "\n---\n" + listar_ultimos_jogos(v, aba, ult, cv)
        await u.effective_message.reply_text(res)

async def forcaupdate_command(u, c):
    msg = await u.message.reply_text("⚡️ Atualizando para 2026...")
    await atualizar_planilhas(c)
    await msg.edit_text("✅ Planilhas Atualizadas!")

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("stats", listar_competicoes))
    app.add_handler(CommandHandler("forcaupdate", forcaupdate_command))
    app.add_handler(CallbackQueryHandler(callback_query_handler))
    
    if client:
        app.job_queue.run_repeating(atualizar_planilhas, interval=3600, first=10)
    
    # Rodar via Polling para teste ou Webhook para Render
    app.run_webhook(listen="0.0.0.0", port=int(os.environ.get("PORT", "8080")), url_path=BOT_TOKEN, webhook_url=os.environ.get("WEBHOOK_URL") + '/' + BOT_TOKEN)

if __name__ == "__main__":
    main()
