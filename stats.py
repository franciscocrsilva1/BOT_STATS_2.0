# ===============================================================================
# 🏆 BOT DE ESTATÍSTICAS DE CONFRONTO V2.7.0 - BSA & BL1
# ===============================================================================

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
nest_asyncio.apply()

# ===== Variáveis de Configuração =====
BOT_TOKEN = os.environ.get("BOT_TOKEN", "SEU_TOKEN_AQUI") 
API_KEY = os.environ.get("API_KEY", "SUA_API_KEY_AQUI")
SHEET_URL = os.environ.get("SHEET_URL", "SUA_URL_DA_PLANILHA_AQUI")

# Mapeamento de Ligas
LIGAS_MAP = {
    "BSA": {"sheet_past": "BSA", "sheet_future": "BSA_FJ", "season": "2026"},
    "BL1": {"sheet_past": "BL1", "sheet_future": "BL1_FJ", "season": "2025"}
}
ABAS_PASSADO = list(LIGAS_MAP.keys())

ULTIMOS = 10
SHEET_CACHE = {}
CACHE_DURATION_SECONDS = 3600 
MAX_GAMES_LISTED = 30

CONFRONTO_FILTROS = [
    ("📊 Estatísticas | GERAIS", "STATS_FILTRO", 0, None, None),
    ("📊 Estatísticas | GERAIS (M CASA vs V FORA)", "STATS_FILTRO", 0, "casa", "fora"),
    ("📅 Resultados | GERAIS", "RESULTADOS_FILTRO", 0, None, None),
    ("📅 Resultados | GERAIS (M CASA vs V FORA)", "RESULTADOS_FILTRO", 0, "casa", "fora"),
    (f"📊 Estatísticas | ÚLTIMOS {ULTIMOS} GERAIS", "STATS_FILTRO", ULTIMOS, None, None),
    (f"📊 Estatísticas | {ULTIMOS} (M CASA vs V FORA)", "STATS_FILTRO", ULTIMOS, "casa", "fora"),
    (f"📅 Resultados | ÚLTIMOS {ULTIMOS} GERAIS", "RESULTADOS_FILTRO", ULTIMOS, None, None),
    (f"📅 Resultados | {ULTIMOS} (M CASA vs V FORA)", "RESULTADOS_FILTRO", ULTIMOS, "casa", "fora"),
]

LIVE_STATUSES = ["IN_PLAY", "HALF_TIME", "PAUSED"]

# ✅ CONEXÃO GSHEETS
CREDS_JSON = os.environ.get("GSPREAD_CREDS_JSON")
client = None

if CREDS_JSON:
    try:
        with tempfile.NamedTemporaryFile(mode="w", delete=False, encoding='utf-8') as tmp_file:
            tmp_file.write(CREDS_JSON)
            tmp_file_path = tmp_file.name
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name(tmp_file_path, scope)
        client = gspread.authorize(creds)
        os.remove(tmp_file_path)
    except Exception as e:
        logging.error(f"❌ ERRO GSHEET: {e}")

# 💾 FUNÇÕES DE SUPORTE
def safe_int(v):
    try: return int(v)
    except: return 0

def pct(part, total):
    return f"{(part/total)*100:.1f}%" if total > 0 else "—"

def media(part, total):
    return f"{(part/total):.2f}" if total > 0 else "—"

def escape_markdown(text):
    return str(text).replace('*', '\\*').replace('_', '\\_').replace('[', '\\[') .replace(']', '\\]')

def get_sheet_data(aba_code):
    global SHEET_CACHE
    agora = datetime.now()
    aba_name = LIGAS_MAP[aba_code]['sheet_past']
    if aba_name in SHEET_CACHE:
        if (agora - SHEET_CACHE[aba_name]['timestamp']).total_seconds() < CACHE_DURATION_SECONDS:
            return SHEET_CACHE[aba_name]['data']
    sh = client.open_by_url(SHEET_URL)
    linhas = sh.worksheet(aba_name).get_all_records()
    SHEET_CACHE[aba_name] = { 'data': linhas, 'timestamp': agora }
    return linhas

def get_sheet_data_future(aba_code):
    aba_name = LIGAS_MAP[aba_code]['sheet_future']
    try:
        sh = client.open_by_url(SHEET_URL)
        linhas_raw = sh.worksheet(aba_name).get_all_values()
        if not linhas_raw or len(linhas_raw) <= 1: return []
        return [{"Mandante_Nome": r[0].strip(), "Visitante_Nome": r[1].strip(), "Data_Hora": r[2], "Matchday": safe_int(r[3])} for r in linhas_raw[1:] if len(r) >= 4]
    except: return []

# 🎯 FUNÇÕES DE API
def buscar_jogos(league_code, status_filter):
    try:
        url = f"https://api.football-data.org/v4/competitions/{league_code}/matches"
        params = {"season": LIGAS_MAP[league_code]["season"]}
        if status_filter != "ALL": params["status"] = status_filter
        r = requests.get(url, headers={"X-Auth-Token": API_KEY}, params=params, timeout=10)
        all_matches = r.json().get("matches", [])
        if status_filter == "ALL": return [m for m in all_matches if m.get('status') in ['SCHEDULED', 'TIMED']]
        jogos = []
        for m in all_matches:
            if m.get('status') == "FINISHED":
                ft, ht = m.get("score", {}).get("fullTime", {}), m.get("score", {}).get("halfTime", {})
                gm, gv = ft.get("home", 0), ft.get("away", 0)
                gm1, gv1 = ht.get("home", 0), ht.get("away", 0)
                jogos.append({"Mandante": m["homeTeam"]["name"].strip(), "Visitante": m["awayTeam"]["name"].strip(), "Gols Mandante": gm, "Gols Visitante": gv, "Gols Mandante 1T": gm1, "Gols Visitante 1T": gv1, "Gols Mandante 2T": gm-gm1, "Gols Visitante 2T": gv-gv1, "Data": datetime.strptime(m['utcDate'][:10], "%Y-%m-%d").strftime("%d/%m/%Y")})
        return sorted(jogos, key=lambda x: datetime.strptime(x['Data'], "%d/%m/%Y"))
    except: return []

def buscar_jogos_live(league_code):
    hoje = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    try:
        r = requests.get(f"https://api.football-data.org/v4/competitions/{league_code}/matches?dateFrom={hoje}&dateTo={hoje}", headers={"X-Auth-Token": API_KEY}, timeout=10)
        jogos = []
        for m in r.json().get("matches", []):
            if m.get('status') in LIVE_STATUSES:
                jogos.append({"Mandante_Nome": m["homeTeam"]["name"].strip(), "Visitante_Nome": m["awayTeam"]["name"].strip(), "Placar_Mandante": m["score"]["fullTime"]["home"], "Placar_Visitante": m["score"]["fullTime"]["away"], "Tempo_Jogo": "Intervalo" if m['status'] == 'HALF_TIME' else m.get("minute", "N/A"), "Matchday": safe_int(m.get("matchday", 0))})
        return jogos
    except: return []

async def atualizar_planilhas(context=None):
    if not client: return False
    try: 
        sh = client.open_by_url(SHEET_URL)
        for aba_code, aba_config in LIGAS_MAP.items():
            ws_past = sh.worksheet(aba_config['sheet_past'])
            jogos_fin = buscar_jogos(aba_code, "FINISHED")
            if jogos_fin:
                exist = ws_past.get_all_records()
                keys = {(r['Mandante'].strip(), r['Visitante'].strip(), r['Data']) for r in exist}
                novos = [[j["Mandante"], j["Visitante"], j["Gols Mandante"], j["Gols Visitante"], j["Gols Mandante 1T"], j["Gols Visitante 1T"], j["Gols Mandante 2T"], j["Gols Visitante 2T"], j["Data"]] for j in jogos_fin if (j["Mandante"], j["Visitante"], j["Data"]) not in keys]
                if novos: ws_past.append_rows(novos)
            ws_future = sh.worksheet(aba_config['sheet_future'])
            futuros = buscar_jogos(aba_code, "ALL")
            ws_future.clear()
            ws_future.update(values=[['Mandante', 'Visitante', 'Data/Hora', 'Matchday']], range_name='A1:D1')
            if futuros: ws_future.append_rows([[m["homeTeam"]["name"], m["awayTeam"]["name"], m['utcDate'], m['matchday']] for m in futuros])
        global SHEET_CACHE
        SHEET_CACHE = {}
        return True
    except: return False

# 📈 CÁLCULOS
def calcular_estatisticas_time(time, aba, ultimos=None, casa_fora=None):
    # .strip() para evitar erro de nomes com espaços extras na planilha
    time = time.strip()
    d = {"time":time,"jogos_time":0,"over15":0,"over25":0,"btts":0,"g_a_t":0,"over05_1T":0,"over05_2T":0,"over15_2T":0,"gols_marcados":0,"gols_sofridos":0,"total_gols":0,"gols_marcados_1T":0,"gols_sofridos_1T":0,"gols_marcados_2T":0,"gols_sofridos_2T":0,"marcou_2_mais":0,"sofreu_2_mais":0,"marcou_ambos_tempos":0,"sofreu_ambos_tempos":0}
    try: 
        linhas = get_sheet_data(aba)
        if casa_fora == "casa": 
            linhas = [l for l in linhas if str(l['Mandante']).strip() == time]
        elif casa_fora == "fora": 
            linhas = [l for l in linhas if str(l['Visitante']).strip() == time]
        else: 
            linhas = [l for l in linhas if str(l['Mandante']).strip() == time or str(l['Visitante']).strip() == time]
        
        linhas.sort(key=lambda x: datetime.strptime(x['Data'], "%d/%m/%Y"))
        if ultimos: linhas = linhas[-ultimos:]
        
        for l in linhas:
            em_casa = (time == str(l['Mandante']).strip())
            gm, gv = safe_int(l['Gols Mandante']), safe_int(l['Gols Visitante'])
            gm1, gv1 = safe_int(l['Gols Mandante 1T']), safe_int(l['Gols Visitante 1T'])
            gm2, gv2 = gm-gm1, gv-gv1
            d["jogos_time"] += 1
            marc, sofr = (gm, gv) if em_casa else (gv, gm)
            m1, s1 = (gm1, gv1) if em_casa else (gv1, gm1)
            m2, s2 = (gm2, gv2) if em_casa else (gv2, gm2)
            d["gols_marcados"] += marc; d["gols_sofridos"] += sofr; d["total_gols"] += (gm+gv)
            if (gm+gv) > 1.5: d["over15"] += 1
            if (gm+gv) > 2.5: d["over25"] += 1
            if gm > 0 and gv > 0: d["btts"] += 1
            if (gm1+gv1) > 0.5: d["over05_1T"] += 1
            if (gm2+gv2) > 0.5: d["over05_2T"] += 1
            if (gm2+gv2) > 1.5: d["over15_2T"] += 1
            if (gm1+gv1) > 0 and (gm2+gv2) > 0: d["g_a_t"] += 1
            if marc >= 2: d["marcou_2_mais"] += 1
            if sofr >= 2: d["sofreu_2_mais"] += 1
            if m1 > 0 and m2 > 0: d["marcou_ambos_tempos"] += 1
            if s1 > 0 and s2 > 0: d["sofreu_ambos_tempos"] += 1
            d["gols_marcados_1T"] += m1; d["gols_sofridos_1T"] += s1
            d["gols_marcados_2T"] += m2; d["gols_sofridos_2T"] += s2
        return d
    except: return {"time":time, "jogos_time": 0}

def formatar_estatisticas(d):
    jt = d["jogos_time"]
    if jt == 0: return f"⚠️ **Nenhum jogo encontrado** para **{escape_markdown(d['time'])}**."
    return (f"📊 **Estatísticas - {escape_markdown(d['time'])}**\n📅 Jogos Analisados: {jt}\n---\n**🔥 PORCENTAGENS**\n⚽ Over 0.5 1T: **{pct(d['over05_1T'], jt)}**\n⚽ Over 0.5 2T: **{pct(d['over05_2T'], jt)}**\n⚽ Over 1.5 2T: **{pct(d['over15_2T'], jt)}**\n⚽ Over 1.5 Total: **{pct(d['over15'], jt)}**\n⚽ Over 2.5 Total: **{pct(d['over25'], jt)}**\n🔁 BTTS: **{pct(d['btts'], jt)}**\n🥅 G.A.T: **{pct(d['g_a_t'], jt)}**\n📈 Marcou 2+: **{pct(d['marcou_2_mais'], jt)}**\n📉 Sofreu 2+: **{pct(d['sofreu_2_mais'], jt)}**\n⚽ M.A.T: **{pct(d['marcou_ambos_tempos'], jt)}**\n🥅 S.A.T: **{pct(d['sofreu_ambos_tempos'], jt)}**\n---\n**🔢 MÉDIAS (Por Jogo)**\n✅ Gols Marcados: **{media(d['gols_marcados'], jt)}**\n❌ Gols Sofridos: **{media(d['gols_sofridos'], jt)}**\n🕐 Marcados 1T: **{media(d['gols_marcados_1T'], jt)}**\n🕐 Sofridos 1T: **{media(d['gols_sofridos_1T'], jt)}**\n🕑 Marcados 2T: **{media(d['gols_marcados_2T'], jt)}**\n🕑 Sofridos 2T: **{media(d['gols_sofridos_2T'], jt)}**\n🏁 MÉDIA TOTAL: **{media(d['total_gols'], jt)}**")

def listar_ultimos_jogos(time, aba, ultimos=None, casa_fora=None):
    time = time.strip()
    try:
        linhas = get_sheet_data(aba)
        if casa_fora == "casa": 
            linhas = [l for l in linhas if str(l['Mandante']).strip() == time]
        elif casa_fora == "fora": 
            linhas = [l for l in linhas if str(l['Visitante']).strip() == time]
        else: 
            linhas = [l for l in linhas if str(l['Mandante']).strip() == time or str(l['Visitante']).strip() == time]
        
        linhas.sort(key=lambda x: datetime.strptime(x['Data'], "%d/%m/%Y"))
        if ultimos: linhas = linhas[-ultimos:]
        texto = ""
        for l in linhas:
            gm, gv = safe_int(l['Gols Mandante']), safe_int(l['Gols Visitante'])
            cor = "🟢" if (str(l['Mandante']).strip() == time and gm > gv) or (str(l['Visitante']).strip() == time and gv > gm) else ("🟡" if gm == gv else "🔴")
            texto += f"{cor} {l['Data']}: {l['Mandante']} {gm}x{gv} {l['Visitante']}\n"
        return texto or "Nenhum jogo encontrado."
    except: return "Erro ao buscar resultados."

# 🤖 HANDLERS
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Bem-vindo!\nUse **/stats** para começar.", parse_mode='Markdown')

async def listar_competicoes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton(aba, callback_data=f"c|{aba}")] for aba in LIGAS_MAP.keys()]
    keyboard.append([InlineKeyboardButton("🔄 FORÇAR ATUALIZAÇÃO", callback_data="FORCE_UPDATE")])
    msg = "📊 **Escolha a Competição:**"
    if update.message: await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    else: await update.callback_query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data
    try:
        if data == "FORCE_UPDATE":
            await update.callback_query.answer("🔄 Atualizando dados...")
            sucesso = await atualizar_planilhas()
            await update.callback_query.edit_message_text("✅ Planilhas atualizadas!" if sucesso else "❌ Erro na atualização.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Voltar", callback_data="VOLTAR_LIGA")]]))
        elif data.startswith("c|"):
            aba = data.split('|')[1]
            keyboard = [[InlineKeyboardButton("🔴 AO VIVO", callback_data=f"STATUS|LIVE|{aba}")], [InlineKeyboardButton("📅 PRÓXIMOS JOGOS", callback_data=f"STATUS|FUTURE|{aba}")], [InlineKeyboardButton("⬅️ Voltar", callback_data="VOLTAR_LIGA")]]
            await update.callback_query.edit_message_text(f"**{aba}** - Escolha o status:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        elif data.startswith("STATUS|"):
            _, status, aba = data.split('|')
            jogos = get_sheet_data_future(aba) if status == "FUTURE" else buscar_jogos_live(aba)
            if not jogos:
                await update.callback_query.edit_message_text("⚠️ Sem jogos.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Voltar", callback_data=f"c|{aba}")]]))
                return
            context.chat_data[f"{aba}_{status.lower()}"] = jogos
            kb = [[InlineKeyboardButton(f"{j['Mandante_Nome']} x {j['Visitante_Nome']}", callback_data=f"J|{aba}|{status}|{i}")] for i, j in enumerate(jogos[:MAX_GAMES_LISTED])]
            kb.append([InlineKeyboardButton("⬅️ Voltar", callback_data=f"c|{aba}")])
            await update.callback_query.edit_message_text("Selecione a partida:", reply_markup=InlineKeyboardMarkup(kb))
        elif data.startswith("J|"):
            _, aba, status, idx = data.split('|')
            j = context.chat_data[f"{aba}_{status.lower()}"][int(idx)]
            context.chat_data.update({'m': j['Mandante_Nome'], 'v': j['Visitante_Nome'], 'aba': aba})
            kb = [[InlineKeyboardButton(f[0], callback_data=f"{f[1]}|{i}")] for i, f in enumerate(CONFRONTO_FILTROS)]
            kb.append([InlineKeyboardButton("⬅️ Voltar", callback_data=f"STATUS|{status}|{aba}")])
            await update.effective_message.reply_text(f"Filtros: **{j['Mandante_Nome']} x {j['Visitante_Nome']}**", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
        elif data.startswith("STATS_FILTRO|"):
            idx = int(data.split('|')[1])
            _, _, ult, cm, cv = CONFRONTO_FILTROS[idx]
            m, v, aba = context.chat_data['m'], context.chat_data['v'], context.chat_data['aba']
            await update.effective_message.reply_text(f"{formatar_estatisticas(calcular_estatisticas_time(m, aba, ult, cm))}\n\n---\n\n{formatar_estatisticas(calcular_estatisticas_time(v, aba, ult, cv))}", parse_mode='Markdown')
        elif data.startswith("RESULTADOS_FILTRO|"):
            idx = int(data.split('|')[1])
            _, _, ult, cm, cv = CONFRONTO_FILTROS[idx]
            m, v, aba = context.chat_data['m'], context.chat_data['v'], context.chat_data['aba']
            await update.effective_message.reply_text(f"📅 **Resultados - {m}**\n{listar_ultimos_jogos(m, aba, ult, cm)}\n\n📅 **Resultados - {v}**\n{listar_ultimos_jogos(v, aba, ult, cv)}", parse_mode='Markdown')
        elif data == "VOLTAR_LIGA": await listar_competicoes(update, context)
    except: pass

def main():
    if not BOT_TOKEN: sys.exit(1)
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("stats", listar_competicoes))
    app.add_handler(CallbackQueryHandler(callback_query_handler))
    if client:
        app.job_queue.run_repeating(atualizar_planilhas, interval=3600, first=0)
    app.run_webhook(listen="0.0.0.0", port=int(os.environ.get("PORT", "8080")), url_path=BOT_TOKEN, webhook_url=f"{os.environ.get('RENDER_EXTERNAL_URL')}/{BOT_TOKEN}")

if __name__ == "__main__":
    main()
