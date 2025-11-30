# ===============================================================================
# 🏆 BOT DE ESTATÍSTICAS DE CONFRONTO V2.3 - ATUALIZAÇÃO FORÇADA E EMOJIS NO MENU
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
from urllib.parse import urlencode

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
# OBS: No Render, use as chaves SECRETAS BOT_TOKEN, API_KEY e SHEET_URL
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

# Mapeamento de Nomes de Ligas (Para uso no menu e títulos)
NOMES_LIGAS = {
    "PL": "Premier League", "BSA": "Brasileirão S.A", 
    "BL1": "Bundesliga", "PD": "La Liga", "SA": "Serie A", 
    "FL1": "Ligue 1", "PPL": "Primeira Liga", "CL": "Champions League", 
    "ELC": "Championship", "DED": "Eredivisie"
}

# ✅ NOVO: Mapeamento para EMOJIS (Conforme solicitação e imagem)
EMOJIS_LIGAS = {
    "PL": "🏴󠁧󠁢󠁥󠁮󠁧󠁿", "BL1": "🇩🇪", "PD": "🇪🇸", "SA": "🇮🇹", "FL1": "🇫🇷",
    "PPL": "🇵🇹", "BSA": "🇧🇷", "CL": "🇪🇺", "ELC": "🏴󠁧󠁢󠁥󠁮󠁧󠁿", "DED": "🇳🇱"
}


# Filtros reutilizáveis para Estatísticas e Resultados
CONFRONTO_FILTROS = [
    # Label | Tipo no callback | Últimos | Condição Mandante | Condição Visitante
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
    logging.error("❌ ERRO DE AUTORIZAÇÃO GSHEET: Variável GSPREAD_CREDS_JSON não encontrada. Configure-a no Render.")
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
    """Escapa caracteres que podem ser interpretados como Markdown (V1) e causavam o erro BadRequest."""
    # O código original do usuário usava o formato Markdown (V1), vamos manter para consistência.
    # O escape_markdown do V2 é mais robusto, mas o do usuário era mais simples. 
    # Usaremos o escape simples do V1.
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
        # ESTA É A OPERAÇÃO SÍNCRONA
        linhas = sh.worksheet(aba_name).get_all_records()
    except Exception as e:
        # Se falhar, retorna o cache antigo (se existir)
        if aba_name in SHEET_CACHE: return SHEET_CACHE[aba_name]['data']
        raise e

    SHEET_CACHE[aba_name] = { 'data': linhas, 'timestamp': agora }
    logging.info(f"Dados da planilha '{aba_name}' atualizados no cache.")
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
            # Roda get_sheet_data, que é síncrona, em um thread separado (obrigatório para inicialização async)
            await asyncio.to_thread(get_sheet_data, aba)
            logging.info(f"Cache de histórico para {aba} pré-carregado.")
        except Exception as e:
            logging.warning(f"Não foi possível pré-carregar cache para {aba}: {e}")
        await asyncio.sleep(1)


# ✅ NOVO: FUNÇÕES SÍNCRONAS PARA FORÇAR ATUALIZAÇÃO E RECARGA
def _limpar_cache_e_recarregar_dados():
    """
    Limpa o cache global e força a recarga de dados para as abas de histórico (past).
    Esta função é SÍNCRONA e deve ser chamada via `asyncio.to_thread`.
    """
    global SHEET_CACHE
    logging.info("Iniciando processo de forçar atualização/limpeza de cache...")
    
    # 1. Limpa o cache para garantir que a próxima leitura será da planilha.
    SHEET_CACHE = {}
    
    if not client:
        logging.error("❌ Não foi possível forçar atualização: Cliente GSheets não autorizado.")
        return False
        
    # 2. Tenta recarregar os dados para popular o cache com as informações mais novas
    recarregadas = 0
    
    for code in ABAS_PASSADO:
        try:
            # Chama get_sheet_data, que agora vai ignorar o cache (pois foi limpo) e recarregar.
            get_sheet_data(code)
            recarregadas += 1
            logging.info(f"✅ Recarregado e cache de '{code}' atualizado com sucesso.")
            
        except Exception as e:
            logging.warning(f"⚠️ Falha ao recarregar dados de '{code}': {e}")
            
    logging.info(f"Processo de forçar atualização finalizado. {recarregadas} de {len(ABAS_PASSADO)} abas recarregadas.")
    return True
    
# ... (O restante das funções de API e atualização periódica 'atualizar_planilhas' 
# é mantido inalterado do seu código base V2.2.8) ...
# =================================================================================
# 🎯 FUNÇÕES DE API E ATUALIZAÇÃO 
# =================================================================================
def buscar_jogos(league_code, status_filter):
    # [Mantido o código original do usuário]
    # ...
    pass # Código completo aqui
    # ...
def buscar_jogos_live(league_code):
    # [Mantido o código original do usuário]
    # ...
    pass # Código completo aqui
    # ...
async def atualizar_planilhas(context: ContextTypes.DEFAULT_TYPE):
    # [Mantido o código original do usuário]
    # ...
    pass # Código completo aqui
    # ...
    
# =================================================================================
# 📈 FUNÇÕES DE CÁLCULO E FORMATAÇÃO DE ESTATÍSTICAS
# =================================================================================
def calcular_estatisticas_time(time, aba, ultimos=None, casa_fora=None):
    # [Mantido o código original do usuário]
    # ...
    pass # Código completo aqui
    # ...
def formatar_estatisticas(d):
    # [Mantido o código original do usuário]
    # ...
    pass # Código completo aqui
    # ...
def listar_ultimos_jogos(time, aba, ultimos=None, casa_fora=None):
    # [Mantido o código original do usuário]
    # ...
    pass # Código completo aqui
    # ...
# =================================================================================
# 🤖 HANDLERS DO BOT (ASSÍNCRONOS) - FLUXO DE NAVEGAÇÃO
# =================================================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Olá! Bem-vindo ao Bot de Estatísticas de Confronto. Use /stats para começar.")

async def listar_competicoes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    ✅ AJUSTADO: Exibe o menu de seleção da liga com emojis conforme a imagem.
    """
    try:
        if not client: raise Exception("Cliente GSheets não autorizado.")
        # Simplesmente tenta ler um dado para confirmar a conexão antes de mostrar o menu
        await asyncio.to_thread(get_sheet_data, "CL") 
        
        keyboard = [
            # Estrutura com 2 colunas e emojis, conforme a imagem
            [InlineKeyboardButton(f"{EMOJIS_LIGAS['PL']} {NOMES_LIGAS['PL']}", callback_data="c|PL"), 
             InlineKeyboardButton(f"{EMOJIS_LIGAS['BL1']} {NOMES_LIGAS['BL1']}", callback_data="c|BL1")],
            [InlineKeyboardButton(f"{EMOJIS_LIGAS['PD']} {NOMES_LIGAS['PD']}", callback_data="c|PD"), 
             InlineKeyboardButton(f"{EMOJIS_LIGAS['SA']} {NOMES_LIGAS['SA']}", callback_data="c|SA")],
            [InlineKeyboardButton(f"{EMOJIS_LIGAS['FL1']} {NOMES_LIGAS['FL1']}", callback_data="c|FL1"), 
             InlineKeyboardButton(f"{EMOJIS_LIGAS['PPL']} {NOMES_LIGAS['PPL']}", callback_data="c|PPL")],
            [InlineKeyboardButton(f"{EMOJIS_LIGAS['BSA']} {NOMES_LIGAS['BSA']}", callback_data="c|BSA"), 
             InlineKeyboardButton(f"{EMOJIS_LIGAS['CL']} {NOMES_LIGAS['CL']}", callback_data="c|CL")],
            [InlineKeyboardButton(f"{EMOJIS_LIGAS['ELC']} {NOMES_LIGAS['ELC']}", callback_data="c|ELC"), 
             InlineKeyboardButton(f"{EMOJIS_LIGAS['DED']} {NOMES_LIGAS['DED']}", callback_data="c|DED")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        title = "*SELECIONE A LIGA*"

        if update.callback_query:
            try:
                await update.callback_query.edit_message_text(title, reply_markup=reply_markup, parse_mode='Markdown')
            except BadRequest:
                await update.callback_query.message.reply_text(title, reply_markup=reply_markup, parse_mode='Markdown')
            await update.callback_query.answer()
        else:
            await update.message.reply_text(title, reply_markup=reply_markup, parse_mode='Markdown')
            
    except Exception as e:
        logging.error(f"❌ ERRO CRÍTICO ao listar competições ou acessar dados: {e}")
        error_message = "❌ *ERRO CRÍTICO DE DADOS!* Não foi possível acessar a planilha ou a API. Verifique as credenciais e os Logs do Render."
        
        if update.callback_query:
            await update.callback_query.edit_message_text(error_message, parse_mode='Markdown')
            await update.callback_query.answer("Falha ao carregar dados.", show_alert=True)
        elif update.message:
            await update.message.reply_text(error_message, parse_mode='Markdown')

# ✅ NOVO: HANDLER PARA FORÇAR ATUALIZAÇÃO DA PLANILHA
async def forcar_atualizacao_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Comando que limpa o cache e força a recarga dos dados da planilha.
    """
    if not client:
        await update.message.reply_text("❌ Não é possível atualizar. O cliente GSheets não está autorizado.")
        return

    try:
        await update.message.reply_text("⏳ *Forçando Atualização:* Limpando cache e recarregando dados da planilha. Aguarde...")
        
        # CRÍTICO: Executa a função síncrona de limpeza/recarregamento em um thread separado
        sucesso = await asyncio.to_thread(_limpar_cache_e_recarregar_dados)
        
        if sucesso:
            await update.message.reply_text("✅ *Atualização Concluída!* O cache de dados de histórico foi limpo e recarregado com as informações mais recentes. Agora você pode usar /stats.", parse_mode='Markdown')
        else:
            await update.message.reply_text("⚠️ *Atualização Parcial/Falha.* Verifique os logs do servidor para mais detalhes.")
            
    except Exception as e:
        logging.error(f"Erro ao forçar atualização: {e}")
        await update.message.reply_text(f"❌ Erro interno ao tentar forçar atualização: {escape_markdown(str(e))}", parse_mode='Markdown')


# ... (O restante das funções do fluxo do bot: mostrar_menu_status_jogo, listar_jogos, etc. 
# e o callback_query_handler são mantidos inalterados do seu código base V2.2.8) ...

async def mostrar_menu_status_jogo(update: Update, context: ContextTypes.DEFAULT_TYPE, aba_code: str):
    # [Mantido o código original do usuário]
    # ...
    pass # Código completo aqui
    # ...
async def listar_jogos(update: Update, context: ContextTypes.DEFAULT_TYPE, aba_code: str, status: str):
    # [Mantido o código original do usuário]
    # ...
    pass # Código completo aqui
    # ...
async def show_confronto_stats_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, aba_code: str, mandante: str, visitante: str, confronto_id: str):
    # [Mantido o código original do usuário]
    # ...
    pass # Código completo aqui
    # ...
async def processar_confronto(update: Update, context: ContextTypes.DEFAULT_TYPE, tipo: str, ultimos: int, mandante_filtro: str | None, visitante_filtro: str | None, aba_code: str, confronto_id: str):
    # [Mantido o código original do usuário]
    # ...
    pass # Código completo aqui
    # ...
async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # [Mantido o código original do usuário]
    # ...
    pass # Código completo aqui
    # ...


# =================================================================================
# 🚀 FUNÇÃO PRINCIPAL
# =================================================================================
def main():
    if not BOT_TOKEN or BOT_TOKEN == "SEU_TOKEN_AQUI":
        logging.error("O token do bot não está configurado. Verifique a variável de ambiente BOT_TOKEN.")
        sys.exit(1)
        
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("stats", listar_competicoes))
    # ✅ NOVO COMANDO: Para forçar a atualização da planilha
    app.add_handler(CommandHandler("atualizar", forcar_atualizacao_command)) 
    app.add_handler(CallbackQueryHandler(callback_query_handler))
    
    # TRATAMENTO DE URL NULA 
    webhook_base_url = os.environ.get("WEBHOOK_URL") or os.environ.get("RENDER_EXTERNAL_URL")

    if not webhook_base_url:
        logging.error("❌ ERRO CRÍTICO: Não foi possível obter a URL pública (WEBHOOK_URL ou RENDER_EXTERNAL_URL). Certifique-se de que está rodando em um Web Service do Render.")
        sys.exit(1) # Finaliza o processo se a URL não for encontrada
    

    if client:
        # A instância do JobQueue já está anexada e pronta para uso em app.job_queue
        app.job_queue.run_repeating(atualizar_planilhas, interval=3600, first=0, name="AtualizacaoPlanilhas")
        
        # Pré-carregamento do cache deve ser feito com asyncio.run()
        # Não precisa de asyncio.run() se o bot está rodando no thread principal
        # A própria função run_webhook inicia o loop.
        # Vou manter o padrão do seu código original que usa asyncio.run()
        try:
            asyncio.run(pre_carregar_cache_sheets())
        except RuntimeError as e:
            # Lidar com o caso de loop já estar rodando (para alguns ambientes)
            if "running event loop" in str(e):
                logging.warning("Event loop já está rodando. Ignorando pré-carregamento síncrono.")
            else:
                raise e

    else:
        logging.warning("Job Queue de atualização desativado: Conexão com GSheets não estabelecida.")
    
    logging.info("Bot rodando!")
    
    # ✅ INICIA O WEBHOOK
    app.run_webhook(listen="0.0.0.0", port=int(os.environ.get("PORT", "8080")), url_path=BOT_TOKEN, webhook_url=webhook_base_url + '/' + BOT_TOKEN)

if __name__ == "__main__":
    main()
