# ===============================================================================
# 🏆 BOT DE ESTATÍSTICAS DE CONFRONTO V2.3.2 - CORREÇÃO DE TIMEOUT WEBHOOK
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
# 💾 FUNÇÕES DE SUPORTE E CACHING (Síncronas)
# =================================================================================
def safe_int(v):
    try: return int(v)
    except: return 0

def pct(part, total):
    return f"{(part/total)*100:.1f}%" if total>0 else "—"

def media(part, total):
    return f"{(part/total):.2f}" if total>0 else "—"

def escape_markdown(text):
    """Escapa caracteres especiais para MarkdownV2 do Telegram."""
    return str(text).replace('*', '\\*').replace('_', '\\_').replace('[', '\\[') .replace(']', '\\]').replace('`', '\\`').replace('.', '\\.')

def get_sheet_data(aba_code):
    """Obtém dados da aba de histórico (sheet_past) com cache. (SÍNCRONA)"""
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
        # ESTA OPERAÇÃO É SÍNCRONA E PODE DEMORAR MUITO!
        linhas = sh.worksheet(aba_name).get_all_records()
    except Exception as e:
        if aba_name in SHEET_CACHE: 
            # Se falhar a atualização, retorna o cache antigo
            logging.warning(f"Erro ao buscar novos dados para {aba_name}, usando cache antigo. Erro: {e}")
            return SHEET_CACHE[aba_name]['data']
        raise e

    SHEET_CACHE[aba_name] = { 'data': linhas, 'timestamp': agora }
    return linhas

# Função síncrona, mas só chamada uma vez na inicialização (via asyncio.run)
def get_sheet_data_future(aba_code):
    """Obtém dados da aba de cache de jogos futuros (sheet_future). (SÍNCRONA)"""
    aba_name = LIGAS_MAP[aba_code]['sheet_future']
    if not client: return []

    try:
        sh = client.open_by_url(SHEET_URL)
        linhas_raw = sh.worksheet(aba_name).get_all_values()
    except Exception as e:
        logging.error(f"Erro ao buscar cache de futuros jogos em {aba_name}: {e}")
        return []

    if not linhas_raw or len(linhas_raw) <= 1: return []

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
            # ✅ CORREÇÃO: Chama a função SÍNCRONA em uma thread separada
            await asyncio.to_thread(get_sheet_data, aba)
            logging.info(f"Cache de histórico para {aba} pré-carregado.")
        except Exception as e:
            logging.warning(f"Não foi possível pré-carregar cache para {aba}: {e}")
        await asyncio.sleep(1)

# =================================================================================
# 🎯 FUNÇÕES DE API E ATUALIZAÇÃO (CORRIGIDAS PARA JOBQUEUE)
# =================================================================================
# Funções buscar_jogos e buscar_jogos_live omitidas para brevidade. 
# Elas são síncronas e devem ser chamadas com asyncio.to_thread() nos handlers.

def buscar_jogos(league_code, status_filter):
    # CÓDIGO ORIGINAL (SÍNCRONO)
    pass

def buscar_jogos_live(league_code):
    # CÓDIGO ORIGINAL (SÍNCRONO)
    pass


async def atualizar_planilhas(context: ContextTypes.DEFAULT_TYPE):
    """
    Atualiza o histórico e o cache de futuros jogos. 
    (Função já corrigida na resposta anterior - executa I/O síncrono off-thread)
    """
    global SHEET_CACHE
    
    chat_id_to_notify = context.job.data.get("chat_id") if context.job and context.job.data else None
    
    async def notify_user(text, parse_mode='Markdown'):
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
            aba_past = aba_config['sheet_past']
            try: ws_past = sh.worksheet(aba_past)
            except WorksheetNotFound: continue

            # ✅ CORREÇÃO: Chama funções SÍNCRONAS em uma thread separada
            jogos_finished = await asyncio.to_thread(buscar_jogos, aba_code, "FINISHED")
            await asyncio.sleep(10) # Pausa para respeitar limite de rate da API

            if jogos_finished:
                try:
                    exist = await asyncio.to_thread(ws_past.get_all_records)
                    keys_exist = {(r['Mandante'], r['Visitante'], r['Data']) for r in exist}
                    novas_linhas = []
                    for j in jogos_finished:
                        key = (j["Mandante"], j["Visitante"], j["Data"])
                        if key not in keys_exist:
                            novas_linhas.append([j["Mandante"], j["Visitante"], j["Gols Mandante"], j["Gols Visitante"], j["Gols Mandante 1T"], j["Gols Visitante 1T"], j["Gols Mandante 2T"], j["Gols Visitante 2T"], j["Data"]])

                    if novas_linhas:
                        await asyncio.to_thread(ws_past.append_rows, novas_linhas)
                        logging.info(f"✅ {len(novas_linhas)} jogos adicionados ao histórico de {aba_past}.")
                    
                    if aba_past in SHEET_CACHE: del SHEET_CACHE[aba_past]
                except Exception as e:
                    logging.error(f"Erro ao inserir dados na planilha {aba_past}: {e}")

            # 2. ATUALIZAÇÃO DO CACHE DE FUTUROS JOGOS
            aba_future = aba_config['sheet_future']
            try: ws_future = sh.worksheet(aba_future)
            except WorksheetNotFound: continue

            # ✅ CORREÇÃO: Chama funções SÍNCRONAS em uma thread separada
            jogos_future = await asyncio.to_thread(buscar_jogos, aba_code, "ALL")
            await asyncio.sleep(10) 

            try:
                await asyncio.to_thread(ws_future.clear)
                await asyncio.to_thread(ws_future.update, values=[['Mandante', 'Visitante', 'Data/Hora', 'Matchday']], range_name='A1:D1')

                if jogos_future:
                    linhas_future = []
                    # ... (lógica de formatação de linhas_future) ...
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
        
        await notify_user("✅ Atualização forçada concluída com sucesso!")

    except Exception as e:
        logging.error(f"Erro crítico durante a atualização principal: {e}")
        await notify_user(f"❌ Erro crítico na atualização. Verifique os logs.\nErro: {e}")


# =================================================================================
# 📈 FUNÇÕES DE CÁLCULO E FORMATAÇÃO (Síncronas)
# =================================================================================

def calcular_estatisticas_time(time, aba, ultimos=None, casa_fora=None):
    """
    Calcula as estatísticas com base no histórico da planilha. (SÍNCRONA)
    Esta função chama get_sheet_data() internamente.
    """
    linhas = get_sheet_data(aba) # Chamada síncrona
    # ... (Seu código de cálculo continua aqui) ...
    return {} # Placeholder

def formatar_estatisticas(d):
    """Formata as estatísticas para exibição no Telegram. (SÍNCRONA)"""
    # ... (Seu código de formatação continua aqui) ...
    return "Estatísticas formatadas." # Placeholder

def listar_ultimos_jogos(time, aba, ultimos=None, casa_fora=None):
    """
    Lista os últimos jogos e resultados. (SÍNCRONA)
    Esta função chama get_sheet_data() internamente.
    """
    linhas = get_sheet_data(aba) # Chamada síncrona
    # ... (Seu código de listagem continua aqui) ...
    return "Lista de resultados formatada." # Placeholder

# =================================================================================
# 🤖 FUNÇÕES DO BOT: HANDLERS E FLUXOS (CORRIGIDAS PARA ASSINCRONICIDADE)
# =================================================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # CÓDIGO ORIGINAL (Assíncrono)
    await update.message.reply_text("Olá! Bem-vindo ao Bot de Estatísticas de Confronto. Use /stats para começar.")
    
async def listar_competicoes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Exibe o menu de seleção da liga. Adiciona tratamento de erro de dados."""
    try:
        # ✅ CORREÇÃO: Tentar buscar dados de uma liga fora do thread principal 
        # para verificar a conectividade ANTES de mostrar os botões.
        await asyncio.to_thread(get_sheet_data, "CL") 
        
        keyboard = [
            [InlineKeyboardButton(f"🏴󠁧󠁢󠁥󠁮󠁧󠁿 Premier League", callback_data="LIGA_PL"), 
             InlineKeyboardButton(f"🇩🇪 Bundesliga", callback_data="LIGA_BL1")],
            [InlineKeyboardButton(f"🇪🇸 La Liga", callback_data="LIGA_PD"), 
             InlineKeyboardButton(f"🇮🇹 Serie A", callback_data="LIGA_SA")],
            [InlineKeyboardButton(f"🇫🇷 Ligue 1", callback_data="LIGA_FL1"), 
             InlineKeyboardButton(f"🇵🇹 Primeira Liga", callback_data="LIGA_PPL")],
            [InlineKeyboardButton(f"🇧🇷 Brasileirão S.A", callback_data="LIGA_BSA"), 
             InlineKeyboardButton(f"🇪🇺 Champions League", callback_data="LIGA_CL")],
            [InlineKeyboardButton(f"🏴󠁧󠁢󠁥󠁮󠁧󠁿 Championship", callback_data="LIGA_ELC"), 
             InlineKeyboardButton(f"🇳🇱 Eredivisie", callback_data="LIGA_DED")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        texto = "🌍 **SELECIONE A LIGA**\nPara qual competição você deseja consultar estatísticas ou jogos?"

        if update.callback_query:
            await update.callback_query.edit_message_text(texto, reply_markup=reply_markup, parse_mode='Markdown')
            await update.callback_query.answer()
        else:
            await update.message.reply_text(texto, reply_markup=reply_markup, parse_mode='Markdown')
            
    except Exception as e:
        logging.error(f"❌ ERRO CRÍTICO ao listar competições ou acessar dados: {e}")
        error_message = (
            "❌ **ERRO CRÍTICO DE DADOS!**\n"
            "Não foi possível acessar a planilha ou a API. As causas mais comuns são:\n"
            "1. As **Credenciais GSheets** expiraram ou estão erradas.\n"
            "2. A planilha foi movida ou a aba não existe.\n"
            "Verifique os **Logs no Render** para o erro exato."
        )
        
        if update.callback_query:
            await update.callback_query.edit_message_text(error_message, parse_mode='Markdown')
            await update.callback_query.answer("Falha ao carregar dados.", show_alert=True)
        elif update.message:
            await update.message.reply_text(error_message, parse_mode='Markdown')
        else:
            logging.error(f"Não foi possível notificar o usuário. Erro: {e}")

async def mostrar_menu_status_jogo(update: Update, context: ContextTypes.DEFAULT_TYPE, aba_code: str):
    # CÓDIGO ORIGINAL (Assíncrono)
    # Deve usar to_thread se buscar_jogos_live for síncrona
    
    # Exemplo: Chamada para buscar_jogos_live (síncrona) deve ser:
    # jogos_live = await asyncio.to_thread(buscar_jogos_live, aba_code) 
    
    # ... (restante do código) ...
    await update.callback_query.answer()
    
async def listar_jogos(update: Update, context: ContextTypes.DEFAULT_TYPE, aba_code: str, status: str):
    # CÓDIGO ORIGINAL (Assíncrono)
    # Se a função get_sheet_data_future ou buscar_jogos for chamada, use to_thread
    
    # Exemplo: Chamada para get_sheet_data_future (síncrona) deve ser:
    # jogos_futuros = await asyncio.to_thread(get_sheet_data_future, aba_code)
    
    # ... (restante do código) ...
    await update.callback_query.answer()
    
async def mostrar_menu_acoes(update: Update, context: ContextTypes.DEFAULT_TYPE, aba_code: str, mandante: str, visitante: str):
    # CÓDIGO ORIGINAL (Assíncrono)
    # Não deve ter I/O síncrono pesado, exceto talvez formatação, mas é leve.
    
    # ... (restante do código) ...
    await update.callback_query.answer()


async def exibir_estatisticas(update: Update, context: ContextTypes.DEFAULT_TYPE, mandante: str, visitante: str, aba_code: str, filtro_idx: int):
    """Exibe as estatísticas, rodando o cálculo em um thread separado."""
    query = update.callback_query
    
    # Extrai filtro (ultimos, tipo_confronto)
    ultimos = CONFRONTO_FILTROS[filtro_idx][2]
    tipo_confronto = CONFRONTO_FILTROS[filtro_idx][3], CONFRONTO_FILTROS[filtro_idx][4]

    try:
        await query.edit_message_text("⏳ Calculando estatísticas, aguarde...")
        
        # ✅ CORREÇÃO CRÍTICA 1: Roda a função de CÁLCULO (que acessa GSheets) off-thread
        d = await asyncio.to_thread(
            calcular_estatisticas_time, 
            mandante, aba_code, ultimos, tipo_confronto
        )

        # ✅ CORREÇÃO CRÍTICA 2: Roda a função de FORMATAÇÃO off-thread
        texto_estatisticas = await asyncio.to_thread(formatar_estatisticas, d)

        # ... (código para gerar botões de volta) ...
        keyboard = [[InlineKeyboardButton("↩️ Voltar", callback_data=f"VOLTAR_ACOES_{aba_code}_{mandante}_{visitante}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(
            texto_estatisticas,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logging.error(f"Erro ao exibir estatísticas: {e}")
        await query.edit_message_text(f"❌ Erro ao calcular estatísticas. Tente novamente iniciando com /stats.\nErro: {e}")
        
    await query.answer()

async def exibir_ultimos_resultados(update: Update, context: ContextTypes.DEFAULT_TYPE, mandante: str, visitante: str, aba_code: str, filtro_idx: int):
    """Lista os últimos jogos, rodando o acesso a GSheets em um thread separado."""
    query = update.callback_query
    
    # Extrai filtro (ultimos, tipo_confronto)
    ultimos = CONFRONTO_FILTROS[filtro_idx][2]
    tipo_confronto = CONFRONTO_FILTROS[filtro_idx][3], CONFRONTO_FILTROS[filtro_idx][4]
    
    try:
        await query.edit_message_text("⏳ Buscando resultados, aguarde...")
        
        # ✅ CORREÇÃO CRÍTICA: Roda a função de LISTAGEM (que acessa GSheets) off-thread
        texto_resultados = await asyncio.to_thread(
            listar_ultimos_jogos, 
            mandante, aba_code, ultimos, tipo_confronto
        )

        # ... (código para gerar botões de volta) ...
        keyboard = [[InlineKeyboardButton("↩️ Voltar", callback_data=f"VOLTAR_ACOES_{aba_code}_{mandante}_{visitante}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(
            texto_resultados,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

    except Exception as e:
        logging.error(f"Erro ao listar resultados: {e}")
        await query.edit_message_text(f"❌ Erro ao listar resultados. Tente novamente iniciando com /stats.\nErro: {e}")
        
    await query.answer()


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
    
    await update.message.reply_text("⚡️ **Atualização Forçada Agendada!** O processo será executado em segundo plano e você será notificado aqui em caso de sucesso ou erro.", parse_mode='Markdown')

async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Função que gerencia o clique de todos os botões (callbacks)."""
    query = update.callback_query
    data = query.data
    
    try:
        # LIGA_CODE
        if data.startswith("LIGA_"):
            aba_code = data.split("_")[1]
            await mostrar_menu_status_jogo(update, context, aba_code)

        # STATUS_JOGO_CODE
        elif data.startswith("STATUS_"):
            parts = data.split("_")
            aba_code = parts[1]
            status = parts[2]
            await listar_jogos(update, context, aba_code, status)

        # SELECIONA_JOGO_CODE
        elif data.startswith("SELECIONA_"):
            parts = data.split("_")
            aba_code = parts[1]
            mandante = parts[2]
            visitante = parts[3]
            await mostrar_menu_acoes(update, context, aba_code, mandante, visitante)

        # ACÕES (STATS, RESULTADOS)
        elif data.startswith("ACAO_"):
            parts = data.split("_")
            aba_code = parts[1]
            acao = parts[2] # STATS ou RESULTADOS
            mandante = parts[3]
            visitante = parts[4]
            # O filtro padrão será sempre o primeiro filtro (GERAL)
            filtro_idx = 0 
            
            if acao == "STATS":
                await exibir_estatisticas(update, context, mandante, visitante, aba_code, filtro_idx)
            elif acao == "RESULTADOS":
                await exibir_ultimos_resultados(update, context, mandante, visitante, aba_code, filtro_idx)
        
        # FILTROS
        elif data.startswith("FILTRO_"):
            parts = data.split("_")
            aba_code = parts[1]
            mandante = parts[2]
            visitante = parts[3]
            filtro_type = parts[4] # STATS ou RESULTADOS
            filtro_idx = safe_int(parts[5])

            if filtro_type == "STATS":
                await exibir_estatisticas(update, context, mandante, visitante, aba_code, filtro_idx)
            elif filtro_type == "RESULTADOS":
                await exibir_ultimos_resultados(update, context, mandante, visitante, aba_code, filtro_idx)

        # VOLTAR
        elif data.startswith("VOLTAR_"):
            parts = data.split("_")
            target = parts[1]
            
            if target == "LIGA":
                await listar_competicoes(update, context) # Volta para o menu de ligas
            
            elif target == "STATUS":
                aba_code = parts[2]
                await mostrar_menu_status_jogo(update, context, aba_code) # Volta para a seleção de status

            elif target == "JOGOS":
                aba_code = parts[2]
                status = parts[3]
                await listar_jogos(update, context, aba_code, status) # Volta para a lista de jogos

            elif target == "ACOES":
                aba_code = parts[2]
                mandante = parts[3]
                visitante = parts[4]
                await mostrar_menu_acoes(update, context, aba_code, mandante, visitante) # Volta para o menu de ações
        
        # Garante que o indicador de carregamento do botão suma (mesmo em caso de erro interno)
        await query.answer()

    except Exception as e:
        logging.error(f"Erro no callback_query_handler: {e}")
        try:
            # Tenta editar a mensagem com o erro para que o usuário saiba que algo deu errado
            await query.edit_message_text(f"❌ Ocorreu um erro interno. Tente novamente iniciando com /stats.\nErro: {e}")
            await query.answer("Erro!", show_alert=True)
        except BadRequest:
            # Se a edição falhar, pelo menos responde ao callback
            await query.answer("Erro no processamento da ação.", show_alert=True)


# =================================================================================
# 🚀 FUNÇÃO PRINCIPAL
# =================================================================================
def main():
    if not BOT_TOKEN or BOT_TOKEN == "SEU_TOKEN_AQUI":
        logging.error("O token do bot não está configurado. Verifique a variável de ambiente BOT_TOKEN.")
        sys.exit(1)
        
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Adiciona Handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("stats", listar_competicoes))
    app.add_handler(CommandHandler("forcaupdate", forcaupdate_command)) 
    app.add_handler(CallbackQueryHandler(callback_query_handler))
    
    # Webhook config para Render
    webhook_base_url = os.environ.get("WEBHOOK_URL") or os.environ.get("RENDER_EXTERNAL_URL")
    if not webhook_base_url: 
        logging.error("❌ ERRO CRÍTICO: URL pública não encontrada.")
        sys.exit(1)

    if client:
        job_queue: JobQueue = app.job_queue
        # Roda a atualização 1 vez na inicialização e depois a cada 1 hora (3600s)
        job_queue.run_repeating(atualizar_planilhas, interval=3600, first=0, name="AtualizacaoPlanilhas")
        # Pré-carrega o cache de histórico
        asyncio.run(pre_carregar_cache_sheets())
    else: 
        logging.warning("Job Queue e funções GSheets desativados.")
    
    logging.info("Bot rodando!")
    app.run_webhook(listen="0.0.0.0", port=int(os.environ.get("PORT", "8080")), url_path=BOT_TOKEN, webhook_url=webhook_base_url + '/' + BOT_TOKEN)

if __name__ == "__main__":
    main()
