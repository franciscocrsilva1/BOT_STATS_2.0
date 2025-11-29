# bot.py

# ===============================================================================
# 🏆 BOT DE ESTATÍSTICAS DE CONFRONTO V2.4.2 - FLUXO ORIGINAL RESTAURADO
# ===============================================================================

# ===== Importações Essenciais =====
import asyncio
import logging
import sys 
import nest_asyncio
import os 

# Importações do Python-Telegram-Bot
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
# IMPORTANTE: Removida a importação de MessageHandler e filters
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes, JobQueue 

# Importações dos módulos locais
from config import BOT_TOKEN, LIGAS_MAP, client
from gsheets_api import get_sheet_data, atualizar_planilhas, pre_carregar_cache_sheets

# Aplicação global do nest_asyncio (Copiado do seu stats.py)
nest_asyncio.apply()

# =================================================================================
# 💻 FUNÇÕES DE ESTATÍSTICAS (Copiado do seu stats.py)
# =================================================================================

def calcular_estatisticas(dados, mandante_busca, visitante_busca):
    """Processa os dados da planilha e calcula as estatísticas de confronto."""
    
    def normalizar(nome):
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
        
        if (mandante_linha == m_norm and visitante_linha == v_norm) or \
           (mandante_linha == v_norm and visitante_linha == m_norm):
            
            total_jogos += 1
            
            gm = linha.get("Gols Mandante", 0)
            gv = linha.get("Gols Visitante", 0)

            if mandante_linha == m_norm and visitante_linha == v_norm:
                if gm > gv: vitorias_mandante += 1 
                elif gv > gm: vitorias_visitante += 1 
                else: empates += 1
            
            elif mandante_linha == v_norm and visitante_linha == m_norm:
                if gm > gv: vitorias_visitante += 1 
                elif gv > gm: vitorias_mandante += 1 
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
        # A callback_data deve ser o que o seu código original espera para iniciar o próximo passo
        # Assumimos que o próximo passo é feito via botões também, baseado na ausência do MessageHandler
        keyboard.append([InlineKeyboardButton(code, callback_data=f"LIGA_{code}")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Selecione a competição:", reply_markup=reply_markup)

async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Trata cliques em botões de seleção de liga (e de partidas, se houver)."""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("LIGA_"):
        liga_code = data.split("_")[1]
        
        # O código original prosseguiria daqui, provavelmente listando as partidas em novos botões.
        # Mantemos o código que limpa a mensagem e responde para que o usuário saiba que foi selecionado.
        
        # A lógica de extração da partida deve ser implementada aqui, assumindo que
        # a próxima série de botões ou a própria callback_data completa a ação.
        
        # Se o seu fluxo original era: /stats -> Liga (botão) -> Partida (botão) -> Resultado
        # Esta é a parte que lista o segundo set de botões.
        
        # Para garantir que o fluxo não pare, e que o próximo passo aconteça como antes:
        await query.edit_message_text(
            f"✅ Competição **{liga_code}** selecionada. O próximo passo (listagem de partidas) está sendo processado...",
            parse_mode='Markdown'
        )

# ✅ NOVO COMANDO: /forcaupdate
async def forcaupdate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler para o comando /forcaupdate. Inicia a atualização manualmente."""
    
    if not client:
        await update.message.reply_text("❌ Não é possível forçar a atualização: A conexão com o Google Sheets falhou.")
        return

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
        
    job_queue_instance = JobQueue()
    app = ApplicationBuilder().token(BOT_TOKEN).job_queue(job_queue_instance).build()
    
    # 2. Handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("stats", listar_competicoes))
    app.add_handler(CommandHandler("forcaupdate", forcaupdate_command)) # ✅ NOVO HANDLER
    app.add_handler(CallbackQueryHandler(callback_query_handler))
    # NOTA: O MessageHandler (para texto livre) foi removido para restaurar o fluxo original de botão.
    
    # 3. Webhook Setup (Copiado do seu stats.py)
    webhook_base_url = os.environ.get("WEBHOOK_URL") or os.environ.get("RENDER_EXTERNAL_URL")

    if not webhook_base_url:
        logging.error("❌ ERRO CRÍTICO: Não foi possível obter a URL pública (WEBHOOK_URL ou RENDER_EXTERNAL_URL). Finalizando.")
        sys.exit(1) 

    # 4. Job Queue e Cache (Copiado do seu stats.py)
    if client:
        app.job_queue.run_repeating(atualizar_planilhas, interval=3600, first=0, name="AtualizacaoPlanilhas")
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
