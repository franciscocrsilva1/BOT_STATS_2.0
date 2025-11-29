# config.py

import os 
import logging
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import tempfile

# Configuração de Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# ===== Variáveis de Configuração =====
BOT_TOKEN = os.environ.get("BOT_TOKEN", "SEU_TOKEN_AQUI") 
API_KEY = os.environ.get("API_KEY", "SUA_API_KEY_AQUI")
SHEET_URL = os.environ.get("SHEET_URL", "https://docs.google.com/spreadsheets/d/1ChFFXQxo1qQElNzh2OC8-UPGofRXxyVWN06ExBQ3YqY/edit?usp=drivesdk")
CREDS_JSON = os.environ.get("GSPREAD_CREDS_JSON")

# Mapeamento de Ligas:
LIGAS_MAP = {
    "CL": {"sheet_past": "CL", "sheet_future": "CL_FJ"}, "BSA": {"sheet_past": "BSA", "sheet_future": "BSA_FJ"},
    "BL1": {"sheet_past": "BL1", "sheet_future": "BL1_FJ"}, "PL": {"sheet_past": "PL", "sheet_future": "PL_FJ"},
    "ELC": {"sheet_past": "ELC", "sheet_future": "ELC_FJ"}, "DED": {"sheet_past": "DED", "sheet_future": "DED_FJ"}, 
    "PD": {"sheet_past": "PD", "sheet_future": "PD_FJ"}, "PPL": {"sheet_past": "PPL", "sheet_future": "PPL_FJ"},
    "SA": {"sheet_past": "SA", "sheet_future": "SA_FJ"}, "FL1": {"sheet_past": "FL1", "sheet_future": "FL1_FJ"},
}

# ===== Variáveis Globais de Conexão e Cache =====
client = None
SHEET_CACHE = {} 
CACHE_DURATION_SECONDS = 3600 

# =================================================================================
# ✅ CONEXÃO GSHEETS
# =================================================================================
def conectar_gsheets():
    global client
    if not CREDS_JSON:
        logging.error("❌ ERRO: GSPREAD_CREDS_JSON não encontrada.")
        return

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

conectar_gsheets()
