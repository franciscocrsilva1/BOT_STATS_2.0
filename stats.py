# ===============================================================================
# 🏆 BOT DE ESTATÍSTICAS BSA V2.4.3 - MODO POLLING (ESTÁVEL RENDER)
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

# Configuração de Logging para monitorar o Render
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)
nest_asyncio.apply()

# ===== Variáveis de Ambiente =====
BOT_TOKEN = os.environ.get("BOT_TOKEN") 
API_KEY = os.environ.get("API_KEY")
SHEET_URL = os.environ.get("SHEET_URL")
CREDS_JSON = os.environ.get("GSPREAD_CREDS_JSON")

LIGAS_MAP = {"BSA": {"sheet_past": "BSA", "sheet_future": "BSA_FJ"}}
ABAS_PASSADO = list(LIGAS_MAP.keys())
ULTIMOS = 10
SHEET_CACHE = {}
CACHE_DURATION_SECONDS = 3600 

# FILTROS REQUISITADOS
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

# ✅ CONEXÃO GSHEETS
client = None
if CREDS_JSON:
    try:
        with tempfile.NamedTemporaryFile(mode="w", delete=False, encoding='utf-8') as tmp:
            tmp.write(CREDS_JSON)
            tmp_path = tmp.name
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name(tmp_path, scope)
        client = gspread.authorize(creds)
        os.remove(tmp_path)
        logger.info("✅ Google Sheets conectado com sucesso.")
    except Exception as e:
        logger.error(f"❌ Falha GSheets: {e}")

# ===== FUNÇÕES LÓGICAS (MANTIDAS IDÊNTICAS AO SEU ORIGINAL) =====
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
    if not client: return []
    try:
        sh = client.open_by_url(SHEET_URL)
        linhas = sh.worksheet(LIGAS_MAP[aba_code]['sheet_future']).get_all_values()
        return [{"Mandante_Nome": r[0], "Visitante_Nome": r[1], "Data_Hora": r[2]} for r in linhas[1:]]
    except: return []

# ===== CÁLCULOS E ESTATÍSTICAS (LÓGICA ORIGINAL) =====
def calcular_estatisticas_time(time, aba, ultimos=None, casa_fora=None):
    d = {"time":time,"jogos_time":0,"jogos_casa":0,"jogos_fora":0, "over15":0,"over15_casa":0,"over15_fora":0, "over25":0,"over25_casa":0,"over25_fora":0, "btts":0,"btts_casa":0,"btts_fora":0, "g_a_t":0, "over05_1T":0, "gols_marcados":0,"gols_sofridos":0, "total_gols":0}
    linhas = get_sheet_data(aba)
    if casa_fora == "casa": linhas = [l for l in linhas if l['Mandante'] == time]
    elif casa_fora == "fora": linhas = [l for l in linhas if l['Visitante'] == time]
    else: linhas = [l for l in linhas if l['Mandante'] == time or l['Visitante'] == time]
    
    if ultimos: linhas = linhas[-ultimos:]
    
    for l in linhas:
        em_casa = (time == l['Mandante'])
        gm, gv = safe_int(l['Gols Mandante']), safe_int(l['Gols Visitante'])
        gm1, gv1 = safe_int(l['Gols Mandante 1T']), safe_int(l['Gols Visitante 1T'])
        gm2, gv2 = gm-gm1, gv-gv1
        total = gm + gv
        d["jogos_time"] += 1
        if total > 1.5: d["over15"] += 1
        if total > 2.5: d["over25"] += 1
        if gm > 0 and gv > 0: d["btts"] += 1
        if (gm1 + gv1) > 0.5: d["over05_1T"] += 1
        # G.A.T (Gol em ambos os tempos para o time analisado)
        if em_casa and gm1 > 0 and gm2 > 0: d["g_a_t"] += 1
        elif not em_casa and gv1 > 0 and gv2 > 0: d["g_a_t"] += 1
        
        d["gols_marcados"] += gm if em_casa else gv
        d["gols_sofridos"] += gv if em_casa else gm
        d["total_gols"] += total
    return d

def formatar_estatisticas(d):
    jt = d["jogos_time"]
    if jt == 0: return f"⚠️ Sem dados para {escape_markdown(d['time'])}"
    return (f"📊 **{escape_markdown(d['time'])}**\n"
            f"📅 Jogos: {jt}\n"
            f"⚽ Over 1.5: {pct(d['over15'], jt)}\n"
            f"⚽ Over 2.5: {pct(d['over25'], jt)}\n"
            f"🔁 BTTS: {pct(d['btts'], jt)}\n"
            f"🥅 G.A.T: {pct(d['g_a_t'], jt)}\n"
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
        res += f"• {l['Data']}: {l['Mandante']} {l['Gols Mandante']}x{l['Gols Visitante']} {l['Visitante']}\n"
    return res or "Sem registros."

# ===== HANDLERS DO BOT =====
async def start_command(u: Update, c): await u.message.reply_text("BSA Bot Online. Use /stats")

async def menu_ligas(u: Update, c):
    kb = [[InlineKeyboardButton("Brasileirão Série A", callback_data="c|BSA")]]
    await u.effective_message.reply_text("Escolha a competição:", reply_markup=InlineKeyboardMarkup(kb))

async def callback_handler(u: Update, context: ContextTypes.DEFAULT_TYPE):
    query = u.callback_query
    data = query.data
    try:
        if data.startswith("c|"):
            aba = data.split('|')[1]
            jogos = get_sheet_data_future(aba)
            context.chat_data.update({'aba': aba, 'jogos': jogos})
            kb = [[InlineKeyboardButton(f"{j['Mandante_Nome']} x {j['Visitante_Nome']}", callback_data=f"j|{i}")] for i, j in enumerate(jogos[:15])]
            await query.edit_message_text("Selecione o Jogo:", reply_markup=InlineKeyboardMarkup(kb))
        elif data.startswith("j|"):
            idx = int(data.split('|')[1])
            jogo = context.chat_data['jogos'][idx]
            context.chat_data.update({'m': jogo['Mandante_Nome'], 'v': jogo['Visitante_Nome']})
            kb = [[InlineKeyboardButton(f[0], callback_data=f"f|{i}")] for i, f in enumerate(CONFRONTO_FILTROS)]
            await query.edit_message_text(f"Partida: {jogo['Mandante_Nome']} x {jogo['Visitante_Nome']}", reply_markup=InlineKeyboardMarkup(kb))
        elif data.startswith("f|"):
            f_idx = int(data.split('|')[1])
            m, v, aba = context.chat_data['m'], context.chat_data['v'], context.chat_data['aba']
            lbl, tipo, q, cm, cv = CONFRONTO_FILTROS[f_idx]
            if tipo == "STATS_FILTRO":
                txt = formatar_estatisticas(calcular_estatisticas_time(m, aba, q, cm)) + "\n\n" + formatar_estatisticas(calcular_estatisticas_time(v, aba, q, cv))
            else:
                txt = f"📅 **{escape_markdown(m)}**\n{listar_ultimos_jogos(m, aba, q, cm)}\n\n📅 **{escape_markdown(v)}**\n{listar_ultimos_jogos(v, aba, q, cv)}"
            await query.message.reply_text(txt, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Erro no Callback: {e}")

def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN não encontrado!")
        return
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("stats", menu_ligas))
    app.add_handler(CallbackQueryHandler(callback_handler))
    
    logger.info("🚀 Iniciando Bot em modo Polling...")
    app.run_polling()

if __name__ == "__main__":
    main()
