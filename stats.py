# ===============================================================================
# 🏆 BOT DE ESTATÍSTICAS - VERSÃO GERAIS + ÚLTIMOS 10
# ===============================================================================

import gspread
from oauth2client.service_account import ServiceAccountCredentials
import requests
import os 
import tempfile
import asyncio
import logging
from datetime import datetime, timezone
import nest_asyncio
import sys 

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.error import BadRequest

# Configurações Iniciais
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
nest_asyncio.apply()

BOT_TOKEN = os.environ.get("BOT_TOKEN", "SEU_TOKEN")
API_KEY = os.environ.get("API_KEY", "SUA_API_KEY")
SHEET_URL = os.environ.get("SHEET_URL", "SUA_URL_DA_PLANILHA")

LIGAS_MAP = {"BSA": {"sheet_past": "BSA", "sheet_future": "BSA_FJ"}}

# ✅ DEFINIÇÃO DOS FILTROS CONFORME SOLICITADO
# Formato: (Nome Exibido, Tipo, Quantidade de Jogos, Filtro Mandante, Filtro Visitante)
# Se Quantidade for None, ele busca "GERAIS" (todos os jogos da planilha)
FILTROS_CONFIG = [
    ("📊 Stats | GERAIS (Todos)", "STATS", None, None, None),
    ("📊 Stats | GERAIS (M Casa vs V Fora)", "STATS", None, "casa", "fora"),
    ("📊 Stats | ÚLTIMOS 10 GERAIS", "STATS", 10, None, None),
    ("📊 Stats | ÚLTIMOS 10 (M Casa vs V Fora)", "STATS", 10, "casa", "fora"),
    
    ("📅 Resultados | GERAIS (Todos)", "RESULT", None, None, None),
    ("📅 Resultados | GERAIS (M Casa vs V Fora)", "RESULT", None, "casa", "fora"),
    ("📅 Resultados | ÚLTIMOS 10 GERAIS", "RESULT", 10, None, None),
    ("📅 Resultados | ÚLTIMOS 10 (M Casa vs V Fora)", "RESULT", 10, "casa", "fora"),
]

# --- Funções de Conexão e Dados ---
# (Mantendo a estrutura de conexão gspread que você já utiliza)
CREDS_JSON = os.environ.get("GSPREAD_CREDS_JSON")
client = None
if CREDS_JSON:
    try:
        with tempfile.NamedTemporaryFile(mode="w", delete=False, encoding='utf-8') as tmp:
            tmp.write(CREDS_JSON)
            path = tmp.name
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name(path, scope)
        client = gspread.authorize(creds)
    except Exception as e: logging.error(f"Erro GSheet: {e}")

def get_sheet_data(aba_code):
    try:
        sh = client.open_by_url(SHEET_URL)
        return sh.worksheet(LIGAS_MAP[aba_code]['sheet_past']).get_all_records()
    except: return []

# --- Lógica de Cálculo (Ajustada para Gerais) ---
def calcular_stats(time, dados, limite=None, local=None):
    # Filtrar por localidade (Casa/Fora)
    if local == "casa":
        jogos = [d for d in dados if d['Mandante'] == time]
    elif local == "fora":
        jogos = [d for d in dados if d['Visitante'] == time]
    else:
        jogos = [d for d in dados if d['Mandante'] == time or d['Visitante'] == time]
    
    # Ordenar por data e aplicar limite (se for Últimos 10)
    jogos.sort(key=lambda x: datetime.strptime(x['Data'], "%d/%m/%Y"))
    if limite:
        jogos = jogos[-limite:]
    
    total = len(jogos)
    if total == 0: return f"Sem dados para {time}"

    ov15 = sum(1 for j in jogos if (int(j['Gols Mandante']) + int(j['Gols Visitante'])) > 1.5)
    btts = sum(1 for j in jogos if int(j['Gols Mandante']) > 0 and int(j['Gols Visitante']) > 0)
    gols_total = sum((int(j['Gols Mandante']) + int(j['Gols Visitante'])) for j in jogos)

    resumo = (f"**{time}** ({total}j)\n"
              f"O1.5: {ov15/total:.1%} | BTTS: {btts/total:.1%}\n"
              f"Média Gols: {gols_total/total:.2f}")
    return resumo

def formatar_resultados(time, dados, limite=None, local=None):
    if local == "casa":
        jogos = [d for d in dados if d['Mandante'] == time]
    elif local == "fora":
        jogos = [d for d in dados if d['Visitante'] == time]
    else:
        jogos = [d for d in dados if d['Mandante'] == time or d['Visitante'] == time]
    
    jogos.sort(key=lambda x: datetime.strptime(x['Data'], "%d/%m/%Y"))
    if limite: jogos = jogos[-limite:]
    
    texto = ""
    for j in jogos:
        texto += f"• {j['Data']}: {j['Mandante']} {j['Gols Mandante']}x{j['Gols Visitante']} {j['Visitante']}\n"
    return texto if texto else "Nenhum jogo encontrado."

# --- Handlers do Telegram ---
async def start(update, context):
    await update.message.reply_text("⚽ Bot de Estatísticas Ativo!\nUse /stats para começar.")

async def listar_competicoes(update, context):
    kb = [[InlineKeyboardButton("BSA - Brasileirão", callback_data="L|BSA")]]
    await update.effective_message.reply_text("Escolha a liga:", reply_markup=InlineKeyboardMarkup(kb))

async def callback_handler(update, context):
    query = update.callback_query
    data = query.data

    if data.startswith("L|"):
        aba = data.split('|')[1]
        context.chat_data['aba'] = aba
        # Aqui você listaria os jogos (Mandante x Visitante) da aba_future
        # Para este exemplo, vamos simular a escolha de um jogo
        context.chat_data['m'] = "Flamengo" # Exemplo
        context.chat_data['v'] = "Palmeiras" # Exemplo
        
        kb = [[InlineKeyboardButton(f[0], callback_data=f"F|{i}")] for i, f in enumerate(FILTROS_CONFIG)]
        await query.edit_message_text("Selecione o filtro desejado:", reply_markup=InlineKeyboardMarkup(kb))

    elif data.startswith("F|"):
        idx = int(data.split('|')[1])
        conf = FILTROS_CONFIG[idx]
        dados = get_sheet_data(context.chat_data['aba'])
        
        m_nome = context.chat_data['m']
        v_nome = context.chat_data['v']

        if conf[1] == "STATS":
            res_m = calcular_stats(m_nome, dados, conf[2], conf[3])
            res_v = calcular_stats(v_nome, dados, conf[2], conf[4])
            msg = f"🏆 **{conf[0]}**\n\n{res_m}\n\n{res_v}"
        else:
            res_m = formatar_resultados(m_nome, dados, conf[2], conf[3])
            res_v = formatar_resultados(v_nome, dados, conf[2], conf[4])
            msg = f"📅 **{conf[0]}**\n\n**{m_nome}:**\n{res_m}\n\n**{v_nome}:**\n{res_v}"
        
        await query.message.reply_text(msg, parse_mode="Markdown")

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", listar_competicoes))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.run_polling()

if __name__ == "__main__":
    main()
