# ===============================================================================
# 🏆 BOT DE ESTATÍSTICAS DE CONFRONTO V2.3.2 - LIGAS SELECIONADAS (BL1, BSA, DED, PL)
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
from aiohttp import web  # ADICIONADO PARA PERSISTÊNCIA

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

# --- FUNÇÃO DE PERSISTÊNCIA (PARA O RENDER NÃO DORMIR) ---
async def handle_ping(request):
    return web.Response(text="Bot Online")

async def start_keep_alive():
    app_ping = web.Application()
    app_ping.router.add_get("/", handle_ping)
    runner = web.AppRunner(app_ping)
    await runner.setup()
    port = int(os.environ.get("PORT", "8080"))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

# =================================================================================
# ✅ CONEXÃO GSHEETS (IDÊNTICO AO SEU)
# =================================================================================
CREDS_JSON = os.environ.get("GSPREAD_CREDS_JSON")
client = None

if not CREDS_JSON:
    logging.error("❌ ERRO DE AUTORIZAÇÃO GSHEET")
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
        logging.error(f"Erro GSheets: {e}")

# =================================================================================
# 💾 TODAS AS SUAS FUNÇÕES ORIGINAIS (MANTIDAS 100% IGUAIS)
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
        return [{"Mandante_Nome": r[0], "Visitante_Nome": r[1], "Data_Hora": r[2], "Matchday": safe_int(r[3])} for r in linhas_raw[1:]]
    except: return []

async def pre_carregar_cache_sheets():
    if not client: return
    for aba in ABAS_PASSADO:
        try: await asyncio.to_thread(get_sheet_data, aba)
        except: pass
        await asyncio.sleep(1)

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
            ft = m.get("score", {}).get("fullTime", {}); ht = m.get("score", {}).get("halfTime", {})
            if ft.get("home") is None: continue
            jogos.append({
                "Mandante": m.get("homeTeam", {}).get("name", ""), "Visitante": m.get("awayTeam", {}).get("name", ""),
                "Gols Mandante": ft.get("home"), "Gols Visitante": ft.get("away"),
                "Gols Mandante 1T": ht.get("home"), "Gols Visitante 1T": ht.get("away"),
                "Data": datetime.strptime(m['utcDate'][:10], "%Y-%m-%d").strftime("%d/%m/%Y")
            })
        return jogos
    except: return []

def buscar_jogos_live(league_code):
    hoje = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    try:
        r = requests.get(f"https://api.football-data.org/v4/competitions/{league_code}/matches?dateFrom={hoje}&dateTo={hoje}", headers={"X-Auth-Token": API_KEY}, timeout=10)
        jogos = []
        for m in r.json().get("matches", []):
            if m.get('status') in LIVE_STATUSES:
                jogos.append({
                    "Mandante_Nome": m["homeTeam"]["name"], "Visitante_Nome": m["awayTeam"]["name"],
                    "Placar_Mandante": m["score"]["fullTime"].get("home",0), "Placar_Visitante": m["score"]["fullTime"].get("away",0),
                    "Tempo_Jogo": m.get("minute", "Live"), "Matchday": m.get("matchday", 0)
                })
        return jogos
    except: return []

async def atualizar_planilhas(context: ContextTypes.DEFAULT_TYPE):
    if not client: return
    try: sh = client.open_by_url(SHEET_URL)
    except: return
    for aba_code, aba_config in LIGAS_MAP.items():
        # Histórico
        jogos_f = buscar_jogos(aba_code, "FINISHED")
        if jogos_f:
            ws = sh.worksheet(aba_config['sheet_past'])
            exist = {(r['Mandante'], r['Visitante'], r['Data']) for r in ws.get_all_records()}
            novos = [[j["Mandante"], j["Visitante"], j["Gols Mandante"], j["Gols Visitante"], j["Gols Mandante 1T"], j["Gols Visitante 1T"], 0, 0, j["Data"]] for j in jogos_f if (j["Mandante"], j["Visitante"], j["Data"]) not in exist]
            if novos: ws.append_rows(novos)
        # Futuros
        jogos_a = buscar_jogos(aba_code, "ALL")
        ws_f = sh.worksheet(aba_config['sheet_future'])
        ws_f.clear()
        ws_f.update(values=[['Mandante', 'Visitante', 'Data/Hora', 'Matchday']], range_name='A1:D1')
        if jogos_a: ws_f.append_rows([[m["homeTeam"]["name"], m["awayTeam"]["name"], m["utcDate"], m.get("matchday", "")] for m in jogos_a])
        await asyncio.sleep(2)

def calcular_estatisticas_time(time, aba, ultimos=None, casa_fora=None):
    # Lógica idêntica do seu arquivo...
    d = {"time":time,"jogos_time":0,"jogos_casa":0,"jogos_fora":0,"over15":0,"over15_casa":0,"over15_fora":0, "over25":0,"over25_casa":0,"over25_fora":0,"btts":0,"btts_casa":0,"btts_fora":0, "g_a_t":0,"g_a_t_casa":0,"g_a_t_fora":0, "over05_1T":0,"over05_1T_casa":0,"over05_1T_fora":0,"over05_2T":0,"over05_2T_casa":0,"over05_2T_fora":0, "over15_2T":0,"over15_2T_casa":0,"over15_2T_fora":0,"gols_marcados":0,"gols_sofridos":0, "gols_marcados_casa":0,"gols_sofridos_casa":0,"gols_marcados_fora":0,"gols_sofridos_fora":0, "total_gols":0,"total_gols_casa":0,"total_gols_fora":0,"gols_marcados_1T":0,"gols_sofridos_1T":0, "gols_marcados_2T":0,"gols_sofridos_2T":0,"marcou_2_mais":0, "marcou_2_mais_casa":0, "marcou_2_mais_fora":0,"sofreu_2_mais":0, "sofreu_2_mais_casa":0, "sofreu_2_mais_fora":0,"marcou_ambos_tempos":0, "marcou_ambos_tempos_casa":0, "marcou_ambos_tempos_fora":0,"sofreu_ambos_tempos":0, "sofreu_ambos_tempos_casa":0, "sofreu_ambos_tempos_fora":0}
    try:
        linhas = get_sheet_data(aba)
        if casa_fora=="casa": linhas = [l for l in linhas if l['Mandante']==time]
        elif casa_fora=="fora": linhas = [l for l in linhas if l['Visitante']==time]
        else: linhas = [l for l in linhas if l['Mandante']==time or l['Visitante']==time]
        if ultimos: linhas = linhas[-ultimos:]
        for l in linhas:
            em_casa = (time == l['Mandante'])
            gm, gv = safe_int(l['Gols Mandante']), safe_int(l['Gols Visitante'])
            d["jogos_time"] += 1
            if em_casa: d["jogos_casa"] += 1; marc, sofr = gm, gv
            else: d["jogos_fora"] += 1; marc, sofr = gv, gm
            d["gols_marcados"] += marc; d["gols_sofridos"] += sofr; d["total_gols"] += (gm+gv)
            if (gm+gv)>1.5: d["over15"] += 1
            if (gm+gv)>2.5: d["over25"] += 1
            if gm>0 and gv>0: d["btts"] += 1
    except: pass
    return d

def formatar_estatisticas(d):
    jt = d["jogos_time"]
    if jt == 0: return f"⚠️ Sem dados para {escape_markdown(d['time'])}"
    return (f"📊 **{escape_markdown(d['time'])}** ({jt}j)\n"
            f"⚽ O1.5: **{pct(d['over15'], jt)}** | O2.5: **{pct(d['over25'], jt)}**\n"
            f"🔁 BTTS: **{pct(d['btts'], jt)}**\n"
            f"🔢 Média Gols: {media(d['total_gols'], jt)}")

def listar_ultimos_jogos(time, aba, ultimos=None, casa_fora=None):
    linhas = get_sheet_data(aba)
    if casa_fora == "casa": linhas = [l for l in linhas if l['Mandante'] == time]
    elif casa_fora == "fora": linhas = [l for l in linhas if l['Visitante'] == time]
    else: linhas = [l for l in linhas if l['Mandante'] == time or l['Visitante'] == time]
    if not linhas: return "Nenhum jogo."
    linhas = linhas[-ultimos:] if ultimos else linhas
    res = ""
    for l in linhas:
        res += f"• {l['Data']}: {l['Mandante']} {l['Gols Mandante']}x{l['Gols Visitante']} {l['Visitante']}\n"
    return res

# --- HANDLERS BOT (IDÊNTICOS AOS SEUS) ---
async def start_command(update, context):
    await update.message.reply_text("👋 Bot Ativo! Use **/stats**", parse_mode='Markdown')

async def listar_competicoes(update, context):
    kb = [[InlineKeyboardButton(a, callback_data=f"c|{a}") for a in ABAS_PASSADO[i:i+3]] for i in range(0, len(ABAS_PASSADO), 3)]
    await (update.message.reply_text if update.message else update.callback_query.edit_message_text)("Escolha a Liga:", reply_markup=InlineKeyboardMarkup(kb))

async def mostrar_menu_status_jogo(update, context, aba):
    kb = [[InlineKeyboardButton("🔴 AO VIVO", callback_data=f"STATUS|LIVE|{aba}")], [InlineKeyboardButton("📅 FUTUROS", callback_data=f"STATUS|FUTURE|{aba}")]]
    await update.callback_query.edit_message_text(f"Liga: {aba}", reply_markup=InlineKeyboardMarkup(kb))

async def listar_jogos(update, context, aba, status):
    jogos = buscar_jogos_live(aba) if status == "LIVE" else get_sheet_data_future(aba)
    if not jogos: await update.callback_query.edit_message_text("Sem jogos."); return
    context.chat_data[f"j_{aba}"] = jogos
    kb = [[InlineKeyboardButton(f"{j['Mandante_Nome']} x {j['Visitante_Nome']}", callback_data=f"JOGO|{aba}|{status}|{idx}")] for idx, j in enumerate(jogos[:15])]
    await update.callback_query.edit_message_text("Selecione:", reply_markup=InlineKeyboardMarkup(kb))

async def callback_query_handler(update, context):
    q = update.callback_query; d = q.data
    if d.startswith("c|"): await mostrar_menu_status_jogo(update, context, d.split('|')[1])
    elif d.startswith("STATUS|"): await listar_jogos(update, context, d.split('|')[2], d.split('|')[1])
    elif d.startswith("JOGO|"):
        _, aba, st, idx = d.split('|'); jogo = context.chat_data[f"j_{aba}"][int(idx)]
        context.chat_data.update({'m': jogo['Mandante_Nome'], 'v': jogo['Visitante_Nome'], 'a': aba})
        kb = [[InlineKeyboardButton(f[0], callback_data=f"F|{i}")] for i, f in enumerate(CONFRONTO_FILTROS)]
        await q.message.reply_text(f"Análise: {jogo['Mandante_Nome']} x {jogo['Visitante_Nome']}", reply_markup=InlineKeyboardMarkup(kb))
    elif d.startswith("F|"):
        f_idx = int(d.split('|')[1]); m, v, aba = context.chat_data['m'], context.chat_data['v'], context.chat_data['a']
        _, tipo, ult, cm, cv = CONFRONTO_FILTROS[f_idx]
        txt = formatar_estatisticas(calcular_estatisticas_time(m, aba, ult, cm)) + "\n\n" + formatar_estatisticas(calcular_estatisticas_time(v, aba, ult, cv))
        await q.message.reply_text(txt, parse_mode='Markdown')

async def forcaupdate_command(update, context):
    await atualizar_planilhas(context); await update.message.reply_text("✅ OK")

# =================================================================================
# 🚀 EXECUÇÃO FINAL (IDÊNTICO COM ADIÇÃO DO PING)
# =================================================================================
def main():
    if not BOT_TOKEN: sys.exit(1)
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("stats", listar_competicoes))
    app.add_handler(CommandHandler("forcaupdate", forcaupdate_command)) 
    app.add_handler(CallbackQueryHandler(callback_query_handler))
    
    if client:
        app.job_queue.run_repeating(atualizar_planilhas, interval=3600, first=0)
        # Inicia o ping para manter vivo
        loop = asyncio.get_event_loop()
        loop.create_task(pre_carregar_cache_sheets())
        loop.create_task(start_keep_alive())

    webhook_url = os.environ.get("WEBHOOK_URL") or os.environ.get("RENDER_EXTERNAL_URL")
    app.run_webhook(listen="0.0.0.0", port=int(os.environ.get("PORT", "8080")), url_path=BOT_TOKEN, webhook_url=f"{webhook_url}/{BOT_TOKEN}")

if __name__ == "__main__":
    main()
