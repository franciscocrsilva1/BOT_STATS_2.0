# ===============================================================================
# 🏆 BOT DE ESTATÍSTICAS DE CONFRONTO V2.4.0 - OTIMIZADO AO VIVO
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

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
nest_asyncio.apply()

BOT_TOKEN = os.environ.get("BOT_TOKEN", "SEU_TOKEN_AQUI") 
API_KEY = os.environ.get("API_KEY", "SUA_API_KEY_AQUI")
SHEET_URL = os.environ.get("SHEET_URL", "https://docs.google.com/spreadsheets/d/1ChFFXQxo1qQElNzh2OC8-UPgofRXxyVWN06ExBQ3YqY/edit?usp=drivesdk")

# Mapeamento de Ligas (Apenas BL1, BSA e PL conforme solicitado)
LIGAS_MAP = {
    "BL1": {"sheet_past": "BL1", "sheet_future": "BL1_FJ"},
    "BSA": {"sheet_past": "BSA", "sheet_future": "BSA_FJ"},
    "PL": {"sheet_past": "PL", "sheet_future": "PL_FJ"},
}
ABAS_PASSADO = list(LIGAS_MAP.keys())

ULTIMOS = 10
SHEET_CACHE = {}
CACHE_DURATION_SECONDS = 3600 
MAX_GAMES_LISTED = 30

CONFRONTO_FILTROS = [
    (f"📊 Estatísticas | ÚLTIMOS {ULTIMOS} GERAL", "STATS_FILTRO", ULTIMOS, None, None),
    (f"📊 Estatísticas | {ULTIMOS} (M CASA vs V FORA)", "STATS_FILTRO", ULTIMOS, "casa", "fora"),
    (f"📅 Resultados | ÚLTIMOS {ULTIMOS} GERAL", "RESULTADOS_FILTRO", ULTIMOS, None, None),
    (f"📅 Resultados | {ULTIMOS} (M CASA vs V FORA)", "RESULTADOS_FILTRO", ULTIMOS, "casa", "fora"),
]

# Melhoria: Inclusão de Prorrogação e Pênaltis
LIVE_STATUSES = ["IN_PLAY", "HALF_TIME", "PAUSED", "EXTRA_TIME", "PENALTY_SHOOTOUT"]

# ... (Funções de conexão GSHEET e suporte permanecem idênticas) ...

CREDS_JSON = os.environ.get("GSPREAD_CREDS_JSON")
client = None

if not CREDS_JSON:
    logging.error("❌ ERRO DE AUTORIZAÇÃO GSHEET")
else:
    try:
        with tempfile.NamedTemporaryFile(mode="w", delete=False, encoding='utf-8') as tmp_file:
            tmp_file.write(CREDS_JSON)
            tmp_file_path = tmp_file.name
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name(tmp_file_path, scope)
        client = gspread.authorize(creds)
        os.remove(tmp_file_path)
    except Exception as e: logging.error(f"Erro credenciais: {e}")

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
        jogos = []
        for row in linhas_raw[1:]:
            if len(row) >= 4:
                jogos.append({"Mandante_Nome": row[0], "Visitante_Nome": row[1], "Data_Hora": row[2], "Matchday": safe_int(row[3])})
        return jogos
    except: return []

async def pre_carregar_cache_sheets():
    if not client: return
    for aba in ABAS_PASSADO:
        try: await asyncio.to_thread(get_sheet_data, aba)
        except: pass
        await asyncio.sleep(1)

def buscar_jogos(league_code, status_filter):
    try:
        url = f"https://api.football-data.org/v4/competitions/{league_code}/matches"
        params = {"status": status_filter} if status_filter != "ALL" else {}
        if league_code == "BSA": params["season"] = "2026"
        r = requests.get(url, headers={"X-Auth-Token": API_KEY}, params=params, timeout=10)
        r.raise_for_status()
        all_matches = r.json().get("matches", [])
        if status_filter == "ALL": return [m for m in all_matches if m.get('status') in ['SCHEDULED', 'TIMED']]
        jogos = []
        for m in all_matches:
            if m.get('status') == "FINISHED":
                ft = m.get("score", {}).get("fullTime", {}); ht = m.get("score", {}).get("halfTime", {})
                if ft.get("home") is None: continue
                gm, gv = ft.get("home",0), ft.get("away",0)
                gm1, gv1 = ht.get("home",0), ht.get("away",0)
                jogos.append({"Mandante": m.get("homeTeam", {}).get("name", ""), "Visitante": m.get("awayTeam", {}).get("name", ""), "Gols Mandante": gm, "Gols Visitante": gv, "Gols Mandante 1T": gm1, "Gols Visitante 1T": gv1, "Gols Mandante 2T": gm-gm1, "Gols Visitante 2T": gv-gv1, "Data": datetime.strptime(m['utcDate'][:10], "%Y-%m-%d").strftime("%d/%m/%Y")})
        return sorted(jogos, key=lambda x: datetime.strptime(x['Data'], "%d/%m/%Y"))
    except: return []

# ✅ MELHORIA: Busca AO VIVO com correção de fuso horário e novos status
def buscar_jogos_live(league_code):
    hoje_utc = datetime.now(timezone.utc)
    ontem_utc = (hoje_utc - timedelta(days=1)).strftime('%Y-%m-%d')
    hoje_str = hoje_utc.strftime('%Y-%m-%d')
    
    try:
        # Busca intervalo de 24h para não perder jogos que viraram o dia no UTC
        url = f"https://api.football-data.org/v4/competitions/{league_code}/matches?dateFrom={ontem_utc}&dateTo={hoje_str}"
        r = requests.get(url, headers={"X-Auth-Token": API_KEY}, timeout=10)
        r.raise_for_status()
    except: return []

    all_matches = r.json().get("matches", [])
    jogos = []
    for m in all_matches:
        status_api = m.get('status')
        if status_api in LIVE_STATUSES:
            ft_score = m.get("score", {}).get("fullTime", {})
            gm_atual = ft_score.get("home", 0) if ft_score.get("home") is not None else 0
            gv_atual = ft_score.get("away", 0) if ft_score.get("away") is not None else 0
            minute = m.get("minute", "N/A")

            # Mapeamento de tempo visual melhorado
            if status_api == 'HALF_TIME': minute = "Intervalo"
            elif status_api == 'PAUSED': minute = "Pausado"
            elif status_api == 'EXTRA_TIME': minute = "Prorrogação"
            elif status_api == 'PENALTY_SHOOTOUT': minute = "Pênaltis"
            elif status_api == "IN_PLAY" and minute == "N/A":
                minute = "2ºT" if m.get("score", {}).get("duration", "") == "REGULAR" else "1ºT"

            jogos.append({
                "Mandante_Nome": m.get("homeTeam", {}).get("name", ""),
                "Visitante_Nome": m.get("awayTeam", {}).get("name", ""),
                "Placar_Mandante": gm_atual, "Placar_Visitante": gv_atual,
                "Tempo_Jogo": minute, "Matchday": safe_int(m.get("matchday", 0))
            })
    return jogos

# ... (Restante das funções de cálculo e handlers permanecem iguais) ...

async def atualizar_planilhas(context: ContextTypes.DEFAULT_TYPE):
    if not client: return
    try: sh = client.open_by_url(SHEET_URL)
    except: return
    for aba_code, aba_config in LIGAS_MAP.items():
        aba_past = aba_config['sheet_past']
        try:
            ws_past = sh.worksheet(aba_past)
            jogos_finished = buscar_jogos(aba_code, "FINISHED")
            await asyncio.sleep(5)
            if jogos_finished:
                exist = ws_past.get_all_records()
                keys_exist = {(r['Mandante'], r['Visitante'], r['Data']) for r in exist}
                novas = [[j["Mandante"], j["Visitante"], j["Gols Mandante"], j["Gols Visitante"], j["Gols Mandante 1T"], j["Gols Visitante 1T"], j["Gols Mandante 2T"], j["Gols Visitante 2T"], j["Data"]] for j in jogos_finished if (j["Mandante"], j["Visitante"], j["Data"]) not in keys_exist]
                if novas: ws_past.append_rows(novas); 
                if aba_past in SHEET_CACHE: del SHEET_CACHE[aba_past]
        except: continue
        
        aba_future = aba_config['sheet_future']
        try:
            ws_future = sh.worksheet(aba_future)
            jogos_future = buscar_jogos(aba_code, "ALL")
            await asyncio.sleep(5)
            ws_future.clear()
            ws_future.update(values=[['Mandante', 'Visitante', 'Data/Hora', 'Matchday']], range_name='A1:D1')
            if jogos_future:
                linhas = [[m.get("homeTeam", {}).get("name"), m.get("awayTeam", {}).get("name"), m.get('utcDate', ''), m.get("matchday", "")] for m in jogos_future]
                ws_future.append_rows(linhas, value_input_option='USER_ENTERED')
        except: continue
        await asyncio.sleep(2)

def calcular_estatisticas_time(time, aba, ultimos=None, casa_fora=None):
    d = {"time":time,"jogos_time":0,"jogos_casa":0,"jogos_fora":0,"over15":0,"over15_casa":0,"over15_fora":0,"over25":0,"over25_casa":0,"over25_fora":0,"btts":0,"btts_casa":0,"btts_fora":0,"g_a_t":0,"g_a_t_casa":0,"g_a_t_fora":0,"over05_1T":0,"over05_1T_casa":0,"over05_1T_fora":0,"over05_2T":0,"over05_2T_casa":0,"over05_2T_fora":0,"over15_2T":0,"over15_2T_casa":0,"over15_2T_fora":0,"gols_marcados":0,"gols_sofridos":0,"gols_marcados_casa":0,"gols_sofridos_casa":0,"gols_marcados_fora":0,"gols_sofridos_fora":0,"total_gols":0,"total_gols_casa":0,"total_gols_fora":0,"gols_marcados_1T":0,"gols_sofridos_1T":0,"gols_marcados_2T":0,"gols_sofridos_2T":0,"marcou_2_mais":0,"marcou_2_mais_casa":0,"marcou_2_mais_fora":0,"sofreu_2_mais":0,"sofreu_2_mais_casa":0,"sofreu_2_mais_fora":0,"marcou_ambos_tempos":0,"marcou_ambos_tempos_casa":0,"marcou_ambos_tempos_fora":0,"sofreu_ambos_tempos":0,"sofreu_ambos_tempos_casa":0,"sofreu_ambos_tempos_fora":0}
    try: 
        linhas = get_sheet_data(aba)
        if casa_fora=="casa": linhas = [l for l in linhas if l['Mandante']==time]
        elif casa_fora=="fora": linhas = [l for l in linhas if l['Visitante']==time]
        else: linhas = [l for l in linhas if l['Mandante']==time or l['Visitante']==time]
        linhas.sort(key=lambda x: datetime.strptime(x['Data'], "%d/%m/%Y"))
        if ultimos: linhas = linhas[-ultimos:]
        for l in linhas:
            em_casa = (time == l['Mandante'])
            gm, gv = safe_int(l['Gols Mandante']), safe_int(l['Gols Visitante'])
            gm1, gv1 = safe_int(l['Gols Mandante 1T']), safe_int(l['Gols Visitante 1T'])
            gm2, gv2 = gm-gm1, gv-gv1
            total, tot1, tot2 = gm+gv, gm1+gv1, gm2+gv2
            d["jogos_time"] += 1
            if em_casa: d["jogos_casa"] += 1; marc, sofr = gm, gv; m1, s1 = gm1, gv1; m2, s2 = gm2, gv2
            else: d["jogos_fora"] += 1; marc, sofr = gv, gm; m1, s1 = gv1, gm1; m2, s2 = gv2, gm2
            d["gols_marcados"] += marc; d["gols_sofridos"] += sofr; d["total_gols"] += total
            if total>1.5: d["over15"] += 1; d["over15_casa" if em_casa else "over15_fora"] += 1
            if total>2.5: d["over25"] += 1; d["over25_casa" if em_casa else "over25_fora"] += 1
            if gm>0 and gv>0: d["btts"] += 1; d["btts_casa" if em_casa else "btts_fora"] += 1
            if tot1>0.5: d["over05_1T"] += 1; d["over05_1T_casa" if em_casa else "over05_1T_fora"] += 1
            if tot2>0.5: d["over05_2T"] += 1; d["over05_2T_casa" if em_casa else "over05_2T_fora"] += 1
            if tot2>1.5: d["over15_2T"] += 1; d["over15_2T_casa" if em_casa else "over15_2T_fora"] += 1
            if tot1>0 and tot2>0: d["g_a_t"] += 1; d["g_a_t_casa" if em_casa else "g_a_t_fora"] += 1
            if marc>=2: d["marcou_2_mais"] += 1; d["marcou_2_mais_casa" if em_casa else "marcou_2_mais_fora"] += 1
            if m1>0 and m2>0: d["marcou_ambos_tempos"] += 1; d["marcou_ambos_tempos_casa" if em_casa else "marcou_ambos_tempos_fora"] += 1
        return d
    except: return d

def formatar_estatisticas(d):
    jt = d["jogos_time"]
    if jt == 0: return f"⚠️ Sem dados para **{escape_markdown(d['time'])}**."
    return (f"📊 **{escape_markdown(d['time'])}** ({jt}j)\n"
            f"⚽ O1.5: **{pct(d['over15'], jt)}** | O2.5: **{pct(d['over25'], jt)}**\n"
            f"🔁 BTTS: **{pct(d['btts'], jt)}** | GAT: {pct(d['g_a_t'], jt)}\n"
            f"📈 Marcou 2+: **{pct(d['marcou_2_mais'], jt)}** | MAT: **{pct(d['marcou_ambos_tempos'], jt)}**\n"
            f"⏱️ 1T O0.5: {pct(d['over05_1T'], jt)} | 2T O0.5: {pct(d['over05_2T'], jt)}\n"
            f"➕ Média GP: {media(d['gols_marcados'], jt)} | GC: {media(d['gols_sofridos'], jt)}")

def listar_ultimos_jogos(time, aba, ultimos=None, casa_fora=None):
    try:
        linhas = get_sheet_data(aba)
        if casa_fora=="casa": linhas = [l for l in linhas if l['Mandante']==time]
        elif casa_fora=="fora": linhas = [l for l in linhas if l['Visitante']==time]
        else: linhas = [l for l in linhas if l['Mandante']==time or l['Visitante']==time]
        linhas.sort(key=lambda x: datetime.strptime(x['Data'], "%d/%m/%Y"))
        if ultimos: linhas = linhas[-ultimos:]
        txt = ""
        for l in linhas:
            gm, gv = safe_int(l['Gols Mandante']), safe_int(l['Gols Visitante'])
            win = (l['Mandante']==time and gm>gv) or (l['Visitante']==time and gv>gm)
            emp = gm==gv
            cor = "🟢" if win else ("🟡" if emp else "🔴")
            txt += f"{cor} {l['Data']}: {l['Mandante']} {gm}x{gv} {l['Visitante']}\n"
        return txt if txt else "Nenhum jogo."
    except: return "Erro ao listar."

async def start_command(u, c):
    await u.message.reply_text("👋 **Bot de Estatísticas BL1, BSA e PL**\nUse /stats para começar.", parse_mode='Markdown')

async def listar_competicoes(u, c):
    kb = [[InlineKeyboardButton(a, callback_data=f"c|{a}")] for a in LIGAS_MAP.keys()]
    rm = InlineKeyboardMarkup(kb)
    txt = "📊 Escolha a Competição:"
    if u.message: await u.message.reply_text(txt, reply_markup=rm, parse_mode='Markdown')
    else: await u.callback_query.edit_message_text(txt, reply_markup=rm, parse_mode='Markdown')

async def mostrar_menu_status_jogo(u, c, aba):
    kb = [[InlineKeyboardButton("🔴 AO VIVO (API)", callback_data=f"STATUS|LIVE|{aba}")],
          [InlineKeyboardButton("📅 PRÓXIMOS JOGOS", callback_data=f"STATUS|FUTURE|{aba}")],
          [InlineKeyboardButton("⬅️ Voltar", callback_data="VOLTAR_LIGA")]]
    await u.callback_query.edit_message_text(f"**{aba}** - Tipo de Partida:", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

async def listar_jogos(u, c, aba, status):
    if status == "LIVE":
        jogos = buscar_jogos_live(aba)
        if not jogos:
            await u.callback_query.edit_message_text(f"⚠️ Sem jogos **AO VIVO** em **{aba}** agora.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Voltar", callback_data=f"VOLTAR_LIGA_STATUS|{aba}")]]), parse_mode='Markdown')
            return
        c.chat_data[f"{aba}_jogos_live"] = jogos
        kb = [[InlineKeyboardButton(f"🔴 {j['Tempo_Jogo']} | {j['Mandante_Nome']} {j['Placar_Mandante']}x{j['Placar_Visitante']} {j['Visitante_Nome']}", callback_data=f"JOGO|{aba}|LIVE|{i}")] for i, j in enumerate(jogos)]
    else:
        jogos = get_sheet_data_future(aba)
        if not jogos:
            await u.callback_query.edit_message_text(f"⚠️ Sem jogos agendados em **{aba}**.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Voltar", callback_data=f"VOLTAR_LIGA_STATUS|{aba}")]]), parse_mode='Markdown')
            return
        c.chat_data[f"{aba}_jogos_future"] = jogos
        kb = [[InlineKeyboardButton(f"{j['Mandante_Nome']} x {j['Visitante_Nome']}", callback_data=f"JOGO|{aba}|FUTURE|{i}")] for i, j in enumerate(jogos[:20])]
    
    kb.append([InlineKeyboardButton("⬅️ Voltar", callback_data=f"VOLTAR_LIGA_STATUS|{aba}")])
    await u.callback_query.edit_message_text(f"**SELECIONE A PARTIDA ({aba}):**", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

async def mostrar_menu_acoes(u, c, aba, m, v):
    kb = [[InlineKeyboardButton(l, callback_data=f"{f}|{i}")] for i, (l, f, _, _, _) in enumerate(CONFRONTO_FILTROS)]
    kb.append([InlineKeyboardButton("⬅️ Voltar", callback_data=f"VOLTAR_LIGA_STATUS|{aba}")])
    await u.effective_message.reply_text(f"Filtros para **{m} x {v}**:", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

async def callback_query_handler(u, c):
    q = u.callback_query; d = q.data
    if d.startswith("c|"): await mostrar_menu_status_jogo(u, c, d.split('|')[1])
    elif d.startswith("STATUS|"): await listar_jogos(u, c, d.split('|')[2], d.split('|')[1])
    elif d.startswith("JOGO|"):
        _, aba, st, idx = d.split('|'); jogo = c.chat_data.get(f"{aba}_jogos_{st.lower()}")[int(idx)]
        c.chat_data['curr_m'], c.chat_data['curr_v'], c.chat_data['curr_aba'] = jogo['Mandante_Nome'], jogo['Visitante_Nome'], aba
        await mostrar_menu_acoes(u, c, aba, jogo['Mandante_Nome'], jogo['Visitante_Nome'])
    elif d.startswith("STATS_FILTRO|"):
        idx = int(d.split('|')[1]); _, _, ult, cm, cv = CONFRONTO_FILTROS[idx]
        res = formatar_estatisticas(calcular_estatisticas_time(c.chat_data['curr_m'], c.chat_data['curr_aba'], ult, cm)) + "\n\n" + formatar_estatisticas(calcular_estatisticas_time(c.chat_data['curr_v'], c.chat_data['curr_aba'], ult, cv))
        await u.effective_message.reply_text(res, parse_mode='Markdown'); await mostrar_menu_acoes(u, c, c.chat_data['curr_aba'], c.chat_data['curr_m'], c.chat_data['curr_v'])
    elif d.startswith("RESULTADOS_FILTRO|"):
        idx = int(d.split('|')[1]); _, _, ult, cm, cv = CONFRONTO_FILTROS[idx]
        res = f"📅 {c.chat_data['curr_m']}:\n{listar_ultimos_jogos(c.chat_data['curr_m'], c.chat_data['curr_aba'], ult, cm)}\n\n📅 {c.chat_data['curr_v']}:\n{listar_ultimos_jogos(c.chat_data['curr_v'], c.chat_data['curr_aba'], ult, cv)}"
        await u.effective_message.reply_text(res, parse_mode='Markdown'); await mostrar_menu_acoes(u, c, c.chat_data['curr_aba'], c.chat_data['curr_m'], c.chat_data['curr_v'])
    elif d == "VOLTAR_LIGA": await listar_competicoes(u, c)
    elif d.startswith("VOLTAR_LIGA_STATUS|"): await mostrar_menu_status_jogo(u, c, d.split('|')[1])
    await q.answer()

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("stats", listar_competicoes))
    app.add_handler(CallbackQueryHandler(callback_query_handler))
    if client: app.job_queue.run_repeating(atualizar_planilhas, interval=3600, first=0)
    app.run_webhook(listen="0.0.0.0", port=int(os.environ.get("PORT", "8080")), url_path=BOT_TOKEN, webhook_url=os.environ.get("WEBHOOK_URL") + '/' + BOT_TOKEN)

if __name__ == "__main__": main()
