# ===============================================================================
# 🏆 BOT DE ESTATÍSTICAS - V3.0 (BL1, BSA, DED, PL) - MODO POLLING (ANTI-HIBERNAR)
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

# Mapeamento Reduzido para as 4 ligas solicitadas
LIGAS_MAP = {
    "BSA": {"sheet_past": "BSA", "sheet_future": "BSA_FJ"},
    "BL1": {"sheet_past": "BL1", "sheet_future": "BL1_FJ"},
    "PL": {"sheet_past": "PL", "sheet_future": "PL_FJ"},
    "DED": {"sheet_past": "DED", "sheet_future": "DED_FJ"},
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
        logging.info("✅ Conexão GSheets estabelecida.")
        os.remove(tmp_file_path)
    except Exception as e:
        logging.error(f"❌ Erro GSheet: {e}")

# =================================================================================
# 💾 FUNÇÕES DE SUPORTE E API (Lógica idêntica ao original)
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
        jogos = []
        for row in linhas_raw[1:]:
            if len(row) >= 4:
                jogos.append({"Mandante_Nome": row[0], "Visitante_Nome": row[1], "Data_Hora": row[2], "Matchday": safe_int(row[3])})
        return jogos
    except: return []

def buscar_jogos(league_code, status_filter):
    try:
        url = f"https://api.football-data.org/v4/competitions/{league_code}/matches"
        params = {"status": status_filter} if status_filter != "ALL" else {}
        if league_code == "BSA": params["season"] = "2026"
        r = requests.get(url, headers={"X-Auth-Token": API_KEY}, params=params, timeout=10)
        r.raise_for_status()
        all_matches = r.json().get("matches", [])
        if status_filter == "ALL":
            return [m for m in all_matches if m.get('status') in ['SCHEDULED', 'TIMED']]
        jogos = []
        for m in all_matches:
            if m.get('status') == "FINISHED":
                ft = m.get("score", {}).get("fullTime", {}); ht = m.get("score", {}).get("halfTime", {})
                if ft.get("home") is None: continue
                gm, gv = ft.get("home", 0), ft.get("away", 0)
                gm1, gv1 = ht.get("home", 0), ht.get("away", 0)
                jogos.append({
                    "Mandante": m.get("homeTeam", {}).get("name"), "Visitante": m.get("awayTeam", {}).get("name"),
                    "Gols Mandante": gm, "Gols Visitante": gv, "Gols Mandante 1T": gm1, "Gols Visitante 1T": gv1,
                    "Gols Mandante 2T": gm-gm1, "Gols Visitante 2T": gv-gv1,
                    "Data": datetime.strptime(m['utcDate'][:10], "%Y-%m-%d").strftime("%d/%m/%Y")
                })
        return jogos
    except: return []

def buscar_jogos_live(league_code):
    hoje_utc = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    try:
        url = f"https://api.football-data.org/v4/competitions/{league_code}/matches?dateFrom={hoje_utc}&dateTo={hoje_utc}"
        r = requests.get(url, headers={"X-Auth-Token": API_KEY}, timeout=10)
        all_matches = r.json().get("matches", [])
        jogos = []
        for m in all_matches:
            if m.get('status') in LIVE_STATUSES:
                ft = m.get("score", {}).get("fullTime", {})
                jogos.append({
                    "Mandante_Nome": m.get("homeTeam", {}).get("name"), "Visitante_Nome": m.get("awayTeam", {}).get("name"),
                    "Placar_Mandante": ft.get("home", 0), "Placar_Visitante": ft.get("away", 0),
                    "Tempo_Jogo": m.get("minute", "Live"), "Matchday": safe_int(m.get("matchday", 0))
                })
        return jogos
    except: return []

async def atualizar_planilhas(context: ContextTypes.DEFAULT_TYPE):
    if not client: return
    try:
        sh = client.open_by_url(SHEET_URL)
        for aba_code, aba_config in LIGAS_MAP.items():
            jogos_fin = buscar_jogos(aba_code, "FINISHED")
            if jogos_fin:
                ws = sh.worksheet(aba_config['sheet_past'])
                exist = ws.get_all_records()
                keys = {(r['Mandante'], r['Visitante'], r['Data']) for r in exist}
                novas = [[j["Mandante"], j["Visitante"], j["Gols Mandante"], j["Gols Visitante"], j["Gols Mandante 1T"], j["Gols Visitante 1T"], j["Gols Mandante 2T"], j["Gols Visitante 2T"], j["Data"]] for j in jogos_fin if (j["Mandante"], j["Visitante"], j["Data"]) not in keys]
                if novas: ws.append_rows(novas)
            ws_f = sh.worksheet(aba_config['sheet_future'])
            ws_f.clear()
            ws_f.update(values=[['Mandante', 'Visitante', 'Data/Hora', 'Matchday']], range_name='A1:D1')
            jogos_fut = buscar_jogos(aba_code, "ALL")
            linhas_fut = [[m.get("homeTeam", {}).get("name"), m.get("awayTeam", {}).get("name"), m.get('utcDate'), m.get("matchday")] for m in jogos_fut]
            if linhas_fut: ws_f.append_rows(linhas_fut)
            await asyncio.sleep(2)
    except: pass

# =================================================================================
# 📈 CÁLCULOS ESTATÍSTICOS (Lógica idêntica ao original)
# =================================================================================

def calcular_estatisticas_time(time, aba, ultimos=None, casa_fora=None):
    d = {"time":time,"jogos_time":0,"jogos_casa":0,"jogos_fora":0,"over15":0,"over15_casa":0,"over15_fora":0, 
         "over25":0,"over25_casa":0,"over25_fora":0,"btts":0,"btts_casa":0,"btts_fora":0, "g_a_t":0,"g_a_t_casa":0,"g_a_t_fora":0,
         "gols_marcados":0,"gols_sofridos":0, "gols_marcados_casa":0,"gols_sofridos_casa":0,"gols_marcados_fora":0,"gols_sofridos_fora":0,
         "total_gols":0,"total_gols_casa":0,"total_gols_fora":0}
    try: 
        linhas = get_sheet_data(aba)
        if casa_fora=="casa": linhas = [l for l in linhas if l['Mandante']==time]
        elif casa_fora=="fora": linhas = [l for l in linhas if l['Visitante']==time]
        else: linhas = [l for l in linhas if l['Mandante']==time or l['Visitante']==time]
        linhas.sort(key=lambda x: datetime.strptime(x['Data'], "%d/%m/%Y"), reverse=False)
        if ultimos: linhas = linhas[-ultimos:]
        for l in linhas:
            em_casa = (time == l['Mandante']); gm, gv = safe_int(l['Gols Mandante']), safe_int(l['Gols Visitante'])
            gm1, gv1 = safe_int(l['Gols Mandante 1T']), safe_int(l['Gols Visitante 1T'])
            gm2, gv2 = gm-gm1, gv-gv1
            d["jogos_time"] += 1
            if em_casa: d["jogos_casa"] += 1; marcados, sofridos = gm, gv
            else: d["jogos_fora"] += 1; marcados, sofridos = gv, gm
            d["gols_marcados"] += marcados; d["gols_sofridos"] += sofridos
            if em_casa: d["gols_marcados_casa"] += marcados; d["gols_sofridos_casa"] += sofridos
            else: d["gols_marcados_fora"] += marcados; d["gols_sofridos_fora"] += sofridos
            d["total_gols"] += (gm+gv)
            if (gm+gv)>1.5: d["over15"] += 1; d["over15_casa" if em_casa else "over15_fora"] += 1
            if (gm+gv)>2.5: d["over25"] += 1; d["over25_casa" if em_casa else "over25_fora"] += 1
            if gm>0 and gv>0: d["btts"] += 1; d["btts_casa" if em_casa else "btts_fora"] += 1
            if (gm1+gv1)>0 and (gm2+gv2)>0: d["g_a_t"] += 1; d["g_a_t_casa" if em_casa else "g_a_t_fora"] += 1
        return d
    except: return d

def formatar_estatisticas(d):
    jt = d["jogos_time"]
    if jt == 0: return f"⚠️ Sem dados para **{d['time']}**."
    return (f"📊 **{escape_markdown(d['time'])}** ({jt}j)\n"
            f"⚽ O1.5: **{pct(d['over15'], jt)}** | O2.5: **{pct(d['over25'], jt)}**\n"
            f"🔁 BTTS: **{pct(d['btts'], jt)}** | 🥅 GAT: **{pct(d['g_a_t'], jt)}**\n"
            f"📈 Gols: {media(d['gols_marcados'], jt)} / {media(d['gols_sofridos'], jt)}")

def listar_ultimos_jogos(time, aba, ultimos=None, casa_fora=None):
    try:
        linhas = get_sheet_data(aba)
        if casa_fora == "casa": linhas = [l for l in linhas if l['Mandante'] == time]
        elif casa_fora == "fora": linhas = [l for l in linhas if l['Visitante'] == time]
        else: linhas = [l for l in linhas if l['Mandante'] == time or l['Visitante'] == time]
        linhas.sort(key=lambda x: datetime.strptime(x['Data'], "%d/%m/%Y"), reverse=False)
        if ultimos: linhas = linhas[-ultimos:]
        t = ""
        for l in linhas:
            gm, gv = l['Gols Mandante'], l['Gols Visitante']
            cor = "🟢" if (l['Mandante']==time and gm>gv) or (l['Visitante']==time and gv>gm) else ("🟡" if gm==gv else "🔴")
            t += f"{cor} {l['Data']}: {l['Mandante']} {gm}x{gv} {l['Visitante']}\n"
        return t or "Sem jogos."
    except: return "Erro ao ler jogos."

# =================================================================================
# 🤖 HANDLERS DO BOT (Polling / Anti-Hibernação)
# =================================================================================

async def listar_competicoes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Teclado ajustado apenas para as 4 ligas
    keyboard = [
        [InlineKeyboardButton("🇧🇷 BSA", callback_data="c|BSA"), InlineKeyboardButton("🇩🇪 BL1", callback_data="c|BL1")],
        [InlineKeyboardButton("🏴󠁧󠁢󠁥󠁮󠁧󠁿 PL", callback_data="c|PL"), InlineKeyboardButton("🇳🇱 DED", callback_data="c|DED")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    msg = "📊 **Escolha a Competição:**"
    if update.message: await update.message.reply_text(msg, reply_markup=reply_markup, parse_mode='Markdown')
    else: await update.callback_query.edit_message_text(msg, reply_markup=reply_markup, parse_mode='Markdown')

async def mostrar_menu_status_jogo(update: Update, context: ContextTypes.DEFAULT_TYPE, aba_code: str):
    keyboard = [[InlineKeyboardButton("🔴 AO VIVO", callback_data=f"STATUS|LIVE|{aba_code}")],
                [InlineKeyboardButton("📅 PRÓXIMOS", callback_data=f"STATUS|FUTURE|{aba_code}")],
                [InlineKeyboardButton("⬅️ Voltar", callback_data="VOLTAR_LIGA")]]
    await update.callback_query.edit_message_text(f"**{aba_code}** - Escolha:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def listar_jogos(update, context, aba, status):
    if status == "FUTURE": jogos = get_sheet_data_future(aba)
    else: jogos = buscar_jogos_live(aba)
    if not jogos: 
        await update.callback_query.edit_message_text("Sem jogos.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Voltar", callback_data=f"VOLTAR_LIGA_STATUS|{aba}")]]))
        return
    context.chat_data[f"{aba}_jogos_{status.lower()}"] = jogos[:MAX_GAMES_LISTED]
    keyboard = [[InlineKeyboardButton(f"{j['Mandante_Nome']} x {j['Visitante_Nome']}", callback_data=f"JOGO|{aba}|{status}|{i}")] for i, j in enumerate(jogos[:MAX_GAMES_LISTED])]
    keyboard.append([InlineKeyboardButton("⬅️ Voltar", callback_data=f"VOLTAR_LIGA_STATUS|{aba}")])
    await update.callback_query.edit_message_text("Selecione:", reply_markup=InlineKeyboardMarkup(keyboard))

async def callback_query_handler(update, context):
    query = update.callback_query; data = query.data
    try:
        if data.startswith("c|"): await mostrar_menu_status_jogo(update, context, data.split('|')[1])
        elif data.startswith("STATUS|"): await listar_jogos(update, context, data.split('|')[2], data.split('|')[1])
        elif data.startswith("JOGO|"):
            _, a, s, idx = data.split('|'); jogo = context.chat_data[f"{a}_jogos_{s.lower()}"][int(idx)]
            context.chat_data.update({'current_mandante': jogo['Mandante_Nome'], 'current_visitante': jogo['Visitante_Nome'], 'current_aba_code': a})
            kb = [[InlineKeyboardButton(f[0], callback_data=f"{f[1]}|{i}")] for i, f in enumerate(CONFRONTO_FILTROS)]
            kb.append([InlineKeyboardButton("⬅️ Voltar", callback_data=f"VOLTAR_LIGA_STATUS|{a}")])
            await query.message.reply_text(f"Filtros: {jogo['Mandante_Nome']} x {jogo['Visitante_Nome']}", reply_markup=InlineKeyboardMarkup(kb))
        elif data.startswith("STATS_FILTRO|"):
            idx = int(data.split('|')[1]); _, _, u, cm, cv = CONFRONTO_FILTROS[idx]
            m, v, a = context.chat_data['current_mandante'], context.chat_data['current_visitante'], context.chat_data['current_aba_code']
            txt = formatar_estatisticas(calcular_estatisticas_time(m, a, u, cm)) + "\n\n" + formatar_estatisticas(calcular_estatisticas_time(v, a, u, cv))
            await query.message.reply_text(txt, parse_mode='Markdown')
        elif data.startswith("RESULTADOS_FILTRO|"):
            idx = int(data.split('|')[1]); _, _, u, cm, cv = CONFRONTO_FILTROS[idx]
            m, v, a = context.chat_data['current_mandante'], context.chat_data['current_visitante'], context.chat_data['current_aba_code']
            txt = f"📅 {m}:\n{listar_ultimos_jogos(m, a, u, cm)}\n\n📅 {v}:\n{listar_ultimos_jogos(v, a, u, cv)}"
            await query.message.reply_text(txt, parse_mode='Markdown')
        elif data.startswith("VOLTAR_LIGA_STATUS|"): await mostrar_menu_status_jogo(update, context, data.split('|')[1])
        elif data == "VOLTAR_LIGA": await listar_competicoes(update, context)
    except: pass

def main():
    if not BOT_TOKEN: sys.exit(1)
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", lambda u, c: u.message.reply_text("Bot Ativo! /stats")))
    app.add_handler(CommandHandler("stats", listar_competicoes))
    app.add_handler(CallbackQueryHandler(callback_query_handler))
    
    if client:
        # Mantém o bot processando tarefas a cada hora para o Render ver atividade
        app.job_queue.run_repeating(atualizar_planilhas, interval=3600, first=10)
    
    logging.info("🤖 Bot iniciado em modo POLLING (Anti-Hibernação)")
    app.run_polling(drop_pending_updates=True) #

if __name__ == "__main__":
    main()
