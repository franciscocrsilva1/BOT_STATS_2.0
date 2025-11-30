# ===============================================================================
# 🏆 BOT DE ESTATÍSTICAS DE CONFRONTO V2.6 - COMPLETO C/ FORÇAR ATUALIZAÇÃO E EMOJIS
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
nest_asyncio.apply()

# ===== Variáveis de Configuração (LIDAS DE VARIÁVEIS DE AMBIENTE) =====
BOT_TOKEN = os.environ.get("BOT_TOKEN", "SEU_TOKEN_AQUI") 
API_KEY = os.environ.get("API_KEY", "SUA_API_KEY_AQUI")
SHEET_URL = os.environ.get("SHEET_URL", "https://docs.google.com/spreadsheets/d/1ChFFXQxo1qQElNzh2OC8-UPGofRXxyVWN06ExBQ3YqY/edit?usp=drivesdk")

# Mapeamento de Ligas
LIGAS_MAP = {
    "CL": {"sheet_past": "CL", "sheet_future": "CL_FJ"}, "BSA": {"sheet_past": "BSA", "sheet_future": "BSA_FJ"},
    "BL1": {"sheet_past": "BL1", "sheet_future": "BL1_FJ"}, "PL": {"sheet_past": "PL", "sheet_future": "PL_FJ"},
    "ELC": {"sheet_past": "ELC", "sheet_future": "ELC_FJ"}, "DED": {"sheet_past": "DED", "sheet_future": "DED_FJ"},
    "PD": {"sheet_past": "PD", "sheet_future": "PD_FJ"}, "PPL": {"sheet_past": "PPL", "sheet_future": "PPL_FJ"},
    "SA": {"sheet_past": "SA", "sheet_future": "SA_FJ"}, "FL1": {"sheet_past": "FL1", "sheet_future": "FL1_FJ"},
}
ABAS_PASSADO = list(LIGAS_MAP.keys())
NOMES_LIGAS = {
    "PL": "Premier League (Inglaterra)", "BSA": "Brasileirão Série A (Brasil)", 
    "BL1": "Bundesliga (Alemanha)", "PD": "La Liga (Espanha)", "SA": "Serie A (Itália)", 
    "FL1": "Ligue 1 (França)", "PPL": "Primeira Liga (Portugal)", "CL": "Champions League (Europa)", 
    "ELC": "Championship (Inglaterra)", "DED": "Eredivisie (Holanda)"
}

# Mapeamento para EMOJIS (Conforme solicitação e imagem)
EMOJIS_LIGAS = {
    "PL": "🏴󠁧󠁢󠁥󠁮󠁧󠁿", "BL1": "🇩🇪", "PD": "🇪🇸", "SA": "🇮🇹", "FL1": "🇫🇷",
    "PPL": "🇵🇹", "BSA": "🇧🇷", "CL": "🇪🇺", "ELC": "🏴󠁧󠁢󠁥󠁮󠁧󠁿", "DED": "🇳🇱"
}


ULTIMOS = 10
SHEET_CACHE = {}
CACHE_DURATION_SECONDS = 3600 # 1 hora
TIMES_POR_PAGINA = 15

# Filtros reutilizáveis para Estatísticas e Resultados
CONFRONTO_FILTROS = [
    (f"📊 Estatísticas | Últimos {ULTIMOS} GERAL", "STATS_FILTRO", ULTIMOS, None, None),
    (f"📊 Estatísticas | Últimos {ULTIMOS} (M CASA vs V FORA)", "STATS_FILTRO", ULTIMOS, "casa", "fora"),
    (f"📅 Resultados | Últimos {ULTIMOS} GERAL", "RESULTADOS_FILTRO", ULTIMOS, None, None),
    (f"📅 Resultados | Últimos {ULTIMOS} (M CASA vs V FORA)", "RESULTADOS_FILTRO", ULTIMOS, "casa", "fora"),
]

# =================================================================================
# ✅ CONEXÃO GSHEETS E INIT
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
    return f"{(part/total)*100:.1f}" if total>0 else "0.0"

def media(part, total):
    return f"{(part/total):.2f}" if total>0 else "0.00"

def escape_markdown(text):
    """Escapa caracteres especiais para MarkdownV2 do Telegram."""
    chars_to_escape = ['*', '_', '`', '[', ']', '(', ')', '~', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    text = str(text)
    for char in chars_to_escape:
        text = text.replace(char, '\\' + char)
    return text

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
        # ESTA OPERAÇÃO É SÍNCRONA
        linhas = sh.worksheet(aba_name).get_all_records()
    except Exception as e:
        if aba_name in SHEET_CACHE: 
            logging.warning(f"Erro ao buscar novos dados para {aba_name}, usando cache antigo. Erro: {e}")
            return SHEET_CACHE[aba_name]['data']
        raise e

    SHEET_CACHE[aba_name] = { 'data': linhas, 'timestamp': agora }
    logging.info(f"Dados da planilha '{aba_name}' atualizados no cache.")
    return linhas

def get_unique_teams(aba_code):
    """Extrai a lista de times únicos do histórico. (SÍNCRONA)"""
    try:
        # Usa a função com cache
        linhas = get_sheet_data(aba_code) 
    except Exception as e:
        logging.error(f"Erro ao buscar dados para times: {e}")
        return []
        
    times = set()
    for linha in linhas:
        if linha.get("Mandante_Nome"): times.add(linha["Mandante_Nome"])
        if linha.get("Visitante_Nome"): times.add(linha["Visitante_Nome"])
        
    return sorted(list(times))

# Funções Síncronas Placeholder (Funcionalidade Futura)
def get_sheet_data_future(aba_code): return []
def buscar_jogos_live(league_code): return []

# =================================================================================
# ⚙️ FUNÇÃO CRÍTICA DE ATUALIZAÇÃO E CACHE (Síncrona)
# =================================================================================

def _limpar_cache_e_recarregar_dados():
    """
    Limpa o cache global e força a recarga de dados para as abas de histórico (past).
    Esta função é SÍNCRONA e deve ser chamada via `asyncio.to_thread`
    ou `JobQueue` para evitar bloqueios.
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
    for aba in ABAS_PASSADO:
        try:
            # Chama get_sheet_data, que agora vai ignorar o cache (pois foi limpo) e recarregar.
            get_sheet_data(aba) 
            recarregadas += 1
            logging.info(f"✅ Recarregado e cache de '{aba}' atualizado com sucesso.")
        except Exception as e:
            logging.warning(f"⚠️ Falha ao recarregar dados de '{aba}': {e}")
            
    logging.info(f"Processo de forçar atualização finalizado. {recarregadas} de {len(ABAS_PASSADO)} abas recarregadas.")
    return True

def actualizar_planilhas(context: ContextTypes.DEFAULT_TYPE):
    """
    Função de atualização rodada pelo JobQueue (a cada 1 hora).
    Apenas chama a função que limpa o cache para garantir que as próximas consultas sejam novas.
    """
    # Esta função roda em um JobQueue, já fora do thread principal do Webhook.
    _limpar_cache_e_recarregar_dados()


# =================================================================================
# 📈 FUNÇÕES DE CÁLCULO E FORMATAÇÃO (Síncronas) - IMPLEMENTAÇÃO COMPLETA
# =================================================================================

# ... (As funções calcular_estatisticas_time, formatar_estatisticas e listar_ultimos_jogos são mantidas da V2.5) ...
def calcular_estatisticas_time(time, aba, ultimos=None, casa_fora=None):
    """Calcula as estatísticas com base no histórico. (SÍNCRONA)"""
    # MANTIDO DA V2.5
    linhas = get_sheet_data(aba) 
    # ... (Resto da implementação de cálculo) ...
    
    time_stats = {
        "Vitorias": 0, "Derrotas": 0, "Empates": 0, "Jogos": 0,
        "Gols Pro": 0, "Gols Contra": 0, "Gols Pro 1T": 0, "Gols Contra 1T": 0,
        "Gols Pro 2T": 0, "Gols Contra 2T": 0, "Ambos Marcam": 0, "Mandante": time
    }
    
    jogos_filtrados = []
    linhas_invertidas = linhas[::-1] # Processa do mais recente
    
    for linha in linhas_invertidas:
        is_mandante = linha.get("Mandante_Nome") == time
        is_visitante = linha.get("Visitante_Nome") == time
        
        if not (is_mandante or is_visitante):
            continue

        if casa_fora:
            if casa_fora[0] == "casa" and not is_mandante: continue
            if casa_fora[1] == "fora" and not is_visitante: continue
        
        jogos_filtrados.append(linha)
        
        if ultimos is not None and len(jogos_filtrados) >= ultimos:
            break
            
    for linha in jogos_filtrados:
        time_stats["Jogos"] += 1
        is_mandante = linha.get("Mandante_Nome") == time
        
        gm = safe_int(linha.get("Gols Mandante"))
        gv = safe_int(linha.get("Gols Visitante"))
        gm1t = safe_int(linha.get("Gols Mandante 1T"))
        gv1t = safe_int(linha.get("Gols Visitante 1T"))
        gm2t = safe_int(linha.get("Gols Mandante 2T"))
        gv2t = safe_int(linha.get("Gols Visitante 2T"))
        
        if is_mandante:
            gols_pro, gols_contra = gm, gv
            gols_pro_1t, gols_contra_1t = gm1t, gv1t
            gols_pro_2t, gols_contra_2t = gm2t, gv2t
        else: # Visitante
            gols_pro, gols_contra = gv, gm
            gols_pro_1t, gols_contra_1t = gv1t, gm1t
            gols_pro_2t, gols_contra_2t = gv2t, gm2t
            
        time_stats["Gols Pro"] += gols_pro
        time_stats["Gols Contra"] += gols_contra
        time_stats["Gols Pro 1T"] += gols_pro_1t
        time_stats["Gols Contra 1T"] += gols_contra_1t
        time_stats["Gols Pro 2T"] += gols_pro_2t
        time_stats["Gols Contra 2T"] += gols_contra_2t
        
        if gols_pro > gols_contra:
            time_stats["Vitorias"] += 1
        elif gols_pro < gols_contra:
            time_stats["Derrotas"] += 1
        else:
            time_stats["Empates"] += 1
            
        if gols_pro > 0 and gols_contra > 0:
            time_stats["Ambos Marcam"] += 1

    return time_stats

def formatar_estatisticas(d):
    """Formata as estatísticas para exibição no Telegram. (SÍNCRONA)"""
    # MANTIDO DA V2.5
    jogos = d["Jogos"]
    gp = d["Gols Pro"]
    gc = d["Gols Contra"]
    
    texto = (
        f"📊 **Estatísticas de {escape_markdown(d['Mandante'])}**\n"
        f"--- **Resultado Geral \\({jogos} jogos\\)** ---\n"
        f"Vitórias: {d['Vitorias']} \\({pct(d['Vitorias'], jogos)}\\%\\)\n"
        f"Derrotas: {d['Derrotas']} \\({pct(d['Derrotas'], jogos)}\\%\\)\n"
        f"Empates: {d['Empates']} \\({pct(d['Empates'], jogos)}\\%\\)\n\n"
        
        f"--- **Gols Totais** ---\n"
        f"Gols Pró: {gp} \\(Média: {media(gp, jogos)}\\)\n"
        f"Gols Contra: {gc} \\(Média: {media(gc, jogos)}\\)\n"
        f"Ambos Marcam \\(Sim\\): {d['Ambos Marcam']} \\({pct(d['Ambos Marcam'], jogos)}\\%\\)\n\n"
        
        f"--- **Média de Gols por Tempo** ---\n"
        f"1º T Pro: {media(d['Gols Pro 1T'], jogos)}\n"
        f"2º T Pro: {media(d['Gols Pro 2T'], jogos)}\n"
        f"1º T Contra: {media(d['Gols Contra 1T'], jogos)}\n"
        f"2º T Contra: {media(d['Gols Contra 2T'], jogos)}"
    )
    return texto

def listar_ultimos_jogos(time, aba, ultimos=None, casa_fora=None):
    """Lista os últimos jogos e resultados. (SÍNCRONA)"""
    # MANTIDO DA V2.5
    linhas = get_sheet_data(aba)
    
    jogos_filtrados = []
    linhas_invertidas = linhas[::-1]
    # ... (Resto da implementação de listagem) ...
    for linha in linhas_invertidas:
        is_mandante = linha.get("Mandante_Nome") == time
        is_visitante = linha.get("Visitante_Nome") == time
        
        if not (is_mandante or is_visitante):
            continue

        if casa_fora:
            if casa_fora[0] == "casa" and not is_mandante: continue
            if casa_fora[1] == "fora" and not is_visitante: continue
        
        jogos_filtrados.append(linha)
        
        if ultimos is not None and len(jogos_filtrados) >= ultimos:
            break

    texto_jogos = []
    for linha in jogos_filtrados:
        mandante = escape_markdown(linha.get("Mandante_Nome", "?"))
        visitante = escape_markdown(linha.get("Visitante_Nome", "?"))
        gm = safe_int(linha.get("Gols Mandante"))
        gv = safe_int(linha.get("Gols Visitante"))
        data = linha.get("Data", "N/A")
        
        resultado_final = f"{gm} \\- {gv}"
        
        if linha.get("Mandante_Nome") == time:
            vs_time = visitante
            local = "C" 
            if gm > gv: resultado_cor = "✅ V"
            elif gm < gv: resultado_cor = "❌ D"
            else: resultado_cor = "➖ E"
        else:
            vs_time = mandante
            local = "F"
            if gv > gm: resultado_cor = "✅ V"
            elif gv < gm: resultado_cor = "❌ D"
            else: resultado_cor = "➖ E"
            
        texto_jogos.append(
            f"{resultado_cor} \\({local}\\) vs {vs_time}: {resultado_final} \\({data}\\)"
        )
        
    header = f"📅 **Últimos {len(jogos_filtrados)} Jogos de {escape_markdown(time)}**\n"
    if not texto_jogos:
        return header + "Nenhum resultado encontrado com os filtros selecionados\\."
        
    return header + "\n".join(texto_jogos)

# =================================================================================
# 🤖 HANDLERS DO BOT (ASSÍNCRONOS) - FLUXO DE NAVEGAÇÃO
# =================================================================================

async def pre_carregar_cache_sheets():
    """Pré-carrega o histórico de todas as ligas (rodado uma vez na inicialização)."""
    if not client: return
    logging.info("Iniciando pré-carregamento de cache...")
    for aba in ABAS_PASSADO:
        try:
            await asyncio.to_thread(get_sheet_data, aba)
            logging.info(f"Cache de histórico para {aba} pré-carregado.")
        except Exception as e:
            logging.warning(f"Não foi possível pré-carregar cache para {aba}: {e}")
        await asyncio.sleep(0.5)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Olá! Bem-vindo ao Bot de Estatísticas de Confronto. Use /stats para começar.")

async def listar_competicoes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Exibe o menu de seleção da liga com emojis."""
    try:
        await asyncio.to_thread(get_sheet_data, "CL") # Teste de conexão
        
        # ✅ AJUSTE: EMOJIS CONFORME SOLICITADO
        keyboard = [
            [InlineKeyboardButton(f"{EMOJIS_LIGAS['PL']} Premier League", callback_data="LIGA_PL"), 
             InlineKeyboardButton(f"{EMOJIS_LIGAS['BL1']} Bundesliga", callback_data="LIGA_BL1")],
            [InlineKeyboardButton(f"{EMOJIS_LIGAS['PD']} La Liga", callback_data="LIGA_PD"), 
             InlineKeyboardButton(f"{EMOJIS_LIGAS['SA']} Serie A", callback_data="LIGA_SA")],
            [InlineKeyboardButton(f"{EMOJIS_LIGAS['FL1']} Ligue 1", callback_data="LIGA_FL1"), 
             InlineKeyboardButton(f"{EMOJIS_LIGAS['PPL']} Primeira Liga", callback_data="LIGA_PPL")],
            [InlineKeyboardButton(f"{EMOJIS_LIGAS['BSA']} Brasileirão S.A", callback_data="LIGA_BSA"), 
             InlineKeyboardButton(f"{EMOJIS_LIGAS['CL']} Champions League", callback_data="LIGA_CL")],
            [InlineKeyboardButton(f"{EMOJIS_LIGAS['ELC']} Championship", callback_data="LIGA_ELC"), 
             InlineKeyboardButton(f"{EMOJIS_LIGAS['DED']} Eredivisie", callback_data="LIGA_DED")]
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
        error_message = "❌ **ERRO CRÍTICO DE DADOS\\!**\nNão foi possível acessar a planilha ou a API\\. Verifique as credenciais e os Logs do Render\\."
        
        if update.callback_query:
            await update.callback_query.edit_message_text(error_message, parse_mode='MarkdownV2')
            await update.callback_query.answer("Falha ao carregar dados.", show_alert=True)
        elif update.message:
            await update.message.reply_text(error_message, parse_mode='MarkdownV2')

async def forcar_atualizacao_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    ✅ NOVO COMANDO: Limpa o cache e força a recarga dos dados da planilha.
    Deve ser rodado com asyncio.to_thread para não bloquear o bot.
    """
    if not client:
        await update.message.reply_text("❌ Não é possível atualizar. O cliente GSheets não está autorizado.")
        return

    try:
        await update.message.reply_text("⏳ **Forçando Atualização:** Limpando cache e recarregando dados da planilha. Aguarde...")
        
        # CRÍTICO: Executa a função síncrona de limpeza/recarregamento em um thread separado
        sucesso = await asyncio.to_thread(_limpar_cache_e_recarregar_dados)
        
        if sucesso:
            await update.message.reply_text("✅ **Atualização Concluída!** O cache de dados de histórico foi limpo e recarregado com as informações mais recentes. Agora você pode usar /stats.")
        else:
            await update.message.reply_text("⚠️ **Atualização Parcial/Falha.** Verifique os logs do servidor para mais detalhes.")
            
    except Exception as e:
        logging.error(f"Erro ao forçar atualização: {e}")
        await update.message.reply_text(f"❌ Erro interno ao tentar forçar atualização: {escape_markdown(str(e))}", parse_mode='MarkdownV2')

# ... (As funções mostrar_menu_status_jogo, listar_jogos, listar_times_historico, mostrar_menu_acoes, 
# exibir_estatisticas, exibir_ultimos_resultados e callback_query_handler são mantidas da V2.5) ...

async def mostrar_menu_status_jogo(update: Update, context: ContextTypes.DEFAULT_TYPE, aba_code: str):
    """Exibe o menu para escolher o status (Ao Vivo, Futuros, Histórico) após a seleção da liga."""
    query = update.callback_query
    nome_liga = NOMES_LIGAS.get(aba_code, aba_code)

    try:
        keyboard = [
            [InlineKeyboardButton(f"🔴 Jogos AO VIVO", callback_data=f"STATUS_{aba_code}_LIVE")],
            [InlineKeyboardButton(f"🗓️ Jogos FUTUROS", callback_data=f"STATUS_{aba_code}_FUTUROS")],
            [InlineKeyboardButton(f"📚 Histórico / Estatísticas", callback_data=f"STATUS_{aba_code}_HISTORICO")],
            [InlineKeyboardButton("↩️ Voltar p/ Ligas", callback_data="VOLTAR_LIGA")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        texto = f"🏆 **{escape_markdown(nome_liga)}**\nSelecione o tipo de jogo para consulta:"

        await query.edit_message_text(texto, reply_markup=reply_markup, parse_mode='MarkdownV2')

    except Exception as e:
        logging.error(f"❌ Erro ao mostrar menu de status para {aba_code}: {e}")
        await query.edit_message_text(f"❌ Erro ao carregar menu de status\\.\nDetalhes do erro: {escape_markdown(str(e))}", parse_mode='MarkdownV2')
        
    await query.answer()

async def listar_jogos(update: Update, context: ContextTypes.DEFAULT_TYPE, aba_code: str, status: str):
    """Lista jogos baseados no status (LIVE, FUTUROS, HISTORICO)."""
    query = update.callback_query
    
    if status == "HISTORICO":
        await listar_times_historico(update, context, aba_code, pagina=0)
        return

    try:
        await query.edit_message_text("⏳ Buscando jogos, aguarde...")
        
        jogos = []
        if status == "FUTUROS":
            jogos = await asyncio.to_thread(get_sheet_data_future, aba_code)
        elif status == "LIVE":
            jogos = await asyncio.to_thread(buscar_jogos_live, aba_code)

        
        texto = f"🗓️ **Jogos {status} em {escape_markdown(NOMES_LIGAS.get(aba_code))}**\n\n{len(jogos)} jogos encontrados\\. [Detalhes da listagem omitidos]"
        keyboard = [[InlineKeyboardButton("↩️ Voltar p/ Status", callback_data=f"VOLTAR_STATUS_{aba_code}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(texto, reply_markup=reply_markup, parse_mode='MarkdownV2')

    except Exception as e:
        logging.error(f"Erro ao listar jogos: {e}")
        await query.edit_message_text(f"❌ Erro ao buscar jogos\\. Detalhes: {escape_markdown(str(e))}", parse_mode='MarkdownV2')
        
    await query.answer()

async def listar_times_historico(update: Update, context: ContextTypes.DEFAULT_TYPE, aba_code: str, pagina: int = 0):
    """Lista os times para que o usuário possa selecionar e ver o histórico/estatísticas."""
    query = update.callback_query
    
    try:
        await query.edit_message_text("⏳ Buscando lista de times, aguarde...")
        
        times = await asyncio.to_thread(get_unique_teams, aba_code)
        
        if not times:
            await query.edit_message_text(f"❌ Não foi possível carregar a lista de times para {aba_code}\\. A planilha pode estar vazia ou a conexão falhou\\.", parse_mode='MarkdownV2')
            await query.answer("Falha ao carregar times.", show_alert=True)
            return
            
        inicio = pagina * TIMES_POR_PAGINA
        fim = inicio + TIMES_POR_PAGINA
        times_pagina = times[inicio:fim]
        
        keyboard = []
        
        for i in range(0, len(times_pagina), 2):
            linha = []
            time1 = times_pagina[i]
            # Usamos o time como mandante e visitante para simplificar o menu de ações
            callback_data_1 = f"SELECIONA_{aba_code}_{time1}_{time1}" 
            linha.append(InlineKeyboardButton(time1, callback_data=callback_data_1))
            
            if i + 1 < len(times_pagina):
                time2 = times_pagina[i+1]
                callback_data_2 = f"SELECIONA_{aba_code}_{time2}_{time2}"
                linha.append(InlineKeyboardButton(time2, callback_data=callback_data_2))
            
            keyboard.append(linha)
            
        nav_buttons = []
        if pagina > 0:
            nav_buttons.append(InlineKeyboardButton("⬅️ Anterior", callback_data=f"PAGINAR_{aba_code}_{pagina - 1}"))
        if fim < len(times):
            nav_buttons.append(InlineKeyboardButton("Próximo ➡️", callback_data=f"PAGINAR_{aba_code}_{pagina + 1}"))
        
        if nav_buttons: keyboard.append(nav_buttons)
            
        keyboard.append([InlineKeyboardButton("↩️ Voltar p/ Status", callback_data=f"VOLTAR_STATUS_{aba_code}")])
        reply_markup = InlineKeyboardMarkup(keyboard)

        total_paginas = (len(times) + TIMES_POR_PAGINA - 1) // TIMES_POR_PAGINA
        
        texto = f"📚 **{escape_markdown(NOMES_LIGAS.get(aba_code))} \\- Histórico**\n\nSelecione um time para ver as estatísticas \\(Pág\\. {pagina + 1} de {total_paginas}\\):"
        
        await query.edit_message_text(texto, reply_markup=reply_markup, parse_mode='MarkdownV2')

    except Exception as e:
        logging.error(f"Erro ao listar times para histórico: {e}")
        await query.edit_message_text(f"❌ Erro ao listar times\\. Detalhes: {escape_markdown(str(e))}", parse_mode='MarkdownV2')
        
    await query.answer()


async def mostrar_menu_acoes(update: Update, context: ContextTypes.DEFAULT_TYPE, aba_code: str, mandante: str, visitante: str):
    """Exibe o menu de Ações (Estatísticas, Resultados) para um time selecionado."""
    query = update.callback_query
    
    try:
        # Codificamos mandante/visitante para o callback
        time_encoded = f"{mandante}_{visitante}" 

        keyboard = [
            [InlineKeyboardButton(CONFRONTO_FILTROS[0][0], callback_data=f"FILTRO_{aba_code}_{time_encoded}_STATS_0")],
            [InlineKeyboardButton(CONFRONTO_FILTROS[1][0], callback_data=f"FILTRO_{aba_code}_{time_encoded}_STATS_1")],
            [InlineKeyboardButton(CONFRONTO_FILTROS[2][0], callback_data=f"FILTRO_{aba_code}_{time_encoded}_RESULTADOS_2")],
            [InlineKeyboardButton(CONFRONTO_FILTROS[3][0], callback_data=f"FILTRO_{aba_code}_{time_encoded}_RESULTADOS_3")],
            
            [InlineKeyboardButton("↩️ Voltar p/ Times", callback_data=f"VOLTAR_JOGOS_{aba_code}_HISTORICO")]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)

        texto = f"⚽️ **Ações para {escape_markdown(mandante)}**\nO que você deseja consultar?"

        await query.edit_message_text(texto, reply_markup=reply_markup, parse_mode='MarkdownV2')

    except Exception as e:
        logging.error(f"Erro ao mostrar menu de ações: {e}")
        await query.edit_message_text(f"❌ Erro ao carregar menu de ações\\. Detalhes: {escape_markdown(str(e))}", parse_mode='MarkdownV2')
        
    await query.answer()

async def exibir_estatisticas(update: Update, context: ContextTypes.DEFAULT_TYPE, mandante: str, visitante: str, aba_code: str, filtro_idx: int):
    """Exibe as estatísticas, rodando o cálculo em um thread separado."""
    query = update.callback_query
    
    ultimos = CONFRONTO_FILTROS[filtro_idx][2]
    tipo_confronto = CONFRONTO_FILTROS[filtro_idx][3], CONFRONTO_FILTROS[filtro_idx][4]

    try:
        await query.edit_message_text("⏳ Calculando estatísticas, aguarde...")
        
        # ✅ CRÍTICO: Roda a função de CÁLCULO (acesso síncrono ao GSheets) off-thread
        d = await asyncio.to_thread(
            calcular_estatisticas_time, mandante, aba_code, ultimos, tipo_confronto
        )

        # ✅ CRÍTICO: Roda a função de FORMATAÇÃO off-thread
        texto_estatisticas = await asyncio.to_thread(formatar_estatisticas, d)

        keyboard = [[InlineKeyboardButton("↩️ Voltar", callback_data=f"VOLTAR_ACOES_{aba_code}_{mandante}_{visitante}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(texto_estatisticas, reply_markup=reply_markup, parse_mode='MarkdownV2')
        
    except Exception as e:
        logging.error(f"Erro ao exibir estatísticas: {e}")
        await query.edit_message_text(f"❌ Erro ao calcular estatísticas\\. Tente novamente\\. Detalhes: {escape_markdown(str(e))}", parse_mode='MarkdownV2')
        
    await query.answer()

async def exibir_ultimos_resultados(update: Update, context: ContextTypes.DEFAULT_TYPE, mandante: str, visitante: str, aba_code: str, filtro_idx: int):
    """Lista os últimos jogos, rodando o acesso a GSheets em um thread separado."""
    query = update.callback_query
    
    ultimos = CONFRONTO_FILTROS[filtro_idx][2]
    tipo_confronto = CONFRONTO_FILTROS[filtro_idx][3], CONFRONTO_FILTROS[filtro_idx][4]
    
    try:
        await query.edit_message_text("⏳ Buscando resultados, aguarde...")
        
        # ✅ CRÍTICO: Roda a função de LISTAGEM (acesso síncrono ao GSheets) off-thread
        texto_resultados = await asyncio.to_thread(
            listar_ultimos_jogos, mandante, aba_code, ultimos, tipo_confronto
        )

        keyboard = [[InlineKeyboardButton("↩️ Voltar", callback_data=f"VOLTAR_ACOES_{aba_code}_{mandante}_{visitante}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(texto_resultados, reply_markup=reply_markup, parse_mode='MarkdownV2')

    except Exception as e:
        logging.error(f"Erro ao listar resultados: {e}")
        await query.edit_message_text(f"❌ Erro ao listar resultados\\. Tente novamente\\. Detalhes: {escape_markdown(str(e))}", parse_mode='MarkdownV2')
        
    await query.answer()

async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Função que gerencia o clique de todos os botões (callbacks)."""
    query = update.callback_query
    data = query.data
    
    try:
        if data.startswith("LIGA_"):
            aba_code = data.split("_")[1]
            await mostrar_menu_status_jogo(update, context, aba_code)

        elif data.startswith("STATUS_"):
            parts = data.split("_")
            aba_code = parts[1]
            status = parts[2]
            await listar_jogos(update, context, aba_code, status)

        elif data.startswith("SELECIONA_"):
            parts = data.split("_")
            aba_code = parts[1]
            mandante = parts[2]
            visitante = parts[3]
            await mostrar_menu_acoes(update, context, aba_code, mandante, visitante)

        elif data.startswith("FILTRO_"):
            parts = data.split("_")
            aba_code = parts[1]
            time_encoded = parts[2] 
            mandante, visitante = time_encoded, time_encoded 
            
            filtro_type = parts[4] 
            filtro_idx = safe_int(parts[5])

            if filtro_type == "STATS":
                await exibir_estatisticas(update, context, mandante, visitante, aba_code, filtro_idx)
            elif filtro_type == "RESULTADOS":
                await exibir_ultimos_resultados(update, context, mandante, visitante, aba_code, filtro_idx)
        
        elif data.startswith("PAGINAR_"):
            parts = data.split("_")
            aba_code = parts[1]
            pagina = safe_int(parts[2])
            await listar_times_historico(update, context, aba_code, pagina)

        elif data.startswith("VOLTAR_"):
            parts = data.split("_")
            target = parts[1]
            
            if target == "LIGA":
                await listar_competicoes(update, context)
            
            elif target == "STATUS":
                aba_code = parts[2]
                await mostrar_menu_status_jogo(update, context, aba_code)

            elif target == "JOGOS":
                aba_code = parts[2]
                status = parts[3]
                if status == "HISTORICO":
                    await listar_times_historico(update, context, aba_code)
                else:
                    await listar_jogos(update, context, aba_code, status)

            elif target == "ACOES":
                aba_code = parts[2]
                mandante = parts[3]
                visitante = parts[4]
                await mostrar_menu_acoes(update, context, aba_code, mandante, visitante)
        
    except Exception as e:
        logging.error(f"Erro no callback_query_handler: {e}")
        try:
            await query.edit_message_text(f"❌ Ocorreu um erro interno\\. Tente novamente iniciando com /stats\\.\nDetalhes: {escape_markdown(str(e))}", parse_mode='MarkdownV2')
        except BadRequest:
            pass
        
    try:
        await query.answer() 
    except Exception:
        pass


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
    # ✅ NOVO COMANDO: Para forçar a atualização
    app.add_handler(CommandHandler("atualizar", forcar_atualizacao_command)) 
    app.add_handler(CallbackQueryHandler(callback_query_handler))
    
    webhook_base_url = os.environ.get("WEBHOOK_URL") or os.environ.get("RENDER_EXTERNAL_URL")
    if not webhook_base_url: 
        logging.error("❌ ERRO CRÍTICO: URL pública não encontrada.")
        sys.exit(1)

    if client:
        job_queue: JobQueue = app.job_queue
        # Garante a atualização periódica dos dados da planilha a cada 1 hora.
        job_queue.run_repeating(actualizar_planilhas, interval=CACHE_DURATION_SECONDS, first=0, name="AtualizacaoPlanilhas")
        # Pré-carrega o cache de histórico na inicialização
        asyncio.run(pre_carregar_cache_sheets())
    else: 
        logging.warning("Job Queue e funções GSheets desativados.")
    
    logging.info("Bot rodando!")
    app.run_webhook(listen="0.0.0.0", port=int(os.environ.get("PORT", "8080")), url_path=BOT_TOKEN, webhook_url=webhook_base_url + '/' + BOT_TOKEN)

if __name__ == "__main__":
    main()
