# ===============================================================================
# 🏆 BOT DE ESTATÍSTICAS DE CONFRONTO V2.4.1 - LIGAS EXPANDIDAS E FILTROS TOTAIS
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

# Mapeamento de Ligas Expandido
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
MAX_GAMES_LISTED = 30

# Filtros Reutilizáveis (Estatísticas e Resultados respeitando a lógica solicitada)
CONFRONTO_FILTROS = [
    # --- BLOCO ÚLTIMOS 10 ---
    (f"📊 Estatísticas | ÚLTIMOS {ULTIMOS} GERAL", "STATS_FILTRO", ULTIMOS, None, None),
    (f"📊 Estatísticas | {ULTIMOS} (M CASA vs V FORA)", "STATS_FILTRO", ULTIMOS, "casa", "fora"),
    (f"📅 Resultados | ÚLTIMOS {ULTIMOS} GERAL", "RESULTADOS_FILTRO", ULTIMOS, None, None),
    (f"📅 Resultados | {ULTIMOS} (M CASA vs V FORA)", "RESULTADOS_FILTRO", ULTIMOS, "casa", "fora"),
    # --- BLOCO TODOS OS JOGOS ---
    ("📊 Estatísticas | TODOS OS JOGOS GERAIS", "STATS_FILTRO", None, None, None),
    ("📊 Estatísticas | TODOS MANDANTE CASA", "STATS_FILTRO", None, "casa", None),
    ("📊 Estatísticas | TODOS VISITANTE FORA", "STATS_FILTRO", None, None, "fora"),
    ("📅 Resultados | TODOS OS JOGOS GERAIS", "RESULTADOS_FILTRO", None, None, None),
    ("📅 Resultados | TODOS MANDANTE CASA", "RESULTADOS_FILTRO", None, "casa", None),
    ("📅 Resultados | TODOS VISITANTE FORA", "RESULTADOS_FILTRO", None, None, "fora"),
]

LIVE_STATUSES = ["IN_PLAY", "HALF_TIME", "PAUSED"]

# =================================================================================
# ✅ CONEXÃO GSHEETS
# =================================================================================
CREDS_JSON = os.environ.get("GSPREAD_CREDS_JSON")
client = None

if not CREDS_JSON:
    logging.error("❌ ERRO: GSPREAD_CREDS_JSON não configurada.")
else:
    try:
        with tempfile.NamedTemporaryFile(mode="w", delete=False, encoding='utf-8') as tmp_file:
            tmp_file.write(CREDS_JSON)
            tmp_file_path = tmp_file.name
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name(tmp_file_path, scope)
        client = gspread.authorize(creds)
        os.remove(tmp_file_path)
    except Exception as e:
        logging.error(f"❌ ERRO DE AUTORIZAÇÃO: {e}")

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
    if not client: return []
    try:
        sh = client.open_by_url(SHEET_URL)
        linhas = sh.worksheet(aba_name).get_all_records()
        SHEET_CACHE[aba_name] = { 'data': linhas, 'timestamp': agora }
        return linhas
    except: return []

def get_sheet_data_future(aba_code):
    aba_name = LIGAS_MAP[aba_code]['sheet_future']
    if not client: return []
    try:
        sh = client.open_by_url(SHEET_URL)
        linhas_raw = sh.worksheet(aba_name).get_all_values()
        if not linhas_raw or len(linhas_raw) <= 1: return []
        return [{"Mandante_Nome": r[0], "Visitante_Nome": r[1], "Data_Hora": r[2], "Matchday": safe_int(r[3])} for r in linhas_raw[1:] if len(r) >= 4]
    except: return []

# =================================================================================
# 🎯 FUNÇÕES DE API E ATUALIZAÇÃO
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
                ft = m.get("score", {}).get("fullTime", {}); ht = m.get("score", {}).get("halfTime", {})
                if ft.get("home") is None: continue
                gm, gv = ft.get("home", 0), ft.get("away", 0)
                gm1, gv1 = ht.get("home", 0), ht.get("away", 0)
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
    hoje = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    try:
        url = f"https://api.football-data.org/v4/competitions/{league_code}/matches?dateFrom={hoje}&dateTo={hoje}"
        r = requests.get(url, headers={"X-Auth-Token": API_KEY}, timeout=10)
        matches = r.json().get("matches", [])
        jogos = []
        for m in matches:
            if m.get('status') in LIVE_STATUSES:
                ft = m.get("score", {}).get("fullTime", {})
                minuto = m.get("minute", "N/A")
                if m.get('status') == 'HALF_TIME': minuto = "Intervalo"
                jogos.append({
                    "Mandante_Nome": m.get("homeTeam", {}).get("name", ""),
                    "Visitante_Nome": m.get("awayTeam", {}).get("name", ""),
                    "Placar_Mandante": ft.get("home", 0), "Placar_Visitante": ft.get("away", 0),
                    "Tempo_Jogo": minuto, "Matchday": m.get("matchday", 0)
                })
        return jogos
    except: return []

async def atualizar_planilhas(context: ContextTypes.DEFAULT_TYPE = None):
    if not client: return
    try: sh = client.open_by_url(SHEET_URL)
    except: return
    for aba_code, aba_config in LIGAS_MAP.items():
        try:
            # Histórico (Passado)
            ws_past = sh.worksheet(aba_config['sheet_past'])
            jogos_f = buscar_jogos(aba_code, "FINISHED")
            if jogos_f:
                exist = ws_past.get_all_records()
                keys = {(r['Mandante'], r['Visitante'], r['Data']) for r in exist}
                novos = [[j["Mandante"], j["Visitante"], j["Gols Mandante"], j["Gols Visitante"], j["Gols Mandante 1T"], j["Gols Visitante 1T"], j["Gols Mandante 2T"], j["Gols Visitante 2T"], j["Data"]] for j in jogos_f if (j["Mandante"], j["Visitante"], j["Data"]) not in keys]
                if novos: ws_past.append_rows(novos)
            
            # Próximos (Futuros)
            ws_future = sh.worksheet(aba_config['sheet_future'])
            jogos_a = buscar_jogos(aba_code, "ALL")
            ws_future.clear()
            ws_future.update(values=[['Mandante', 'Visitante', 'Data/Hora', 'Matchday']], range_name='A1:D1')
            if jogos_a:
                linhas = [[m.get("homeTeam", {}).get("name"), m.get("awayTeam", {}).get("name"), m.get('utcDate'), m.get("matchday")] for m in jogos_a]
                ws_future.append_rows(linhas, value_input_option='USER_ENTERED')
            await asyncio.sleep(2)
        except: continue

# =================================================================================
# 📈 CÁLCULOS E FORMATAÇÃO
# =================================================================================
def calcular_estatisticas_time(time, aba, ultimos=None, casa_fora=None):
    d = {"time":time,"jogos_time":0,"jogos_casa":0,"jogos_fora":0,"over15":0,"over15_casa":0,"over15_fora":0, "over25":0,"over25_casa":0,"over25_fora":0,"btts":0,"btts_casa":0,"btts_fora":0, "g_a_t":0,"g_a_t_casa":0,"g_a_t_fora":0, "over05_1T":0,"over05_1T_casa":0,"over05_1T_fora":0,"over05_2T":0,"over05_2T_casa":0,"over05_2T_fora":0, "over15_2T":0,"over15_2T_casa":0,"over15_2T_fora":0,"gols_marcados":0,"gols_sofridos":0, "gols_marcados_casa":0,"gols_sofridos_casa":0,"gols_marcados_fora":0,"gols_sofridos_fora":0, "total_gols":0,"total_gols_casa":0,"total_gols_fora":0,"gols_marcados_1T":0,"gols_sofridos_1T":0, "gols_marcados_2T":0,"gols_sofridos_2T":0,"marcou_2_mais":0, "marcou_2_mais_casa":0, "marcou_2_mais_fora":0,"sofreu_2_mais":0, "sofreu_2_mais_casa":0, "sofreu_2_mais_fora":0,"marcou_ambos_tempos":0, "marcou_ambos_tempos_casa":0, "marcou_ambos_tempos_fora":0,"sofreu_ambos_tempos":0, "sofreu_ambos_tempos_casa":0, "sofreu_ambos_tempos_fora":0}
    linhas = get_sheet_data(aba)
    if not linhas: return d
    
    if casa_fora == "casa": filtered = [l for l in linhas if l['Mandante'] == time]
    elif casa_fora == "fora": filtered = [l for l in linhas if l['Visitante'] == time]
    else: filtered = [l for l in linhas if l['Mandante'] == time or l['Visitante'] == time]
    
    try: filtered.sort(key=lambda x: datetime.strptime(x['Data'], "%d/%m/%Y"))
    except: pass
    
    if ultimos: filtered = filtered[-ultimos:]
    
    for l in filtered:
        em_casa = (time == l['Mandante'])
        gm, gv = safe_int(l['Gols Mandante']), safe_int(l['Gols Visitante'])
        gm1, gv1 = safe_int(l['Gols Mandante 1T']), safe_int(l['Gols Visitante 1T'])
        gm2, gv2 = gm-gm1, gv-gv1
        total, t1, t2 = gm+gv, gm1+gv1, gm2+gv2
        
        d["jogos_time"] += 1
        if em_casa: 
            d["jogos_casa"] += 1
            marc, sofr = gm, gv; m1, s1 = gm1, gv1; m2, s2 = gm2, gv2
            d["total_gols_casa"] += total; d["gols_marcados_casa"] += gm; d["gols_sofridos_casa"] += gv
        else: 
            d["jogos_fora"] += 1
            marc, sofr = gv, gm; m1, s1 = gv1, gm1; m2, s2 = gv2, gm2
            d["total_gols_fora"] += total; d["gols_marcados_fora"] += gv; d["gols_sofridos_fora"] += gm

        d["total_gols"] += total; d["gols_marcados"] += marc; d["gols_sofridos"] += sofr
        d["gols_marcados_1T"] += m1; d["gols_sofridos_1T"] += s1
        d["gols_marcados_2T"] += m2; d["gols_sofridos_2T"] += s2
        
        if total > 1.5: d["over15"] += 1; d["over15_casa" if em_casa else "over15_fora"] += 1
        if total > 2.5: d["over25"] += 1; d["over25_casa" if em_casa else "over25_fora"] += 1
        if gm > 0 and gv > 0: d["btts"] += 1; d["btts_casa" if em_casa else "btts_fora"] += 1
        if t1 > 0.5: d["over05_1T"] += 1; d["over05_1T_casa" if em_casa else "over05_1T_fora"] += 1
        if t2 > 0.5: d["over05_2T"] += 1; d["over05_2T_casa" if em_casa else "over05_2T_fora"] += 1
        if t2 > 1.5: d["over15_2T"] += 1; d["over15_2T_casa" if em_casa else "over15_2T_fora"] += 1
        if t1 > 0 and t2 > 0: d["g_a_t"] += 1; d["g_a_t_casa" if em_casa else "g_a_t_fora"] += 1
        if marc >= 2: d["marcou_2_mais"] += 1; d["marcou_2_mais_casa" if em_casa else "marcou_2_mais_fora"] += 1
        if sofr >= 2: d["sofreu_2_mais"] += 1; d["sofreu_2_mais_casa" if em_casa else "sofreu_2_mais_fora"] += 1
        if m1 > 0 and m2 > 0: d["marcou_ambos_tempos"] += 1; d["marcou_ambos_tempos_casa" if em_casa else "marcou_ambos_tempos_fora"] += 1
        if s1 > 0 and s2 > 0: d["sofreu_ambos_tempos"] += 1; d["sofreu_ambos_tempos_casa" if em_casa else "sofreu_ambos_tempos_fora"] += 1
    return d

def formatar_estatisticas(d):
    jt, jc, jf = d["jogos_time"], d.get("jogos_casa", 0), d.get("jogos_fora", 0)
    if jt == 0: return f"⚠️ Sem dados para **{escape_markdown(d['time'])}**."
    return (f"📊 **{escape_markdown(d['time'])}**\n📅 Jogos: {jt} (C: {jc} | F: {jf})\n\n"
            f"⚽ Over 1.5: **{pct(d['over15'], jt)}** (C: {pct(d['over15_casa'], jc)} | F: {pct(d['over15_fora'], jf)})\n"
            f"⚽ Over 2.5: **{pct(d['over25'], jt)}** (C: {pct(d['over25_casa'], jc)} | F: {pct(d['over25_fora'], jf)})\n"
            f"🔁 BTTS: **{pct(d['btts'], jt)}** (C: {pct(d['btts_casa'], jc)} | F: {pct(d['btts_fora'], jf)})\n"
            f"🥅 G.A.T.: {pct(d['g_a_t'], jt)} (C: {pct(d['g_a_t_casa'], jc)} | F: {pct(d['g_a_t_fora'], jf)})\n"
            f"📈 Marcou 2+: **{pct(d['marcou_2_mais'], jt)}**\n📉 Sofreu 2+: **{pct(d['sofreu_2_mais'], jt)}**\n\n"
            f"🔢 **Média Gols:** {media(d['total_gols'], jt)} (C: {media(d['total_gols_casa'], jc)} | F: {media(d['total_gols_fora'], jf)})")

def listar_ultimos_jogos(time, aba, ultimos=None, casa_fora=None):
    linhas = get_sheet_data(aba)
    if not linhas: return "⚠️ Sem dados."
    if casa_fora == "casa": filtered = [l for l in linhas if l['Mandante'] == time]
    elif casa_fora == "fora": filtered = [l for l in linhas if l['Visitante'] == time]
    else: filtered = [l for l in linhas if l['Mandante'] == time or l['Visitante'] == time]
    
    try: filtered.sort(key=lambda x: datetime.strptime(x['Data'], "%d/%m/%Y"))
    except: pass
    
    if ultimos: filtered = filtered[-ultimos:]
    if not filtered: return "Nenhum jogo encontrado."
    
    txt = ""
    for l in filtered:
        gm, gv = safe_int(l['Gols Mandante']), safe_int(l['Gols Visitante'])
        res = "🟢" if (l['Mandante'] == time and gm > gv) or (l['Visitante'] == time and gv > gm) else ("🟡" if gm == gv else "🔴")
        txt += f"{res} {l['Data']}: {escape_markdown(l['Mandante'])} {gm}x{gv} {escape_markdown(l['Visitante'])}\n"
    return txt

# =================================================================================
# 🤖 HANDLERS DO TELEGRAM
# =================================================================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Use **/stats** para escolher uma liga.", parse_mode='Markdown')

async def forcaupdate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⚡️ **Sincronizando todas as ligas agora...**", parse_mode='Markdown')
    try:
        await atualizar_planilhas() 
        await msg.edit_text("✅ Sincronização concluída com sucesso!")
    except Exception as e:
        await msg.edit_text(f"❌ Erro na sincronização: {str(e)}")

async def listar_competicoes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = []
    abas = list(LIGAS_MAP.keys())
    for i in range(0, len(abas), 3):
        row = [InlineKeyboardButton(a, callback_data=f"c|{a}") for a in abas[i:i+3]]
        keyboard.append(row)
    reply = InlineKeyboardMarkup(keyboard)
    if update.message: await update.message.reply_text("🏆 **Selecione a Competição:**", reply_markup=reply, parse_mode='Markdown')
    else: await update.callback_query.edit_message_text("🏆 **Selecione a Competição:**", reply_markup=reply, parse_mode='Markdown')

async def mostrar_menu_status_jogo(update: Update, context: ContextTypes.DEFAULT_TYPE, aba_code: str):
    keyboard = [[InlineKeyboardButton("🔴 AO VIVO", callback_data=f"STATUS|LIVE|{aba_code}")],
                [InlineKeyboardButton("📅 PRÓXIMOS", callback_data=f"STATUS|FUTURE|{aba_code}")],
                [InlineKeyboardButton("⬅️ Voltar", callback_data="VOLTAR_LIGA")]]
    await update.callback_query.edit_message_text(f"**Liga {aba_code}** - Escolha:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def listar_jogos(update: Update, context: ContextTypes.DEFAULT_TYPE, aba_code: str, status: str):
    if status == "FUTURE":
        jogos = get_sheet_data_future(aba_code)
        jogos = [j for j in jogos if datetime.strptime(j['Data_Hora'][:16], '%Y-%m-%dT%H:%M').replace(tzinfo=timezone.utc) > datetime.now(timezone.utc)][:MAX_GAMES_LISTED]
    else: jogos = buscar_jogos_live(aba_code)
    
    if not jogos:
        await update.callback_query.edit_message_text(f"⚠️ Sem jogos {status} agora.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Voltar", callback_data=f"c|{aba_code}")]]))
        return
    
    context.chat_data[f"{aba_code}_{status}"] = jogos
    keyboard = []
    for idx, j in enumerate(jogos):
        label = f"{j['Mandante_Nome']} x {j['Visitante_Nome']}"
        if status == "LIVE": label = f"🔴 {j['Tempo_Jogo']} | " + label
        keyboard.append([InlineKeyboardButton(label, callback_data=f"JOGO|{aba_code}|{status}|{idx}")])
    keyboard.append([InlineKeyboardButton("⬅️ Voltar", callback_data=f"c|{aba_code}")])
    await update.callback_query.edit_message_text("⚽ **Selecione a Partida:**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    if data.startswith("c|"): await mostrar_menu_status_jogo(update, context, data.split('|')[1])
    elif data.startswith("STATUS|"): await listar_jogos(update, context, data.split('|')[2], data.split('|')[1])
    elif data.startswith("JOGO|"):
        _, aba, stat, idx = data.split('|')
        jogo = context.chat_data[f"{aba}_{stat}"][int(idx)]
        context.chat_data.update({'m': jogo['Mandante_Nome'], 'v': jogo['Visitante_Nome'], 'aba': aba, 'stat': stat})
        keyboard = []
        for i, f in enumerate(CONFRONTO_FILTROS):
            keyboard.append([InlineKeyboardButton(f[0], callback_data=f"FILTRO|{i}")])
        keyboard.append([InlineKeyboardButton("⬅️ Voltar para Jogos", callback_data=f"STATUS|{stat}|{aba}")])
        await query.edit_message_text(f"📊 **{escape_markdown(jogo['Mandante_Nome'])} x {escape_markdown(jogo['Visitante_Nome'])}**\nEscolha o filtro:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    
    elif data.startswith("FILTRO|"):
        idx = int(data.split('|')[1])
        f_info = CONFRONTO_FILTROS[idx]
        m, v, aba = context.chat_data['m'], context.chat_data['v'], context.chat_data['aba']
        
        if f_info[1] == "STATS_FILTRO":
            dm = calcular_estatisticas_time(m, aba, f_info[2], f_info[3])
            dv = calcular_estatisticas_time(v, aba, f_info[2], f_info[4])
            await query.message.reply_text(f"{formatar_estatisticas(dm)}\n\n---\n\n{formatar_estatisticas(dv)}", parse_mode='Markdown')
        else:
            rm = listar_ultimos_jogos(m, aba, f_info[2], f_info[3])
            rv = listar_ultimos_jogos(v, aba, f_info[2], f_info[4])
            await query.message.reply_text(f"📅 **Resultados {m}**\n{rm}\n\n📅 **Resultados {v}**\n{rv}", parse_mode='Markdown')
            
    elif data == "VOLTAR_LIGA": await listar_competicoes(update, context)

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("stats", listar_competicoes))
    app.add_handler(CommandHandler("forcaupdate", forcaupdate_command))
    app.add_handler(CallbackQueryHandler(callback_query_handler))
    
    if client:
        app.job_queue.run_repeating(atualizar_planilhas, interval=3600, first=10)
    
    app.run_polling()

if __name__ == "__main__":
    main()
