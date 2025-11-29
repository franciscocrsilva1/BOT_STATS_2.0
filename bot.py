# bot.py (Seu novo arquivo principal)

# ===============================================================================
# 🏆 BOT DE ESTATÍSTICAS DE CONFRONTO V2.4.0 - MODULARIZADO + FORÇA UPDATE
# ===============================================================================

# ===== Importações Essenciais =====
import asyncio
import logging
import sys 
import nest_asyncio
import os 

# Importações do Python-Telegram-Bot
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes, JobQueue, MessageHandler, filters 

# Importações dos módulos locais
from config import BOT_TOKEN, LIGAS_MAP, client
# Importamos as funções necessárias do módulo gsheets_api
from gsheets_api import get_sheet_data, atualizar_planilhas, pre_carregar_cache_sheets

# Inicialização (o logging e a conexão GSheets estão em config.py)
nest_asyncio.apply()

# =================================================================================
# 💻 FUNÇÕES DE ESTATÍSTICAS (A lógica de cálculo de Confrontos)
# =================================================================================

def calcular_estatisticas(dados, mandante_busca, visitante_busca):
    """Processa os dados da planilha e calcula as estatísticas de confronto."""
    
    def normalizar(nome):
        # Normalização aprimorada para comparação
        return nome.lower().strip().replace(' ', '').replace('.', '').replace('-', '') 

    m_norm = normalizar(mandante_busca)
    v_norm = normalizar(visitante_busca)

    total_jogos = 0
    vitorias_mandante = 0
    vitorias_visitante = 0
    empates = 0
    
    for linha in dados:
        mandante_linha = normalizar(linha.get("Mandante", ""))
        visitante_linha = normalizar(linha.get("Visitante", ""))
        
        # Filtra jogos onde A jogou contra B (A vs B ou B vs A)
        if (mandante_linha == m_norm and visitante_linha == v_norm) or \
           (mandante_linha == v_norm and visitante_linha == m_norm):
            
            total_jogos += 1
            
            gm = linha.get("Gols Mandante", 0)
            gv = linha.get("Gols Visitante", 0)

            # Lógica para atribuir a vitória ao time Mandante na busca (m_norm) ou Visitante (v_norm)
            
            # Caso 1: O jogo na planilha é M_norm (casa) vs V_norm (fora)
            if mandante_linha == m_norm and visitante_linha == v_norm:
                if gm > gv: vitorias_mandante += 1 
                elif gv > gm: vitorias_visitante += 1 
                else: empates += 1
            
            # Caso 2: O jogo na planilha é V_norm (casa) vs M_norm (fora)
            elif mandante_linha == v_norm and visitante_linha == m_norm:
                if gm > gv: vitorias_visitante += 1 # Vitória de V_norm (o time Visitante na sua busca)
                elif gv > gm: vitorias_mandante += 1 # Vitória de M_norm (o time Mandante na sua busca)
                else: empates += 1
                
    if total_jogos == 0:
        return f"Não foram encontrados confrontos históricos entre **{mandante_busca}** e **{visitante_busca}**."

    return f"""
Estatísticas de Confronto:
- Total de Jogos: **{total_jogos}**
- Vitórias de **{mandante_busca}**: **{vitorias_mandante}**
- Vitórias de **{visitante_busca}**: **{vitorias_visitante}**
- Empates: **{empates}**
"""


# =================================================================================
# 💬 HANDLERS (TELEGRAM)
# =================================================================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Responde ao comando /start."""
    await update.message.reply_text(
        "Olá! Sou o Bot de Estatísticas de Confronto. Use o comando /stats para começar a analisar as ligas."
    )

async def listar_competicoes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lista as competições disponíveis."""
    keyboard = []
    
    for code in LIGAS_MAP:
        keyboard.append([InlineKeyboardButton(code, callback_data=f"LIGA_{code}")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Selecione a competição:", reply_markup=reply_markup)

async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Trata cliques em botões de seleção de liga."""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("LIGA_"):
        liga_code = data.split("_")[1]
        
        # Armazena a liga selecionada
        context.user_data['liga_code'] = liga_code
        
        await query.edit_message_text(
            f"✅ Competição **{liga_code}** selecionada.\nAgora, envie o confronto (ex: `Time A vs. Time B`):",
            parse_mode='Markdown'
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processa a mensagem de confronto enviada pelo usuário após selecionar a liga."""
    text = update.message.text
    
    if 'liga_code' not in context.user_data:
        await update.message.reply_text("❌ Por favor, selecione uma competição primeiro usando /stats.")
        return
        
    liga_code = context.user_data['liga_code']
    
    # 1. Parsing do Confronto
    mandante_busca = None
    visitante_busca = None
    
    if ' vs. ' in text:
        parts = text.split(' vs. ')
    elif ' vs ' in text:
        parts = text.split(' vs ')
    else:
        await update.message.reply_text("❌ Formato de confronto inválido. Use: `Time A vs. Time B`.")
        return

    if len(parts) == 2:
        mandante_busca = parts[0].strip()
        visitante_busca = parts[1].strip()
    
    if not mandante_busca or not visitante_busca:
        await update.message.reply_text("❌ Formato de confronto inválido. Certifique-se de que há um time mandante e um visitante.")
        return


    await update.message.reply_text(f"🔍 Buscando histórico para **{mandante_busca}** vs **{visitante_busca}** na liga **{liga_code}**...", parse_mode='Markdown')
    
    # 2. Busca e Cálculo
    try:
        # Chama a função de dados do gsheets_api.py
        dados_historico = get_sheet_data(liga_code)
        
        if not dados_historico:
            await update.message.reply_text("⚠️ Não foi possível obter o histórico da planilha. Verifique a conexão GSheets ou se a planilha está vazia.")
            return

        # Chama a lógica de cálculo
        resultado_stats = calcular_estatisticas(dados_historico, mandante_busca, visitante_busca)
        
        await update.message.reply_text(resultado_stats, parse_mode='Markdown')

    except Exception as e:
        logging.error(f"Erro ao calcular estatísticas: {e}")
        await update.message.reply_text("❌ Ocorreu um erro ao processar as estatísticas. Tente novamente.")


async def forcaupdate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler para o comando /forcaupdate. Inicia a atualização manualmente."""
    
    # Verificação de segurança (opcional: pode adicionar verificação de usuário/chat ID)
    if not client:
        await update.message.reply_text("❌ Não é possível forçar a atualização: A conexão com o Google Sheets falhou.")
        return

    # Envia uma mensagem de ACK para o usuário
    await update.message.reply_text(
        "⚡️ **Atualização Forçada Iniciada!** Isso pode levar até 5 minutos (dependendo das 10 ligas).\n\n"
        "Acompanhe o log do Render para ver os resultados da API (busca de jogos) e da escrita.",
        parse_mode='Markdown'
    )

    try:
        # Roda a função síncrona em um thread separado para não bloquear o bot
        await asyncio.to_thread(atualizar_planilhas, context)
        
        await update.message.reply_text("✅ **Atualização de Planilhas Concluída!** Verifique as 20 abas.")
        
    except Exception as e:
        logging.error(f"Erro durante a atualização forçada: {e}")
        await update.message.reply_text(f"❌ Erro Crítico durante a atualização. Verifique o log.")

# =================================================================================
# 🚀 FUNÇÃO PRINCIPAL
# =================================================================================
def main():
    if not BOT_TOKEN or BOT_TOKEN == "SEU_TOKEN_AQUI":
        logging.error("O token do bot não está configurado. Verifique a variável de ambiente BOT_TOKEN.")
        sys.exit(1) 
        
    # 1. Configuração do Bot
    job_queue_instance = JobQueue()
    app = ApplicationBuilder().token(BOT_TOKEN).job_queue(job_queue_instance).build()
    
    # 2. Handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("stats", listar_competicoes))
    app.add_handler(CommandHandler("forcaupdate", forcaupdate_command)) # ✅ NOVO COMANDO
    app.add_handler(CallbackQueryHandler(callback_query_handler))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    
    # 3. Webhook Setup (Para o Render)
    webhook_base_url = os.environ.get("WEBHOOK_URL") or os.environ.get("RENDER_EXTERNAL_URL")

    if not webhook_base_url:
        logging.error("❌ ERRO CRÍTICO: Não foi possível obter a URL pública para o Webhook. Finalizando.")
        sys.exit(1) 

    # 4. Job Queue e Cache (Depende da conexão com GSheets)
    if client:
        # Agendamento da atualização (Roda 1x imediatamente e depois a cada hora)
        app.job_queue.run_repeating(atualizar_planilhas, interval=3600, first=0, name="AtualizacaoPlanilhas")
        # Pré-carregamento do cache
        asyncio.run(pre_carregar_cache_sheets())
    else:
        logging.warning("Job Queue de atualização desativado: Conexão com GSheets não estabelecida.")
    
    logging.info("Bot rodando!")
    
    # 5. Inicia o Webhook
    PORT = int(os.environ.get('PORT', '10000')) 
    
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=BOT_TOKEN,
        webhook_url=f"{webhook_base_url}/{BOT_TOKEN}"
    )

if __name__ == "__main__":
    main()
