# bot.py (Seu novo arquivo principal)

# ===== Importações Essenciais =====
import asyncio
import logging
import sys 
import nest_asyncio
import os # Importar os para o Webhook

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes, JobQueue, MessageHandler, filters 

from config import BOT_TOKEN, LIGAS_MAP, client
from gsheets_api import get_sheet_data, atualizar_planilhas, pre_carregar_cache_sheets

nest_asyncio.apply()

# =================================================================================
# 💻 FUNÇÕES DE ESTATÍSTICAS
# =================================================================================
def calcular_estatisticas(dados, mandante_busca, visitante_busca):
    # (A lógica completa da sua função calcular_estatisticas deve estar aqui)
    def normalizar(nome): return nome.lower().strip().replace(' ', '').replace('.', '').replace('-', '') 

    m_norm = normalizar(mandante_busca); v_norm = normalizar(visitante_busca)
    total_jogos = 0; vitorias_mandante = 0; vitorias_visitante = 0; empates = 0
    
    for linha in dados:
        mandante_linha = normalizar(linha.get("Mandante", "")); visitante_linha = normalizar(linha.get("Visitante", ""))
        
        if (mandante_linha == m_norm and visitante_linha == v_norm) or \
           (mandante_linha == v_norm and visitante_linha == m_norm):
            
            total_jogos += 1
            gm = linha.get("Gols Mandante", 0); gv = linha.get("Gols Visitante", 0)
            
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
    await update.message.reply_text("Use o comando /stats para começar a analisar as ligas.")

async def listar_competicoes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton(code, callback_data=f"LIGA_{code}")] for code in LIGAS_MAP]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Selecione a competição:", reply_markup=reply_markup)

async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    if query.data.startswith("LIGA_"):
        liga_code = query.data.split("_")[1]
        context.user_data['liga_code'] = liga_code
        await query.edit_message_text(f"✅ Competição **{liga_code}** selecionada.\nAgora, envie o confronto (ex: `Time A vs. Time B`):", parse_mode='Markdown')

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if 'liga_code' not in context.user_data:
        await update.message.reply_text("❌ Selecione uma competição primeiro usando /stats.")
        return
        
    liga_code = context.user_data['liga_code']
    
    try:
        if ' vs. ' in text: mandante_busca, visitante_busca = text.split(' vs. ')
        else: mandante_busca, visitante_busca = text.split(' vs ')
        mandante_busca = mandante_busca.strip(); visitante_busca = visitante_busca.strip()
    except ValueError:
        await update.message.reply_text("❌ Formato inválido. Use: `Time A vs. Time B`.")
        return

    await update.message.reply_text(f"🔍 Buscando histórico para **{mandante_busca}** vs **{visitante_busca}** na liga **{liga_code}**...", parse_mode='Markdown')
    
    try:
        dados_historico = get_sheet_data(liga_code)
        if not dados_historico:
            await update.message.reply_text("⚠️ Não foi possível obter o histórico da planilha.")
            return

        resultado_stats = calcular_estatisticas(dados_historico, mandante_busca, visitante_busca)
        await update.message.reply_text(resultado_stats, parse_mode='Markdown')

    except Exception as e:
        logging.error(f"Erro ao calcular estatísticas: {e}")
        await update.message.reply_text("❌ Ocorreu um erro ao processar as estatísticas.")

# =================================================================================
# 🚀 FUNÇÃO PRINCIPAL
# =================================================================================
def main():
    if not BOT_TOKEN:
        logging.error("O token do bot não está configurado."); sys.exit(1) 
        
    job_queue_instance = JobQueue()
    app = ApplicationBuilder().token(BOT_TOKEN).job_queue(job_queue_instance).build()
    
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("stats", listar_competicoes))
    app.add_handler(CallbackQueryHandler(callback_query_handler))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    
    webhook_base_url = os.environ.get("WEBHOOK_URL") or os.environ.get("RENDER_EXTERNAL_URL")

    if not webhook_base_url:
        logging.error("❌ ERRO CRÍTICO: Não foi possível obter a URL pública para o Webhook."); sys.exit(1) 

    if client:
        app.job_queue.run_repeating(atualizar_planilhas, interval=3600, first=0, name="AtualizacaoPlanilhas")
        asyncio.run(pre_carregar_cache_sheets())
    else:
        logging.warning("Job Queue de atualização desativado.")
    
    logging.info("Bot rodando!")
    
    PORT = int(os.environ.get('PORT', '10000')) 
    
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=BOT_TOKEN,
        webhook_url=f"{webhook_base_url}/{BOT_TOKEN}"
    )

if __name__ == "__main__":
    main()
