# ===============================================================================
# 🏆 BOT DE ESTATÍSTICAS DE CONFRONTO V2.4.0 - LIGAS EXPANDIDAS & NOVOS FILTROS
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

# Mapeamento de Ligas Atualizado (Inclusão de CL, PD, FL1, ELC, PPL, SA)
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
CACHE_DURATION_SECONDS = 3600 # 1 hora
MAX_GAMES_LISTED = 30

# Filtros reutilizáveis para Estatísticas e Resultados (Incluindo novos filtros de "TODOS")
CONFRONTO_FILTROS = [
    (f"📊 Estatísticas | ÚLTIMOS {ULTIMOS} GERAL", "STATS_FILTRO", ULTIMOS, None, None),
    (f"📊 Estatísticas | {ULTIMOS} (M CASA vs V FORA)", "STATS_FILTRO", ULTIMOS, "casa", "fora"),
    (f"📊 Estatísticas | TODOS OS JOGOS GERAIS", "STATS_FILTRO", None, None, None),
    (f"📊 Estatísticas | TODOS MANDANTE CASA", "STATS_FILTRO", None, "casa", "fora"),
    (f"📅 Resultados | ÚLTIMOS {ULTIMOS} GERAL", "RESULTADOS_FILTRO", ULTIMOS, None, None),
    (f"📅 Resultados | {ULTIMOS} (M CASA vs V FORA)", "RESULTADOS_FILTRO", ULTIMOS, "casa", "fora"),
    (f"📅 Resultados | TODOS OS JOGOS GERAIS", "RESULTADOS_FILTRO", None, None, None),
    (f"📅 Resultados | TODOS VISITANTE FORA", "RESULTADOS_FILTRO", None, "casa", "fora"),
]

LIVE_STATUSES = ["IN_PLAY", "HALF_TIME", "PAUSED"]

# =================================================================================
# ✅ CONEXÃO GSHEETS VIA VARIÁVEL DE AMBIENTE 
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
# 💾 FUNÇÕES DE SUPORTE E CACHING 
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
        cache_tempo = SHEET_CACHE[aba_name]['timestamp']
        if (agora - cache_tempo).total_seconds() < CACHE_DURATION_SECONDS:
            return SHEET_CACHE[aba_name]['data']

    if not client: raise Exception("Cliente GSheets não autorizado.")
    try:
        sh = client.open_by_url(SHEET_URL)
        linhas = sh.worksheet(aba_name).get_all_records()
    except Exception as e:
        if aba_name in SHEET_CACHE: return SHEET_CACHE[aba_name]['data']
        raise e

    SHEET_CACHE[aba_name] = { 'data': linhas, 'timestamp': agora }
    return linhas

def get_sheet_data_future(aba_code):
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
                "Mandante_Nome": row[0], "Visitante_Nome": row[1],
                "Data_Hora": row[2], "Matchday": safe_int(row[3])
            })
    return jogos

async def pre_carregar_cache_sheets():
    if not client: return
    logging.info("Iniciando pré-carregamento de cache...")
    for aba in ABAS_PASSADO:
        try:
            await asyncio.to_thread(get_sheet_data, aba)
        except: continue
        await asyncio.sleep(1)

# =================================================================================
# 🎯 FUNÇÕES DE API E ATUALIZAÇÃO 
# =================================================================================
def buscar_jogos(league_code, status_filter):
    try:
        url = f"https://api.football-data.org/v4/competitions/{league_code}/matches"
        params = {}
        if status_filter != "ALL": params["status"] = status_filter
        if league_code == "BSA": params["season"] = "2026"

        r = requests.get(url, headers={"X-Auth-Token": API_KEY}, params=params, timeout=10)
        r.raise_for_status()
    except Exception as e:
        logging.error(f"Erro API {league_code}: {e}")
        return []

    all_matches = r.json().get("matches", [])
    if status_filter == "ALL":
        return [m for m in all_matches if m.get('status') in ['SCHEDULED', 'TIMED']]
    else:
        jogos = []
        for m in all_matches:
            if m.get('status') == "FINISHED":
                try:
                    jogo_data = datetime.strptime(m['utcDate'][:10], "%Y-%m-%d")
                    ft = m.get("score", {}).get("fullTime", {}); ht = m.get("score", {}).get("halfTime", {})
                    if ft.get("home") is None: continue
                    gm, gv = ft.get("home",0), ft.get("away",0)
                    gm1, gv1 = ht.get("home",0), ht.get("away",0)
                    jogos.append({
                        "Mandante": m.get("homeTeam", {}).get("name", ""),
                        "Visitante": m.get("awayTeam", {}).get("name", ""),
                        "Gols Mandante": gm, "Gols Visitante": gv,
                        "Gols Mandante 1T": gm1, "Gols Visitante 1T": gv1,
                        "Gols Mandante 2T": gm-gm1, "Gols Visitante 2T": gv-gv1,
                        "Data": jogo_data.strftime("%d/%m/%Y")
                    })
                except: continue
        return sorted(jogos, key=lambda x: datetime.strptime(x['Data'], "%d/%m/%Y"))

def buscar_jogos_live(league_code):
    hoje_utc = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    try:
        url = f"https://api.football-data.org/v4/competitions/{league_code}/matches?dateFrom={hoje_utc}&dateTo={hoje_utc}"
        r = requests.get(url, headers={"X-Auth-Token": API_KEY}, timeout=10)
        r.raise_for_status()
    except: return []

    all_matches = r.json().get("matches", [])
    jogos = []
    for m in all_matches:
        status_api = m.get('status')
        if status_api in LIVE_STATUSES:
            try:
                ft_score = m.get("score", {}).get("fullTime", {})
                minute = m.get("minute", "N/A")
                if status_api in ['PAUSED', 'HALF_TIME']: minute = "Intervalo" if status_api == 'HALF_TIME' else "Pausado"
                jogos.append({
                    "Mandante_Nome": m.get("homeTeam", {}).get("name", ""),
                    "Visitante_Nome": m.get("awayTeam", {}).get("name", ""),
                    "Placar_Mandante": ft_score.get("home", 0), "Placar_Visitante": ft_score.get("away", 0),
                    "Tempo_Jogo": minute, "Matchday": safe_int(m.get("matchday", 0))
                })
            except: continue
    return jogos

async def atualizar_planilhas(context: ContextTypes.DEFAULT_TYPE):
    global SHEET_CACHE
    if not client: return
    try: sh = client.open_by_url(SHEET_URL)
    except: return

    for aba_code, aba_config in LIGAS_MAP.items():
        # Histórico
        try:
            ws_past = sh.worksheet(aba_config['sheet_past'])
            jogos_finished = buscar_jogos(aba_code, "FINISHED")
            if jogos_finished:
                exist = ws_past.get_all_records()
                keys_exist = {(r['Mandante'], r['Visitante'], r['Data']) for r in exist}
                novas = [[j["Mandante"], j["Visitante"], j["Gols Mandante"], j["Gols Visitante"],
                          j["Gols Mandante 1T"], j["Gols Visitante 1T"], j["Gols Mandante 2T"], 
                          j["Gols Visitante 2T"], j["Data"]] for j in jogos_finished if (j["Mandante"], j["Visitante"], j["Data"]) not in keys_exist]
                if novas: ws_past.append_rows(novas)
                if aba_config['sheet_past'] in SHEET_CACHE: del SHEET_CACHE[aba_config['sheet_past']]
        except: pass
        await asyncio.sleep(2)

        # Futuros
        try:
            ws_future = sh.worksheet(aba_config['sheet_future'])
            jogos_f = buscar_jogos(aba_code, "ALL")
            ws_future.clear()
            ws_future.update(values=[['Mandante', 'Visitante', 'Data/Hora', 'Matchday']], range_name='A1:D1')
            if jogos_f:
                linhas = [[m.get("homeTeam", {}).get("name"), m.get("awayTeam", {}).get("name"), 
                           m.get('utcDate', ''), m.get("matchday", "")] for m in jogos_f]
                ws_future.append_rows(linhas, value_input_option='USER_ENTERED')
        except: pass
        await asyncio.sleep(2)

# =================================================================================
# 📈 FUNÇÕES DE CÁLCULO E FORMATAÇÃO (IDÊNTICO AO ORIGINAL)
# =================================================================================
def calcular_estatisticas_time(time, aba, ultimos=None, casa_fora=None):
    d = {"time":time,"jogos_time":0,"jogos_casa":0,"jogos_fora":0,"over15":0,"over15_casa":0,"over15_fora":0, 
         "over25":0,"over25_casa":0,"over25_fora":0,"btts":0,"btts_casa":0,"btts_fora":0, "g_a_t":0,"g_a_t_casa":0,"g_a_t_fora":0, 
         "over05_1T":0,"over05_1T_casa":0,"over05_1T_fora":0,"over05_2T":0,"over05_2T_casa":0,"over05_2T_fora":0, 
         "over15_2T":0,"over15_2T_casa":0,"over15_2T_fora":0,"gols_marcados":0,"gols_sofridos":0, 
         "gols_marcados_casa":0,"gols_sofridos_casa":0,"gols_marcados_fora":0,"gols_sofridos_fora":0, 
         "total_gols":0,"total_gols_casa":0,"total_gols_fora":0,"gols_marcados_1T":0,"gols_sofridos_1T":0, 
         "gols_marcados_2T":0,"gols_sofridos_2T":0,"marcou_2_mais":0, "marcou_2_mais_casa":0, "marcou_2_mais_fora":0,
         "sofreu_2_mais":0, "sofreu_2_mais_casa":0, "sofreu_2_mais_fora":0,"marcou_ambos_tempos":0, 
         "marcou_ambos_tempos_casa":0, "marcou_ambos_tempos_fora":0,"sofreu_ambos_tempos":0, 
         "sofreu_ambos_tempos_casa":0, "sofreu_ambos_tempos_fora":0}

    try: linhas = get_sheet_data(aba)
    except: return {"time":time, "jogos_time": 0}

    if casa_fora == "casa": linhas = [l for l in linhas if l['Mandante'] == time]
    elif casa_fora == "fora": linhas = [l for l in linhas if l['Visitante'] == time]
    else: linhas = [l for l in linhas if l['Mandante'] == time or l['Visitante'] == time]

    try: linhas.sort(key=lambda x: datetime.strptime(x['Data'], "%d/%m/%Y"))
    except: pass

    if ultimos: linhas = linhas[-ultimos:]

    for linha in linhas:
        em_casa = (time == linha['Mandante'])
        gm, gv = safe_int(linha['Gols Mandante']), safe_int(linha['Gols Visitante'])
        gm1, gv1 = safe_int(linha['Gols Mandante 1T']), safe_int(linha['Gols Visitante 1T'])
        gm2, gv2 = gm-gm1, gv-gv1 
        total, total1, total2 = gm+gv, gm1+gv1, gm2+gv2
        d["jogos_time"] += 1

        if em_casa:
            marcados, sofridos, m1, s1, m2, s2 = gm, gv, gm1, gv1, gm2, gv2
            d["jogos_casa"] += 1
            d["gols_marcados_casa"] += gm; d["gols_sofridos_casa"] += gv
            d["total_gols_casa"] += total
        else:
            marcados, sofridos, m1, s1, m2, s2 = gv, gm, gv1, gm1, gv2, gm2
            d["jogos_fora"] += 1
            d["gols_marcados_fora"] += gv; d["gols_sofridos_fora"] += gm
            d["total_gols_fora"] += total

        d["gols_marcados"] += marcados; d["gols_sofridos"] += sofridos; d["total_gols"] += total
        d["over15"] += (1 if total > 1.5 else 0); d["over25"] += (1 if total > 2.5 else 0)
        d["btts"] += (1 if gm > 0 and gv > 0 else 0)
        d["over05_1T"] += (1 if total1 > 0.5 else 0); d["over05_2T"] += (1 if total2 > 0.5 else 0)
        d["over15_2T"] += (1 if total2 > 1.5 else 0)
        if total1 > 0 and total2 > 0: d["g_a_t"] += 1; d["g_a_t_casa" if em_casa else "g_a_t_fora"] += 1
        if marcados >= 2: d["marcou_2_mais"] += 1; d["marcou_2_mais_casa" if em_casa else "marcou_2_mais_fora"] += 1
        if sofridos >= 2: d["sofreu_2_mais"] += 1; d["sofreu_2_mais_casa" if em_casa else "sofreu_2_mais_fora"] += 1
        if m1 > 0 and m2 > 0: d["marcou_ambos_tempos"] += 1; d["marcou_ambos_tempos_casa" if em_casa else "marcou_ambos_tempos_fora"] += 1
        if s1 > 0 and s2 > 0: d["sofreu_ambos_tempos"] += 1; d["sofreu_ambos_tempos_casa" if em_casa else "sofreu_ambos_tempos_fora"] += 1
        
        d["over15_casa" if em_casa else "over15_fora"] += (1 if total > 1.5 else 0)
        d["over25_casa" if em_casa else "over25_fora"] += (1 if total > 2.5 else 0)
        d["btts_casa" if em_casa else "btts_fora"] += (1 if gm > 0 and gv > 0 else 0)
        d["over05_1T_casa" if em_casa else "over05_1T_fora"] += (1 if total1 > 0.5 else 0)
        d["over05_2T_casa" if em_casa else "over05_2T_fora"] += (1 if total2 > 0.5 else 0)
        d["over15_2T_casa" if em_casa else "over15_2T_fora"] += (1 if total2 > 1.5 else 0)
        d["gols_marcados_1T"] += m1; d["gols_sofridos_1T"] += s1
        d["gols_marcados_2T"] += m2; d["gols_sofridos_2T"] += s2 
    return d

def formatar_estatisticas(d):
    jt, jc, jf = d["jogos_time"], d.get("jogos_casa", 0), d.get("jogos_fora", 0)
    if jt == 0: return f"⚠️ Sem jogos para **{escape_markdown(d['time'])}**."
    return (f"📊 **Estatísticas - {escape_markdown(d['time'])}**\n"
            f"📅 Jogos: {jt} (C: {jc} | F: {jf})\n\n"
            f"⚽ Over 1.5: **{pct(d['over15'], jt)}** (C: {pct(d['over15_casa'], jc)} | F: {pct(d['over15_fora'], jf)})\n"
            f"⚽ Over 2.5: **{pct(d['over25'], jt)}** (C: {pct(d['over25_casa'], jc)} | F: {pct(d['over25_fora'], jf)})\n"
            f"🔁 BTTS: **{pct(d['btts'], jt)}** (C: {pct(d['btts_casa'], jc)} | F: {pct(d['btts_fora'], jf)})\n"
            f"🥅 G.A.T.: {pct(d['g_a_t'], jt)} (C: {pct(d['g_a_t_casa'], jc)} | F: {pct(d['g_a_t_fora'], jf)})\n"
            f"📈 Marcou 2+: **{pct(d['marcou_2_mais'], jt)}**\n"
            f"📉 Sofreu 2+: **{pct(d['sofreu_2_mais'], jt)}**\n"
            f"⚽ M.A.T.: **{pct(d['marcou_ambos_tempos'], jt)}**\n"
            f"⏱️ 1ºT > 0.5: {pct(d['over05_1T'], jt)} | 2ºT > 0.5: {pct(d['over05_2T'], jt)}\n"
            f"🔢 **Média Total:** {media(d['total_gols'], jt)}")

def listar_ultimos_jogos(time, aba, ultimos=None, casa_fora=None):
    try: linhas = get_sheet_data(aba)
    except: return "⚠️ Erro na planilha."
    if casa_fora == "casa": linhas = [l for l in linhas if l['Mandante'] == time]
    elif casa_fora == "fora": linhas = [l for l in linhas if l['Visitante'] == time]
    else: linhas = [l for l in linhas if l['Mandante'] == time or l['Visitante'] == time]
    try: linhas.sort(key=lambda x: datetime.strptime(x['Data'], "%d/%m/%Y"))
    except: pass
    if ultimos: linhas = linhas[-ultimos:]
    if not linhas: return "Nenhum jogo encontrado."
    res = ""
    for l in linhas:
        gm, gv = safe_int(l['Gols Mandante']), safe_int(l['Gols Visitante'])
        cor = "🟢" if (l['Mandante']==time and gm>gv) or (l['Visitante']==time and gv>gm) else ("🟡" if gm==gv else "🔴")
        res += f"{cor} {l['Data']}: {escape_markdown(l['Mandante'])} {gm}x{gv} {escape_markdown(l['Visitante'])}\n"
    return res

# =================================================================================
# 🤖 HANDLERS E FLUXOS
# =================================================================================
async def start_command(update, context):
    await update.message.reply_text("👋 **Bot de Estatísticas V2.4**\nUse **/stats** para começar.", parse_mode='Markdown')

async def listar_competicoes(update, context):
    kb = [[InlineKeyboardButton(aba, callback_data=f"c|{aba}") for aba in ABAS_PASSADO[i:i+3]] for i in range(0, len(ABAS_PASSADO), 3)]
    await (update.message.reply_text if update.message else update.callback_query.edit_message_text)("🏆 **Escolha a Liga:**", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

async def mostrar_menu_status_jogo(update, context, aba_code):
    kb = [[InlineKeyboardButton("🔴 AO VIVO", callback_data=f"STATUS|LIVE|{aba_code}")],
          [InlineKeyboardButton("📅 PRÓXIMOS", callback_data=f"STATUS|FUTURE|{aba_code}")],
          [InlineKeyboardButton("⬅️ Voltar", callback_data="VOLTAR_LIGA")]]
    await update.callback_query.edit_message_text(f"🎮 **{aba_code}** - Tipo de Partida:", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

async def listar_jogos(update, context, aba_code, status):
    key = f"{aba_code}_jogos_{status.lower()}"
    if status == "FUTURE":
        jogos = get_sheet_data_future(aba_code)
        jogos = [j for j in jogos if datetime.strptime(j['Data_Hora'][:16], '%Y-%m-%dT%H:%M').replace(tzinfo=timezone.utc) > datetime.now(timezone.utc)][:MAX_GAMES_LISTED]
    else:
        jogos = buscar_jogos_live(aba_code)
    
    if not jogos:
        await update.callback_query.edit_message_text("⚠️ Nenhum jogo encontrado.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Voltar", callback_data=f"c|{aba_code}")]]))
        return
    
    context.chat_data[key] = jogos
    kb = [[InlineKeyboardButton(f"{j.get('Tempo_Jogo', j['Data_Hora'][11:16])} | {j['Mandante_Nome']} x {j['Visitante_Nome']}", callback_data=f"JOGO|{aba_code}|{status}|{i}")] for i, j in enumerate(jogos)]
    kb.append([InlineKeyboardButton("⬅️ Voltar", callback_data=f"c|{aba_code}")])
    await update.callback_query.edit_message_text("⚽ **Selecione o Jogo:**", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

async def callback_query_handler(update, context):
    q = update.callback_query; d = q.data
    try:
        if d.startswith("c|"): await mostrar_menu_status_jogo(update, context, d.split('|')[1])
        elif d.startswith("STATUS|"): await listar_jogos(update, context, d.split('|')[2], d.split('|')[1])
        elif d.startswith("JOGO|"):
            _, aba, st, idx = d.split('|'); jogo = context.chat_data[f"{aba}_jogos_{st.lower()}"][int(idx)]
            context.chat_data.update({'current_mandante': jogo['Mandante_Nome'], 'current_visitante': jogo['Visitante_Nome'], 'current_aba_code': aba})
            kb = [[InlineKeyboardButton(f[0], callback_data=f"{f[1]}|{i}")] for i, f in enumerate(CONFRONTO_FILTROS)]
            kb.append([InlineKeyboardButton("⬅️ Voltar", callback_data=f"STATUS|{st}|{aba}")])
            await q.edit_message_text(f"🎯 **{jogo['Mandante_Nome']} x {jogo['Visitante_Nome']}**", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
        elif d.startswith("STATS_FILTRO|"):
            f = CONFRONTO_FILTROS[int(d.split('|')[1])]
            dm = calcular_estatisticas_time(context.chat_data['current_mandante'], context.chat_data['current_aba_code'], f[2], f[3])
            dv = calcular_estatisticas_time(context.chat_data['current_visitante'], context.chat_data['current_aba_code'], f[2], f[4])
            await q.message.reply_text(f"{formatar_estatisticas(dm)}\n\n{formatar_estatisticas(dv)}", parse_mode='Markdown')
        elif d.startswith("RESULTADOS_FILTRO|"):
            f = CONFRONTO_FILTROS[int(d.split('|')[1])]
            rm = listar_ultimos_jogos(context.chat_data['current_mandante'], context.chat_data['current_aba_code'], f[2], f[3])
            rv = listar_ultimos_jogos(context.chat_data['current_visitante'], context.chat_data['current_aba_code'], f[2], f[4])
            await q.message.reply_text(f"📅 **Resultados {context.chat_data['current_mandante']}**\n{rm}\n\n📅 **Resultados {context.chat_data['current_visitante']}**\n{rv}", parse_mode='Markdown')
        elif d == "VOLTAR_LIGA": await listar_competicoes(update, context)
    except: pass

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command)); app.add_handler(CommandHandler("stats", listar_competicoes))
    app.add_handler(CallbackQueryHandler(callback_query_handler))
    if client: 
        app.job_queue.run_repeating(atualizar_planilhas, interval=3600, first=0)
        asyncio.run(pre_carregar_cache_sheets())
    app.run_polling()

if __name__ == "__main__":
    main()
