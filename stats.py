# ===============================================================================
# 🏆 BOT DE ESTATÍSTICAS DE CONFRONTO V2.3.2 - LIGA SELECIONADA (BSA)
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
nest_asyncio.apply()

# ===== Variáveis de Configuração (LIDAS DE VARIÁVEIS DE AMBIENTE) =====
BOT_TOKEN = os.environ.get("BOT_TOKEN", "SEU_TOKEN_AQUI") 
API_KEY = os.environ.get("API_KEY", "SUA_API_KEY_AQUI")
SHEET_URL = os.environ.get("SHEET_URL", "https://docs.google.com/spreadsheets/d/1ChFFXQxo1qQElNzh2OC8-UPgofRXxyVWN06ExBQ3YqY/edit?usp=drivesdk")

# Mapeamento de Ligas (Filtrado apenas para BSA)
LIGAS_MAP = {
    "BSA": {"sheet_past": "BSA", "sheet_future": "BSA_FJ"}
}
ABAS_PASSADO = list(LIGAS_MAP.keys())

ULTIMOS = 10
SHEET_CACHE = {}
CACHE_DURATION_SECONDS = 3600 # 1 hora
MAX_GAMES_LISTED = 30

# =================================================================================
# 🆕 FILTROS ATUALIZADOS (GERAIS E ÚLTIMOS 10)
# =================================================================================
# Estrutura: (Label, Tipo, Quantidade, Filtro_Mandante, Filtro_Visitante)
# 0 ou None na quantidade indica que buscará todos os jogos (GERAIS)
CONFRONTO_FILTROS = [
    # --- GERAIS (Temporada Completa) ---
    ("📊 Estatísticas | GERAIS", "STATS_FILTRO", 0, None, None),
    ("📊 Estatísticas | GERAIS (M CASA vs V FORA)", "STATS_FILTRO", 0, "casa", "fora"),
    ("📅 Resultados | GERAIS", "RESULTADOS_FILTRO", 0, None, None),
    ("📅 Resultados | GERAIS (M CASA vs V FORA)", "RESULTADOS_FILTRO", 0, "casa", "fora"),

    # --- ÚLTIMOS 10 ---
    (f"📊 Estatísticas | ÚLTIMOS {ULTIMOS} GERAIS", "STATS_FILTRO", ULTIMOS, None, None),
    (f"📊 Estatísticas | {ULTIMOS} (M CASA vs V FORA)", "STATS_FILTRO", ULTIMOS, "casa", "fora"),
    (f"📅 Resultados | ÚLTIMOS {ULTIMOS} GERAIS", "RESULTADOS_FILTRO", ULTIMOS, None, None),
    (f"📅 Resultados | {ULTIMOS} (M CASA vs V FORA)", "RESULTADOS_FILTRO", ULTIMOS, "casa", "fora"),
]

LIVE_STATUSES = ["IN_PLAY", "HALF_TIME", "PAUSED"]

# =================================================================================
# ✅ CONEXÃO GSHEETS
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
        logging.info("✅ Conexão GSheets estabelecida.")
        os.remove(tmp_file_path)
    except Exception as e:
        logging.error(f"❌ ERRO GSHEET: {e}")
        client = None

# =================================================================================
# 💾 FUNÇÕES DE SUPORTE
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
    global SHEET_CACHE
    agora = datetime.now()
    aba_name = LIGAS_MAP[aba_code]['sheet_past']

    if aba_name in SHEET_CACHE:
        if (agora - SHEET_CACHE[aba_name]['timestamp']).total_seconds() < CACHE_DURATION_SECONDS:
            return SHEET_CACHE[aba_name]['data']

    if not client: raise Exception("Cliente GSheets não autorizado.")
    sh = client.open_by_url(SHEET_URL)
    linhas = sh.worksheet(aba_name).get_all_records()
    SHEET_CACHE[aba_name] = { 'data': linhas, 'timestamp': agora }
    return linhas

def get_sheet_data_future(aba_code):
    aba_name = LIGAS_MAP[aba_code]['sheet_future']
    if not client: return []
    try:
        sh = client.open_by_url(SHEET_URL)
        linhas_raw = sh.worksheet(aba_name).get_all_values()
        if not linhas_raw or len(linhas_raw) <= 1: return []
        return [{"Mandante_Nome": r[0], "Visitante_Nome": r[1], "Data_Hora": r[2], "Matchday": safe_int(r[3])} for r in linhas_raw[1:] if len(r) >= 4]
    except: return []

async def pre_carregar_cache_sheets():
    if not client: return
    for aba in ABAS_PASSADO:
        try: await asyncio.to_thread(get_sheet_data, aba)
        except: pass
        await asyncio.sleep(1)

# =================================================================================
# 🎯 FUNÇÕES DE API E ATUALIZAÇÃO
# =================================================================================
def buscar_jogos(league_code, status_filter):
    try:
        url = f"https://api.football-data.org/v4/competitions/{league_code}/matches"
        params = {"season": "2026"} if league_code == "BSA" else {}
        if status_filter != "ALL": params["status"] = status_filter

        r = requests.get(url, headers={"X-Auth-Token": API_KEY}, params=params, timeout=10)
        r.raise_for_status()
        all_matches = r.json().get("matches", [])

        if status_filter == "ALL":
            return [m for m in all_matches if m.get('status') in ['SCHEDULED', 'TIMED']]
        
        jogos = []
        for m in all_matches:
            if m.get('status') == "FINISHED":
                ft = m.get("score", {}).get("fullTime", {})
                ht = m.get("score", {}).get("halfTime", {})
                if ft.get("home") is None: continue
                gm, gv = ft.get("home",0), ft.get("away",0)
                gm1, gv1 = ht.get("home",0), ht.get("away",0)
                jogos.append({
                    "Mandante": m.get("homeTeam", {}).get("name", ""),
                    "Visitante": m.get("awayTeam", {}).get("name", ""),
                    "Gols Mandante": gm, "Gols Visitante": gv,
                    "Gols Mandante 1T": gm1, "Gols Visitante 1T": gv1,
                    "Gols Mandante 2T": gm-gm1, "Gols Visitante 2T": gv-gv1,
                    "Data": datetime.strptime(m['utcDate'][:10], "%Y-%m-%d").strftime("%d/%m/%Y")
                })
        return sorted(jogos, key=lambda x: datetime.strptime(x['Data'], "%d/%m/%Y"))
    except: return []

def buscar_jogos_live(league_code):
    hoje_utc = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    try:
        url = f"https://api.football-data.org/v4/competitions/{league_code}/matches?dateFrom={hoje_utc}&dateTo={hoje_utc}"
        r = requests.get(url, headers={"X-Auth-Token": API_KEY}, timeout=10)
        all_matches = r.json().get("matches", [])
        jogos = []
        for m in all_matches:
            if m.get('status') in LIVE_STATUSES:
                ft = m.get("score", {}).get("fullTime", {})
                minute = m.get("minute", "N/A")
                if m.get('status') == 'HALF_TIME': minute = "Intervalo"
                jogos.append({
                    "Mandante_Nome": m.get("homeTeam", {}).get("name", ""),
                    "Visitante_Nome": m.get("awayTeam", {}).get("name", ""),
                    "Placar_Mandante": ft.get("home", 0), "Placar_Visitante": ft.get("away", 0),
                    "Tempo_Jogo": minute, "Matchday": safe_int(m.get("matchday", 0))
                })
        return jogos
    except: return []

async def atualizar_planilhas(context: ContextTypes.DEFAULT_TYPE):
    if not client: return
    try: 
        sh = client.open_by_url(SHEET_URL)
        for aba_code, aba_config in LIGAS_MAP.items():
            # Histórico
            ws_past = sh.worksheet(aba_config['sheet_past'])
            jogos_fin = buscar_jogos(aba_code, "FINISHED")
            if jogos_fin:
                exist = ws_past.get_all_records()
                keys = {(r['Mandante'], r['Visitante'], r['Data']) for r in exist}
                novos = [[j["Mandante"], j["Visitante"], j["Gols Mandante"], j["Gols Visitante"], j["Gols Mandante 1T"], j["Gols Visitante 1T"], j["Gols Mandante 2T"], j["Gols Visitante 2T"], j["Data"]] for j in jogos_fin if (j["Mandante"], j["Visitante"], j["Data"]) not in keys]
                if novos: ws_past.append_rows(novos)
            
            # Futuros
            ws_future = sh.worksheet(aba_config['sheet_future'])
            futuros = buscar_jogos(aba_code, "ALL")
            ws_future.clear()
            ws_future.update(values=[['Mandante', 'Visitante', 'Data/Hora', 'Matchday']], range_name='A1:D1')
            if futuros:
                linhas = [[m.get("homeTeam", {}).get("name"), m.get("awayTeam", {}).get("name"), m.get('utcDate'), m.get("matchday")] for m in futuros]
                ws_future.append_rows(linhas)
    except: pass

# =================================================================================
# 📈 CÁLCULOS
# =================================================================================
def calcular_estatisticas_time(time, aba, ultimos=None, casa_fora=None):
    d = {"time":time,"jogos_time":0,"jogos_casa":0,"jogos_fora":0,"over15":0,"over15_casa":0,"over15_fora":0, "over25":0,"over25_casa":0,"over25_fora":0,"btts":0,"btts_casa":0,"btts_fora":0, "g_a_t":0,"g_a_t_casa":0,"g_a_t_fora":0, "over05_1T":0,"over05_1T_casa":0,"over05_1T_fora":0,"over05_2T":0,"over05_2T_casa":0,"over05_2T_fora":0, "over15_2T":0,"over15_2T_casa":0,"over15_2T_fora":0,"gols_marcados":0,"gols_sofridos":0, "gols_marcados_casa":0,"gols_sofridos_casa":0,"gols_marcados_fora":0,"gols_sofridos_fora":0, "total_gols":0,"total_gols_casa":0,"total_gols_fora":0,"gols_marcados_1T":0,"gols_sofridos_1T":0, "gols_marcados_2T":0,"gols_sofridos_2T":0,"marcou_2_mais":0, "marcou_2_mais_casa":0, "marcou_2_mais_fora":0,"sofreu_2_mais":0, "sofreu_2_mais_casa":0, "sofreu_2_mais_fora":0,"marcou_ambos_tempos":0, "marcou_ambos_tempos_casa":0, "marcou_ambos_tempos_fora":0,"sofreu_ambos_tempos":0, "sofreu_ambos_tempos_casa":0, "sofreu_ambos_tempos_fora":0}

    try: 
        linhas = get_sheet_data(aba)
        if casa_fora == "casa": linhas = [l for l in linhas if l['Mandante'] == time]
        elif casa_fora == "fora": linhas = [l for l in linhas if l['Visitante'] == time]
        else: linhas = [l for l in linhas if l['Mandante'] == time or l['Visitante'] == time]
        
        linhas.sort(key=lambda x: datetime.strptime(x['Data'], "%d/%m/%Y"))
        if ultimos and ultimos > 0: linhas = linhas[-ultimos:]
        
        for l in linhas:
            em_casa = (time == l['Mandante'])
            gm, gv = safe_int(l['Gols Mandante']), safe_int(l['Gols Visitante'])
            gm1, gv1 = safe_int(l['Gols Mandante 1T']), safe_int(l['Gols Visitante 1T'])
            gm2, gv2 = gm-gm1, gv-gv1
            total, total1, total2 = gm+gv, gm1+gv1, gm2+gv2
            
            d["jogos_time"] += 1
            if em_casa: d["jogos_casa"] += 1; marc, sofr = gm, gv; m1, s1 = gm1, gv1; m2, s2 = gm2, gv2
            else: d["jogos_fora"] += 1; marc, sofr = gv, gm; m1, s1 = gv1, gm1; m2, s2 = gv2, gm2

            d["gols_marcados"] += marc; d["gols_sofridos"] += sofr
            if em_casa: d["gols_marcados_casa"] += marc; d["gols_sofridos_casa"] += sofr
            else: d["gols_marcados_fora"] += marc; d["gols_sofridos_fora"] += sofr

            d["total_gols"] += total
            if em_casa: d["total_gols_casa"] += total
            else: d["total_gols_fora"] += total

            if total>1.5: d["over15"] += 1; d["over15_casa" if em_casa else "over15_fora"] += 1
            if total>2.5: d["over25"] += 1; d["over25_casa" if em_casa else "over25_fora"] += 1
            if gm>0 and gv>0: d["btts"] += 1; d["btts_casa" if em_casa else "btts_fora"] += 1
            if total1>0.5: d["over05_1T"] += 1; d["over05_1T_casa" if em_casa else "over05_1T_fora"] += 1
            if total2>0.5: d["over05_2T"] += 1; d["over05_2T_casa" if em_casa else "over05_2T_fora"] += 1
            if total2>1.5: d["over15_2T"] += 1; d["over15_2T_casa" if em_casa else "over15_2T_fora"] += 1
            if total1>0 and total2>0: d["g_a_t"] += 1; d["g_a_t_casa" if em_casa else "g_a_t_fora"] += 1
            if marc >= 2: d["marcou_2_mais"] += 1; d["marcou_2_mais_casa" if em_casa else "marcou_2_mais_fora"] += 1
            if sofr >= 2: d["sofreu_2_mais"] += 1; d["sofreu_2_mais_casa" if em_casa else "sofreu_2_mais_fora"] += 1
            if m1>0 and m2>0: d["marcou_ambos_tempos"] += 1; d["marcou_ambos_tempos_casa" if em_casa else "marcou_ambos_tempos_fora"] += 1
            if s1>0 and s2>0: d["sofreu_ambos_tempos"] += 1; d["sofreu_ambos_tempos_casa" if em_casa else "sofreu_ambos_tempos_fora"] += 1
            
            d["gols_marcados_1T"] += m1; d["gols_sofridos_1T"] += s1
            d["gols_marcados_2T"] += m2; d["gols_sofridos_2T"] += s2
        return d
    except: return {"time":time, "jogos_time": 0}

def formatar_estatisticas(d):
    jt, jc, jf = d["jogos_time"], d.get("jogos_casa", 0), d.get("jogos_fora", 0)
    if jt == 0: return f"⚠️ **Nenhum jogo encontrado** para **{escape_markdown(d['time'])}**."
    return (f"📊 **Estatísticas - {escape_markdown(d['time'])}**\n"
            f"📅 Jogos: {jt} (C: {jc} | F: {jf})\n\n"
            f"⚽ Over 1.5: **{pct(d['over15'], jt)}** (C: {pct(d['over15_casa'], jc)} | F: {pct(d['over15_fora'], jf)})\n"
            f"⚽ Over 2.5: **{pct(d['over25'], jt)}** (C: {pct(d['over25_casa'], jc)} | F: {pct(d['over25_fora'], jf)})\n"
            f"🔁 BTTS: **{pct(d['btts'], jt)}** (C: {pct(d['btts_casa'], jc)} | F: {pct(d['btts_fora'], jf)})\n"
            f"🥅 G.A.T. (Gols Ambos Tempos): {pct(d['g_a_t'], jt)}\n"
            f"📈 Marcou 2+: **{pct(d['marcou_2_mais'], jt)}** | 📉 Sofreu 2+: **{pct(d['sofreu_2_mais'], jt)}**\n"
            f"⚽ M.A.T.: {pct(d['marcou_ambos_tempos'], jt)} | 🥅 S.A.T.: {pct(d['sofreu_ambos_tempos'], jt)}\n\n"
            f"⏱️ 1ºT > 0.5: {pct(d['over05_1T'], jt)} | 2ºT > 0.5: {pct(d['over05_2T'], jt)}\n"
            f"➕ Média Marcados: {media(d['gols_marcados'], jt)} | ➖ Média Sofridos: {media(d['gols_sofridos'], jt)}\n"
            f"🔢 Média Total Gols: {media(d['total_gols'], jt)}")

def listar_ultimos_jogos(time, aba, ultimos=None, casa_fora=None):
    try:
        linhas = get_sheet_data(aba)
        if casa_fora == "casa": linhas = [l for l in linhas if l['Mandante'] == time]
        elif casa_fora == "fora": linhas = [l for l in Bauhaus if l['Visitante'] == time]
        else: linhas = [l for l in linhas if l['Mandante'] == time or l['Visitante'] == time]
        linhas.sort(key=lambda x: datetime.strptime(x['Data'], "%d/%m/%Y"))
        if ultimos and ultimos > 0: linhas = linhas[-ultimos:]
        if not linhas: return "Nenhum jogo encontrado."
        
        texto = ""
        for l in linhas:
            gm, gv = safe_int(l['Gols Mandante']), safe_int(l['Gols Visitante'])
            if l['Mandante'] == time:
                cor = "🟢" if gm > gv else ("🟡" if gm == gv else "🔴")
                texto += f"{cor} {l['Data']} (C): **{time}** {gm}x{gv} {l['Visitante']}\n"
            else:
                cor = "🟢" if gv > gm else ("🟡" if gv == gm else "🔴")
                texto += f"{cor} {l['Data']} (F): {l['Mandante']} {gm}x{gv} **{time}**\n"
        return texto
    except: return "Erro ao buscar resultados."

# =================================================================================
# 🤖 HANDLERS
# =================================================================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Bem-vindo!\nUse **/stats** para começar.", parse_mode='Markdown')

async def listar_competicoes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton(aba, callback_data=f"c|{aba}")] for aba in LIGAS_MAP.keys()]
    reply_markup = InlineKeyboardMarkup(keyboard)
    msg = "📊 **Escolha a Competição:**"
    if update.message: await update.message.reply_text(msg, reply_markup=reply_markup, parse_mode='Markdown')
    else: await update.callback_query.edit_message_text(msg, reply_markup=reply_markup, parse_mode='Markdown')

async def mostrar_menu_status_jogo(update: Update, context: ContextTypes.DEFAULT_TYPE, aba_code: str):
    keyboard = [[InlineKeyboardButton("🔴 AO VIVO", callback_data=f"STATUS|LIVE|{aba_code}")],
                [InlineKeyboardButton("📅 PRÓXIMOS JOGOS", callback_data=f"STATUS|FUTURE|{aba_code}")],
                [InlineKeyboardButton("⬅️ Voltar", callback_data="VOLTAR_LIGA")]]
    await update.callback_query.edit_message_text(f"**{aba_code}** - Escolha:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def listar_jogos(update: Update, context: ContextTypes.DEFAULT_TYPE, aba_code: str, status: str):
    jogos = get_sheet_data_future(aba_code) if status == "FUTURE" else buscar_jogos_live(aba_code)
    if not jogos:
        await update.callback_query.edit_message_text("⚠️ Nenhum jogo encontrado.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Voltar", callback_data=f"VOLTAR_LIGA_STATUS|{aba_code}")]]), parse_mode='Markdown')
        return
    
    context.chat_data[f"{aba_code}_jogos_{status.lower()}"] = jogos
    keyboard = []
    for idx, j in enumerate(jogos[:MAX_GAMES_LISTED]):
        label = f"{j['Mandante_Nome']} x {j['Visitante_Nome']}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"JOGO|{aba_code}|{status}|{idx}")])
    keyboard.append([InlineKeyboardButton("⬅️ Voltar", callback_data=f"VOLTAR_LIGA_STATUS|{aba_code}")])
    await update.callback_query.edit_message_text("Selecione a partida:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def mostrar_menu_acoes(update: Update, context: ContextTypes.DEFAULT_TYPE, aba_code: str, mandante: str, visitante: str):
    keyboard = [[InlineKeyboardButton(f[0], callback_data=f"{f[1]}|{i}")] for i, f in enumerate(CONFRONTO_FILTROS)]
    keyboard.append([InlineKeyboardButton("⬅️ Voltar para Jogos", callback_data=f"VOLTAR_LIGA_STATUS|{aba_code}")])
    await update.effective_message.reply_text(f"Filtros para: **{mandante} x {visitante}**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def exibir_estatisticas(update: Update, context: ContextTypes.DEFAULT_TYPE, mandante: str, visitante: str, aba_code: str, idx: int):
    _, _, ult, cm, cv = CONFRONTO_FILTROS[idx]
    dm = calcular_estatisticas_time(mandante, aba_code, ultimos=ult, casa_fora=cm)
    dv = calcular_estatisticas_time(visitante, aba_code, ultimos=ult, casa_fora=cv)
    await update.effective_message.reply_text(f"{formatar_estatisticas(dm)}\n\n---\n\n{formatar_estatisticas(dv)}", parse_mode='Markdown')
    await mostrar_menu_acoes(update, context, aba_code, mandante, visitante)

async def exibir_ultimos_resultados(update: Update, context: ContextTypes.DEFAULT_TYPE, mandante: str, visitante: str, aba_code: str, idx: int):
    _, _, ult, cm, cv = CONFRONTO_FILTROS[idx]
    rm = listar_ultimos_jogos(mandante, aba_code, ultimos=ult, casa_fora=cm)
    rv = listar_ultimos_jogos(visitante, aba_code, ultimos=ult, casa_fora=cv)
    await update.effective_message.reply_text(f"📅 **Resultados - {mandante}**\n{rm}\n\n📅 **Resultados - {visitante}**\n{rv}", parse_mode='Markdown')
    await mostrar_menu_acoes(update, context, aba_code, mandante, visitante)

async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data
    try:
        if data.startswith("c|"): await mostrar_menu_status_jogo(update, context, data.split('|')[1])
        elif data.startswith("STATUS|"): await listar_jogos(update, context, data.split('|')[2], data.split('|')[1])
        elif data.startswith("JOGO|"):
            _, aba, status, idx = data.split('|')
            jogo = context.chat_data[f"{aba}_jogos_{status.lower()}"][int(idx)]
            context.chat_data.update({'current_mandante': jogo['Mandante_Nome'], 'current_visitante': jogo['Visitante_Nome'], 'current_aba_code': aba})
            await mostrar_menu_acoes(update, context, aba, jogo['Mandante_Nome'], jogo['Visitante_Nome'])
        elif data.startswith("STATS_FILTRO|"): await exibir_estatisticas(update, context, context.chat_data['current_mandante'], context.chat_data['current_visitante'], context.chat_data['current_aba_code'], int(data.split('|')[1]))
        elif data.startswith("RESULTADOS_FILTRO|"): await exibir_ultimos_resultados(update, context, context.chat_data['current_mandante'], context.chat_data['current_visitante'], context.chat_data['current_aba_code'], int(data.split('|')[1]))
        elif data.startswith("VOLTAR_LIGA_STATUS|"): await mostrar_menu_status_jogo(update, context, data.split('|')[1])
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
        asyncio.run(pre_carregar_cache_sheets())
    
    webhook_url = os.environ.get("WEBHOOK_URL") or os.environ.get("RENDER_EXTERNAL_URL")
    app.run_webhook(listen="0.0.0.0", port=int(os.environ.get("PORT", "8080")), url_path=BOT_TOKEN, webhook_url=f"{webhook_url}/{BOT_TOKEN}")

if __name__ == "__main__":
    main()
