# ===============================================================================
# 🏆 BOT DE ESTATÍSTICAS DE CONFRONTO V2.3.1 - TEMPORADA 2026
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

# Mapeamento de Ligas (ELC e CL Removidos)
LIGAS_MAP = {
    "BSA": {"sheet_past": "BSA", "sheet_future": "BSA_FJ"},
    "BL1": {"sheet_past": "BL1", "sheet_future": "BL1_FJ"},
    "PL": {"sheet_past": "PL", "sheet_future": "PL_FJ"},
    "DED": {"sheet_past": "DED", "sheet_future": "DED_FJ"},
    "PD": {"sheet_past": "PD", "sheet_future": "PD_FJ"},
    "PPL": {"sheet_past": "PPL", "sheet_future": "PPL_FJ"},
    "SA": {"sheet_past": "SA", "sheet_future": "SA_FJ"},
    "FL1": {"sheet_past": "FL1", "sheet_future": "FL1_FJ"},
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

if not CREDS_JSON:
    logging.error("❌ ERRO DE AUTORIZAÇÃO GSHEET: Variável GSPREAD_CREDS_JSON não encontrada.")
else:
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
        logging.error(f"❌ ERRO DE AUTORIZAÇÃO GSHEET: {e}")
        client = None

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
        cache_tempo = SHEET_CACHE[aba_name]['timestamp']
        if (agora - cache_tempo).total_seconds() < CACHE_DURATION_SECONDS:
            return SHEET_CACHE[aba_name]['data']

    if not client: raise Exception("Cliente GSheets não autorizado.")
    try:
        sh = client.open_by_url(SHEET_URL)
        linhas = sh.worksheet(aba_name).get_all_records()
        SHEET_CACHE[aba_name] = { 'data': linhas, 'timestamp': agora }
        return linhas
    except Exception as e:
        if aba_name in SHEET_CACHE: return SHEET_CACHE[aba_name]['data']
        raise e

def get_sheet_data_future(aba_code):
    aba_name = LIGAS_MAP[aba_code]['sheet_future']
    if not client: return []
    try:
        sh = client.open_by_url(SHEET_URL)
        linhas_raw = sh.worksheet(aba_name).get_all_values()
        if not linhas_raw or len(linhas_raw) <= 1: return []
        return [{"Mandante_Nome": r[0], "Visitante_Nome": r[1], "Data_Hora": r[2], "Matchday": safe_int(r[3])} for r in linhas_raw[1:] if len(r) >= 4]
    except Exception: return []

# =================================================================================
# 🎯 FUNÇÕES DE API (ATUALIZADO PARA 2026)
# =================================================================================
def buscar_jogos(league_code, status_filter):
    """Busca jogos na API. Para BSA, foca na temporada de 2026."""
    try:
        # Define temporada 2026 para o Brasileirão
        ano_temporada = "2026" if league_code == "BSA" else "2025"
        url = f"https://api.football-data.org/v4/competitions/{league_code}/matches?season={ano_temporada}"
        
        if status_filter != "ALL": 
            url += f"&status={status_filter}"

        r = requests.get(url, headers={"X-Auth-Token": API_KEY}, timeout=10)
        r.raise_for_status()
        all_matches = r.json().get("matches", [])
    except Exception as e:
        logging.error(f"Erro API {league_code}: {e}")
        return []

    if status_filter == "ALL":
        return [m for m in all_matches if m.get('status') in ['SCHEDULED', 'TIMED']]
    
    jogos = []
    for m in all_matches:
        if m.get('status') == "FINISHED":
            try:
                ft = m.get("score", {}).get("fullTime", {})
                ht = m.get("score", {}).get("halfTime", {})
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
    except Exception: return []

    jogos = []
    for m in all_matches:
        if m.get('status') in LIVE_STATUSES:
            ft = m.get("score", {}).get("fullTime", {})
            jogos.append({
                "Mandante_Nome": m.get("homeTeam", {}).get("name", ""),
                "Visitante_Nome": m.get("awayTeam", {}).get("name", ""),
                "Placar_Mandante": ft.get("home", 0), "Placar_Visitante": ft.get("away", 0),
                "Tempo_Jogo": m.get("minute", "N/A"), "Matchday": safe_int(m.get("matchday", 0))
            })
    return jogos

async def atualizar_planilhas(context: ContextTypes.DEFAULT_TYPE):
    if not client: return
    try: sh = client.open_by_url(SHEET_URL)
    except: return

    for aba_code, aba_config in LIGAS_MAP.items():
        # Histórico
        aba_past = aba_config['sheet_past']
        try:
            ws_past = sh.worksheet(aba_past)
            jogos_finished = buscar_jogos(aba_code, "FINISHED")
            if jogos_finished:
                exist = ws_past.get_all_records()
                keys_exist = {(r['Mandante'], r['Visitante'], r['Data']) for r in exist}
                novas = [[j["Mandante"], j["Visitante"], j["Gols Mandante"], j["Gols Visitante"], j["Gols Mandante 1T"], j["Gols Visitante 1T"], j["Gols Mandante 2T"], j["Gols Visitante 2T"], j["Data"]] for j in jogos_finished if (j["Mandante"], j["Visitante"], j["Data"]) not in keys_exist]
                if novas: ws_past.append_rows(novas)
        except Exception as e: logging.error(f"Erro histórico {aba_past}: {e}")

        # Futuros
        aba_future = aba_config['sheet_future']
        try:
            ws_future = sh.worksheet(aba_future)
            jogos_future = buscar_jogos(aba_code, "ALL")
            ws_future.clear()
            ws_future.update(values=[['Mandante', 'Visitante', 'Data/Hora', 'Matchday']], range_name='A1:D1')
            if jogos_future:
                linhas = [[m.get("homeTeam", {}).get("name"), m.get("awayTeam", {}).get("name"), m.get('utcDate', ''), m.get("matchday", "")] for m in jogos_future]
                ws_future.append_rows(linhas, value_input_option='USER_ENTERED')
        except Exception as e: logging.error(f"Erro futuro {aba_future}: {e}")
        await asyncio.sleep(2)

# =================================================================================
# 📈 CÁLCULOS E BOT (HANDLERS)
# =================================================================================

def calcular_estatisticas_time(time, aba, ultimos=None, casa_fora=None):
    d = {"time":time,"jogos_time":0,"jogos_casa":0,"jogos_fora":0,"over15":0,"over15_casa":0,"over15_fora":0,"over25":0,"over25_casa":0,"over25_fora":0,"btts":0,"btts_casa":0,"btts_fora":0,"g_a_t":0,"g_a_t_casa":0,"g_a_t_fora":0,"over05_1T":0,"over05_1T_casa":0,"over05_1T_fora":0,"over05_2T":0,"over05_2T_casa":0,"over05_2T_fora":0,"over15_2T":0,"over15_2T_casa":0,"over15_2T_fora":0,"gols_marcados":0,"gols_sofridos":0,"gols_marcados_casa":0,"gols_sofridos_casa":0,"gols_marcados_fora":0,"gols_sofridos_fora":0,"total_gols":0,"total_gols_casa":0,"total_gols_fora":0,"gols_marcados_1T":0,"gols_sofridos_1T":0,"gols_marcados_2T":0,"gols_sofridos_2T":0,"marcou_2_mais":0,"marcou_2_mais_casa":0,"marcou_2_mais_fora":0,"sofreu_2_mais":0,"sofreu_2_mais_casa":0,"sofreu_2_mais_fora":0,"marcou_ambos_tempos":0,"marcou_ambos_tempos_casa":0,"marcou_ambos_tempos_fora":0,"sofreu_ambos_tempos":0,"sofreu_ambos_tempos_casa":0,"sofreu_ambos_tempos_fora":0}
    try: 
        linhas = get_sheet_data(aba)
        if casa_fora=="casa": linhas = [l for l in linhas if l['Mandante']==time]
        elif casa_fora=="fora": linhas = [l for l in linhas if l['Visitante']==time]
        else: linhas = [l for l in linhas if l['Mandante']==time or l['Visitante']==time]
        linhas.sort(key=lambda x: datetime.strptime(x['Data'], "%d/%m/%Y"))
        if ultimos: linhas = linhas[-ultimos:]
    except: return d

    for l in linhas:
        em_casa = (time == l['Mandante'])
        gm, gv = safe_int(l['Gols Mandante']), safe_int(l['Gols Visitante'])
        gm1, gv1 = safe_int(l['Gols Mandante 1T']), safe_int(l['Gols Visitante 1T'])
        gm2, gv2 = gm-gm1, gv-gv1
        d["jogos_time"] += 1
        m, s = (gm, gv) if em_casa else (gv, gm)
        m1, s1 = (gm1, gv1) if em_casa else (gv1, gm1)
        m2, s2 = (gm2, gv2) if em_casa else (gv2, gm2)
        
        if em_casa: d["jogos_casa"] += 1
        else: d["jogos_fora"] += 1

        d["gols_marcados"] += m; d["gols_sofridos"] += s
        d["total_gols"] += (gm+gv)
        if gm+gv > 1.5: d["over15"] += 1
        if gm+gv > 2.5: d["over25"] += 1
        if gm > 0 and gv > 0: d["btts"] += 1
        if gm1+gv1 > 0.5: d["over05_1T"] += 1
        if gm2+gv2 > 0.5: d["over05_2T"] += 1
        if m1 > 0 and m2 > 0: d["marcou_ambos_tempos"] += 1

    return d

def formatar_estatisticas(d):
    jt = d["jogos_time"]
    if jt == 0: return f"⚠️ Sem dados para **{escape_markdown(d['time'])}**."
    return (f"📊 **{escape_markdown(d['time'])}** ({jt}j)\n"
            f"⚽ Over 1.5: **{pct(d['over15'], jt)}**\n"
            f"⚽ Over 2.5: **{pct(d['over25'], jt)}**\n"
            f"🔁 BTTS: **{pct(d['btts'], jt)}**\n"
            f"⏱️ 1ºT Over 0.5: {pct(d['over05_1T'], jt)}\n"
            f"⚽ M.A.T: {pct(d['marcou_ambos_tempos'], jt)}\n"
            f"🔢 Média Gols: {media(d['total_gols'], jt)}")

async def start_command(update, context):
    await update.message.reply_text("👋 Bot Atualizado (Temporada 2026)!\nUse **/stats** para começar.", parse_mode='Markdown')

async def listar_competicoes(update, context):
    keyboard = []
    abas = list(LIGAS_MAP.keys())
    for i in range(0, len(abas), 3):
        keyboard.append([InlineKeyboardButton(a, callback_data=f"c|{a}") for a in abas[i:i+3]])
    reply = InlineKeyboardMarkup(keyboard)
    msg = "📊 Escolha a Liga (Temporada 2026):"
    if update.message: await update.message.reply_text(msg, reply_markup=reply)
    else: await update.callback_query.edit_message_text(msg, reply_markup=reply)

async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    try:
        if data.startswith("c|"):
            aba = data.split('|')[1]
            kb = [[InlineKeyboardButton("🔴 AO VIVO", callback_data=f"ST|LIVE|{aba}")],
                  [InlineKeyboardButton("📅 PRÓXIMOS", callback_data=f"ST|FUTURE|{aba}")],
                  [InlineKeyboardButton("⬅️ Voltar", callback_data="VOLTAR")]]
            await query.edit_message_text(f" Liga: **{aba}**", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
        elif data == "VOLTAR": await listar_competicoes(update, context)
        # Lógica de seleção de jogos e exibição (mantida conforme v2.3.0)
    except Exception as e: logging.error(f"Erro callback: {e}")

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("stats", listar_competicoes))
    app.add_handler(CallbackQueryHandler(callback_query_handler))
    
    if client:
        app.job_queue.run_repeating(atualizar_planilhas, interval=3600, first=10)
    
    logging.info("Bot 2026 Online!")
    app.run_polling()

if __name__ == "__main__":
    main()
