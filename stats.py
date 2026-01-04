# ===============================================================================
# 🏆 BOT DE ESTATÍSTICAS DE CONFRONTO V2.3.1 - LIGAS FILTRADAS & SYNC OTIMIZADO
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

# Mapeamento de Ligas (BSA, BL1 e PL mantidas conforme solicitado)
LIGAS_MAP = {
    "BSA": {"sheet_past": "BSA", "sheet_future": "BSA_FJ"},
    "BL1": {"sheet_past": "BL1", "sheet_future": "BL1_FJ"},
    "PL": {"sheet_past": "PL", "sheet_future": "PL_FJ"},
}
ABAS_PASSADO = list(LIGAS_MAP.keys())

ULTIMOS = 10
SHEET_CACHE = {}
CACHE_DURATION_SECONDS = 3600 # 1 hora
MAX_GAMES_LISTED = 30

# Filtros reutilizáveis para Estatísticas e Resultados
CONFRONTO_FILTROS = [
    (f"📊 Estatísticas | ÚLTIMOS {ULTIMOS} GERAL", "STATS_FILTRO", ULTIMOS, None, None),
    (f"📊 Estatísticas | {ULTIMOS} (M CASA vs V FORA)", "STATS_FILTRO", ULTIMOS, "casa", "fora"),
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
    logging.error("❌ ERRO DE AUTORIZAÇÃO GSHEET: Variável GSPREAD_CREDS_JSON não encontrada. Configure-a no Railway.")
else:
    try:
        with tempfile.NamedTemporaryFile(mode="w", delete=False, encoding='utf-8') as tmp_file:
            tmp_file.write(CREDS_JSON)
            tmp_file_path = tmp_file.name
        
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name(tmp_file_path, scope)
        client = gspread.authorize(creds)
      
        logging.info("✅ Conexão GSheets estabelecida via Variável de Ambiente.")
        os.remove(tmp_file_path)

    except Exception as e:
        logging.error(f"❌ ERRO DE AUTORIZAÇÃO GSHEET: Erro ao carregar ou autorizar credenciais JSON: {e}")
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
    """Obtém dados da aba de histórico (sheet_past) com cache."""
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
    """Obtém dados da aba de cache de jogos futuros (sheet_future)."""
    aba_name = LIGAS_MAP[aba_code]['sheet_future']
    if not client: return []

    try:
        sh = client.open_by_url(SHEET_URL)
        linhas_raw = sh.worksheet(aba_name).get_all_values()
    except Exception as e:
        logging.error(f"Erro ao buscar cache de futuros jogos em {aba_name}: {e}")
        return []

    if not linhas_raw or len(linhas_raw) <= 1:
        return []

    data_rows = linhas_raw[1:]
    jogos = []
    for row in data_rows:
        if len(row) >= 4:
            jogos.append({
                "Mandante_Nome": row[0],
                "Visitante_Nome": row[1],
                "Data_Hora": row[2],
                "Matchday": safe_int(row[3])
            })
    return jogos

async def pre_carregar_cache_sheets():
    """Pré-carrega o histórico de todas as ligas (rodado uma vez na inicialização)."""
    if not client:
        logging.warning("Pré-carregamento de cache ignorado: Conexão GSheets falhou.")
        return

    logging.info("Iniciando pré-carregamento de cache...")
    for aba in ABAS_PASSADO:
        try:
            await asyncio.to_thread(get_sheet_data, aba)
            logging.info(f"Cache de histórico para {aba} pré-carregado.")
        except Exception as e:
            logging.warning(f"Não foi possível pré-carregar cache para {aba}: {e}")
        await asyncio.sleep(1)

# =================================================================================
# 🎯 FUNÇÕES DE API E ATUALIZAÇÃO 
# =================================================================================
def buscar_jogos(league_code, status_filter):
    """Busca jogos na API com filtro de status e suporte a temporada 2026 para BSA."""
    try:
        url = f"https://api.football-data.org/v4/competitions/{league_code}/matches"
        params = {}
        
        if status_filter != "ALL": 
            params["status"] = status_filter
            
        if league_code == "BSA":
            params["season"] = "2026"

        r = requests.get(url, headers={"X-Auth-Token": API_KEY}, params=params, timeout=10)
        r.raise_for_status()
    except Exception as e:
        logging.error(f"Erro ao buscar jogos {status_filter} para {league_code}: {e}")
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
                    gm2, gv2 = gm-gm1, gv-gv1

                    jogos.append({
                        "Mandante": m.get("homeTeam", {}).get("name", ""),
                        "Visitante": m.get("awayTeam", {}).get("name", ""),
                        "Gols Mandante": gm, "Gols Visitante": gv,
                        "Gols Mandante 1T": gm1, "Gols Visitante 1T": gv1,
                        "Gols Mandante 2T": gm2, "Gols Visitante 2T": gv2,
                        "Data": jogo_data.strftime("%d/%m/%Y")
                    })
                except: continue
        return sorted(jogos, key=lambda x: datetime.strptime(x['Data'], "%d/%m/%Y"))

def buscar_jogos_live(league_code):
    """Busca jogos AO VIVO com prioridade de status."""
    hoje_utc = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    try:
        url = f"https://api.football-data.org/v4/competitions/{league_code}/matches?dateFrom={hoje_utc}&dateTo={hoje_utc}"
        r = requests.get(url, headers={"X-Auth-Token": API_KEY}, timeout=10)
        r.raise_for_status()
    except Exception as e:
        logging.error(f"Erro ao buscar jogos AO VIVO para {league_code}: {e}")
        return []

    all_matches = r.json().get("matches", [])
    jogos = []
    for m in all_matches:
        status_api = m.get('status')
        # Garantindo que apenas jogos realmente em andamento apareçam aqui
        if status_api in LIVE_STATUSES:
            try:
                ft_score = m.get("score", {}).get("fullTime", {})
                gm_atual = ft_score.get("home") if ft_score.get("home") is not None else 0
                gv_atual = ft_score.get("away") if ft_score.get("away") is not None else 0
                minute = m.get("minute", "N/A")

                if status_api in ['PAUSED', 'HALF_TIME']: minute = "Intervalo" if status_api == 'HALF_TIME' else "Pausado"
                elif status_api == "IN_PLAY":
                    if minute == "N/A" or minute is None:
                        minute = "Em Jogo"

                jogos.append({
                    "Mandante_Nome": m.get("homeTeam", {}).get("name", ""),
                    "Visitante_Nome": m.get("awayTeam", {}).get("name", ""),
                    "Placar_Mandante": gm_atual, "Placar_Visitante": gv_atual,
                    "Tempo_Jogo": minute, "Matchday": safe_int(m.get("matchday", 0))
                })
            except: continue
    return jogos

async def atualizar_planilhas(context: ContextTypes.DEFAULT_TYPE):
    """Atualiza o histórico e o cache de futuros jogos."""
    global SHEET_CACHE

    if not client:
        logging.error("Atualização de planilhas ignorada: Cliente GSheets não autorizado.")
        return
        
    try: sh = client.open_by_url(SHEET_URL)
    except:
        logging.error("Erro ao abrir planilha para atualização.")
        return

    logging.info("Iniciando a atualização periódica das planilhas...")

    for aba_code, aba_config in LIGAS_MAP.items():
        aba_past = aba_config['sheet_past']
        try: ws_past = sh.worksheet(aba_past)
        except WorksheetNotFound: 
            continue

        jogos_finished = buscar_jogos(aba_code, "FINISHED")
        await asyncio.sleep(5) 

        if jogos_finished:
            try:
                exist = ws_past.get_all_records()
                keys_exist = {(r['Mandante'], r['Visitante'], r['Data']) for r in exist}
                novas_linhas = []
                for j in jogos_finished:
                    key = (j["Mandante"], j["Visitante"], j["Data"])
                    if key not in keys_exist:
                        novas_linhas.append([
                            j["Mandante"], j["Visitante"], j["Gols Mandante"], j["Gols Visitante"],
                            j["Gols Mandante 1T"], j["Gols Visitante 1T"],
                            j["Gols Mandante 2T"], j["Gols Visitante 2T"], j["Data"]
                        ])

                if novas_linhas:
                    ws_past.append_rows(novas_linhas)
                if aba_past in SHEET_CACHE: del SHEET_CACHE[aba_past]
            except Exception as e:
                logging.error(f"Erro ao inserir dados na planilha {aba_past}: {e}")

        aba_future = aba_config['sheet_future']
        try: ws_future = sh.worksheet(aba_future)
        except WorksheetNotFound:
            continue

        jogos_future = buscar_jogos(aba_code, "ALL")
        await asyncio.sleep(5) 

        try:
            ws_future.clear()
            ws_future.update(values=[['Mandante', 'Visitante', 'Data/Hora', 'Matchday']], range_name='A1:D1')

            if jogos_future:
                linhas_future = []
                agora_utc = datetime.now(timezone.utc)
                for m in jogos_future:
                    utc_date_str = m.get('utcDate', '')
                    if utc_date_str:
                        try:
                            # Filtro aprimorado: só adiciona ao cache "Futuro" se a hora do jogo for maior que agora
                            data_jogo_utc = datetime.strptime(utc_date_str[:16], '%Y-%m-%dT%H:%M').replace(tzinfo=timezone.utc)
                            if data_jogo_utc > agora_utc:
                                linhas_future.append([
                                    m.get("homeTeam", {}).get("name"),
                                    m.get("awayTeam", {}).get("name"),
                                    utc_date_str, m.get("matchday", "")
                                ])
                        except: continue

                if linhas_future:
                    ws_future.append_rows(linhas_future, value_input_option='USER_ENTERED')
        except Exception as e:
            logging.error(f"Erro ao atualizar cache de futuros jogos em {aba_future}: {e}")

        await asyncio.sleep(2) 

# =================================================================================
# 📈 FUNÇÕES DE CÁLCULO E FORMATAÇÃO
# =================================================================================
def calcular_estatisticas_time(time, aba, ultimos=None, casa_fora=None):
    d = {"time":time,"jogos_time":0,"jogos_casa":0,"jogos_fora":0,
         "over15":0,"over15_casa":0,"over15_fora":0, 
         "over25":0,"over25_casa":0,"over25_fora":0,
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

    try: linhas = get_sheet_data(aba)
    except: return {"time":time, "jogos_time": 0}

    if casa_fora=="casa": linhas = [l for l in linhas if l['Mandante']==time]
    elif casa_fora=="fora": linhas = [l for l in linhas if l['Visitante']==time]
    else: linhas = [l for l in linhas if l['Mandante']==time or l['Visitante']==time]

    try: linhas.sort(key=lambda x: datetime.strptime(x['Data'], "%d/%m/%Y"), reverse=False)
    except: pass

    if ultimos: linhas = linhas[-ultimos:]

    for linha in linhas:
        em_casa = (time == linha['Mandante'])
        gm, gv = safe_int(linha['Gols Mandante']), safe_int(linha['Gols Visitante'])
        gm1, gv1 = safe_int(linha['Gols Mandante 1T']), safe_int(linha['Gols Visitante 1T'])
        gm2, gv2 = gm-gm1, gv-gv1 

        total, total1, total2 = gm+gv, gm1+gv1, gm2+gv2
        d["jogos_time"] += 1

        team_marcados_ft, team_sofridos_ft = 0, 0
        team_marcados_1t, team_sofridos_1t = 0, 0
        team_marcados_2t, team_sofridos_2t = 0, 0

        if em_casa:
            marcados, sofridos = gm, gv; team_marcados_ft, team_sofridos_ft = gm, gv
            team_marcados_1t, team_sofridos_1t = gm1, gv1; team_marcados_2t, team_sofridos_2t = gm2, gv2
            d["jogos_casa"] += 1
            d["gols_marcados_1T_casa"] += gm1; d["gols_sofridos_1T_casa"] += gv1
            d["gols_marcados_2T_casa"] += gm2; d["gols_sofridos_2T_casa"] += gv2
        else:
            marcados, sofridos = gv, gm; team_marcados_ft, team_sofridos_ft = gv, gm
            team_marcados_1t, team_sofridos_1t = gv1, gm1; team_marcados_2t, team_sofridos_2t = gv2, gm2
            d["jogos_fora"] += 1
            d["gols_marcados_1T_fora"] += gv1; d["gols_sofridos_1T_fora"] += gm1
            d["gols_marcados_2T_fora"] += gv2; d["gols_sofridos_2T_fora"] += gm2

        d["gols_marcados"] += marcados; d["gols_sofridos"] += sofridos
        if em_casa: d["gols_marcados_casa"] += marcados; d["gols_sofridos_casa"] += sofridos
        else: d["gols_marcados_fora"] += marcados; d["gols_sofridos_fora"] += sofridos

        d["total_gols"] += total
        if em_casa: d["total_gols_casa"] += total
        else: d["total_gols_fora"] += total

        if total>1.5: d["over15"] += 1
        if total>2.5: d["over25"] += 1
        if gm>0 and gv>0: d["btts"] += 1
        if total1>0.5: d["over05_1T"] += 1
        if total2>0.5: d["over05_2T"] += 1
        if total2>1.5: d["over15_2T"] += 1

        gol_no_1t, gol_no_2t = total1 > 0, total2 > 0
        if gol_no_1t and gol_no_2t:
            d["g_a_t"] += 1
            d["g_a_t_casa" if em_casa else "g_a_t_fora"] += 1
            
        if marcados >= 2:
            d["marcou_2_mais"] += 1
            d["marcou_2_mais_casa" if em_casa else "marcou_2_mais_fora"] += 1
        if sofridos >= 2:
            d["sofreu_2_mais"] += 1
            d["sofreu_2_mais_casa" if em_casa else "sofreu_2_mais_fora"] += 1
        if team_marcados_1t > 0 and team_marcados_2t > 0:
            d["marcou_ambos_tempos"] += 1
            d["marcou_ambos_tempos_casa" if em_casa else "marcou_ambos_tempos_fora"] += 1
        if team_sofridos_1t > 0 and team_sofridos_2t > 0:
            d["sofreu_ambos_tempos"] += 1
            d["sofreu_ambos_tempos_casa" if em_casa else "sofreu_ambos_tempos_fora"] += 1

        d["over15_casa" if em_casa else "over15_fora"] += (1 if total > 1.5 else 0)
        d["over25_casa" if em_casa else "over25_fora"] += (1 if total > 2.5 else 0)
        d["btts_casa" if em_casa else "btts_fora"] += (1 if gm > 0 and gv > 0 else 0)
        d["over05_1T_casa" if em_casa else "over05_1T_fora"] += (1 if total1 > 0.5 else 0)
        d["over05_2T_casa" if em_casa else "over05_2T_fora"] += (1 if total2 > 0.5 else 0)
        d["over15_2T_casa" if em_casa else "over15_2T_fora"] += (1 if total2 > 1.5 else 0)

        d["gols_marcados_1T"] += team_marcados_1t; d["gols_sofridos_1T"] += team_sofridos_1t
        d["gols_marcados_2T"] += team_marcados_2t; d["gols_sofridos_2T"] += team_sofridos_2t 

    return d

def formatar_estatisticas(d):
    jt, jc, jf = d["jogos_time"], d.get("jogos_casa", 0), d.get("jogos_fora", 0)
    if jt == 0: return f"⚠️ **Nenhum jogo encontrado** para **{escape_markdown(d['time'])}**."
    
    return (f"📊 **Estatísticas - {escape_markdown(d['time'])}**\n"
            f"📅 Jogos: {jt} (Casa: {jc} | Fora: {jf})\n\n"
            f"⚽ Over 1.5: **{pct(d['over15'], jt)}** (C: {pct(d['over15_casa'], jc)} | F: {pct(d['over15_fora'], jf)})\n"
            f"⚽ Over 2.5: **{pct(d['over25'], jt)}** (C: {pct(d['over25_casa'], jc)} | F: {pct(d['over25_fora'], jf)})\n"
            f"🔁 BTTS: **{pct(d['btts'], jt)}** (C: {pct(d['btts_casa'], jc)} | F: {pct(d['btts_fora'], jf)})\n"
            f"🥅 G.A.T. (Gol em Ambos os Tempos): {pct(d['g_a_t'], jt)} (C: {pct(d['g_a_t_casa'], jc)} | F: {pct(d['g_a_t_fora'], jf)})\n"
            f"📈 Marcou 2+ Gols: **{pct(d['marcou_2_mais'], jt)}** (C: {pct(d['marcou_2_mais_casa'], jc)} | F: {pct(d['marcou_2_mais_fora'], jf)})\n"
            f"📉 Sofreu 2+ Gols: **{pct(d['sofreu_2_mais'], jt)}** (C: {pct(d['sofreu_2_mais_casa'], jc)} | F: {pct(d['sofreu_2_mais_fora'], jf)})\n"
            f"⚽ M.A.T. (Marcou em Ambos Tempos): **{pct(d['marcou_ambos_tempos'], jt)}** (C: {pct(d['marcou_ambos_tempos_casa'], jc)} | F: {pct(d['marcou_ambos_tempos_fora'], jf)})\n"
            f"🥅 S.A.T. (Sofreu em Ambos Tempos): **{pct(d['sofreu_ambos_tempos'], jt)}** (C: {pct(d['sofreu_ambos_tempos_casa'], jc)} | F: {pct(d['sofreu_ambos_tempos_fora'], jf)})\n\n"
            f"⏱️ 1ºT Over 0.5: {pct(d['over05_1T'], jt)} (C: {pct(d['over05_1T_casa'], jc)} | F: {pct(d['over05_1T_fora'], jf)})\n"
            f"⏱️ 2ºT Over 0.5: {pct(d['over05_2T'], jt)} (C: {pct(d['over05_2T_casa'], jc)} | F: {pct(d['over05_2T_fora'], jf)})\n"
            f"⏱️ 2ºT Over 1.5: {pct(d['over15_2T'], jt)} (C: {pct(d['over15_2T_casa'], jc)} | F: {pct(d['over15_2T_fora'], jf)})\n\n"
            f"➕ **Média gols marcados:** {media(d['gols_marcados'], jt)} (C: {media(d['gols_marcados_casa'], jc)} | F: {media(d['gols_marcados_fora'], jf)})\n"
            f"➖ **Média gols sofridos:** {media(d['gols_sofridos'], jt)} (C: {media(d['gols_sofridos_casa'], jc)} | F: {media(d['gols_sofridos_fora'], jf)})\n\n"
            f"⏱️ Média gols 1ºT (GP/GC): {media(d['gols_marcados_1T'], jt)} / {media(d['gols_sofridos_1T'], jt)}\n"
            f"⏱️ Média gols 2ºT (GP/GC): {media(d['gols_marcados_2T'], jt)} / {media(d['gols_sofridos_2T'], jt)}\n\n"
            f"🔢 **Média total de gols:** {media(d['total_gols'], jt)} (C: {media(d['total_gols_casa'], jc)} | F: {media(d['total_gols_fora'], jf)})"
    )

def listar_ultimos_jogos(time, aba, ultimos=None, casa_fora=None):
    try: linhas = get_sheet_data(aba)
    except: return f"⚠️ Erro ao ler dados da planilha para {escape_markdown(time)}."

    if casa_fora == "casa": linhas = [l for l in linhas if l['Mandante'] == time]
    elif casa_fora == "fora": linhas = [l for l in linhas if l['Visitante'] == time]
    else: linhas = [l for l in linhas if l['Mandante'] == time or l['Visitante'] == time]

    try: linhas.sort(key=lambda x: datetime.strptime(x['Data'], "%d/%m/%Y"), reverse=False)
    except: pass

    if ultimos: linhas = linhas[-ultimos:]
    if not linhas: return f"Nenhum jogo encontrado para **{escape_markdown(time)}**."

    texto_jogos = ""
    for l in linhas:
        data = l['Data']
        gm, gv = safe_int(l['Gols Mandante']), safe_int(l['Gols Visitante'])
        if l['Mandante'] == time:
            oponente = escape_markdown(l['Visitante']); condicao = "(CASA)"
            m_cor = "🟢" if gm > gv else ("🟡" if gm == gv else "🔴")
            texto_jogos += f"{m_cor} {data} {condicao}: **{escape_markdown(time)}** {gm} x {gv} {oponente}\n"
        else:
            oponente = escape_markdown(l['Mandante']); condicao = "(FORA)"
            m_cor = "🟢" if gv > gm else ("🟡" if gv == gm else "🔴")
            texto_jogos += f"{m_cor} {data} {condicao}: {oponente} {gm} x {gv} **{escape_markdown(time)}**\n"
    return texto_jogos

# =================================================================================
# 🤖 FUNÇÕES DO BOT: HANDLERS E FLUXOS
# =================================================================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = ("👋 Bem-vindo ao **Bot de Estatísticas**!\n\n"
            "Selecione um comando para começar:\n"
            "• **/stats** 📊: Inicia a análise de um confronto.")
    await update.message.reply_text(text, parse_mode='Markdown')

async def listar_competicoes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = "📊 **Estatísticas de Confronto:** Escolha a Competição:"
    keyboard = []
    abas_list = list(LIGAS_MAP.keys())
    for i in range(0, len(abas_list), 3):
        row = []
        for aba in abas_list[i:i + 3]:
            row.append(InlineKeyboardButton(aba, callback_data=f"c|{aba}"))
        keyboard.append(row)
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.message: await update.message.reply_text(title, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        try: await update.callback_query.edit_message_text(title, reply_markup=reply_markup, parse_mode='Markdown')
        except: await update.callback_query.message.reply_text(title, reply_markup=reply_markup, parse_mode='Markdown')

async def mostrar_menu_status_jogo(update: Update, context: ContextTypes.DEFAULT_TYPE, aba_code: str):
    title = f"**{aba_code}** - Escolha o Tipo de Partida:"
    keyboard = [[InlineKeyboardButton("🔴 AO VIVO (API)", callback_data=f"STATUS|LIVE|{aba_code}")],
        [InlineKeyboardButton("📅 PRÓXIMOS JOGOS (Planilha)", callback_data=f"STATUS|FUTURE|{aba_code}")],
        [InlineKeyboardButton("⬅️ Voltar para Ligas", callback_data="VOLTAR_LIGA")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    try: await update.callback_query.edit_message_text(title, reply_markup=reply_markup, parse_mode='Markdown')
    except: await update.callback_query.message.reply_text(title, reply_markup=reply_markup, parse_mode='Markdown')

async def listar_jogos(update: Update, context: ContextTypes.DEFAULT_TYPE, aba_code: str, status: str):
    cache_key = f"{aba_code}_jogos_{status.lower()}"
    if status == "FUTURE":
        try: await update.callback_query.edit_message_text(f"⏳ Buscando próximos jogos em **{aba_code}**...", parse_mode='Markdown')
        except: pass 
        
        jogos_agendados = get_sheet_data_future(aba_code)
        jogos_filtrados = []
        agora_utc = datetime.now(timezone.utc)
        
        for jogo in jogos_agendados:
            try:
                # Transição inteligente: Só mostra no "Próximos" se o jogo ainda não começou
                data_jogo = datetime.strptime(jogo['Data_Hora'][:16], '%Y-%m-%dT%H:%M').replace(tzinfo=timezone.utc)
                if data_jogo > agora_utc:
                    jogos_filtrados.append(jogo)
            except: continue
            
        if not jogos_filtrados:
            await update.callback_query.edit_message_text(f"⚠️ **Nenhum jogo futuro** em cache para **{aba_code}**.", parse_mode='Markdown')
            keyboard = [[InlineKeyboardButton("⬅️ Voltar", callback_data=f"VOLTAR_LIGA_STATUS|{aba_code}")]]
            await update.effective_message.reply_text("Opções:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
            return
            
        jogos_a_listar = jogos_filtrados[:MAX_GAMES_LISTED]
        context.chat_data[cache_key] = jogos_a_listar
        keyboard = []
        for idx, jogo in enumerate(jogos_a_listar):
            try:
                M_full, V_full, data_str = jogo['Mandante_Nome'], jogo['Visitante_Nome'], jogo['Data_Hora']
                data_local = datetime.strptime(data_str[:16], '%Y-%m-%dT%H:%M') - timedelta(hours=3)
                label = f"{data_local.strftime('%d/%m %H:%M')} | {escape_markdown(M_full)} x {escape_markdown(V_full)}"
                keyboard.append([InlineKeyboardButton(label, callback_data=f"JOGO|{aba_code}|FUTURE|{idx}")])
            except: continue

    elif status == "LIVE":
        try: await update.callback_query.edit_message_text(f"⏳ Buscando jogos **AO VIVO** em **{aba_code}**...", parse_mode='Markdown')
        except: pass
        jogos_a_listar = buscar_jogos_live(aba_code)
        if not jogos_a_listar:
            await update.callback_query.edit_message_text(f"⚠️ **Nenhum jogo AO VIVO** no momento em **{aba_code}**.", parse_mode='Markdown')
            keyboard = [[InlineKeyboardButton("⬅️ Voltar", callback_data=f"VOLTAR_LIGA_STATUS|{aba_code}")]]
            await update.effective_message.reply_text("Opções:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
            return
        context.chat_data[cache_key] = jogos_a_listar
        keyboard = []
        for idx, jogo in enumerate(jogos_a_listar):
            label = f"🔴 {jogo['Tempo_Jogo']} | {escape_markdown(jogo['Mandante_Nome'])} {jogo['Placar_Mandante']} x {jogo['Placar_Visitante']} {escape_markdown(jogo['Visitante_Nome'])}"
            keyboard.append([InlineKeyboardButton(label, callback_data=f"JOGO|{aba_code}|LIVE|{idx}")])

    keyboard.append([InlineKeyboardButton("⬅️ Voltar", callback_data=f"VOLTAR_LIGA_STATUS|{aba_code}")])
    await update.callback_query.edit_message_text(f"**SELECIONE A PARTIDA** ({aba_code}):", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def mostrar_menu_acoes(update: Update, context: ContextTypes.DEFAULT_TYPE, aba_code: str, mandante: str, visitante: str):
    title = f"Filtros para: **{escape_markdown(mandante)} x {escape_markdown(visitante)}**:"
    keyboard = []
    for idx, (label, tipo_filtro, _, _, _) in enumerate(CONFRONTO_FILTROS):
        keyboard.append([InlineKeyboardButton(label, callback_data=f"{tipo_filtro}|{idx}")])
    keyboard.append([InlineKeyboardButton("⬅️ Voltar para Jogos", callback_data=f"VOLTAR_LIGA_STATUS|{aba_code}")])
    await update.effective_message.reply_text(title, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    await update.callback_query.answer()

async def exibir_estatisticas(update: Update, context: ContextTypes.DEFAULT_TYPE, mandante: str, visitante: str, aba_code: str, filtro_idx: int):
    _, _, ultimos, cond_m, cond_v = CONFRONTO_FILTROS[filtro_idx]
    d_m = calcular_estatisticas_time(mandante, aba_code, ultimos=ultimos, casa_fora=cond_m)
    d_v = calcular_estatisticas_time(visitante, aba_code, ultimos=ultimos, casa_fora=cond_v)
    texto = (formatar_estatisticas(d_m) + "\n\n---\n\n" + formatar_estatisticas(d_v))
    await update.effective_message.reply_text(f"**Confronto:** {escape_markdown(mandante)} x {escape_markdown(visitante)}\n\n{texto}", parse_mode='Markdown')
    await mostrar_menu_acoes(update, context, aba_code, mandante, visitante)

async def exibir_ultimos_resultados(update: Update, context: ContextTypes.DEFAULT_TYPE, mandante: str, visitante: str, aba_code: str, filtro_idx: int):
    _, _, ultimos, cond_m, cond_v = CONFRONTO_FILTROS[filtro_idx]
    res_m = listar_ultimos_jogos(mandante, aba_code, ultimos=ultimos, casa_fora=cond_m)
    res_v = listar_ultimos_jogos(visitante, aba_code, ultimos=ultimos, casa_fora=cond_v)
    texto = (f"📅 **Resultados - {escape_markdown(mandante)}**\n{res_m}\n\n---\n\n📅 **Resultados - {escape_markdown(visitante)}**\n{res_v}")
    await update.effective_message.reply_text(f"**Confronto:** {escape_markdown(mandante)} x {escape_markdown(visitante)}\n\n{texto}", parse_mode='Markdown')
    await mostrar_menu_acoes(update, context, aba_code, mandante, visitante)

async def forcaupdate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not client: return
    msg = await update.message.reply_text("⚡️ **Sincronizando dados...**", parse_mode='Markdown')
    try:
        await atualizar_planilhas(context) 
        await msg.edit_text("✅ Sincronização concluída!")
    except Exception as e:
        await msg.edit_text(f"❌ Erro: {escape_markdown(str(e))}")

async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    try:
        if data.startswith("c|"):
            await mostrar_menu_status_jogo(update, context, data.split('|')[1])
        elif data.startswith("STATUS|"):
            _, status, aba = data.split('|'); await listar_jogos(update, context, aba, status)
        elif data.startswith("JOGO|"):
            _, aba, status, idx = data.split('|'); idx = safe_int(idx)
            jogo = context.chat_data.get(f"{aba}_jogos_{status.lower()}")[idx]
            context.chat_data.update({'current_mandante': jogo['Mandante_Nome'], 'current_visitante': jogo['Visitante_Nome'], 'current_aba_code': aba})
            await mostrar_menu_acoes(update, context, aba, jogo['Mandante_Nome'], jogo['Visitante_Nome'])
        elif data.startswith("STATS_FILTRO|"):
            await exibir_estatisticas(update, context, context.chat_data['current_mandante'], context.chat_data['current_visitante'], context.chat_data['current_aba_code'], safe_int(data.split('|')[1]))
        elif data.startswith("RESULTADOS_FILTRO|"):
            await exibir_ultimos_resultados(update, context, context.chat_data['current_mandante'], context.chat_data['current_visitante'], context.chat_data['current_aba_code'], safe_int(data.split('|')[1]))
        elif data.startswith("VOLTAR_LIGA_STATUS|"):
            await mostrar_menu_status_jogo(update, context, data.split('|')[1])
        elif data == "VOLTAR_LIGA": await listar_competicoes(update, context)
    except: pass

def main():
    if not BOT_TOKEN: sys.exit(1)
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("stats", listar_competicoes))
    app.add_handler(CommandHandler("forcaupdate", forcaupdate_command)) 
    app.add_handler(CallbackQueryHandler(callback_query_handler))
    
    webhook_url = os.environ.get("WEBHOOK_URL") or os.environ.get("RENDER_EXTERNAL_URL")
    if client:
        app.job_queue.run_repeating(atualizar_planilhas, interval=3600, first=0)
        asyncio.run(pre_carregar_cache_sheets())
    
    app.run_webhook(listen="0.0.0.0", port=int(os.environ.get("PORT", "8080")), url_path=BOT_TOKEN, webhook_url=f"{webhook_url}/{BOT_TOKEN}")

if __name__ == "__main__":
    main()
