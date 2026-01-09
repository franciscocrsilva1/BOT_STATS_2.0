# ===============================================================================
# 🏆 BOT DE ESTATÍSTICAS V3.1 - PRÓXIMA RODADA & FILTRO DE MÉDIAS
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
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
from gspread.exceptions import WorksheetNotFound

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
nest_asyncio.apply()

# ===== Configurações =====
BOT_TOKEN = os.environ.get("BOT_TOKEN", "SEU_TOKEN") 
API_KEY = os.environ.get("API_KEY", "SUA_API_KEY")
SHEET_URL = os.environ.get("SHEET_URL", "SUA_URL")

LIGAS_MAP = {
    "BSA": {"sheet_past": "BSA", "sheet_future": "BSA_FJ"},
    "BL1": {"sheet_past": "BL1", "sheet_future": "BL1_FJ"},
    "PL": {"sheet_past": "PL", "sheet_future": "PL_FJ"},
    "DED": {"sheet_past": "DED", "sheet_future": "DED_FJ"},
    "PD": {"sheet_past": "PD", "sheet_future": "PD_FJ"},
    "FL1": {"sheet_past": "FL1", "sheet_future": "FL1_FJ"},
    "ELC": {"sheet_past": "ELC", "sheet_future": "ELC_FJ"},
    "PPL": {"sheet_past": "PPL", "sheet_future": "PPL_FJ"},
    "SA": {"sheet_past": "SA", "sheet_future": "SA_FJ"},
}

SHEET_CACHE = {}
CACHE_DURATION_SECONDS = 3600 

# Filtros Fixos: Apenas 10 jogos Casa/Fora
CONFRONTO_FILTROS = [
    ("📊 Estatísticas | Últimos 10 (M Casa / V Fora)", "STATS_FILTRO", 10, "casa", "fora"),
    ("📅 Resultados | Últimos 10 (M Casa / V Fora)", "RESULTADOS_FILTRO", 10, "casa", "fora"),
]

# =================================================================================
# 🛠 FUNÇÕES DE APOIO E GSHEETS
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

CREDS_JSON = os.environ.get("GSPREAD_CREDS_JSON")
client = None
if CREDS_JSON:
    try:
        with tempfile.NamedTemporaryFile(mode="w", delete=False, encoding='utf-8') as tmp:
            tmp.write(CREDS_JSON)
            path = tmp.name
        client = gspread.authorize(ServiceAccountCredentials.from_json_keyfile_name(path, ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]))
        os.remove(path)
    except: client = None

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
    except: return []

# =================================================================================
# 🧠 LÓGICA DE FILTRAGEM (PRÓXIMA RODADA + MÉDIAS)
# =================================================================================

def validar_criterio_gols(time, aba_code):
    """Média Geral de: Marcados >= 1.5 E Sofridos >= 1.0"""
    linhas = get_sheet_data(aba_code)
    jogos_time = [l for l in linhas if l['Mandante'] == time or l['Visitante'] == time]
    if not jogos_time: return False
    
    total_gp, total_gc = 0, 0
    total_j = len(jogos_time)
    
    for l in jogos_time:
        if l['Mandante'] == time:
            total_gp += safe_int(l['Gols Mandante'])
            total_gc += safe_int(l['Gols Visitante'])
        else:
            total_gp += safe_int(l['Gols Visitante'])
            total_gc += safe_int(l['Gols Mandante'])
            
    return (total_gp / total_j) >= 1.5 and (total_gc / total_j) >= 1.0

async def listar_jogos_proxima_rodada(update: Update, context: ContextTypes.DEFAULT_TYPE, aba_code: str):
    await update.callback_query.edit_message_text(f"🔍 Escaneando a PRÓXIMA RODADA de {aba_code}...")
    
    try:
        sh = client.open_by_url(SHEET_URL)
        ws_future = sh.worksheet(LIGAS_MAP[aba_code]['sheet_future'])
        todos_futuros = ws_future.get_all_records()
    except:
        await update.callback_query.edit_message_text("❌ Erro ao acessar planilha de jogos futuros.")
        return

    agora_utc = datetime.now(timezone.utc)
    
    # 1. Identificar qual é o Matchday da Próxima Rodada
    proximos_jogos = []
    for j in todos_futuros:
        try:
            data_j = datetime.strptime(j['Data/Hora'][:16], '%Y-%m-%dT%H:%M').replace(tzinfo=timezone.utc)
            if data_j > agora_utc:
                proximos_jogos.append(j)
        except: continue
    
    if not proximos_jogos:
        await update.callback_query.edit_message_text("❌ Nenhum jogo futuro encontrado.")
        return

    # Pega o menor Matchday entre os jogos que ainda vão acontecer
    proxima_rodada_num = min([safe_int(j.get('Matchday', 999)) for j in proximos_jogos])
    
    # 2. Filtrar apenas os jogos dessa rodada específica que atendem ao critério de gols
    jogos_finais = []
    for j in proximos_jogos:
        if safe_int(j.get('Matchday')) == proxima_rodada_num:
            if validar_criterio_gols(j['Mandante'], aba_code) and validar_criterio_gols(j['Visitante'], aba_code):
                jogos_finais.append(j)

    if not jogos_finais:
        await update.callback_query.edit_message_text(f"⚠️ Rodada {proxima_rodada_num}: Nenhum jogo cumpre o critério (1.5+ GP / 1.0+ GC).", 
                                                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Voltar", callback_data="VOLTAR_LIGA")]]))
        return

    context.chat_data[f"{aba_code}_filtered"] = jogos_finais
    keyboard = []
    for idx, j in enumerate(jogos_finais):
        label = f"{j['Mandante']} x {j['Visitante']}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"J|{aba_code}|{idx}")])
    keyboard.append([InlineKeyboardButton("⬅️ Voltar", callback_data="VOLTAR_LIGA")])
    
    await update.callback_query.edit_message_text(f"🏟 **RODADA {proxima_rodada_num}**\nJogos dentro do seu critério:", 
                                                 reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

# =================================================================================
# 🎯 HANDLERS E ESTATÍSTICAS (ÚLTIMOS 10)
# =================================================================================

def calcular_estatisticas_time(time, aba, ultimos=10, casa_fora=None):
    d = {"time":time,"jogos_time":0,"over15":0,"over25":0,"btts":0,"g_a_t":0,
         "over05_1T":0,"over15_2T":0,"gols_marcados":0,"gols_sofridos":0,"total_gols":0,
         "marcou_2_mais":0,"sofreu_2_mais":0}
    
    linhas = get_sheet_data(aba)
    if casa_fora=="casa": linhas = [l for l in linhas if l['Mandante']==time]
    elif casa_fora=="fora": linhas = [l for l in linhas if l['Visitante']==time]
    else: linhas = [l for l in linhas if l['Mandante']==time or l['Visitante']==time]
    
    linhas.sort(key=lambda x: datetime.strptime(x['Data'], "%d/%m/%Y"))
    linhas = linhas[-ultimos:]
    
    for l in linhas:
        em_casa = (time == l['Mandante'])
        gm, gv = safe_int(l['Gols Mandante']), safe_int(l['Gols Visitante'])
        gm1, gv1 = safe_int(l['Gols Mandante 1T']), safe_int(l['Gols Visitante 1T'])
        gm2, gv2 = gm-gm1, gv-gv1
        m, s = (gm, gv) if em_casa else (gv, gm)
        
        d["jogos_time"] += 1
        d["gols_marcados"] += m; d["gols_sofridos"] += s; d["total_gols"] += (gm+gv)
        if (gm+gv)>1.5: d["over15"] += 1
        if (gm+gv)>2.5: d["over25"] += 1
        if gm>0 and gv>0: d["btts"] += 1
        if (gm1+gv1)>0.5: d["over05_1T"] += 1
        if (gm2+gv2)>1.5: d["over15_2T"] += 1
        if (gm1+gv1)>0 and (gm2+gv2)>0: d["g_a_t"] += 1
        if m>=2: d["marcou_2_mais"] += 1
        if s>=2: d["sofreu_2_mais"] += 1
    return d

def formatar_estatisticas(d):
    jt = d["jogos_time"]
    return (f"📊 **{escape_markdown(d['time'])}** (Últimos {jt}j)\n"
            f"⚽ O1.5: **{pct(d['over15'], jt)}** | O2.5: **{pct(d['over25'], jt)}**\n"
            f"🔁 BTTS: **{pct(d['btts'], jt)}** | GAT: {pct(d['g_a_t'], jt)}\n"
            f"📈 Marcou 2+: **{pct(d['marcou_2_mais'], jt)}** | Sofreu 2+: **{pct(d['sofreu_2_mais'], jt)}**\n"
            f"⏱️ 1T O0.5: {pct(d['over05_1T'], jt)} | 2T O1.5: {pct(d['over15_2T'], jt)}\n"
            f"➕ Média GP: {media(d['gols_marcados'], jt)} | ➖ Média GC: {media(d['gols_sofridos'], jt)}")

def listar_ultimos_jogos(time, aba, ultimos, casa_fora):
    linhas = get_sheet_data(aba)
    if casa_fora == "casa": linhas = [l for l in linhas if l['Mandante'] == time]
    else: linhas = [l for l in linhas if l['Visitante'] == time]
    linhas.sort(key=lambda x: datetime.strptime(x['Data'], "%d/%m/%Y"))
    linhas = linhas[-ultimos:]
    txt = ""
    for l in linhas:
        gm, gv = safe_int(l['Gols Mandante']), safe_int(l['Gols Visitante'])
        res = "🟢" if (l['Mandante']==time and gm>gv) or (l['Visitante']==time and gv>gm) else ("🟡" if gm==gv else "🔴")
        txt += f"{res} {l['Data']}: {l['Mandante']} {gm}x{gv} {l['Visitante']}\n"
    return txt if txt else "Sem histórico."

async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    d = q.data
    
    if d == "VOLTAR_LIGA":
        keyboard = []
        abas = list(LIGAS_MAP.keys())
        for i in range(0, len(abas), 3):
            keyboard.append([InlineKeyboardButton(a, callback_data=f"c|{a}") for a in abas[i:i+3]])
        await q.edit_message_text("Escolha a Liga:", reply_markup=InlineKeyboardMarkup(keyboard))
        
    elif d.startswith("c|"):
        await listar_jogos_proxima_rodada(update, context, d.split('|')[1])
        
    elif d.startswith("J|"):
        _, aba, idx = d.split('|')
        jogo = context.chat_data[f"{aba}_filtered"][int(idx)]
        context.chat_data.update({'m': jogo['Mandante'], 'v': jogo['Visitante'], 'aba': aba})
        keyboard = [[InlineKeyboardButton(f[0], callback_data=f"F|{i}")] for i, f in enumerate(CONFRONTO_FILTROS)]
        keyboard.append([InlineKeyboardButton("⬅️ Voltar", callback_data=f"c|{aba}")])
        await q.message.reply_text(f"📌 {jogo['Mandante']} x {jogo['Visitante']}", reply_markup=InlineKeyboardMarkup(keyboard))
        
    elif d.startswith("F|"):
        idx = int(d.split('|')[1])
        m, v, aba = context.chat_data['m'], context.chat_data['v'], context.chat_data['aba']
        _, tipo, u, cm, cv = CONFRONTO_FILTROS[idx]
        
        if "STATS" in tipo:
            txt = formatar_estatisticas(calcular_estatisticas_time(m, aba, u, cm)) + "\n\n" + \
                  formatar_estatisticas(calcular_estatisticas_time(v, aba, u, cv))
        else:
            txt = f"📅 **{m}** (Casa):\n{listar_ultimos_jogos(m, aba, u, cm)}\n\n📅 **{v}** (Fora):\n{listar_ultimos_jogos(v, aba, u, cv)}"
        await q.message.reply_text(txt, parse_mode='Markdown')

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = []
    abas = list(LIGAS_MAP.keys())
    for i in range(0, len(abas), 3):
        keyboard.append([InlineKeyboardButton(a, callback_data=f"c|{a}") for a in abas[i:i+3]])
    await update.message.reply_text("Escolha a Liga:", reply_markup=InlineKeyboardMarkup(keyboard))

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CallbackQueryHandler(callback_query_handler))
    app.run_polling()

if __name__ == "__main__":
    main()
