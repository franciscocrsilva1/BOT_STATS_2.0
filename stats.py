# ===============================================================================
# 🏆 BOT DE ESTATÍSTICAS V3.2 - PRÓXIMA RODADA + MÉDIAS + RENDER FIX
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

# ===== Configurações de Ambiente =====
BOT_TOKEN = os.environ.get("BOT_TOKEN", "SEU_TOKEN") 
API_KEY = os.environ.get("API_KEY", "SUA_API_KEY")
SHEET_URL = os.environ.get("SHEET_URL", "SUA_URL")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL") # URL do seu serviço no Render (ex: https://meubot.onrender.com)
PORT = int(os.environ.get("PORT", 8080))

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

CONFRONTO_FILTROS = [
    ("📊 Estatísticas | Últimos 10 (M Casa / V Fora)", "STATS_FILTRO", 10, "casa", "fora"),
    ("📅 Resultados | Últimos 10 (M Casa / V Fora)", "RESULTADOS_FILTRO", 10, "casa", "fora"),
]

# =================================================================================
# 🛠 GSHEETS CONEXÃO
# =================================================================================

CREDS_JSON = os.environ.get("GSPREAD_CREDS_JSON")
client = None
if CREDS_JSON:
    try:
        with tempfile.NamedTemporaryFile(mode="w", delete=False, encoding='utf-8') as tmp:
            tmp.write(CREDS_JSON)
            path = tmp.name
        client = gspread.authorize(ServiceAccountCredentials.from_json_keyfile_name(path, ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]))
        os.remove(path)
    except Exception as e:
        logging.error(f"Erro GSheets: {e}")

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
# 🧠 LÓGICA DE FILTRAGEM (RODADA + CRITÉRIOS)
# =================================================================================

def validar_criterio_gols(time, aba_code):
    linhas = get_sheet_data(aba_code)
    jogos_time = [l for l in linhas if l['Mandante'] == time or l['Visitante'] == time]
    if not jogos_time: return False
    
    total_gp, total_gc = 0, 0
    total_j = len(jogos_time)
    for l in jogos_time:
        if l['Mandante'] == time:
            total_gp += (int(l['Gols Mandante']) if l['Gols Mandante'] != '' else 0)
            total_gc += (int(l['Gols Visitante']) if l['Gols Visitante'] != '' else 0)
        else:
            total_gp += (int(l['Gols Visitante']) if l['Gols Visitante'] != '' else 0)
            total_gc += (int(l['Gols Mandante']) if l['Gols Mandante'] != '' else 0)
            
    return (total_gp / total_j) >= 1.5 and (total_gc / total_j) >= 1.0

async def listar_jogos_filtrados(update: Update, context: ContextTypes.DEFAULT_TYPE, aba_code: str):
    await update.callback_query.edit_message_text(f"🔍 Escaneando próxima rodada de {aba_code}...")
    try:
        sh = client.open_by_url(SHEET_URL)
        ws_fut = sh.worksheet(LIGAS_MAP[aba_code]['sheet_future'])
        todos = ws_fut.get_all_records()
    except:
        await update.callback_query.edit_message_text("❌ Erro ao acessar planilha.")
        return

    agora = datetime.now(timezone.utc)
    futuros = []
    for j in todos:
        try:
            dt = datetime.strptime(j['Data/Hora'][:16], '%Y-%m-%dT%H:%M').replace(tzinfo=timezone.utc)
            if dt > agora: futuros.append(j)
        except: continue
    
    if not futuros:
        await update.callback_query.edit_message_text("❌ Sem jogos futuros.")
        return

    # Pega apenas a exata próxima rodada
    rodada_alvo = min([int(j['Matchday']) for j in futuros if j.get('Matchday')])
    
    jogos_finais = []
    for j in futuros:
        if int(j['Matchday']) == rodada_alvo:
            if validar_criterio_gols(j['Mandante'], aba_code) and validar_criterio_gols(j['Visitante'], aba_code):
                jogos_finais.append(j)

    if not jogos_finais:
        await update.callback_query.edit_message_text(f"⚠️ Rodada {rodada_alvo}: Nenhum jogo com critério 1.5+ GP / 1.0+ GC.", 
                                                     reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Voltar", callback_data="VOLTAR")]]))
        return

    context.chat_data[f"{aba_code}_f"] = jogos_finais
    buttons = [[InlineKeyboardButton(f"{j['Mandante']} x {j['Visitante']}", callback_data=f"J|{aba_code}|{i}")] for i, j in enumerate(jogos_finais)]
    buttons.append([InlineKeyboardButton("⬅️ Voltar", callback_data="VOLTAR")])
    await update.callback_query.edit_message_text(f"🏟 **RODADA {rodada_alvo}**\nSelecione o confronto:", reply_markup=InlineKeyboardMarkup(buttons), parse_mode='Markdown')

# =================================================================================
# 🎯 CÁLCULO (ÚLTIMOS 10) E INTERFACE
# =================================================================================

def calc_10(time, aba, casa_fora):
    linhas = get_sheet_data(aba)
    if casa_fora == "casa": 
        linhas = [l for l in linhas if l['Mandante'] == time][-10:]
    else: 
        linhas = [l for l in linhas if l['Visitante'] == time][-10:]
    
    jt = len(linhas)
    if jt == 0: return None
    
    res = {"time": time, "j": jt, "o15": 0, "o25": 0, "btts": 0, "gp": 0, "gc": 0}
    for l in linhas:
        gm, gv = (int(l['Gols Mandante']) if l['Gols Mandante']!='' else 0), (int(l['Gols Visitante']) if l['Gols Visitante']!='' else 0)
        res["gp"] += (gm if casa_fora=="casa" else gv)
        res["gc"] += (gv if casa_fora=="casa" else gm)
        if (gm+gv) > 1.5: res["o15"] += 1
        if (gm+gv) > 2.5: res["o25"] += 1
        if gm > 0 and gv > 0: res["btts"] += 1
    return res

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    d = q.data
    
    if d == "VOLTAR":
        abas = list(LIGAS_MAP.keys())
        btns = [[InlineKeyboardButton(a, callback_data=f"c|{a}") for a in abas[i:i+3]] for i in range(0, len(abas), 3)]
        await q.edit_message_text("Escolha a Liga:", reply_markup=InlineKeyboardMarkup(btns))
        
    elif d.startswith("c|"):
        await listar_jogos_filtrados(update, context, d.split('|')[1])
        
    elif d.startswith("J|"):
        _, aba, idx = d.split('|')
        jogo = context.chat_data[f"{aba}_f"][int(idx)]
        context.chat_data.update({'m': jogo['Mandante'], 'v': jogo['Visitante'], 'aba': aba})
        btns = [[InlineKeyboardButton(f[0], callback_data=f"F|{i}")] for i, f in enumerate(CONFRONTO_FILTROS)]
        btns.append([InlineKeyboardButton("⬅️ Voltar", callback_data=f"c|{aba}")])
        await q.message.reply_text(f"📌 {jogo['Mandante']} x {jogo['Visitante']}", reply_markup=InlineKeyboardMarkup(btns))
        
    elif d.startswith("F|"):
        idx = int(d.split('|')[1])
        m_nome, v_nome, aba = context.chat_data['m'], context.chat_data['v'], context.chat_data['aba']
        
        if idx == 0: # Stats
            m = calc_10(m_nome, aba, "casa")
            v = calc_10(v_nome, aba, "fora")
            txt = f"📊 **{m_nome}** (Casa 10j):\nO1.5: {int(m['o15']*10)}% | O2.5: {int(m['o25']*10)}%\nBTTS: {int(m['btts']*10)}% | Média GP: {m['gp']/10:.2f}\n\n"
            txt += f"📊 **{v_nome}** (Fora 10j):\nO1.5: {int(v['o15']*10)}% | O2.5: {int(v['o25']*10)}%\nBTTS: {int(v['btts']*10)}% | Média GP: {v['gp']/10:.2f}"
            await q.message.reply_text(txt, parse_mode='Markdown')
        else: # Resultados
            await q.message.reply_text("Funcionalidade de lista de resultados simplificada para 10 jogos.")

async def start(u, c):
    abas = list(LIGAS_MAP.keys())
    btns = [[InlineKeyboardButton(a, callback_data=f"c|{a}") for a in abas[i:i+3]] for i in range(0, len(abas), 3)]
    await u.message.reply_text("Escolha a Liga:", reply_markup=InlineKeyboardMarkup(btns))

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("stats", start))
    app.add_handler(CallbackQueryHandler(callback_handler))
    
    if WEBHOOK_URL:
        # Configuração crucial para o Render
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=BOT_TOKEN,
            webhook_url=f"{WEBHOOK_URL}/{BOT_TOKEN}"
        )
    else:
        app.run_polling()

if __name__ == "__main__":
    main()
