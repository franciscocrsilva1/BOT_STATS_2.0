# ===============================================================================
# 🏆 BOT DE ESTATÍSTICAS DE CONFRONTO V2.5.0 - LIGAS EXPANDIDAS & FILTROS TOTAIS
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
SHEET_URL = os.environ.get("SHEET_URL", "https://docs.google.com/spreadsheets/d/1ChFFXQxo1qQElNzh2OC8-UPgofRXxyVWN06ExBQ3YqY/edit?usp=drivesdk")

# Mapeamento de Ligas (Adicionadas: CL, PD, FL1, ELC, PPL, SA)
LIGAS_MAP = {
    "BSA": {"sheet_past": "BSA", "sheet_future": "BSA_FJ"},
    "BL1": {"sheet_past": "BL1", "sheet_future": "BL1_FJ"},
    "PL": {"sheet_past": "PL", "sheet_future": "PL_FJ"},
    "DED": {"sheet_past": "DED", "sheet_future": "DED_FJ"},
    "CL": {"sheet_past": "CL", "sheet_future": "CL_FJ"},
    "PD": {"sheet_past": "PD", "sheet_future": "PD_FJ"},
    "FL1": {"sheet_past": "FL1", "sheet_future": "FL1_FJ"},
    "ELC": {"sheet_past": "ELC", "sheet_future": "ELC_FJ"},
    "PPL": {"sheet_past": "PPL", "sheet_future": "PPL_FJ"},
    "SA": {"sheet_past": "SA", "sheet_future": "SA_FJ"},
}
ABAS_PASSADO = list(LIGAS_MAP.keys())

ULTIMOS = 10
SHEET_CACHE = {}
CACHE_DURATION_SECONDS = 3600 
MAX_GAMES_LISTED = 35

# Filtros Expandidos (Adicionado filtros GERAIS sem limite de 'ULTIMOS')
CONFRONTO_FILTROS = [
    (f"📊 Estatísticas | ÚLTIMOS {ULTIMOS} GERAL", "STATS_FILTRO", ULTIMOS, None, None),
    (f"📊 Estatísticas | {ULTIMOS} (M CASA vs V FORA)", "STATS_FILTRO", ULTIMOS, "casa", "fora"),
    (f"📊 Estatísticas | TODOS OS JOGOS GERAIS", "STATS_FILTRO", None, None, None),
    (f"📊 Estatísticas | TODOS (M CASA vs V FORA)", "STATS_FILTRO", None, "casa", "fora"),
    (f"📅 Resultados | ÚLTIMOS {ULTIMOS} GERAL", "RESULTADOS_FILTRO", ULTIMOS, None, None),
    (f"📅 Resultados | {ULTIMOS} (M CASA vs V FORA)", "RESULTADOS_FILTRO", ULTIMOS, "casa", "fora"),
    (f"📅 Resultados | TODOS OS JOGOS GERAIS", "RESULTADOS_FILTRO", None, None, None),
    (f"📅 Resultados | TODOS (M CASA vs V FORA)", "RESULTADOS_FILTRO", None, "casa", "fora"),
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
        logging.error(f"❌ ERRO DE AUTORIZAÇÃO GSHEET: {e}")
        client = None

# =================================================================================
# 💾 FUNÇÕES DE SUPORTE
# =================================================================================
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
        cache_tempo = SHEET_CACHE[aba_name]['timestamp']
        if (agora - cache_tempo).total_seconds() < CACHE_DURATION_SECONDS:
            return SHEET_CACHE[aba_name]['data']

    if not client: raise Exception("Cliente GSheets não autorizado.")
    try:
        sh = client.open_by_url(SHEET_URL)
        linhas = sh.worksheet(aba_name).get_all_records()
        SHEET_CACHE[aba_name] = { 'data': linhas, 'timestamp': agora }
        return linhas
    except Exception as e:
        if aba_name in SHEET_CACHE: return SHEET_CACHE[aba_name]['data']
        raise e

def get_sheet_data_future(aba_code):
    aba_name = LIGAS_MAP[aba_code]['sheet_future']
    if not client: return []
    try:
        sh = client.open_by_url(SHEET_URL)
        linhas_raw = sh.worksheet(aba_name).get_all_values()
        if not linhas_raw or len(linhas_raw) <= 1: return []
        data_rows = linhas_raw[1:]
        return [{"Mandante_Nome": r[0], "Visitante_Nome": r[1], "Data_Hora": r[2], "Matchday": safe_int(r[3])} for r in data_rows if len(r) >= 4]
    except: return []

# =================================================================================
# 🎯 API E SINCRONIZAÇÃO
# =================================================================================
def buscar_jogos(league_code, status_filter):
    try:
        url = f"https://api.football-data.org/v4/competitions/{league_code}/matches"
        params = {"status": status_filter} if status_filter != "ALL" else {}
        if league_code == "BSA": params["season"] = "2026"
        
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
                gm, gv = ft.get("home"), ft.get("away")
                gm1, gv1 = ht.get("home", 0), ht.get("away", 0)
                jogos.append({
                    "Mandante": m.get("homeTeam", {}).get("name"),
                    "Visitante": m.get("awayTeam", {}).get("name"),
                    "Gols Mandante": gm, "Gols Visitante": gv,
                    "Gols Mandante 1T": gm1, "Gols Visitante 1T": gv1,
                    "Gols Mandante 2T": gm - gm1, "Gols Visitante 2T": gv - gv1,
                    "Data": datetime.strptime(m['utcDate'][:10], "%Y-%m-%d").strftime("%d/%m/%Y")
                })
        return sorted(jogos, key=lambda x: datetime.strptime(x['Data'], "%d/%m/%Y"))
    except: return []

async def atualizar_planilhas(context: ContextTypes.DEFAULT_TYPE):
    if not client: return
    try: sh = client.open_by_url(SHEET_URL)
    except: return

    for aba_code, aba_config in LIGAS_MAP.items():
        try:
            ws_past = sh.worksheet(aba_config['sheet_past'])
            jogos_fin = buscar_jogos(aba_code, "FINISHED")
            if jogos_fin:
                exist = ws_past.get_all_records()
                keys = {(r['Mandante'], r['Visitante'], r['Data']) for r in exist}
                novas = [[j["Mandante"], j["Visitante"], j["Gols Mandante"], j["Gols Visitante"], j["Gols Mandante 1T"], j["Gols Visitante 1T"], j["Gols Mandante 2T"], j["Gols Visitante 2T"], j["Data"]] for j in jogos_fin if (j["Mandante"], j["Visitante"], j["Data"]) not in keys]
                if novas: ws_past.append_rows(novas)
                if aba_config['sheet_past'] in SHEET_CACHE: del SHEET_CACHE[aba_config['sheet_past']]
        except: pass
        await asyncio.sleep(2)

        try:
            ws_fut = sh.worksheet(aba_config['sheet_future'])
            jogos_fut = buscar_jogos(aba_code, "ALL")
            ws_fut.clear()
            ws_fut.update(values=[['Mandante', 'Visitante', 'Data/Hora', 'Matchday']], range_name='A1:D1')
            if jogos_fut:
                linhas = [[m["homeTeam"]["name"], m["awayTeam"]["name"], m["utcDate"], m.get("matchday", "")] for m in jogos_fut]
                ws_fut.append_rows(linhas)
        except: pass
        await asyncio.sleep(2)

# =================================================================================
# 📈 CÁLCULOS E FORMATAÇÃO
# =================================================================================
def calcular_estatisticas_time(time, aba, ultimos=None, casa_fora=None):
    d = {"time":time,"jogos_time":0,"jogos_casa":0,"jogos_fora":0,
         "over15":0,"over15_casa":0,"over15_fora":0, "over25":0,"over25_casa":0,"over25_fora":0,
         "btts":0,"btts_casa":0,"btts_fora":0, "g_a_t":0,"g_a_t_casa":0,"g_a_t_fora":0, "over05_1T":0,"over05_1T_casa":0,"over05_1T_fora":0,
         "over05_2T":0,"over05_2T_casa":0,"over05_2T_fora":0, "over15_2T":0,"over15_2T_casa":0,"over15_2T_fora":0,
         "gols_marcados":0,"gols_sofridos":0, "gols_marcados_casa":0,"gols_sofridos_casa":0,
         "gols_marcados_fora":0,"gols_sofridos_fora":0, "total_gols":0,"total_gols_casa":0,"total_gols_fora":0,
         "gols_marcados_1T":0,"gols_sofridos_1T":0, "gols_marcados_2T":0,"gols_sofridos_2T":0,
         "gols_marcados_1T_casa":0,"gols_sofridos_1T_casa":0, "gols_marcados_1T_fora":0,"gols_sofridos_1T_fora":0,
         "gols_marcados_2T_casa":0,"gols_sofridos_2T_casa":0, "gols_marcados_2T_fora":0,"gols_sofridos_2T_fora":0,
         "marcou_2_mais":0, "marcou_2_mais_casa":0, "marcou_2_mais_fora":0,
         "sofreu_2_mais":0, "sofreu_2_mais_casa":0, "sofreu_2_mais_fora":0,
         "marcou_ambos_tempos":0, "marcou_ambos_tempos_casa":0, "marcou_ambos_tempos_fora":0,
         "sofreu_ambos_tempos":0, "sofreu_ambos_tempos_casa":0, "sofreu_ambos_tempos_fora":0}

    try: 
        linhas = get_sheet_data(aba)
    except: 
        return {"time":time, "jogos_time": 0}

    if casa_fora == "casa": 
        linhas = [l for l in linhas if l['Mandante'] == time]
    elif casa_fora == "fora": 
        linhas = [l for l in linhas if l['Visitante'] == time]
    else: 
        linhas = [l for l in linhas if l['Mandante'] == time or l['Visitante'] == time]

    try: 
        linhas.sort(key=lambda x: datetime.strptime(x['Data'], "%d/%m/%Y"))
    except: 
        pass

    if ultimos: 
        linhas = linhas[-ultimos:]

    for linha in linhas:
        em_casa = (time == linha['Mandante'])
        gm, gv = safe_int(linha['Gols Mandante']), safe_int(linha['Gols Visitante'])
        gm1, gv1 = safe_int(linha['Gols Mandante 1T']), safe_int(linha['Gols Visitante 1T'])
        gm2, gv2 = gm-gm1, gv-gv1 
        total, total1, total2 = gm+gv, gm1+gv1, gm2+gv2
        
        d["jogos_time"] += 1
        if em_casa:
            d["jogos_casa"] += 1
            m, s = gm, gv
            m1, s1, m2, s2 = gm1, gv1, gm2, gv2
            d["gols_marcados_casa"] += m; d["gols_sofridos_casa"] += s
            d["total_gols_casa"] += total
            d["gols_marcados_1T_casa"] += m1; d["gols_sofridos_1T_casa"] += s1
            d["gols_marcados_2T_casa"] += m2; d["gols_sofridos_2T_casa"] += s2
        else:
            d["jogos_fora"] += 1
            m, s = gv, gm
            m1, s1, m2, s2 = gv1, gm1, gv2, gm2
            d["gols_marcados_fora"] += m; d["gols_sofridos_fora"] += s
            d["total_gols_fora"] += total
            d["gols_marcados_1T_fora"] += m1; d["gols_sofridos_1T_fora"] += s1
            d["gols_marcados_2T_fora"] += m2; d["gols_sofridos_2T_fora"] += s2

        d["gols_marcados"] += m; d["gols_sofridos"] += s
        d["total_gols"] += total
        d["gols_marcados_1T"] += m1; d["gols_sofridos_1T"] += s1
        d["gols_marcados_2T"] += m2; d["gols_sofridos_2T"] += s2

        if total > 1.5: d["over15"] += 1; d["over15_casa" if em_casa else "over15_fora"] += 1
        if total > 2.5: d["over25"] += 1; d["over25_casa" if em_casa else "over25_fora"] += 1
        if gm > 0 and gv > 0: d["btts"] += 1; d["btts_casa" if em_casa else "btts_fora"] += 1
        if total1 > 0.5: d["over05_1T"] += 1; d["over05_1T_casa" if em_casa else "over05_1T_fora"] += 1
        if total2 > 0.5: d["over05_2T"] += 1; d["over05_2T_casa" if em_casa else "over05_2T_fora"] += 1
        if total2 > 1.5: d["over15_2T"] += 1; d["over15_2T_casa" if em_casa else "over15_2T_fora"] += 1
        
        if total1 > 0 and total2 > 0: d["g_a_t"] += 1; d["g_a_t_casa" if em_casa else "g_a_t_fora"] += 1
        if m >= 2: d["marcou_2_mais"] += 1; d["marcou_2_mais_casa" if em_casa else "marcou_2_mais_fora"] += 1
        if s >= 2: d["sofreu_2_mais"] += 1; d["sofreu_2_mais_casa" if em_casa else "sofreu_2_mais_fora"] += 1
        if m1 > 0 and m2 > 0: d["marcou_ambos_tempos"] += 1; d["marcou_ambos_tempos_casa" if em_casa else "marcou_ambos_tempos_fora"] += 1
        if s1 > 0 and s2 > 0: d["sofreu_ambos_tempos"] += 1; d["sofreu_ambos_tempos_casa" if em_casa else "sofreu_ambos_tempos_fora"] += 1

    return d

def formatar_estatisticas(d):
    jt, jc, jf = d["jogos_time"], d.get("jogos_casa", 0), d.get("jogos_fora", 0)
    if jt == 0: return f"⚠️ **Nenhum jogo encontrado** para **{escape_markdown(d['time'])}**."
    
    return (f"📊 **Estatísticas - {escape_markdown(d['time'])}**\n"
            f"📅 Jogos Analisados: {jt} (C: {jc} | F: {jf})\n\n"
            f"⚽ Over 1.5: **{pct(d['over15'], jt)}** (C: {pct(d['over15_casa'], jc)} | F: {pct(d['over15_fora'], jf)})\n"
            f"⚽ Over 2.5: **{pct(d['over25'], jt)}** (C: {pct(d['over25_casa'], jc)} | F: {pct(d['over25_fora'], jf)})\n"
            f"🔁 BTTS: **{pct(d['btts'], jt)}** (C: {pct(d['btts_casa'], jc)} | F: {pct(d['btts_fora'], jf)})\n"
            f"🥅 G.A.T. (Gol em Ambos Tempos): {pct(d['g_a_t'], jt)}\n"
            f"📈 Marcou 2+: **{pct(d['marcou_2_mais'], jt)}** | 📉 Sofreu 2+: **{pct(d['sofreu_2_mais'], jt)}**\n"
            f"⚽ M.A.T. (Marcou Amb. Tempos): {pct(d['marcou_ambos_tempos'], jt)}\n\n"
            f"⏱️ 1ºT Over 0.5: {pct(d['over05_1T'], jt)} | ⏱️ 2ºT Over 0.5: {pct(d['over05_2T'], jt)}\n\n"
            f"➕ **Média GP:** {media(d['gols_marcados'], jt)} | ➖ **Média GC:** {media(d['gols_sofridos'], jt)}\n"
            f"🔢 **Média Total:** {media(d['total_gols'], jt)}")

def listar_ultimos_jogos(time, aba, ultimos=None, casa_fora=None):
    try: 
        linhas = get_sheet_data(aba)
    except: return "Erro ao acessar planilha."
    
    if casa_fora == "casa": linhas = [l for l in linhas if l['Mandante'] == time]
    elif casa_fora == "fora": linhas = [l for l in linhas if l['Visitante'] == time]
    else: linhas = [l for l in linhas if l['Mandante'] == time or l['Visitante'] == time]
    
    try: linhas.sort(key=lambda x: datetime.strptime(x['Data'], "%d/%m/%Y"))
    except: pass
    
    if ultimos: linhas = linhas[-ultimos:]
    if not linhas: return "Nenhum resultado disponível."

    texto = ""
    for l in linhas:
        gm, gv = safe_int(l['Gols Mandante']), safe_int(l['Gols Visitante'])
        if l['Mandante'] == time:
            cor = "🟢" if gm > gv else ("🟡" if gm == gv else "🔴")
            texto += f"{cor} {l['Data']} (C): **{escape_markdown(time)}** {gm}x{gv} {l['Visitante']}\n"
        else:
            cor = "🟢" if gv > gm else ("🟡" if gv == gm else "🔴")
            texto += f"{cor} {l['Data']} (F): {l['Mandante']} {gm}x{gv} **{escape_markdown(time)}**\n"
    return texto

# =================================================================================
# 🤖 HANDLERS DO BOT
# =================================================================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Bem-vindo! Use **/stats** para analisar um confronto.", parse_mode='Markdown')

async def listar_competicoes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = []
    abas = list(LIGAS_MAP.keys())
    for i in range(0, len(abas), 3):
        keyboard.append([InlineKeyboardButton(a, callback_data=f"c|{a}") for a in abas[i:i+3]])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = "📊 **Escolha a Competição:**"
    if update.message: await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    else: await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')

async def mostrar_menu_status_jogo(update: Update, aba_code: str):
    keyboard = [
        [InlineKeyboardButton("📅 PRÓXIMOS JOGOS", callback_data=f"STATUS|FUTURE|{aba_code}")],
        [InlineKeyboardButton("⬅️ Voltar", callback_data="VOLTAR_LIGA")]
    ]
    await update.callback_query.edit_message_text(f"🏆 LIGA: **{aba_code}**\nSelecione:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def listar_jogos(update: Update, context: ContextTypes.DEFAULT_TYPE, aba_code: str, status: str):
    jogos = get_sheet_data_future(aba_code)
    agora = datetime.now(timezone.utc)
    
    # Filtrar apenas jogos futuros
    jogos_filtrados = []
    for j in jogos:
        try:
            dt = datetime.strptime(j['Data_Hora'][:16], '%Y-%m-%dT%H:%M').replace(tzinfo=timezone.utc)
            if dt > agora:
                jogos_filtrados.append(j)
        except: continue
    
    jogos_filtrados = jogos_filtrados[:MAX_GAMES_LISTED]

    if not jogos_filtrados:
        await update.callback_query.answer("Nenhum jogo futuro encontrado.", show_alert=True)
        return

    context.chat_data[f"{aba_code}_{status}"] = jogos_filtrados
    keyboard = []
    for idx, j in enumerate(jogos_filtrados):
        label = f"{j['Mandante_Nome']} x {j['Visitante_Nome']}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"J|{aba_code}|{status}|{idx}")])
    
    keyboard.append([InlineKeyboardButton("⬅️ Voltar", callback_data=f"c|{aba_code}")])
    await update.callback_query.edit_message_text("Selecione a partida:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def mostrar_menu_acoes(update: Update, context: ContextTypes.DEFAULT_TYPE, aba_code: str, m: str, v: str):
    context.chat_data.update({'m': m, 'v': v, 'aba': aba_code})
    keyboard = []
    for idx, (label, _, _, _, _) in enumerate(CONFRONTO_FILTROS):
        keyboard.append([InlineKeyboardButton(label, callback_data=f"F|{idx}")])
    keyboard.append([InlineKeyboardButton("⬅️ Voltar", callback_data=f"c|{aba_code}")])
    
    text = f"Analisando: **{escape_markdown(m)} x {escape_markdown(v)}**"
    await update.callback_query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    
    if data == "VOLTAR_LIGA": await listar_competicoes(update, context)
    elif data.startswith("c|"): await mostrar_menu_status_jogo(update, data.split('|')[1])
    elif data.startswith("STATUS|"):
        _, stat, aba = data.split('|'); await listar_jogos(update, context, aba, stat)
    elif data.startswith("J|"):
        _, aba, stat, idx = data.split('|'); j = context.chat_data[f"{aba}_{stat}"][int(idx)]
        await mostrar_menu_acoes(update, context, aba, j['Mandante_Nome'], j['Visitante_Nome'])
    elif data.startswith(
