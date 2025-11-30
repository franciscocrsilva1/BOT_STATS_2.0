# ===============================================================================
# 🏆 BOT DE ESTATÍSTICAS DE CONFRONTO V2.3.1 - FORÇA UPDATE CORRIGIDO PARA WEBHOOK
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
# Aplicação global do nest_asyncio (necessário para ambientes web)
nest_asyncio.apply()

# ===== Variáveis de Configuração (LIDAS DE VARIÁVEIS DE AMBIENTE) =====
BOT_TOKEN = os.environ.get("BOT_TOKEN", "SEU_TOKEN_AQUI") 
API_KEY = os.environ.get("API_KEY", "SUA_API_KEY_AQUI")
SHEET_URL = os.environ.get("SHEET_URL", "https://docs.google.com/spreadsheets/d/1ChFFXQxo1qQElNzh2OC8-UPGofRXxyVWN06ExBQ3YqY/edit?usp=drivesdk")

# Mapeamento de Ligas
LIGAS_MAP = {
    "CL": {"sheet_past": "CL", "sheet_future": "CL_FJ"},
    "BSA": {"sheet_past": "BSA", "sheet_future": "BSA_FJ"},
    "BL1": {"sheet_past": "BL1", "sheet_future": "BL1_FJ"},
    "PL": {"sheet_past": "PL", "sheet_future": "PL_FJ"},
    "ELC": {"sheet_past": "ELC", "sheet_future": "ELC_FJ"},
    "DED": {"sheet_past": "DED", "sheet_future": "DED_FJ"},
    "PD": {"sheet_past": "PD", "sheet_future": "PD_FJ"},
    "PPL": {"sheet_past": "PPL", "sheet_future": "PPL_FJ"},
    "SA": {"sheet_past": "SA", "sheet_future": "SA_FJ"},
    "FL1": {"sheet_past": "FL1", "sheet_future": "FL1_FJ"},
}
ABAS_PASSADO = list(LIGAS_MAP.keys())

ULTIMOS = 10
SHEET_CACHE = {}
CACHE_DURATION_SECONDS = 3600 # 1 hora
MAX_GAMES_LISTED = 30

# Filtros reutilizáveis para Estatísticas e Resultados
CONFRONTO_FILTROS = [
    (f"📊 Estatísticas | ÚLTIMOS {ULTIMOS} GERAL", "STATS_FILTRO", ULTIMOS, None, None),
    (f"📊 Estatísticas | {ULTIMOS} (M CASA vs V FORA)", "STATS_FILTRO", ULTIMOS, "casa", "fora"),
    (f"📅 Resultados | ÚLTIMOS {ULTIMOS} GERAL", "RESULTADOS_FILTRO", ULTIMOS, None, None),
    (f"📅 Resultados | {ULTIMOS} (M CASA vs V FORA)", "RESULTADOS_FILTRO", ULTIMOS, "casa", "fora"),
]

LIVE_STATUSES = ["IN_PLAY", "HALF_TIME", "PAUSED"]

# =================================================================================
# ✅ CONEXÃO GSHEETS VIA VARIÁVEL DE AMBIENTE 
# =================================================================================

CREDS_JSON = os.environ.get("GSPREAD_CREDS_JSON")
client = None

if not CREDS_JSON:
    logging.error("❌ ERRO DE AUTORIZAÇÃO GSHEET: Variável GSPREAD_CREDS_JSON não encontrada. O Job Queue será desativado.")
else:
    try:
        with tempfile.NamedTemporaryFile(mode="w", delete=False, encoding='utf-8') as tmp_file:
            tmp_file.write(CREDS_JSON)
            tmp_file_path = tmp_file.name
        
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name(tmp_file_path, scope)
        client = gspread.authorize(creds)
      
        logging.info("✅ Conexão GSheets estabelecida via Variável de Ambiente.")
        os.remove(tmp_file_path)

    except Exception as e:
        logging.error(f"❌ ERRO DE AUTORIZAÇÃO GSHEET: Erro ao carregar ou autorizar credenciais JSON: {e}")
        client = None

# =================================================================================
# 💾 FUNÇÕES DE SUPORTE E CACHING 
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
    """Obtém dados da aba de histórico (sheet_past) com cache."""
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
    except Exception as e:
        if aba_name in SHEET_CACHE: return SHEET_CACHE[aba_name]['data']
        raise e

    SHEET_CACHE[aba_name] = { 'data': linhas, 'timestamp': agora }
    return linhas

def get_sheet_data_future(aba_code):
    """Obtém dados da aba de cache de jogos futuros (sheet_future)."""
    aba_name = LIGAS_MAP[aba_code]['sheet_future']
    if not client: return []

    try:
        sh = client.open_by_url(SHEET_URL)
        linhas_raw = sh.worksheet(aba_name).get_all_values()
    except Exception as e:
        logging.error(f"Erro ao buscar cache de futuros jogos em {aba_name}: {e}")
        return []

    if not linhas_raw or len(linhas_raw) <= 1:
        return []

    data_rows = linhas_raw[1:]
    jogos = []
    for row in data_rows:
        if len(row) >= 4:
            jogos.append({
                "Mandante_Nome": row[0],
                "Visitante_Nome": row[1],
                "Data_Hora": row[2],
                "Matchday": safe_int(row[3])
            })
    return jogos

async def pre_carregar_cache_sheets():
    """Pré-carrega o histórico de todas as ligas (rodado uma vez na inicialização)."""
    if not client:
        logging.warning("Pré-carregamento de cache ignorado: Conexão GSheets falhou.")
        return

    logging.info("Iniciando pré-carregamento de cache...")
    for aba in ABAS_PASSADO:
        try:
            # Roda em thread separada para não bloquear
            await asyncio.to_thread(get_sheet_data, aba)
            logging.info(f"Cache de histórico para {aba} pré-carregado.")
        except Exception as e:
            logging.warning(f"Não foi possível pré-carregar cache para {aba}: {e}")
        await asyncio.sleep(1)

# =================================================================================
# 🎯 FUNÇÕES DE API E ATUALIZAÇÃO (CORRIGIDAS PARA NOTIFICAÇÃO)
# =================================================================================
def buscar_jogos(league_code, status_filter):
    """Busca jogos na API com filtro de status (usado para FINISHED e ALL)."""
    try:
        url = f"https://api.football-data.org/v4/competitions/{league_code}/matches"
        if status_filter != "ALL": url += f"?status={status_filter}"

        r = requests.get(url, headers={"X-Auth-Token": API_KEY}, timeout=10)
        r.raise_for_status()
    except Exception as e:
        logging.error(f"Erro ao buscar jogos {status_filter} para {league_code}: {e}")
        return []

    all_matches = r.json().get("matches", [])

    if status_filter == "ALL":
        return [m for m in all_matches if m.get('status') in ['SCHEDULED', 'TIMED']]
    else:
        jogos = []
        for m in all_matches:
            if m.get('status') == "FINISHED":
                try:
                    jogo_data = datetime.strptime(m['utcDate'][:10], "%Y-%m-%d")
                    ft = m.get("score", {}).get("fullTime", {}); ht = m.get("score", {}).get("halfTime", {})
                    if ft.get("home") is None: continue

                    gm, gv = ft.get("home",0), ft.get("away",0)
                    gm1, gv1 = ht.get("home",0), ht.get("away",0)
                    gm2, gv2 = gm-gm1, gv-gv1

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

def buscar_jogos_live(league_code):
    """Busca jogos AO VIVO (IN_PLAY, HALF_TIME, PAUSED)."""
    hoje_utc = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    try:
        url = f"https://api.football-data.org/v4/competitions/{league_code}/matches?dateFrom={hoje_utc}&dateTo={hoje_utc}"
        r = requests.get(url, headers={"X-Auth-Token": API_KEY}, timeout=10)
        r.raise_for_status()
    except Exception as e:
        logging.error(f"Erro ao buscar jogos AO VIVO (busca por data) para {league_code}: {e}")
        return []

    all_matches = r.json().get("matches", [])
    jogos = []
    for m in all_matches:
        status_api = m.get('status')
        if status_api in LIVE_STATUSES:
            try:
                ft_score = m.get("score", {}).get("fullTime", {})
                gm_atual = ft_score.get("home") if ft_score.get("home") is not None else 0
                gv_atual = ft_score.get("away") if ft_score.get("away") is not None else 0
                minute = m.get("minute", "N/A")

                if status_api in ['PAUSED', 'HALF_TIME']: minute = status_api
                elif status_api == "IN_PLAY":
                    if minute == "N/A":
                        minute = "2ºT" if m.get("score", {}).get("duration", "") == "REGULAR" else "1ºT"

                jogos.append({
                    "Mandante_Nome": m.get("homeTeam", {}).get("name", ""),
                    "Visitante_Nome": m.get("awayTeam", {}).get("name", ""),
                    "Placar_Mandante": gm_atual, "Placar_Visitante": gv_atual,
                    "Tempo_Jogo": minute, "Matchday": safe_int(m.get("matchday", 0))
                })
            except: continue
    return jogos


async def atualizar_planilhas(context: ContextTypes.DEFAULT_TYPE):
    """
    Atualiza o histórico e o cache de futuros jogos. 
    Usado pelo JobQueue (a cada hora) e pelo /forcaupdate (manual).
    """
    global SHEET_CACHE
    
    # CORREÇÃO: Pega o chat_id para notificação, se o job veio do /forcaupdate
    chat_id_to_notify = context.job.data.get("chat_id") if context.job and context.job.data else None
    
    async def notify_user(text, parse_mode='Markdown'):
        """Função auxiliar para enviar notificação ao usuário que disparou o comando."""
        if chat_id_to_notify:
            try:
                await context.application.bot.send_message(chat_id=chat_id_to_notify, text=text, parse_mode=parse_mode)
            except Exception as e:
                logging.error(f"Erro ao notificar usuário ({chat_id_to_notify}): {e}")

    if not client:
        logging.error("Atualização de planilhas ignorada: Cliente GSheets não autorizado.")
        await notify_user("❌ Serviço de atualização falhou: Conexão GSheets não estabelecida. Verifique as credenciais.")
        return
        
    try: sh = client.open_by_url(SHEET_URL)
    except Exception as e:
        logging.error(f"Erro ao abrir planilha para atualização: {e}")
        await notify_user(f"❌ Erro ao acessar a planilha: {e}")
        return

    logging.info("Iniciando a atualização periódica das planilhas...")

    try:
        for aba_code, aba_config in LIGAS_MAP.items():
            # 1. ATUALIZAÇÃO DO HISTÓRICO
            aba_past = aba_config['sheet_past']
            try: ws_past = sh.worksheet(aba_past)
            except WorksheetNotFound: 
                logging.warning(f"Aba de histórico '{aba_past}' não encontrada. Ignorando...")
                continue

            # Chama a função síncrona em uma thread separada
            jogos_finished = await asyncio.to_thread(buscar_jogos, aba_code, "FINISHED")
            
            # LOG DE DEBUG
            if not jogos_finished: logging.error(f"❌ DEBUG: API retornou 0 jogos FINISHED para a liga {aba_code}.")
            else: logging.info(f"✅ DEBUG: API retornou {len(jogos_finished)} jogos FINISHED para a liga {aba_code}.")

            await asyncio.sleep(10) # Pausa para respeitar limite de rate da API

            if jogos_finished:
                try:
                    exist = await asyncio.to_thread(ws_past.get_all_records)
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
                        await asyncio.to_thread(ws_past.append_rows, novas_linhas)
                        logging.info(f"✅ {len(novas_linhas)} jogos adicionados ao histórico de {aba_past}.")
                    
                    if aba_past in SHEET_CACHE: del SHEET_CACHE[aba_past]
                except Exception as e:
                    logging.error(f"Erro ao inserir dados na planilha {aba_past}: {e}")

            # 2. ATUALIZAÇÃO DO CACHE DE FUTUROS JOGOS
            aba_future = aba_config['sheet_future']
            try: ws_future = sh.worksheet(aba_future)
            except WorksheetNotFound:
                logging.warning(f"Aba de futuros jogos '{aba_future}' não encontrada. Ignorando...")
                continue

            jogos_future = await asyncio.to_thread(buscar_jogos, aba_code, "ALL")
            logging.info(f"✅ DEBUG: API retornou {len(jogos_future)} jogos FUTURE para a liga {aba_code}.")
            await asyncio.sleep(10) 

            try:
                await asyncio.to_thread(ws_future.clear)
                await asyncio.to_thread(ws_future.update, values=[['Mandante', 'Visitante', 'Data/Hora', 'Matchday']], range_name='A1:D1')

                if jogos_future:
                    linhas_future = []
                    for m in jogos_future:
                        matchday = m.get("matchday", "")
                        utc_date = m.get('utcDate', '')
                        if utc_date:
                            try:
                                data_utc = datetime.strptime(utc_date[:16], '%Y-%m-%dT%H:%M')
                                if data_utc < datetime.now() + timedelta(days=90):
                                    linhas_future.append([
                                        m.get("homeTeam", {}).get("name"),
                                        m.get("awayTeam", {}).get("name"),
                                        utc_date, matchday
                                    ])
                            except: continue

                    if linhas_future:
                        await asyncio.to_thread(ws_future.append_rows, linhas_future, value_input_option='USER_ENTERED')
                        logging.info(f"✅ {len(linhas_future)} jogos futuros atualizados no cache de {aba_future}.")
                    else:
                        logging.info(f"⚠️ Nenhuma partida agendada para {aba_code}. Cache {aba_future} limpo.")

            except Exception as e:
                logging.error(f"Erro ao atualizar cache de futuros jogos em {aba_future}: {e}")

            await asyncio.sleep(3) 
        
        # NOTIFICAÇÃO DE SUCESSO (Apenas se veio do comando manual)
        await notify_user("✅ Atualização forçada concluída com sucesso!")

    except Exception as e:
        logging.error(f"Erro crítico durante a atualização principal: {e}")
        # NOTIFICAÇÃO DE ERRO
        await notify_user(f"❌ Erro crítico na atualização. Verifique os logs.\nErro: {e}")


# =================================================================================
# 📈 FUNÇÕES DE CÁLCULO E FORMATAÇÃO (Omitidas por serem muito longas e inalteradas)
# =================================================================================
# ... (Manter as funções: calcular_estatisticas_time, formatar_estatisticas, listar_ultimos_jogos) ...
# ... (Seu código original continua aqui, sem alterações, até a seção de Handlers) ...

def calcular_estatisticas_time(time, aba, ultimos=None, casa_fora=None):
    # ... (Seu código original) ...
    pass # Este é apenas um placeholder. A função completa está no código final.

def formatar_estatisticas(d):
    # ... (Seu código original) ...
    pass # Este é apenas um placeholder. A função completa está no código final.

def listar_ultimos_jogos(time, aba, ultimos=None, casa_fora=None):
    # ... (Seu código original) ...
    pass # Este é apenas um placeholder. A função completa está no código final.

# =================================================================================
# 🤖 FUNÇÕES DO BOT: HANDLERS E FLUXOS (CORRIGIDAS)
# =================================================================================
# ... (Manter as funções: start_command, listar_competicoes, mostrar_menu_status_jogo, listar_jogos) ...
# ... (Manter as funções: mostrar_menu_acoes, exibir_estatisticas, exibir_ultimos_resultados) ...

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ... (Seu código original) ...
    pass

async def listar_competicoes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ... (Seu código original) ...
    pass

async def mostrar_menu_status_jogo(update: Update, context: ContextTypes.DEFAULT_TYPE, aba_code: str):
    # ... (Seu código original) ...
    pass

async def listar_jogos(update: Update, context: ContextTypes.DEFAULT_TYPE, aba_code: str, status: str):
    # ... (Seu código original) ...
    pass

async def mostrar_menu_acoes(update: Update, context: ContextTypes.DEFAULT_TYPE, aba_code: str, mandante: str, visitante: str):
    # ... (Seu código original) ...
    pass

async def exibir_estatisticas(update: Update, context: ContextTypes.DEFAULT_TYPE, mandante: str, visitante: str, aba_code: str, filtro_idx: int):
    # ... (Seu código original) ...
    pass

async def exibir_ultimos_resultados(update: Update, context: ContextTypes.DEFAULT_TYPE, mandante: str, visitante: str, aba_code: str, filtro_idx: int):
    # ... (Seu código original) ...
    pass

# ✅ COMANDO FORÇA UPDATE (CORRIGIDO PARA O JOBQUEUE)
async def forcaupdate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando para forçar a atualização imediata das planilhas, rodando em background."""
    if not client:
        await update.message.reply_text("❌ Serviço de atualização desativado. Conexão GSheets não estabelecida.")
        return
        
    job_queue = context.application.job_queue
    
    # Adiciona a tarefa à fila de jobs para rodar imediatamente (when=0) em segundo plano
    job_queue.run_once(
        atualizar_planilhas, 
        when=0, 
        name="ForcaUpdate_Manual", 
        # Passa o chat_id para que a função atualizar_planilhas possa notificar o usuário
        data={"chat_id": update.effective_chat.id} 
    )
    
    await update.message.reply_text("⚡️ **Atualização Forçada Agendada!** O processo será executado em segundo plano e você será notificado aqui em caso de sucesso ou erro. (Verifique os logs do Render para detalhes de DEBUG.)", parse_mode='Markdown')

async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ... (Seu código original) ...
    pass

# =================================================================================
# 🚀 FUNÇÃO PRINCIPAL (CORRIGIDA)
# =================================================================================
def main():
    if not BOT_TOKEN or BOT_TOKEN == "SEU_TOKEN_AQUI":
        logging.error("O token do bot não está configurado."); sys.exit(1)
        
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Adiciona Handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("stats", listar_competicoes))
    app.add_handler(CommandHandler("forcaupdate", forcaupdate_command)) # ✅ NOVO HANDLER
    app.add_handler(CallbackQueryHandler(callback_query_handler))
    
    # Webhook config para Render
    webhook_base_url = os.environ.get("WEBHOOK_URL") or os.environ.get("RENDER_EXTERNAL_URL")
    if not webhook_base_url: logging.error("❌ ERRO WEBHOOK URL"); sys.exit(1)

    if client:
        job_queue: JobQueue = app.job_queue
        # Roda a atualização 1 vez na inicialização e depois a cada 1 hora (3600s)
        # O jobqueue é a maneira mais segura de rodar I/O síncrono em Webhooks
        job_queue.run_repeating(atualizar_planilhas, interval=3600, first=0, name="AtualizacaoPlanilhas")
        # Pré-carrega o cache de histórico
        asyncio.run(pre_carregar_cache_sheets())
    else: 
        logging.warning("Job Queue e funções GSheets desativados.")
    
    logging.info("Bot rodando!")
    app.run_webhook(listen="0.0.0.0", port=int(os.environ.get("PORT", "8080")), url_path=BOT_TOKEN, webhook_url=webhook_base_url + '/' + BOT_TOKEN)

if __name__ == "__main__":
    main()
