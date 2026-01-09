# ===============================================================================
# 🏆 BOT DE ESTATÍSTICAS DE CONFRONTO V3.0 - FILTRAGEM DE ELITE (1.5+ GP / 1.0+ GC)
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

# Configuração de Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
nest_asyncio.apply()

# ===== Variáveis de Configuração =====
BOT_TOKEN = os.environ.get("BOT_TOKEN", "SEU_TOKEN_AQUI") 
API_KEY = os.environ.get("API_KEY", "SUA_API_KEY_AQUI")
SHEET_URL = os.environ.get("SHEET_URL", "https://docs.google.com/spreadsheets/d/1ChFFXQxo1qQElNzh2OC8-UPgofRXxyVWN06ExBQ3YqY/edit?usp=drivesdk")

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
ABAS_PASSADO = list(LIGAS_MAP.keys())

ULTIMOS = 10
SHEET_CACHE = {}
CACHE_DURATION_SECONDS = 3600 

# Filtros simplificados: Apenas Casa x Fora conforme solicitado
CONFRONTO_FILTROS = [
    (f"📊 Estatísticas | {ULTIMOS} (M CASA vs V FORA)", "STATS_FILTRO", ULTIMOS, "casa", "fora"),
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
        logging.error(f"Erro GSheet: {e}")

# =================================================================================
# 💾 FUNÇÕES DE SUPORTE
# =================================================================================
def safe_int(v):
    try: return int(v)
    except: return 0

def pct(part, total):
    return f"{(part/total)*100:.1f}%" if total>0 else "—"

def media_val(part, total):
    return (part/total) if total>0 else 0

def media_str(part, total):
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
        SHEET_CACHE[aba_name] = { 'data': linhas, 'timestamp': agora }
        return linhas
    except: return []

def get_sheet_data_future(aba_code):
    aba_name = LIGAS_MAP[aba_code]['sheet_future']
    try:
        sh = client.open_by_url(SHEET_URL)
        linhas_raw = sh.worksheet(aba_name).get_all_values()
        if not linhas_raw or len(linhas_raw) <= 1: return []
        return [{"Mandante_Nome": r[0], "Visitante_Nome": r[1], "Data_Hora": r[2], "Matchday": safe_int(r[3])} for r in linhas_raw[1:]]
    except: return []

# =================================================================================
# 📈 LÓGICA DE CÁLCULO E FILTRAGEM
# =================================================================================
def calcular_estatisticas_time(time, aba, ultimos=None, casa_fora=None):
    """Calcula estatísticas baseadas no histórico."""
    d = {"time":time,"jogos_time":0,"jogos_casa":0,"jogos_fora":0,
         "over15":0,"over15_casa":0,"over15_fora":0, "over25":0,"over25_casa":0,"over25_fora":0,
         "btts":0,"btts_casa":0,"btts_fora":0, "g_a_t":0,"g_a_t_casa":0,"g_a_t_fora":0,
         "gols_marcados":0,"gols_sofridos":0, "gols_marcados_casa":0,"gols_sofridos_casa":0,
         "gols_marcados_fora":0,"gols_sofridos_fora":0, "total_gols":0,
         "marcou_2_mais":0, "sofreu_2_mais":0, "marcou_ambos_tempos":0, "sofreu_ambos_tempos":0,
         "over05_1T":0, "over05_2T":0, "over15_2T":0, "gols_marcados_1T":0, "gols_sofridos_1T":0,
         "gols_marcados_2T":0, "gols_sofridos_2T":0}

    linhas = get_sheet_data(aba)
    if not linhas: return d

    if casa_fora=="casa": filtered = [l for l in linhas if l['Mandante']==time]
    elif casa_fora=="fora": filtered = [l for l in linhas if l['Visitante']==time]
    else: filtered = [l for l in linhas if l['Mandante']==time or l['Visitante']==time]

    try: filtered.sort(key=lambda x: datetime.strptime(x['Data'], "%d/%m/%Y"))
    except: pass

    if ultimos: filtered = filtered[-ultimos:]

    for linha in filtered:
        em_casa = (time == linha['Mandante'])
        gm, gv = safe_int(linha['Gols Mandante']), safe_int(linha['Gols Visitante'])
        gm1, gv1 = safe_int(linha['Gols Mandante 1T']), safe_int(linha['Gols Visitante 1T'])
        gm2, gv2 = gm-gm1, gv-gv1
        d["jogos_time"] += 1
        
        m, s = (gm, gv) if em_casa else (gv, gm)
        m1, s1 = (gm1, gv1) if em_casa else (gv1, gm1)
        m2, s2 = (gm2, gv2) if em_casa else (gv2, gm2)

        d["gols_marcados"] += m; d["gols_sofridos"] += s
        if em_casa: 
            d["jogos_casa"] += 1; d["gols_marcados_casa"] += m; d["gols_sofridos_casa"] += s
        else: 
            d["jogos_fora"] += 1; d["gols_marcados_fora"] += m; d["gols_sofridos_fora"] += s
        
        d["total_gols"] += (gm+gv)
        if (gm+gv) > 1.5: d["over15"] += 1
        if (gm+gv) > 2.5: d["over25"] += 1
        if gm > 0 and gv > 0: d["btts"] += 1
        if (m1+s1) > 0.5: d["over05_1T"] += 1
        if (m2+s2) > 0.5: d["over05_2T"] += 1
        if m >= 2: d["marcou_2_mais"] += 1
        if s >= 2: d["sofreu_2_mais"] += 1
        if m1 > 0 and m2 > 0: d["marcou_ambos_tempos"] += 1

    return d

def validar_confronto_elite(mandante, visitante, aba_code):
    """Verifica critério: Marcou 1.5+ e Sofreu 1.0+ no Geral E no Casa/Fora."""
    st_m_geral = calcular_estatisticas_time(mandante, aba_code)
    st_m_casa = calcular_estatisticas_time(mandante, aba_code, casa_fora="casa")
    st_v_geral = calcular_estatisticas_time(visitante, aba_code)
    st_v_fora = calcular_estatisticas_time(visitante, aba_code, casa_fora="fora")

    def check(s):
        if s['jogos_time'] == 0: return False
        return media_val(s['gols_marcados'], s['jogos_time']) >= 1.5 and \
               media_val(s['gols_sofridos'], s['jogos_time']) >= 1.0

    return all([check(st_m_geral), check(st_m_casa), check(st_v_geral), check(st_v_fora)])

# =================================================================================
# 🤖 HANDLERS DO TELEGRAM
# =================================================================================
async def listar_jogos(update: Update, context: ContextTypes.DEFAULT_TYPE, aba_code: str, status: str):
    query = update.callback_query
    if status == "FUTURE":
        await query.edit_message_text(f"🔍 Analisando elite na próxima rodada de **{aba_code}**...")
        jogos_futuros = get_sheet_data_future(aba_code)
        if not jogos_futuros: return await query.edit_message_text("⚠️ Sem jogos futuros.")

        proxima_rd = min(j['Matchday'] for j in jogos_futuros if j['Matchday'] > 0)
        jogos_rodada = [j for j in jogos_futuros if j['Matchday'] == proxima_rd]
        
        validados = []
        for j in jogos_rodada:
            if validar_confronto_elite(j['Mandante_Nome'], j['Visitante_Nome'], aba_code):
                validados.append(j)

        if not validados:
            keyboard = [[InlineKeyboardButton("⬅️ Voltar", callback_data=f"VOLTAR_LIGA_STATUS|{aba_code}")]]
            return await query.edit_message_text(f"❌ Nenhum jogo na Rodada {proxima_rd} atende aos critérios (1.5+ GP / 1.0+ GC).", reply_markup=InlineKeyboardMarkup(keyboard))

        context.chat_data[f"{aba_code}_jogos_future"] = validados
        keyboard = [[InlineKeyboardButton(f"💎 {j['Mandante_Nome']} x {j['Visitante_Nome']}", callback_data=f"JOGO|{aba_code}|FUTURE|{i}")] for i, j in enumerate(validados)]
        keyboard.append([InlineKeyboardButton("⬅️ Voltar", callback_data=f"VOLTAR_LIGA_STATUS|{aba_code}")])
        await query.edit_message_text(f"✅ **Rodada {proxima_rd} - Jogos de Valor**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    elif status == "LIVE":
        # Mantém lógica live original
        jogos = buscar_jogos_live(aba_code)
        if not jogos: return await query.edit_message_text("⚠️ Nenhum jogo ao vivo.")
        context.chat_data[f"{aba_code}_jogos_live"] = jogos
        keyboard = [[InlineKeyboardButton(f"🔴 {j['Tempo_Jogo']} | {j['Mandante_Nome']} {j['Placar_Mandante']}x{j['Placar_Visitante']} {j['Visitante_Nome']}", callback_data=f"JOGO|{aba_code}|LIVE|{i}")] for i, j in enumerate(jogos)]
        keyboard.append([InlineKeyboardButton("⬅️ Voltar", callback_data=f"VOLTAR_LIGA_STATUS|{aba_code}")])
        await query.edit_message_text("🎮 **Jogos em Andamento:**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def exibir_estatisticas(update: Update, context: ContextTypes.DEFAULT_TYPE, mandante: str, visitante: str, aba_code: str, filtro_idx: int):
    _, _, ultimos, cond_m, cond_v = CONFRONTO_FILTROS[filtro_idx]
    d_m = calcular_estatisticas_time(mandante, aba_code, ultimos=ultimos, casa_fora=cond_m)
    d_v = calcular_estatisticas_time(visitante, aba_code, ultimos=ultimos, casa_fora=cond_v)
    
    # Formatação visual
    def fmt(d, cond):
        jt = d['jogos_time']
        return (f"📊 **{escape_markdown(d['time'])}** ({cond.upper()})\n"
                f"⚽ Over 1.5: {pct(d['over15'], jt)} | Over 2.5: {pct(d['over25'], jt)}\n"
                f"🔁 BTTS: {pct(d['btts'], jt)} | 1ºT O0.5: {pct(d['over05_1T'], jt)}\n"
                f"➕ Média GP: {media_str(d['gols_marcados'], jt)} | ➖ Média GC: {media_str(d['gols_sofridos'], jt)}")

    texto = f"🔥 **ANÁLISE CASA X FORA (Últimos {ultimos})**\n\n{fmt(d_m, 'casa')}\n\n{fmt(d_v, 'fora')}"
    await update.effective_message.reply_text(texto, parse_mode='Markdown')
    # Re-exibe menu de ações conforme código original
    await mostrar_menu_acoes(update, context, aba_code, mandante, visitante)

# ===== Funções de apoio do bot (Start, Menu, Callbacks) =====
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⚽ **Bot Stats Elite**\nUse **/stats** para ver a próxima rodada filtrada.", parse_mode='Markdown')

async def listar_competicoes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = []
    abas = list(LIGAS_MAP.keys())
    for i in range(0, len(abas), 3):
        keyboard.append([InlineKeyboardButton(a, callback_data=f"c|{a}") for a in abas[i:i+3]])
    await (update.message.reply_text if update.message else update.callback_query.edit_message_text)("🏆 Escolha a Liga:", reply_markup=InlineKeyboardMarkup(keyboard))

async def mostrar_menu_status_jogo(update: Update, context: ContextTypes.DEFAULT_TYPE, aba_code: str):
    keyboard = [[InlineKeyboardButton("📅 PRÓXIMA RODADA (Elite)", callback_data=f"STATUS|FUTURE|{aba_code}")],
                [InlineKeyboardButton("🔴 AO VIVO", callback_data=f"STATUS|LIVE|{aba_code}")],
                [InlineKeyboardButton("⬅️ Voltar", callback_data="VOLTAR_LIGA")]]
    await update.callback_query.edit_message_text(f"📌 **{aba_code}**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def mostrar_menu_acoes(update: Update, context: ContextTypes.DEFAULT_TYPE, aba_code: str, mandante: str, visitante: str):
    keyboard = [[InlineKeyboardButton(label, callback_data=f"{tipo}|{idx}")] for idx, (label, tipo, _, _, _) in enumerate(CONFRONTO_FILTROS)]
    keyboard.append([InlineKeyboardButton("⬅️ Voltar", callback_data=f"VOLTAR_LIGA_STATUS|{aba_code}")])
    await update.effective_message.reply_text(f"🆚 {mandante} x {visitante}", reply_markup=InlineKeyboardMarkup(keyboard))

async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    if data.startswith("c|"): await mostrar_menu_status_jogo(update, context, data.split('|')[1])
    elif data.startswith("STATUS|"): await listar_jogos(update, context, data.split('|')[2], data.split('|')[1])
    elif data.startswith("JOGO|"):
        _, aba, status, idx = data.split('|')
        jogo = context.chat_data[f"{aba}_jogos_{status.lower()}"][int(idx)]
        context.chat_data.update({'current_mandante': jogo['Mandante_Nome'], 'current_visitante': jogo['Visitante_Nome'], 'current_aba_code': aba})
        await mostrar_menu_acoes(update, context, aba, jogo['Mandante_Nome'], jogo['Visitante_Nome'])
    elif data.startswith("STATS_FILTRO|"):
        await exibir_estatisticas(update, context, context.chat_data['current_mandante'], context.chat_data['current_visitante'], context.chat_data['current_aba_code'], int(data.split('|')[1]))
    elif data == "VOLTAR_LIGA": await listar_competicoes(update, context)
    elif data.startswith("VOLTAR_LIGA_STATUS|"): await mostrar_menu_status_jogo(update, context, data.split('|')[1])

# Inclusão da função buscar_jogos_live que faltava no escopo de colagem
def buscar_jogos_live(league_code):
    try:
        url = f"https://api.football-data.org/v4/competitions/{league_code}/matches"
        r = requests.get(url, headers={"X-Auth-Token": API_KEY}, timeout=10)
        matches = r.json().get("matches", [])
        return [{"Mandante_Nome": m['homeTeam']['name'], "Visitante_Nome": m['awayTeam']['name'], 
                 "Placar_Mandante": m['score']['fullTime']['home'], "Placar_Visitante": m['score']['fullTime']['away'],
                 "Tempo_Jogo": m.get('status')} for m in matches if m['status'] in LIVE_STATUSES]
    except: return []

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("stats", listar_competicoes))
    app.add_handler(CallbackQueryHandler(callback_query_handler))
    app.run_polling()

if __name__ == "__main__":
    main()
