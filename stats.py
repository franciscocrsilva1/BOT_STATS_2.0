# ===============================================================================
# 🏆 BOT DE ESTATÍSTICAS DE CONFRONTO V2.3.1
# REMOÇÃO ELC + CL | BSA TEMPORADA 2026
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
SHEET_URL = os.environ.get("SHEET_URL")

# ===== MAPEAMENTO DE LIGAS =====
# ❌ ELC REMOVIDA
# ❌ CL REMOVIDA
# ✅ BSA TEMPORADA 2026
LIGAS_MAP = {
    "BSA": {"sheet_past": "BSA", "sheet_future": "BSA_FJ", "season": 2026},
    "BL1": {"sheet_past": "BL1", "sheet_future": "BL1_FJ", "season": None},
    "PL":  {"sheet_past": "PL",  "sheet_future": "PL_FJ",  "season": None},
    "DED": {"sheet_past": "DED", "sheet_future": "DED_FJ", "season": None},
    "PD":  {"sheet_past": "PD",  "sheet_future": "PD_FJ",  "season": None},
    "PPL": {"sheet_past": "PPL", "sheet_future": "PPL_FJ", "season": None},
    "SA":  {"sheet_past": "SA",  "sheet_future": "SA_FJ",  "season": None},
    "FL1": {"sheet_past": "FL1", "sheet_future": "FL1_FJ", "season": None},
}

ABAS_PASSADO = list(LIGAS_MAP.keys())

ULTIMOS = 10
SHEET_CACHE = {}
CACHE_DURATION_SECONDS = 3600
MAX_GAMES_LISTED = 30

LIVE_STATUSES = ["IN_PLAY", "HALF_TIME", "PAUSED"]

# =================================================================================
# 🔑 CONEXÃO GSHEETS
# =================================================================================
CREDS_JSON = os.environ.get("GSPREAD_CREDS_JSON")
client = None

if CREDS_JSON:
    try:
        with tempfile.NamedTemporaryFile(mode="w", delete=False, encoding="utf-8") as f:
            f.write(CREDS_JSON)
            path = f.name

        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive"
        ]
        creds = ServiceAccountCredentials.from_json_keyfile_name(path, scope)
        client = gspread.authorize(creds)
        os.remove(path)
        logging.info("✅ GSheets conectado.")
    except Exception as e:
        logging.error(f"❌ Erro GSheets: {e}")

# =================================================================================
# 🧠 FUNÇÕES DE SUPORTE
# =================================================================================
def safe_int(v):
    try: return int(v)
    except: return 0

def get_sheet_data(aba):
    global SHEET_CACHE
    now = datetime.now()
    aba_name = LIGAS_MAP[aba]["sheet_past"]

    if aba_name in SHEET_CACHE:
        if (now - SHEET_CACHE[aba_name]["timestamp"]).total_seconds() < CACHE_DURATION_SECONDS:
            return SHEET_CACHE[aba_name]["data"]

    sh = client.open_by_url(SHEET_URL)
    data = sh.worksheet(aba_name).get_all_records()
    SHEET_CACHE[aba_name] = {"data": data, "timestamp": now}
    return data

# =================================================================================
# 🌐 API FOOTBALL-DATA
# =================================================================================
def buscar_jogos(league_code, status):
    season = LIGAS_MAP[league_code].get("season")
    url = f"https://api.football-data.org/v4/competitions/{league_code}/matches"

    params = []
    if status != "ALL":
        params.append(f"status={status}")
    if season:
        params.append(f"season={season}")

    if params:
        url += "?" + "&".join(params)

    try:
        r = requests.get(url, headers={"X-Auth-Token": API_KEY}, timeout=10)
        r.raise_for_status()
        return r.json().get("matches", [])
    except Exception as e:
        logging.error(f"Erro API {league_code}: {e}")
        return []

# =================================================================================
# 🤖 BOT
# =================================================================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 **Bot de Estatísticas de Confronto**\n\nUse /stats para iniciar.",
        parse_mode="Markdown"
    )

async def listar_competicoes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = []
    abas = list(LIGAS_MAP.keys())
    for i in range(0, len(abas), 3):
        keyboard.append([
            InlineKeyboardButton(a, callback_data=f"c|{a}")
            for a in abas[i:i+3]
        ])
    await update.message.reply_text(
        "📊 **Escolha a competição:**",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

# =================================================================================
# 🚀 MAIN
# =================================================================================
def main():
    if not BOT_TOKEN or BOT_TOKEN == "SEU_TOKEN_AQUI":
        logging.error("❌ BOT_TOKEN não configurado")
        sys.exit(1)

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("stats", listar_competicoes))

    webhook_url = os.environ.get("WEBHOOK_URL") or os.environ.get("RENDER_EXTERNAL_URL")
    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", 8080)),
        url_path=BOT_TOKEN,
        webhook_url=f"{webhook_url}/{BOT_TOKEN}"
    )

if __name__ == "__main__":
    main()
