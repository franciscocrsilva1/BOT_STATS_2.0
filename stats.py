# ===============================================================================
# 🏆 BOT DE ESTATÍSTICAS DE CONFRONTO V2.4.1 - LIGA BSA
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
ULTIMOS = 10
SHEET_CACHE = {}
CACHE_DURATION_SECONDS = 3600
MAX_GAMES_LISTED = 30

# NOVOS FILTROS (Gerais e Últimos 10)
CONFRONTO_FILTROS = [
    ("📊 Estatísticas | GERAIS", "STATS_FILTRO", None, None, None),
    ("📊 Estatísticas | GERAIS (M CASA vs V FORA)", "STATS_FILTRO", None, "casa", "fora"),
    (f"📊 Estatísticas | ÚLTIMOS {ULTIMOS} GERAL", "STATS_FILTRO", ULTIMOS, None, None),
    (f"📊 Estatísticas | {ULTIMOS} (M CASA vs V FORA)", "STATS_FILTRO", ULTIMOS, "casa", "fora"),
    ("📅 Resultados | GERAIS", "RESULTADOS_FILTRO", None, None, None),
    ("📅 Resultados | GERAIS (M CASA vs V FORA)", "RESULTADOS_FILTRO", None, "casa", "fora"),
    (f"📅 Resultados | ÚLTIMOS {ULTIMOS} GERAL", "RESULTADOS_FILTRO", ULTIMOS, None, None),
    (f"📅 Resultados | {ULTIMOS} (M CASA vs V FORA)", "RESULTADOS_FILTRO", ULTIMOS, "casa", "fora"),
]

LIVE_STATUSES = ["IN_PLAY", "HALF_TIME", "PAUSED"]

# ✅ CONEXÃO GSHEETS
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
        logging.info("✅ Conexão GSheets estabelecida.")
        os.remove(tmp_file_path)
    except Exception as e:
        logging.error(f"❌ Erro GSheets: {e}")

# ===== FUNÇÕES DE SUPORTE =====
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
        SHEET_CACHE[aba_name] = {'data': linhas, 'timestamp': agora}
        return linhas
    except: return SHEET_CACHE.get(aba_name, {}).get('data', [])

def get_sheet_data_future(aba_code):
    aba_name = LIGAS_MAP[aba_code]['sheet_future']
    if not client: return []
    try:
        sh = client.open_by_url(SHEET_URL)
        linhas = sh.worksheet(aba_name).get_all_values()
        if len(linhas) <= 1: return []
        return [{"Mandante_Nome": r[0], "Visitante_Nome": r[1], "Data_Hora": r[2], "Matchday": safe_int(r[3])} for r in linhas[1:]]
    except: return []

# ===== API E ATUALIZAÇÃO =====
def buscar_jogos(league_code, status_filter):
    try:
        url = f"https://api.football-data.org/v4/competitions/{league_code}/matches"
        params = {"season": "2026"} if league_code == "BSA" else {}
        if status_filter != "ALL": params["status"] = status_filter
        r = requests.get(url, headers={"X-Auth-Token": API_KEY}, params=params, timeout=10)
        all_matches = r.json().get("matches", [])
        if status_filter == "ALL": return [m for m in all_matches if m.get('status') in ['SCHEDULED', 'TIMED']]
        jogos = []
        for m in all_matches:
            if m.get('status') == "FINISHED":
                ft = m.get("score", {}).get("fullTime", {}); ht = m.get("score", {}).get("halfTime", {})
                gm, gv = ft.get("home", 0), ft.get("away", 0)
                gm1, gv1 = ht.get("home", 0), ht.get("away", 0)
                jogos.append({"Mandante": m['homeTeam']['name'], "Visitante": m['awayTeam']['name'], "Gols Mandante": gm, "Gols Visitante": gv, "Gols Mandante 1T": gm1, "Gols Visitante 1T": gv1, "Data": m['utcDate'][:10]})
        return jogos
    except: return []

async def atualizar_planilhas(context: ContextTypes.DEFAULT_TYPE):
    if not client: return
    try:
        sh = client.open_by_url(SHEET_URL)
        for aba_code, config in LIGAS_MAP.items():
            ws_past = sh.worksheet(config['sheet_past'])
            novos = buscar_jogos(aba_code, "FINISHED")
            if novos:
                existentes = ws_past.get_all_records()
                keys = {(r['Mandante'], r['Visitante'], r['Data']) for r in existentes}
                upload = [[j['Mandante'], j['Visitante'], j['Gols Mandante'], j['Gols Visitante'], j['Gols Mandante 1T'], j['Gols Visitante 1T'], 0, 0, j['Data']] for j in novos if (j['Mandante'], j['Visitante'], j['Data']) not in keys]
                if upload: ws_past.append_rows(upload)
            
            ws_future = sh.worksheet(config['sheet_future'])
            futuros = buscar_jogos(aba_code, "ALL")
            if futuros:
                ws_future.clear()
                ws_future.update(values=[['Mandante', 'Visitante', 'Data/Hora', 'Matchday']] + [[f['homeTeam']['name'], f['awayTeam']['name'], f['utcDate'], f['matchday']] for f in futuros], range_name='A1')
    except Exception as e: logging.error(f"Erro update: {e}")

# ===== CÁLCULOS =====
def calcular_estatisticas_time(time, aba, ultimos=None, casa_fora=None):
    d = {"time":time,"jogos_time":0,"jogos_casa":0,"jogos_fora":0, "over15":0,"over15_casa":0,"over15_fora":0, "over25":0,"over25_casa":0,"over25_fora":0, "btts":0,"btts_casa":0,"btts_fora":0, "g_a_t":0,"g_a_t_casa":0,"g_a_t_fora":0, "over05_1T":0,"over05_1T_casa":0,"over05_1T_fora":0, "over05_2T":0,"over05_2T_casa":0,"over05_2T_fora":0, "gols_marcados":0,"gols_sofridos":0, "total_gols":0}
    linhas = get_sheet_data(aba)
    if casa_fora == "casa": linhas = [l for l in linhas if l['Mandante'] == time]
    elif casa_fora == "fora": linhas = [l for l in linhas if l['Visitante'] == time]
    else: linhas = [l for l in linhas if l['Mandante'] == time or l['Visitante'] == time]
    
    if ultimos: linhas = linhas[-ultimos:]
    
    for l in linhas:
        em_casa = (time == l['Mandante'])
        gm, gv = safe_int(l['Gols Mandante']), safe_int(l['Gols Visitante'])
        gm1, gv1 = safe_int(l['Gols Mandante 1T']), safe_int(l['Gols Visitante 1T'])
        total = gm + gv
        d["jogos_time"] += 1
        if em_casa: d["jogos_casa"] += 1
        else: d["jogos_fora"] += 1
        
        if total > 1.5: d["over15"] += 1; d["over15_casa" if em_casa else "over15_fora"] += 1
        if total > 2.5: d["over25"] += 1; d["over25_casa" if em_casa else "over25_fora"] += 1
        if gm > 0 and gv > 0: d["btts"] += 1; d["btts_casa" if em_casa else "btts_fora"] += 1
        if (gm1 + gv1) > 0.5: d["over05_1T"] += 1; d["over05_1T_casa" if em_casa else "over05_1T_fora"] += 1
        d["gols_marcados"] += gm if em_casa else gv
        d["gols_sofridos"] += gv if em_casa else gm
        d["total_gols"] += total
    return d

def formatar_estatisticas(d):
    jt = d["jogos_time"]
    if jt == 0: return f"⚠️ Sem dados para {d['time']}"
    return (f"📊 **{escape_markdown(d['time'])}** ({jt}j)\n"
            f"⚽ Over 1.5: {pct(d['over15'], jt)}\n"
            f"⚽ Over 2.5: {pct(d['over25'], jt)}\n"
            f"🔁 BTTS: {pct(d['btts'], jt)}\n"
            f"⏱️ 1ºT > 0.5: {pct(d['over05_1T'], jt)}\n"
            f"🔢 Média Gols: {media(d['total_gols'], jt)}")

def listar_ultimos_jogos(time, aba, ultimos=None, casa_fora=None):
    linhas = get_sheet_data(aba)
    if casa_fora == "casa": linhas = [l for l in linhas if l['Mandante'] == time]
    elif casa_fora == "fora": linhas = [l for l in linhas if l['Visitante'] == time]
    else: linhas = [l for l in linhas if l['Mandante'] == time or l['Visitante'] == time]
    if ultimos: linhas = linhas[-ultimos:]
    res = ""
    for l in linhas:
        gm, gv = l['Gols Mandante'], l['Gols Visitante']
        res += f"• {l['Data']}: {l['Mandante']} {gm}x{gv} {l['Visitante']}\n"
    return res or "Sem jogos."

# ===== HANDLERS =====
async def start(u: Update, c): await u.message.reply_text("BSA Bot Ativo. /stats")

async def menu_ligas(u: Update, c):
    kb = [[InlineKeyboardButton("Brasileirão Série A", callback_data="c|BSA")]]
    await u.effective_message.reply_text("Escolha:", reply_markup=InlineKeyboardMarkup(kb))

async def callback_handler(u: Update, context: ContextTypes.DEFAULT_TYPE):
    query = u.callback_query
    data = query.data
    if data.startswith("c|"):
        aba = data.split('|')[1]
        jogos = get_sheet_data_future(aba)
        context.chat_data['aba'] = aba
        context.chat_data['jogos'] = jogos
        kb = [[InlineKeyboardButton(f"{j['Mandante_Nome']} x {j['Visitante_Nome']}", callback_data=f"j|{i}")] for i, j in enumerate(jogos[:15])]
        await query.edit_message_text("Selecione o Jogo:", reply_markup=InlineKeyboardMarkup(kb))
    elif data.startswith("j|"):
        idx = int(data.split('|')[1])
        jogo = context.chat_data['jogos'][idx]
        context.chat_data.update({'m': jogo['Mandante_Nome'], 'v': jogo['Visitante_Nome']})
        kb = [[InlineKeyboardButton(f[0], callback_data=f"f|{i}")] for i, f in enumerate(CONFRONTO_FILTROS)]
        await query.edit_message_text(f"Jogo: {jogo['Mandante_Nome']} x {jogo['Visitante_Nome']}", reply_markup=InlineKeyboardMarkup(kb))
    elif data.startswith("f|"):
        f_idx = int(data.split('|')[1])
        m, v, aba = context.chat_data['m'], context.chat_data['v'], context.chat_data['aba']
        lbl, tipo, q, cm, cv = CONFRONTO_FILTROS[f_idx]
        if tipo == "STATS_FILTRO":
            txt = formatar_estatisticas(calcular_estatisticas_time(m, aba, q, cm)) + "\n\n" + formatar_estatisticas(calcular_estatisticas_time(v, aba, q, cv))
        else:
            txt = f"📅 **Resultados {m}**\n{listar_ultimos_jogos(m, aba, q, cm)}\n\n📅 **Resultados {v}**\n{listar_ultimos_jogos(v, aba, q, cv)}"
        await query.message.reply_text(txt, parse_mode='Markdown')

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", menu_ligas))
    app.add_handler(CallbackQueryHandler(callback_handler))
    
    # Render/Railway: Usar Polling se o Webhook estiver falhando
    if client: app.job_queue.run_repeating(atualizar_planilhas, interval=3600, first=0)
    
    print("Bot iniciado...")
    app.run_polling()

if __name__ == "__main__":
    main()
