# ===============================================================================
# 🏆 SCRIPT DE ATUALIZAÇÃO DE PLANILHA - APENAS LOGÍSTICA DE DADOS
# ===============================================================================

# ===== Importações Essenciais =====
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import requests
import os 
import tempfile
import time # Usado para pausas síncronas
import logging
from datetime import datetime, timedelta, timezone
import sys 

from gspread.exceptions import WorksheetNotFound

# Configuração de Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# ===== Variáveis de Configuração (LIDAS DE VARIÁVEIS DE AMBIENTE) =====
# OBS: No Render, use as chaves SECRETAS BOT_TOKEN, API_KEY e SHEET_URL
# O BOT_TOKEN não é usado, mas mantido como variável de ambiente por conveniência
BOT_TOKEN = os.environ.get("BOT_TOKEN", "SEU_TOKEN_AQUI") 
API_KEY = os.environ.get("API_KEY", "SUA_API_KEY_AQUI")
SHEET_URL = os.environ.get("SHEET_URL", "https://docs.google.com/spreadsheets/d/1ChFFXQxo1qQElNzh2OC8-UPGofRXxyVWN06ExBQ3YqY/edit?usp=drivesdk")

# Mapeamento de Ligas: APENAS DED para teste
LIGAS_MAP = {
    "DED": {"sheet_past": "DED", "sheet_future": "DED_FJ"},
}

# Variáveis globais
client = None
SHEET_CACHE = {} # Cache não será usado para stats, mas o dicionário é mantido
CACHE_DURATION_SECONDS = 3600 

# =================================================================================
# ✅ CONEXÃO GSHEETS VIA VARIÁVEL DE AMBIENTE 
# =================================================================================

CREDS_JSON = os.environ.get("GSPREAD_CREDS_JSON")

if not CREDS_JSON:
    logging.error("❌ ERRO DE AUTORIZAÇÃO GSHEET: Variável GSPREAD_CREDS_JSON não encontrada. Configure-a no seu ambiente.")
else:
    try:
        # Usa um arquivo temporário para carregar as credenciais
        with tempfile.NamedTemporaryFile(mode="w", delete=False, encoding='utf-8') as tmp_file:
            tmp_file.write(CREDS_JSON)
            tmp_file_path = tmp_file.name
        
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name(tmp_file_path, scope)
        client = gspread.authorize(creds)
      
        logging.info("✅ Conexão GSheets estabelecida via Variável de Ambiente.")
        os.remove(tmp_file_path) # Limpa o arquivo temporário

    except Exception as e:
        logging.error(f"❌ ERRO DE AUTORIZAÇÃO GSHEET: Erro ao carregar ou autorizar credenciais JSON: {e}")
        client = None

# =================================================================================
# 💾 FUNÇÕES DE SUPORTE
# =================================================================================
def safe_int(v):
    """Converte para inteiro com segurança."""
    try: return int(v)
    except: return 0

def get_sheet_data(aba_code):
    """Obtém dados da aba de histórico (sheet_past) com cache SIMPLES (sem tempo)."""
    global SHEET_CACHE
    aba_name = LIGAS_MAP[aba_code]['sheet_past']

    if aba_name in SHEET_CACHE:
        return SHEET_CACHE[aba_name]['data'] # Retorna do cache se estiver presente

    if not client: raise Exception("Cliente GSheets não autorizado.")
    
    try:
        sh = client.open_by_url(SHEET_URL)
        linhas = sh.worksheet(aba_name).get_all_records()
    except Exception as e:
        if aba_name in SHEET_CACHE: return SHEET_CACHE[aba_name]['data']
        raise e

    # Cache temporário (apenas enquanto o script estiver rodando)
    SHEET_CACHE[aba_name] = { 'data': linhas }
    return linhas

# =================================================================================
# 🎯 FUNÇÕES DE API E ATUALIZAÇÃO 
# =================================================================================
def buscar_jogos(league_code, status_filter):
    """Busca jogos na API com filtro de status (usado para FINISHED e ALL)."""
    if not API_KEY or API_KEY == "SUA_API_KEY_AQUI":
        logging.error("❌ API_KEY não configurada. Impossível buscar jogos.")
        return []
  
    try:
        url = f"https://api.football-data.org/v4/competitions/{league_code}/matches"

        if status_filter != "ALL":
             url += f"?status={status_filter}"

        r = requests.get(
            url,
            headers={"X-Auth-Token": API_KEY}, timeout=10
        )
        r.raise_for_status()
    except Exception as e:
        logging.error(f"❌ Erro ao buscar jogos {status_filter} para {league_code} na API: {e}")
        return []

    all_matches = r.json().get("matches", [])

    if status_filter == "ALL":
        # Retorna apenas jogos agendados ou cronometrados (futuros)
        return [m for m in all_matches if m.get('status') in ['SCHEDULED', 'TIMED']]

    else:
        # Lógica para jogos FINISHED
        jogos = []
        for m in all_matches:
            if m.get('status') == "FINISHED":
                try:
                    jogo_data = datetime.strptime(m['utcDate'][:10], "%Y-%m-%d")
                    ft = m.get("score", {}).get("fullTime", {})
                    ht = m.get("score", {}).get("halfTime", {})
                    if ft.get("home") is None: continue

                    gm, gv = ft.get("home",0), ft.get("away",0)
                    gm1, gv1 = ht.get("home",0), ht.get("away",0)
                    
                    gm2 = gm - gm1
                    gv2 = gv - gv1

                    jogos.append({
                        "Mandante": m.get("homeTeam", {}).get("name", ""),
                        "Visitante": m.get("awayTeam", {}).get("name", ""),
                        "Gols Mandante": gm, "Gols Visitante": gv,
                        "Gols Mandante 1T": gm1, "Gols Visitante 1T": gv1,
                        "Gols Mandante 2T": gm2, "Gols Visitante 2T": gv2,
                        "Data": jogo_data.strftime("%d/%m/%Y")
                    })
                except: continue
        return sorted(jogos, key=lambda x: datetime.strptime(x['Data'], "%d/%m/%Y"))

def atualizar_planilhas():
    """Atualiza o histórico e o cache de futuros jogos para todas as ligas configuradas."""
    global SHEET_CACHE

    if not client:
        logging.error("Atualização de planilhas ignorada: Cliente GSheets não autorizado.")
        return
        
    try: 
        sh = client.open_by_url(SHEET_URL)
    except Exception as e:
        logging.error(f"❌ Erro ao abrir planilha para atualização: {e}. Verifique a SHEET_URL e o acesso do serviço.")
        return

    logging.info("Iniciando a atualização das planilhas...")

    
    for aba_code, aba_config in LIGAS_MAP.items():
        # 1. ATUALIZAÇÃO DO HISTÓRICO (ABA_PASSADO)
        aba_past = aba_config['sheet_past']
        try: ws_past = sh.worksheet(aba_past)
        except WorksheetNotFound: 
            logging.warning(f"⚠️ Aba de histórico '{aba_past}' não encontrada. Ignorando...")
            continue

        logging.info(f"-> Buscando jogos FINISHED para {aba_code}...")
        jogos_finished = buscar_jogos(aba_code, "FINISHED")
        logging.info(f"<- Encontrados {len(jogos_finished)} jogos FINISHED para {aba_code}.")

        time.sleep(10) # Pausa SÍNCRONA de 10s para respeitar limite de rate da API

        if jogos_finished:
            try:
                # Tenta ler apenas o necessário para checar duplicidade
                exist = ws_past.get_all_records() 
                
                logging.info(f"   {len(exist)} linhas de histórico lidas em {aba_past} (para checar duplicidade).")
                
                keys_exist = {(r['Mandante'], r['Visitante'], r['Data']) for r in exist}

                novas_linhas = []
            
                for j in jogos_finished:
                    key = (j["Mandante"], j["Visitante"], j["Data"])
                    if key not in keys_exist:
           
                        novas_linhas.append([
                            j["Mandante"], j["Visitante"], j["Gols Mandante"], j["Gols Visitante"],
                            j["Gols Mandante 1T"], j["Gols Visitante 1T"],
                            j["Gols Mandante 2T"], j["Gols Visitante 2T"], j["Data"]
                        ])

                if novas_linhas:
                    ws_past.append_rows(novas_linhas)
                    logging.info(f"✅ {len(novas_linhas)} jogos adicionados ao histórico de {aba_past}.")
                else:
                    logging.info(f"⚠️ Nenhuma nova partida FINALIZADA para adicionar em {aba_past}.")
 
                if aba_past in SHEET_CACHE: del SHEET_CACHE[aba_past]
      
            except Exception as e:
                logging.error(f"❌ Erro ao ler ou inserir dados na planilha {aba_past}: {e}. Verifique se os cabeçalhos das colunas (Mandante, Visitante, Gols Mandante, Gols Visitante, Gols Mandante 1T, Gols Visitante 1T, Gols Mandante 2T, Gols Visitante 2T, Data) correspondem **exatamente** aos nomes no código.")

        # 2. ATUALIZAÇÃO DO CACHE DE FUTUROS JOGOS (ABA_FUTURE)
        aba_future = aba_config['sheet_future']
        
        try: ws_future = sh.worksheet(aba_future)
        except WorksheetNotFound:
            logging.warning(f"⚠️ Aba de futuros jogos '{aba_future}' não encontrada. Ignorando...")
            continue

        logging.info(f"-> Buscando jogos ALL (Future) para {aba_code}...")
        jogos_future = buscar_jogos(aba_code, "ALL")
        logging.info(f"<- Encontrados {len(jogos_future)} jogos ALL (Future) para {aba_code}.")

        time.sleep(10) # Pausa SÍNCRONA de 10s para respeitar limite de rate da API

        try:
            ws_future.clear()
            # Garante que o cabeçalho está sempre correto
            ws_future.update(values=[['Mandante', 'Visitante', 'Data/Hora', 'Matchday']], range_name='A1:D1')
            logging.info(f"Cache {aba_future} limpo e cabeçalho reescrito.")

            if jogos_future:
          
                linhas_future = []

                for m in jogos_future:
                    matchday = m.get("matchday", "")
                    utc_date = m.get('utcDate', '')
    
                    if utc_date:
                        try:
                            data_utc = datetime.strptime(utc_date[:16], '%Y-%m-%dT%H:%M')
                            # Limita a busca a jogos de até 90 dias no futuro
                            if data_utc < datetime.now() + timedelta(days=90):
                                linhas_future.append([
                                    m.get("homeTeam", {}).get("name"),
                                    m.get("awayTeam", {}).get("name"),
                                    utc_date,
                                    matchday
                                ])
                        except:
                            continue

             
                if linhas_future:
                    ws_future.append_rows(linhas_future, value_input_option='USER_ENTERED')
                    logging.info(f"✅ {len(linhas_future)} jogos futuros atualizados no cache de {aba_future}.")
                else:
                    logging.info(f"⚠️ Nenhuma partida agendada para {aba_code}. Cache {aba_future} permanece com cabeçalho.")

        except Exception as e:
            logging.error(f"❌ Erro ao atualizar cache de futuros jogos em {aba_future}: {e}. Verifique se a aba existe e tem permissão de escrita.")

        time.sleep(3) # Pausa entre ligas (embora só haja uma, é bom manter para uma futura expansão)

# =================================================================================
# 🚀 FUNÇÃO PRINCIPAL
# =================================================================================
def main():
    """Roda a função principal de atualização de planilhas."""
    logging.info("Iniciando script de atualização de dados...")
    
    if not API_KEY or not SHEET_URL:
        logging.error("❌ Variáveis de ambiente API_KEY ou SHEET_URL não configuradas. Encerrando.")
        sys.exit(1)

    if not client:
        logging.error("❌ Conexão com Google Sheets falhou. Verifique GSPREAD_CREDS_JSON. Encerrando.")
        sys.exit(1)
        
    # Executa a atualização
    atualizar_planilhas()
    
    logging.info("✅ Script finalizado. Verifique os logs para sucesso ou falha na atualização.")

if __name__ == "__main__":
    # Este script será executado de forma síncrona
    main()
