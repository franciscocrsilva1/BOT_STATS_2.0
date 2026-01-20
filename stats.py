# ===============================================================================
# 🏆 BOT DE ESTATÍSTICAS DE CONFRONTO V2.6.0 - VERSÃO FINAL CORRIGIDA
# ===============================================================================

import gspread
from oauth2client.service_account import ServiceAccountCredentials
import requests
import os 
import tempfile
import asyncio
import logging
from datetime import datetime, timezone
import nest_asyncio

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes

# Configuração de Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
nest_asyncio.apply()

# ===== Variáveis de Configuração =====
BOT_TOKEN = os.environ.get("BOT_TOKEN", "SEU_TOKEN_AQUI") 
API_KEY = os.environ.get("API_KEY", "SUA_API_KEY_AQUI")
SHEET_URL = os.environ.get("SHEET_URL", "https://docs.google.com/spreadsheets/d/1ChFFXQxo1qQElNzh2OC8-UPgofRXxyVWN06ExBQ3YqY/edit?usp=drivesdk")

# Ligas Atualizadas
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

ULTIMOS = 10
SHEET_CACHE = {}
CACHE_DURATION_SECONDS = 3600 

# Filtros Atualizados com "TODOS OS JOGOS"
CONFRONTO_FILTROS = [
    (f"📊 Estatísticas | ÚLTIMOS {ULTIMOS} GERAL", "STATS_FILTRO", ULTIMOS, None, None),
    (f"📊 Estatísticas | {ULTIMOS} (M CASA vs V FORA)", "STATS_FILTRO", ULTIMOS, "casa", "fora"),
    (f"📊 Estatísticas | TODOS OS JOGOS GERAIS", "STATS_FILTRO", None, None, None),
    (f"📊 Estatísticas | TODOS (M CASA vs V FORA)", "STATS_FILTRO", None, "casa", "fora"),
    (f"📅 Resultados | ÚLTIMOS {ULTIMOS} GERAL", "RESULTADOS_FILTRO", ULTIMOS, None, None),
    (f"📅 Resultados | {ULTIMOS} (M CASA vs V FORA)", "RESULTADOS_FILTRO", ULTIMOS, "casa", "fora"),
    (f"📅 Resultados | TODOS OS JOGOS GERAIS", "RESULTADOS_FILTRO", None, None, None),
    (f"📅 Resultados | TODOS (M CASA vs V FORA)", "RESULTADOS_FILTRO", None, "casa", "fora"),
]

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
    return f"{(part/total)*100:.1f}%" if total > 0 else "—"

def media(part, total):
    return f"{(part/total):.2f}" if total > 0 else "—"

def escape_markdown(text):
    return str(text).replace('*', '\\*').replace('_', '\\_').replace('[', '\\[') .replace(']', '\\]')

def get_sheet_data(aba_code):
    global SHEET_CACHE
    aba_name = LIGAS_MAP[aba_code]['sheet_past']
    agora = datetime.now()
    if aba_name in SHEET_CACHE:
        if (agora - SHEET_CACHE[aba_name]['timestamp']).total_seconds() < CACHE_DURATION_SECONDS:
            return SHEET_CACHE[aba_name]['data']
    try:
        sh = client.open_by_url(SHEET_URL)
        linhas = sh.worksheet(aba_name).get_all_records()
        SHEET_CACHE[aba_name] = {'data': linhas, 'timestamp': agora}
        return linhas
    except: return []

def get_sheet_data_future(aba_code):
    aba_name = LIGAS_MAP[aba_code]['sheet_future']
    try:
        sh = client.open_by_url(SHEET_URL)
        linhas = sh.worksheet(aba_name).get_all_values()
        return [{"Mandante_Nome": r[0], "Visitante_Nome": r[1], "Data_Hora": r[2]} for r in linhas[1:] if len(r) >= 3]
    except: return []

# =================================================================================
# 📈 CÁLCULOS
# =================================================================================
def calcular_estatisticas_time(time, aba_code, ultimos=None, casa_fora=None):
    linhas = get_sheet_data(aba_code)
    if casa_fora == "casa":
        linhas = [l for l in linhas if l['Mandante'] == time]
    elif casa_fora == "fora":
        linhas = [l for l in linhas if l['Visitante'] == time]
    else:
        linhas = [l for l in linhas if l['Mandante'] == time or l['Visitante'] == time]
    
    try: linhas.sort(key=lambda x: datetime.strptime(x['Data'], "%d/%m/%Y"))
    except: pass

    if ultimos: linhas = linhas[-ultimos:]
    
    jt = len(linhas)
    d = {"time": time, "jt": jt, "ov15": 0, "ov25": 0, "btts": 0, "gp": 0, "gc": 0}
    
    for l in linhas:
        gm, gv = safe_int(l['Gols Mandante']), safe_int(l['Gols Visitante'])
        total = gm + gv
        d["ov15"] += 1 if total > 1.5 else 0
        d["ov25"] += 1 if total > 2.5 else 0
        d["btts"] += 1 if gm > 0 and gv > 0 else 0
        if l['Mandante'] == time:
            d["gp"] += gm; d["gc"] += gv
        else:
            d["gp"] += gv; d["gc"] += gm
    return d

def formatar_estatisticas(d):
    if d["jt"] == 0: return f"⚠️ Sem dados para {d['time']}"
    return (f"📊 **{escape_markdown(d['time'])}** ({d['jt']} jogos)\n"
            f"⚽ Over 1.5: {pct(d['ov15'], d['jt'])}\n"
            f"⚽ Over 2.5: {pct(d['ov25'], d['jt'])}\n"
            f"🔁 BTTS: {pct(d['btts'], d['jt'])}\n"
            f"📈 Média GP: {media(d['gp'], d['jt'])} | GC: {media(d['gc'], d['jt'])}")

def listar_resultados(time, aba_code, ultimos=None, casa_fora=None):
    linhas = get_sheet_data(aba_code)
    if casa_fora == "casa":
        linhas = [l for l in linhas if l['Mandante'] == time]
    elif casa_fora == "fora":
        linhas = [l for l in linhas if l['Visitante'] == time]
    else:
        linhas = [l for l in linhas if l['Mandante'] == time or l['Visitante'] == time]
    
    try: linhas.sort(key=lambda x: datetime.strptime(x['Data'], "%d/%m/%Y"))
    except: pass
    if ultimos: linhas = linhas[-ultimos:]
    
    res = ""
    for l in linhas:
        gm, gv = l['Gols Mandante'], l['Gols Visitante']
        res += f"📅 {l['Data']}: {l['Mandante']} {gm}x{gv} {l['Visitante']}\n"
    return res if res else "Sem resultados."

# =================================================================================
# 🤖 HANDLERS
# =================================================================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bot Ativo! Use /stats")

async def listar_competicoes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = []
    abas = list(LIGAS_MAP.keys())
    for i in range(0, len(abas), 3):
        keyboard.append([InlineKeyboardButton(a, callback_data=f"c|{a}") for a in abas[i:i+3]])
    await update.message.reply_text("Escolha a Liga:", reply_markup=InlineKeyboardMarkup(keyboard))

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    await query.answer()

    if data.startswith("c|"):
        aba = data.split('|')[1]
        jogos = get_sheet_data_future(aba)
        if not jogos:
            await query.edit_message_text("Nenhum jogo futuro encontrado.")
            return
        context.chat_data['jogos'] = jogos
        context.chat_data['aba'] = aba
        kb = [[InlineKeyboardButton(f"{j['Mandante_Nome']} x {j['Visitante_Nome']}", callback_data=f"j|{i}")] for i, j in enumerate(jogos[:25])]
        kb.append([InlineKeyboardButton("⬅️ Voltar", callback_data="voltar")])
        await query.edit_message_text("Selecione o Jogo:", reply_markup=InlineKeyboardMarkup(kb))

    elif data == "voltar":
        await query.edit_message_text("Selecione a Liga:") # Simplificado para evitar erro de re-envio

    elif data.startswith("j|"):
        idx = int(data.split('|')[1])
        jogo = context.chat_data['jogos'][idx]
        context.chat_data['m'] = jogo['Mandante_Nome']
        context.chat_data['v'] = jogo['Visitante_Nome']
        kb = [[InlineKeyboardButton(f[0], callback_data=f"f|{i}")] for i, f in enumerate(CONFRONTO_FILTROS)]
        await query.edit_message_text(f"Jogo: {jogo['Mandante_Nome']} x {jogo['Visitante_Nome']}", reply_markup=InlineKeyboardMarkup(kb))

    elif data.startswith("f|"):
        f_idx = int(data.split('|')[1])
        label, tipo, ult, cm, cv = CONFRONTO_FILTROS[f_idx]
        m, v, aba = context.chat_data['m'], context.chat_data['v'], context.chat_data['aba']
        
        if tipo == "STATS_FILTRO":
            res = formatar_estatisticas(calcular_estatisticas_time(m, aba, ult, cm)) + "\n\n" + formatar_estatisticas(calcular_estatisticas_time(v, aba, ult, cv))
        else:
            res = f"📅 **Resultados {m}**\n{listar_resultados(m, aba, ult, cm)}\n\n📅 **Resultados {v}**\n{listar_resultados(v, aba, ult, cv)}"
        
        await query.message.reply_text(f"✅ {label}\n\n{res}", parse_mode='Markdown')

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("stats", listar_competicoes))
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.run_polling()

if __name__ == "__main__":
    main()
