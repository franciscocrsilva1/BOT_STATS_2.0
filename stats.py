# ===============================================================================
# 🏆 BOT DE ESTATÍSTICAS DE CONFRONTO V2.4.0 - LIGA BSA (FILTROS GERAIS E 10)
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

# Mapeamento de Ligas (Apenas BSA)
LIGAS_MAP = {
    "BSA": {"sheet_past": "BSA", "sheet_future": "BSA_FJ"}
}
ABAS_PASSADO = list(LIGAS_MAP.keys())

ULTIMOS = 10
SHEET_CACHE = {}
CACHE_DURATION_SECONDS = 3600 # 1 hora
MAX_GAMES_LISTED = 30

# Novos Filtros configurados conforme solicitado: GERAIS e ÚLTIMOS 10
CONFRONTO_FILTROS = [
    # === ESTATÍSTICAS ===
    ("📊 Estatísticas | GERAIS", "STATS_FILTRO", None, None, None),
    ("📊 Estatísticas | GERAIS (M CASA vs V FORA)", "STATS_FILTRO", None, "casa", "fora"),
    (f"📊 Estatísticas | ÚLTIMOS {ULTIMOS} GERAL", "STATS_FILTRO", ULTIMOS, None, None),
    (f"📊 Estatísticas | {ULTIMOS} (M CASA vs V FORA)", "STATS_FILTRO", ULTIMOS, "casa", "fora"),

    # === RESULTADOS ===
    ("📅 Resultados | GERAIS", "RESULTADOS_FILTRO", None, None, None),
    ("📅 Resultados | GERAIS (M CASA vs V FORA)", "RESULTADOS_FILTRO", None, "casa", "fora"),
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
        jogos = []
        for row in data_rows:
            if len(row) >= 4:
                jogos.append({"Mandante_Nome": row[0], "Visitante_Nome": row[1], "Data_Hora": row[2], "Matchday": safe_int(row[3])})
        return jogos
    except Exception as e:
        logging.error(f"Erro ao buscar cache futuro: {e}")
        return []

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
        all_matches = r.json().get("matches", [])
    except Exception as e:
        logging.error(f"Erro API: {e}")
        return []

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
        all_matches = r.json().get("matches", [])
    except: return []
    
    jogos = []
    for m in all_matches:
        if m.get('status') in LIVE_STATUSES:
            ft_score = m.get("score", {}).get("fullTime", {})
            minute = m.get("minute", "N/A")
            jogos.append({
                "Mandante_Nome": m.get("homeTeam", {}).get("name", ""),
                "Visitante_Nome": m.get("awayTeam", {}).get("name", ""),
                "Placar_Mandante": ft_score.get("home", 0), "Placar_Visitante": ft_score.get("away", 0),
                "Tempo_Jogo": minute, "Matchday": safe_int(m.get("matchday", 0))
            })
    return jogos

async def atualizar_planilhas(context: ContextTypes.DEFAULT_TYPE):
    global SHEET_CACHE
    if not client: return
    try: sh = client.open_by_url(SHEET_URL)
    except: return

    for aba_code, aba_config in LIGAS_MAP.items():
        ws_past = sh.worksheet(aba_config['sheet_past'])
        jogos_finished = buscar_jogos(aba_code, "FINISHED")
        if jogos_finished:
            exist = ws_past.get_all_records()
            keys_exist = {(r['Mandante'], r['Visitante'], r['Data']) for r in exist}
            novas = [[j["Mandante"], j["Visitante"], j["Gols Mandante"], j["Gols Visitante"], j["Gols Mandante 1T"], j["Gols Visitante 1T"], j["Gols Mandante 2T"], j["Gols Visitante 2T"], j["Data"]] for j in jogos_finished if (j["Mandante"], j["Visitante"], j["Data"]) not in keys_exist]
            if novas: ws_past.append_rows(novas)
            if aba_config['sheet_past'] in SHEET_CACHE: del SHEET_CACHE[aba_config['sheet_past']]
        
        ws_future = sh.worksheet(aba_config['sheet_future'])
        jogos_future = buscar_jogos(aba_code, "ALL")
        ws_future.clear()
        ws_future.update(values=[['Mandante', 'Visitante', 'Data/Hora', 'Matchday']], range_name='A1:D1')
        if jogos_future:
            linhas = [[m.get("homeTeam", {}).get("name"), m.get("awayTeam", {}).get("name"), m.get("utcDate", ""), m.get("matchday", "")] for m in jogos_future]
            ws_future.append_rows(linhas, value_input_option='USER_ENTERED')
        await asyncio.sleep(2)

# =================================================================================
# 📈 FUNÇÕES DE CÁLCULO
# =================================================================================
def calcular_estatisticas_time(time, aba, ultimos=None, casa_fora=None):
    d = {"time":time,"jogos_time":0,"jogos_casa":0,"jogos_fora":0, "over15":0,"over15_casa":0,"over15_fora":0, "over25":0,"over25_casa":0,"over25_fora":0, "btts":0,"btts_casa":0,"btts_fora":0, "g_a_t":0,"g_a_t_casa":0,"g_a_t_fora":0, "over05_1T":0,"over05_1T_casa":0,"over05_1T_fora":0, "over05_2T":0,"over05_2T_casa":0,"over05_2T_fora":0, "over15_2T":0,"over15_2T_casa":0,"over15_2T_fora":0, "gols_marcados":0,"gols_sofridos":0, "gols_marcados_casa":0,"gols_sofridos_casa":0, "gols_marcados_fora":0,"gols_sofridos_fora":0, "total_gols":0,"total_gols_casa":0,"total_gols_fora":0, "gols_marcados_1T":0,"gols_sofridos_1T":0, "gols_marcados_2T":0,"gols_sofridos_2T":0, "gols_marcados_1T_casa":0,"gols_sofridos_1T_casa":0, "gols_marcados_1T_fora":0,"gols_sofridos_1T_fora":0, "gols_marcados_2T_casa":0,"gols_sofridos_2T_casa":0, "gols_marcados_2T_fora":0,"gols_sofridos_2T_fora":0, "marcou_2_mais":0, "marcou_2_mais_casa":0, "marcou_2_mais_fora":0, "sofreu_2_mais":0, "sofreu_2_mais_casa":0, "sofreu_2_mais_fora":0, "marcou_ambos_tempos":0, "marcou_ambos_tempos_casa":0, "marcou_ambos_tempos_fora":0, "sofreu_ambos_tempos":0, "sofreu_ambos_tempos_casa":0, "sofreu_ambos_tempos_fora":0}
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
            marcados, sofridos = gm, gv
            d["jogos_casa"] += 1
            d["gols_marcados_casa"] += gm; d["gols_sofridos_casa"] += gv
            d["gols_marcados_1T_casa"] += gm1; d["gols_sofridos_1T_casa"] += gv1
            d["gols_marcados_2T_casa"] += gm2; d["gols_sofridos_2T_casa"] += gv2
            tm1, ts1, tm2, ts2 = gm1, gv1, gm2, gv2
        else:
            marcados, sofridos = gv, gm
            d["jogos_fora"] += 1
            d["gols_marcados_fora"] += gv; d["gols_sofridos_fora"] += gm
            d["gols_marcados_1T_fora"] += gv1; d["gols_sofridos_1T_fora"] += gm1
            d["gols_marcados_2T_fora"] += gv2; d["gols_sofridos_2T_fora"] += gm2
            tm1, ts1, tm2, ts2 = gv1, gm1, gv2, gm2

        d["gols_marcados"] += marcados; d["gols_sofridos"] += sofridos
        d["total_gols"] += total
        if total>1.5: d["over15"] += 1
        if total>2.5: d["over25"] += 1
        if gm>0 and gv>0: d["btts"] += 1
        if total1>0.5: d["over05_1T"] += 1
        if total2>0.5: d["over05_2T"] += 1
        if total2>1.5: d["over15_2T"] += 1
        if total1>0 and total2>0: d["g_a_t"] += 1; d["g_a_t_casa" if em_casa else "g_a_t_fora"] += 1
        if marcados >= 2: d["marcou_2_mais"] += 1; d["marcou_2_mais_casa" if em_casa else "marcou_2_mais_fora"] += 1
        if sofridos >= 2: d["sofreu_2_mais"] += 1; d["sofreu_2_mais_casa" if em_casa else "sofreu_2_mais_fora"] += 1
        if tm1 > 0 and tm2 > 0: d["marcou_ambos_tempos"] += 1; d["marcou_ambos_tempos_casa" if em_casa else "marcou_ambos_tempos_fora"] += 1
        if ts1 > 0 and ts2 > 0: d["sofreu_ambos_tempos"] += 1; d["sofreu_ambos_tempos_casa" if em_casa else "sofreu_ambos_tempos_fora"] += 1

        d["over15_casa" if em_casa else "over15_fora"] += (1 if total > 1.5 else 0)
        d["over25_casa" if em_casa else "over25_fora"] += (1 if total > 2.5 else 0)
        d["btts_casa" if em_casa else "btts_fora"] += (1 if gm > 0 and gv > 0 else 0)
        d["over05_1T_casa" if em_casa else "over05_1T_fora"] += (1 if total1 > 0.5 else 0)
        d["over05_2T_casa" if em_casa else "over05_2T_fora"] += (1 if total2 > 0.5 else 0)
        d["over15_2T_casa" if em_casa else "over15_2T_fora"] += (1 if total2 > 1.5 else 0)
        d["gols_marcados_1T"] += tm1; d["gols_sofridos_1T"] += ts1
        d["gols_marcados_2T"] += tm2; d["gols_sofridos_2T"] += ts2 
    return d

def formatar_estatisticas(d):
    jt, jc, jf = d["jogos_time"], d["jogos_casa"], d["jogos_fora"]
    if jt == 0: return f"⚠️ Sem dados para **{escape_markdown(d['time'])}**."
    return (f"📊 **Estatísticas - {escape_markdown(d['time'])}**\n"
            f"📅 Jogos: {jt} (C: {jc} | F: {jf})\n\n"
            f"⚽ Over 1.5: **{pct(d['over15'], jt)}** (C: {pct(d['over15_casa'], jc)} | F: {pct(d['over15_fora'], jf)})\n"
            f"⚽ Over 2.5: **{pct(d['over25'], jt)}** (C: {pct(d['over25_casa'], jc)} | F: {pct(d['over25_fora'], jf)})\n"
            f"🔁 BTTS: **{pct(d['btts'], jt)}** (C: {pct(d['btts_casa'], jc)} | F: {pct(d['btts_fora'], jf)})\n"
            f"🥅 G.A.T.: {pct(d['g_a_t'], jt)} (C: {pct(d['g_a_t_casa'], jc)} | F: {pct(d['g_a_t_fora'], jf)})\n"
            f"📈 Marcou 2+: **{pct(d['marcou_2_mais'], jt)}**\n"
            f"📉 Sofreu 2+: **{pct(d['sofreu_2_mais'], jt)}**\n"
            f"⚽ M.A.T.: **{pct(d['marcou_ambos_tempos'], jt)}**\n"
            f"🥅 S.A.T.: **{pct(d['sofreu_ambos_tempos'], jt)}**\n\n"
            f"⏱️ 1ºT > 0.5: {pct(d['over05_1T'], jt)} | 2ºT > 0.5: {pct(d['over05_2T'], jt)}\n"
            f"➕ Média GP: {media(d['gols_marcados'], jt)} | GC: {media(d['gols_sofridos'], jt)}\n"
            f"🔢 Média Total: {media(d['total_gols'], jt)}")

def listar_ultimos_jogos(time, aba, ultimos=None, casa_fora=None):
    try: linhas = get_sheet_data(aba)
    except: return "⚠️ Erro na planilha."
    if casa_fora == "casa": linhas = [l for l in linhas if l['Mandante'] == time]
    elif casa_fora == "fora": linhas = [l for l in linhas if l['Visitante'] == time]
    else: linhas = [l for l in linhas if l['Mandante'] == time or l['Visitante'] == time]
    try: linhas.sort(key=lambda x: datetime.strptime(x['Data'], "%d/%m/%Y"))
    except: pass
    if ultimos: linhas = linhas[-ultimos:]
    if not linhas: return "Nenhum jogo."
    res = ""
    for l in linhas:
        gm, gv = safe_int(l['Gols Mandante']), safe_int(l['Gols Visitante'])
        if l['Mandante'] == time:
            cor = "🟢" if gm > gv else ("🟡" if gm == gv else "🔴")
            res += f"{cor} {l['Data']} (C): **{time}** {gm}x{gv} {l['Visitante']}\n"
        else:
            cor = "🟢" if gv > gm else ("🟡" if gv == gm else "🔴")
            res += f"{cor} {l['Data']} (F): {l['Mandante']} {gm}x{gv} **{time}**\n"
    return res

# =================================================================================
# 🤖 HANDLERS DO BOT
# =================================================================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📊 **Bot de Estatísticas BSA**\nUse **/stats** para começar.", parse_mode='Markdown')

async def listar_competicoes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton(aba, callback_data=f"c|{aba}")] for aba in LIGAS_MAP.keys()]
    await update.effective_message.reply_text("Escolha a Competição:", reply_markup=InlineKeyboardMarkup(kb))

async def mostrar_menu_status_jogo(update: Update, context: ContextTypes.DEFAULT_TYPE, aba_code: str):
    kb = [[InlineKeyboardButton("🔴 AO VIVO", callback_data=f"STATUS|LIVE|{aba_code}")],
          [InlineKeyboardButton("📅 PRÓXIMOS JOGOS", callback_data=f"STATUS|FUTURE|{aba_code}")],
          [InlineKeyboardButton("⬅️ Voltar", callback_data="VOLTAR_LIGA")]]
    await update.callback_query.edit_message_text(f"**{aba_code}** - Opções:", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

async def listar_jogos(update: Update, context: ContextTypes.DEFAULT_TYPE, aba_code: str, status: str):
    if status == "FUTURE":
        jogos = get_sheet_data_future(aba_code)
        label_func = lambda j: f"{j['Data_Hora'][11:16]} | {j['Mandante_Nome']} x {j['Visitante_Nome']}"
    else:
        jogos = buscar_jogos_live(aba_code)
        label_func = lambda j: f"🔴 {j['Tempo_Jogo']} | {j['Mandante_Nome']} {j['Placar_Mandante']}x{j['Placar_Visitante']} {j['Visitante_Nome']}"
    
    if not jogos:
        await update.callback_query.answer("Nenhum jogo encontrado.")
        return

    context.chat_data[f"{aba_code}_jogos_{status.lower()}"] = jogos
    kb = [[InlineKeyboardButton(label_func(j), callback_data=f"JOGO|{aba_code}|{status}|{i}")] for i, j in enumerate(jogos[:MAX_GAMES_LISTED])]
    kb.append([InlineKeyboardButton("⬅️ Voltar", callback_data=f"STATUS_VOLTAR|{aba_code}")])
    await update.callback_query.edit_message_text("Selecione a partida:", reply_markup=InlineKeyboardMarkup(kb))

async def mostrar_menu_acoes(update: Update, context: ContextTypes.DEFAULT_TYPE, aba_code: str, mandante: str, visitante: str):
    kb = [[InlineKeyboardButton(f[0], callback_data=f"{f[1]}|{i}")] for i, f in enumerate(CONFRONTO_FILTROS)]
    kb.append([InlineKeyboardButton("⬅️ Voltar", callback_data=f"STATUS_VOLTAR|{aba_code}")])
    await update.effective_message.reply_text(f"Filtros: **{mandante} x {visitante}**", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    if data.startswith("c|"): await mostrar_menu_status_jogo(update, context, data.split('|')[1])
    elif data.startswith("STATUS|"): await listar_jogos(update, context, data.split('|')[2], data.split('|')[1])
    elif data.startswith("STATUS_VOLTAR|"): await mostrar_menu_status_jogo(update, context, data.split('|')[1])
    elif data.startswith("JOGO|"):
        _, aba, status, idx = data.split('|')
        jogo = context.chat_data[f"{aba}_jogos_{status.lower()}"][int(idx)]
        context.chat_data.update({'current_mandante': jogo['Mandante_Nome'], 'current_visitante': jogo['Visitante_Nome'], 'current_aba_code': aba})
        await mostrar_menu_acoes(update, context, aba, jogo['Mandante_Nome'], jogo['Visitante_Nome'])
    elif data.startswith("STATS_FILTRO|") or data.startswith("RESULTADOS_FILTRO|"):
        idx = int(data.split('|')[1])
        m, v, aba = context.chat_data['current_mandante'], context.chat_data['current_visitante'], context.chat_data['current_aba_code']
        _, tipo, q, cm, cv = CONFRONTO_FILTROS[idx]
        if "STATS" in data:
            txt = formatar_estatisticas(calcular_estatisticas_time(m, aba, q, cm)) + "\n\n" + formatar_estatisticas(calcular_estatisticas_time(v, aba, q, cv))
        else:
            txt = f"📅 **Resultados - {m}**\n" + listar_ultimos_jogos(m, aba, q, cm) + f"\n\n📅 **Resultados - {v}**\n" + listar_ultimos_jogos(v, aba, q, cv)
        await update.effective_message.reply_text(txt, parse_mode='Markdown')
        await mostrar_menu_acoes(update, context, aba, m, v)
    elif data == "VOLTAR_LIGA": await listar_competicoes(update, context)

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command)); app.add_handler(CommandHandler("stats", listar_competicoes))
    app.add_handler(CallbackQueryHandler(callback_query_handler))
    if client: app.job_queue.run_repeating(atualizar_planilhas, interval=3600, first=0)
    app.run_polling()

if __name__ == "__main__":
    main()
