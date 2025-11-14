# ===============================================================================
# 🏆 BOT DE ESTATÍSTICAS DE CONFRONTO V2.2.9 - CÓDIGO FINAL E COMPLETO (JOBQUEUE FIX)
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
import json # NOVO: Necessário para carregar o JSON da string

# ===== Novas Importações para Webhook (Flask) =====
import flask
from flask import Flask, request

# ===== Importações do Telegram Bot =====
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, Bot 
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes, JobQueue 
from telegram.error import BadRequest
from gspread.exceptions import WorksheetNotFound

# Configuração de Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
# Aplica patch no asyncio para permitir que o bot rode dentro do servidor web (Flask/Werkzeug)
nest_asyncio.apply()

# ===== Variáveis de Configuração (LIDAS DE VARIÁVEIS DE AMBIENTE) =====
BOT_TOKEN = os.environ.get("BOT_TOKEN", "SEU_TOKEN_AQUI") 
API_KEY = os.environ.get("API_KEY", "SUA_API_KEY_AQUI")
SHEET_URL = os.environ.get("SHEET_URL", "https://docs.google.com/spreadsheets/d/1ChFFXQxo1qQElNzh2OC8-UPgofRXxyVWN06ExBQ3YqY/edit?usp=drivesdk")

# Mapeamento de Ligas
LIGAS_MAP = {
    "CL": {"sheet_past": "CL", "sheet_future": "CL_FJ"},
    "BSA": {"sheet_past": "BSA", "sheet_future": "BSA_FJ"},
    "BL1": {"sheet_past": "BL1", "sheet_future": "BL1_FJ"},
    "PL": {"sheet_past": "PL", "sheet_future": "PL_FJ"},
    "ELC": {"sheet_past": "ELC", "sheet_future": "ELC_FJ"},
    "DED": {"sheet_past": "DED", "sheet_future": "DED_FJ"},
    "PD": {"sheet_past": "PD", "sheet_future": "PD_FJ"},
    "PPL": {"sheet_past": "PPL", "sheet_future": "PPL_FJ"},
    "SA": {"sheet_past": "SA", "sheet_future": "SA_FJ"},
    "FL1": {"sheet_past": "FL1", "sheet_future": "FL1_FJ"},
}
ABAS_PASSADO = list(LIGAS_MAP.keys())

ULTIMOS = 10
SHEET_CACHE = {}
CACHE_DURATION_SECONDS = 3600 # 1 hora
MAX_GAMES_LISTED = 30

# Filtros reutilizáveis para Estatísticas e Resultados
CONFRONTO_FILTROS = [
    # Label | Tipo no callback | Últimos | Condição Mandante | Condição Visitante
    (f"📊 Estatísticas | ÚLTIMOS {ULTIMOS} GERAL", "STATS_FILTRO", ULTIMOS, None, None),
    (f"📊 Estatísticas | {ULTIMOS} (M CASA vs V FORA)", "STATS_FILTRO", ULTIMOS, "casa", "fora"),
    (f"📅 Resultados | ÚLTIMOS {ULTIMOS} GERAL", "RESULTADOS_FILTRO", ULTIMOS, None, None),
    (f"📅 Resultados | {ULTIMOS} (M CASA vs V FORA)", "RESULTADOS_FILTRO", ULTIMOS, "casa", "fora"),
]

LIVE_STATUSES = ["IN_PLAY", "HALF_TIME", "PAUSED"]

# =================================================================================
# ✅ CONEXÃO GSHEETS VIA VARIÁVEL DE AMBIENTE (CARREGAMENTO FINAL)
# =================================================================================

CREDS_JSON_STRING = os.environ.get("GSPREAD_CREDS_JSON")
client = None

if not CREDS_JSON_STRING:
    logging.error("❌ ERRO DE AUTORIZAÇÃO GSHEET: Variável GSPREAD_CREDS_JSON não encontrada. Configure-a no Render.")
else:
    try:
        # Carrega o JSON da string para um objeto Python (diretamente na memória)
        creds_info = json.loads(CREDS_JSON_STRING)
            
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        
        # Cria as credenciais diretamente do objeto carregado (sem usar tempfile)
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_info, scope)
        client = gspread.authorize(creds)
        
        logging.info("✅ Conexão GSheets estabelecida via Variável de Ambiente (String Load).")

    except Exception as e:
        logging.error(f"❌ ERRO CRÍTICO DE AUTORIZAÇÃO GSHEET: Erro ao carregar o JSON da string. Verifique o formato: {e}")
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
    """Escapa caracteres que podem ser interpretados como Markdown (V1)."""
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

async def pre_carregar_cache_sheets(context: ContextTypes.DEFAULT_TYPE):
    """Pré-carrega o histórico de todas as ligas (rodado uma vez na inicialização)."""
    if not client:
        logging.warning("Pré-carregamento de cache ignorado: Conexão GSheets falhou.")
        return

    logging.info("Iniciando pré-carregamento de cache...")
    for aba in ABAS_PASSADO:
        try:
            get_sheet_data(aba)
            logging.info(f"Cache de histórico para {aba} pré-carregado.")
        except Exception as e:
            logging.warning(f"Não foi possível pré-carregar cache para {aba}: {e}")
        await asyncio.sleep(1)

# =================================================================================
# 🎯 FUNÇÕES DE API E ATUALIZAÇÃO 
# =================================================================================
def buscar_jogos(league_code, status_filter):
    """Busca jogos na API com filtro de status (usado para FINISHED e ALL)."""
    
    try:
        url = f"https://api.football-data.org/v4/competitions/{league_code}/matches"

        if status_filter != "ALL":
             url += f"?status={status_filter}"

        r = requests.get(
            url,
            headers={"X-Auth-Token": API_KEY}, timeout=10
        )
        r.raise_for_status()
    except Exception as e:
        logging.error(f"Erro ao buscar jogos {status_filter} para {league_code}: {e}")
        return []

    all_matches = r.json().get("matches", [])

    if status_filter == "ALL":
        # Garante que apenas jogos agendados ou cronometrados (futuros) sejam retornados.
        return [m for m in all_matches if m.get('status') in ['SCHEDULED', 'TIMED']]

    else:
        # Lógica original para jogos FINISHED
        jogos = []
        for m in all_matches:
            if m.get('status') == "FINISHED":
                try:
                    jogo_data = datetime.strptime(m['utcDate'][:10], "%Y-%m-%d")
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
                        "Gols Mandante 2T": gm - gm1, "Gols Visitante 2T": gv - gv1,
                        "Data": jogo_data.strftime("%d/%m/%Y")
                    })
                except: continue
        return sorted(jogos, key=lambda x: datetime.strptime(x['Data'], "%d/%m/%Y"))

def buscar_jogos_live(league_code):
    """Busca jogos AO VIVO (IN_PLAY, HALF_TIME, PAUSED) buscando todos os jogos do dia na API."""
    hoje_utc = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    try:
        # Busca todos os jogos da liga que ocorrem na data de hoje
        url = f"https://api.football-data.org/v4/competitions/{league_code}/matches?dateFrom={hoje_utc}&dateTo={hoje_utc}"

        r = requests.get(
            url,
            headers={"X-Auth-Token": API_KEY}, timeout=10
        )
        r.raise_for_status()
    except Exception as e:
        logging.error(f"Erro ao buscar jogos AO VIVO (busca por data) para {league_code}: {e}")
        return []

    all_matches = r.json().get("matches", [])

    jogos = []
    for m in all_matches:
        status_api = m.get('status')
        # Filtra manually apenas os status que representam um jogo ativo
        if status_api in LIVE_STATUSES:
            try:
                ft_score = m.get("score", {}).get("fullTime", {})

                gm_atual = ft_score.get("home") if ft_score.get("home") is not None else 0
                gv_atual = ft_score.get("away") if ft_score.get("away") is not None else 0

                minute = m.get("minute", "N/A")

                if status_api in ['PAUSED', 'HALF_TIME']:
                    minute = status_api # Mostra o status exato (e.g. HALF_TIME)
                elif status_api == "IN_PLAY":
                    # Tentativa de obter o minuto, se não vier, infere o tempo
                    if minute == "N/A":
                        if m.get("score", {}).get("duration", "") == "REGULAR":
                            minute = "2ºT"
                        else:
                            minute = "1ºT"

                jogos.append({
                    "Mandante_Nome": m.get("homeTeam", {}).get("name", ""),
                    "Visitante_Nome": m.get("awayTeam", {}).get("name", ""),
                    "Placar_Mandante": gm_atual,
                    "Placar_Visitante": gv_atual,
                    "Tempo_Jogo": minute,
                    "Matchday": safe_int(m.get("matchday", 0))
                })
            except: continue

    return jogos

async def atualizar_planilhas(context: ContextTypes.DEFAULT_TYPE):
    """Atualiza o histórico e o cache de futuros jogos. Função para o JobQueue."""
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
        # 1. ATUALIZAÇÃO DO HISTÓRICO (ABA_PASSADO)
        aba_past = aba_config['sheet_past']
        try: ws_past = sh.worksheet(aba_past)
        except WorksheetNotFound: 
            logging.warning(f"Aba de histórico '{aba_past}' não encontrada. Ignorando...")
            continue

        jogos_finished = buscar_jogos(aba_code, "FINISHED")
        await asyncio.sleep(10) # Pausa para respeitar limite de rate da API

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
                    logging.info(f"✅ {len(novas_linhas)} jogos adicionados ao histórico de {aba_past}.")

                if aba_past in SHEET_CACHE: del SHEET_CACHE[aba_past]
            except Exception as e:
                logging.error(f"Erro ao inserir dados na planilha {aba_past}: {e}")

        # 2. ATUALIZAÇÃO DO CACHE DE FUTUROS JOGOS (ABA_FUTURE)
        aba_future = aba_config['sheet_future']
        
        try: ws_future = sh.worksheet(aba_future)
        except WorksheetNotFound:
            logging.warning(f"Aba de futuros jogos '{aba_future}' não encontrada. Ignorando...")
            continue

        jogos_future = buscar_jogos(aba_code, "ALL")
        await asyncio.sleep(10) # Pausa para respeitar limite de rate da API

        try:
            ws_future.clear()
            ws_future.update(values=[['Mandante', 'Visitante', 'Data/Hora', 'Matchday']], range_name='A1:D1')

            if jogos_future:
                linhas_future = []

                for m in jogos_future:
                    matchday = m.get("matchday", "")
                    utc_date = m.get('utcDate', '')
    
                    if utc_date:
                        try:
                            data_utc = datetime.strptime(utc_date[:16], '%Y-%m-%dT%H:%M')
                            # Limita a busca a jogos de até 90 dias no futuro
                            if data_utc < datetime.now() + timedelta(days=90):
                                linhas_future.append([
                                    m.get("homeTeam", {}).get("name"),
                                    m.get("awayTeam", {}).get("name"),
                                    utc_date,
                                    matchday
                                ])
                        except:
                            continue

                if linhas_future:
                    ws_future.append_rows(linhas_future, value_input_option='USER_ENTERED')
                    logging.info(f"✅ {len(linhas_future)} jogos futuros atualizados no cache de {aba_future}.")
                else:
                    logging.info(f"⚠️ Nenhuma partida agendada para {aba_code}. Cache {aba_future} limpo.")

        except Exception as e:
            logging.error(f"Erro ao atualizar cache de futuros jogos em {aba_future}: {e}")

        await asyncio.sleep(3) # Pausa entre ligas

# =================================================================================
# 📈 FUNÇÕES DE CÁLCULO E FORMATAÇÃO DE ESTATÍSTICAS
# =================================================================================

def calcular_estatisticas_time(time, aba, ultimos=None, casa_fora=None):
    """Calcula estatísticas detalhadas para um time em uma liga."""

    # Dicionário de resultados (Inicialização completa e detalhada)
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
         
         # ===== Contadores das Novas Métricas =====
         "marcou_2_mais":0, "marcou_2_mais_casa":0, "marcou_2_mais_fora":0,
         "sofreu_2_mais":0, "sofreu_2_mais_casa":0, "sofreu_2_mais_fora":0,
         "marcou_ambos_tempos":0, "marcou_ambos_tempos_casa":0, "marcou_ambos_tempos_fora":0,
         "sofreu_ambos_tempos":0, "sofreu_ambos_tempos_casa":0, "sofreu_ambos_tempos_fora":0
         # ===============================================
         }

    try:
        linhas = get_sheet_data(aba)
    except:
        return {"time":time, "jogos_time": 0}

    # Aplica filtro casa/fora
    if casa_fora=="casa":
        linhas = [l for l in linhas if l['Mandante']==time]
    elif casa_fora=="fora":
        linhas = [l for l in linhas if l['Visitante']==time]
    else:
        linhas = [l for l in linhas if l['Mandante']==time or l['Visitante']==time]

    # Ordena e filtra os N últimos jogos
    try:
        linhas.sort(key=lambda x: datetime.strptime(x['Data'], "%d/%m/%Y"), reverse=False)
    except: pass

    if ultimos:
        linhas = linhas[-ultimos:]

    for linha in linhas:
        em_casa = (time == linha['Mandante'])
        gm, gv = safe_int(linha['Gols Mandante']), safe_int(linha['Gols Visitante'])
        gm1, gv1 = safe_int(linha['Gols Mandante 1T']), safe_int(linha['Gols Visitante 1T'])
        gm2, gv2 = gm-gm1, gv-gv1 # Gols no 2T (FT - 1T)

        total, total1, total2 = gm+gv, gm1+gv1, gm2+gv2
        d["jogos_time"] += 1

        # Variáveis de Gols para o *time específico*
        team_marcados_ft = 0
        team_sofridos_ft = 0
        team_marcados_1t = 0
        team_sofridos_1t = 0
        team_marcados_2t = 0
        team_sofridos_2t = 0

        if em_casa:
            marcados, sofridos = gm, gv # 'marcados' e 'sofridos' são para o time (FT)
            team_marcados_ft = gm
            team_sofridos_ft = gv
            team_marcados_1t = gm1
            team_sofridos_1t = gv1
            team_marcados_2t = gm2
            team_sofridos_2t = gv2
            
            d["jogos_casa"] += 1
            d["gols_marcados_1T_casa"] += gm1
            d["gols_sofridos_1T_casa"] += gv1
            d["gols_marcados_2T_casa"] += gm2
            d["gols_sofridos_2T_casa"] += gv2
        else:
            marcados, sofridos = gv, gm # 'marcados' e 'sofridos' são para o time (FT)
            team_marcados_ft = gv
            team_sofridos_ft = gm
            team_marcados_1t = gv1
            team_sofridos_1t = gm1
            team_marcados_2t = gv2
            team_sofridos_2t = gm2 # Correção de lógica anterior: gm2
            
            d["jogos_fora"] += 1
            d["gols_marcados_1T_fora"] += gv1
            d["gols_sofridos_1T_fora"] += gm1
            d["gols_marcados_2T_fora"] += gv2
            d["gols_sofridos_2T_fora"] += gm2

        # Lógica de Gols (total)
        d["gols_marcados"] += marcados
        d["gols_sofridos"] += sofridos
        if em_casa:
            d["gols_marcados_casa"] += marcados
            d["gols_sofridos_casa"] += sofridos
        else:
            d["gols_marcados_fora"] += marcados
            d["gols_sofridos_fora"] += sofridos

        d["total_gols"] += total
        if em_casa: d["total_gols_casa"] += total
        else: d["total_gols_fora"] += total

        # Lógica de Over/Under/BTTS (Geral do Jogo)
        if total>1.5: d["over15"] += 1
        if total>2.5: d["over25"] += 1
        if gm>0 and gv>0: d["btts"] += 1
        if total1>0.5: d["over05_1T"] += 1
        if total2>0.5: d["over05_2T"] += 1
        if total2>1.5: d["over15_2T"] += 1

        # GAT (Gol em Ambos os Tempos - Geral do Jogo)
        gol_no_1t = total1 > 0
        gol_no_2t = total2 > 0
        if gol_no_1t and gol_no_2t:
            d["g_a_t"] += 1
            d["g_a_t_casa" if em_casa else "g_a_t_fora"] += 1
            
        # ===== Lógica dos Novos Cálculos =====
        
        # 1. Marcou 2+ Gols (Usa o FT do time: 'marcados')
        if marcados >= 2:
            d["marcou_2_mais"] += 1
            d["marcou_2_mais_casa" if em_casa else "marcou_2_mais_fora"] += 1

        # 2. Sofreu 2+ Gols (Usa o FT sofrido pelo time: 'sofridos')
        if sofridos >= 2:
            d["sofreu_2_mais"] += 1
            d["sofreu_2_mais_casa" if em_casa else "sofreu_2_mais_fora"] += 1

        # 3. Marcou em Ambos os Tempos (Usa 1T e 2T do time)
        if team_marcados_1t > 0 and team_marcados_2t > 0:
            d["marcou_ambos_tempos"] += 1
            d["marcou_ambos_tempos_casa" if em_casa else "marcou_ambos_tempos_fora"] += 1

        # 4. Sofreu em Ambos os Tempos (Usa 1T e 2T sofridos pelo time)
        if team_sofridos_1t > 0 and team_sofridos_2t > 0:
            d["sofreu_ambos_tempos"] += 1
            d["sofreu_ambos_tempos_casa" if em_casa else "sofreu_ambos_tempos_fora"] += 1

        # =======================================================

        # Estatísticas por condição (casa/fora) - Lógica existente
        d["over15_casa" if em_casa else "over15_fora"] += (1 if total > 1.5 else 0)
        d["over25_casa" if em_casa else "over25_fora"] += (1 if total > 2.5 else 0)
        d["btts_casa" if em_casa else "btts_fora"] += (1 if gm > 0 and gv > 0 else 0)
        d["over05_1T_casa" if em_casa else "over05_1T_fora"] += (1 if total1 > 0.5 else 0)
        d["over05_2T_casa" if em_casa else "over05_2T_fora"] += (1 if total2 > 0.5 else 0)
        d["over15_2T_casa" if em_casa else "over15_2T_fora"] += (1 if total2 > 1.5 else 0)

        d["gols_marcados_1T"] += team_marcados_1t
        d["gols_sofridos_1T"] += team_sofridos_1t
        d["gols_marcados_2T"] += team_marcados_2t
        d["gols_sofridos_2T"] += team_sofridos_2t 

    return d

def formatar_estatisticas(d):
    """Formata o dicionário de estatísticas para a mensagem do Telegram."""
    jt, jc, jf = d["jogos_time"], d.get("jogos_casa", 0), d.get("jogos_fora", 0)

    if jt == 0: return f"⚠️ **Nenhum jogo encontrado** para **{escape_markdown(d['time'])}** com o filtro selecionado."
    
    return (f"📊 **Estatísticas - {escape_markdown(d['time'])}**\n"
            f"📅 Jogos: {jt} (Casa: {jc} | Fora: {jf})\n\n"
            f"⚽ Over 1.5: **{pct(d['over15'], jt)}** (C: {pct(d['over15_casa'], jc)} | F: {pct(d['over15_fora'], jf)})\n"
            f"⚽ Over 2.5: **{pct(d['over25'], jt)}** (C: {pct(d['over25_casa'], jc)} | F: {pct(d['over25_fora'], jf)})\n"
            f"🔁 BTTS: **{pct(d['btts'], jt)}** (C: {pct(d['btts_casa'], jc)} | F: {pct(d['btts_fora'], jf)})\n"
            f"🥅 G.A.T. (Gol em Ambos os Tempos): {pct(d['g_a_t'], jt)} (C: {pct(d['g_a_t_casa'], jc)} | F: {pct(d['g_a_t_fora'], jf)})\n"
            
            # ===== Novas Métricas na Formatação =====
            f"📈 Marcou 2+ Gols: **{pct(d['marcou_2_mais'], jt)}** (C: {pct(d['marcou_2_mais_casa'], jc)} | F: {pct(d['marcou_2_mais_fora'], jf)})\n"
            f"📉 Sofreu 2+ Gols: **{pct(d['sofreu_2_mais'], jt)}** (C: {pct(d['sofreu_2_mais_casa'], jc)} | F: {pct(d['sofreu_2_mais_fora'], jf)})\n"
            f"⚽ M.A.T. (Marcou em Ambos Tempos): **{pct(d['marcou_ambos_tempos'], jt)}** (C: {pct(d['marcou_ambos_tempos_casa'], jc)} | F: {pct(d['marcou_ambos_tempos_fora'], jf)})\n"
            f"🥅 S.A.T. (Sofreu em Ambos Tempos): **{pct(d['sofreu_ambos_tempos'], jt)}** (C: {pct(d['sofreu_ambos_tempos_casa'], jc)} | F: {pct(d['sofreu_ambos_tempos_fora'], jf)})\n\n"
            # ============================================
            
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
    """Lista os últimos N jogos de um time com filtros."""
    try: linhas = get_sheet_data(aba)
    except: return f"⚠️ Erro ao ler dados da planilha para {escape_markdown(time)}."

    if casa_fora == "casa":
        linhas = [l for l in linhas if l['Mandante'] == time]
    elif casa_fora == "fora":
        linhas = [l for l in linhas if l['Visitante'] == time]
    else:
        linhas = [l for l in linhas if l['Mandante'] == time or l['Visitante'] == time]

    try: linhas.sort(key=lambda x: datetime.strptime(x['Data'], "%d/%m/%Y"), reverse=False)
    except: pass

    if ultimos:
        linhas = linhas[-ultimos:]

    if not linhas: return f"Nenhum jogo encontrado para **{escape_markdown(time)}** com o filtro selecionado."

    texto_jogos = ""
    for l in linhas:
        data = l['Data']
        gm, gv = safe_int(l['Gols Mandante']), safe_int(l['Gols Visitante'])

        if l['Mandante'] == time:
            oponente = escape_markdown(l['Visitante'])
            condicao = "(CASA)"
            m_cor = "🟢" if gm > gv else ("🟡" if gm == gv else "🔴")
            texto_jogos += f"{m_cor} {data} {condicao}: **{escape_markdown(time)}** {gm} x {gv} {oponente}\n"
        else:
            oponente = escape_markdown(l['Mandante'])
            condicao = "(FORA)"
            m_cor = "🟢" if gv > gm else ("🟡" if gv == gm else "🔴")
            texto_jogos += f"{m_cor} {data} {condicao}: {oponente} {gm} x {gv} **{escape_markdown(time)}**\n"

    return texto_jogos


# =================================================================================
# 🤖 FUNÇÕES DO BOT: HANDLERS E FLUXOS
# =================================================================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "👋 Bem-vindo ao **Bot de Estatísticas de Confronto**!\n\n"
        "Selecione um comando para começar:\n"
        "• **/stats** 📊: Inicia a análise estatística de um confronto futuro ou ao vivo."
    )
    await update.message.reply_text(text, parse_mode='Markdown')

async def listar_competicoes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Primeira tela: Lista todas as competições."""
    title = "📊 **Estatísticas de Confronto:** Escolha a Competição:"

    keyboard = []
    abas_list = list(LIGAS_MAP.keys())
    for i in range(0, len(abas_list), 3):
        row = []
        for aba in abas_list[i:i + 3]:
            row.append(InlineKeyboardButton(aba, callback_data=f"c|{aba}"))
        keyboard.append(row)

    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.message:
        await update.message.reply_text(title, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        # Se for um callback, edita a mensagem anterior
        try:
            await update.callback_query.edit_message_text(title, reply_markup=reply_markup, parse_mode='Markdown')
        except BadRequest:
            # Fallback: Se a edição falhar, envia nova mensagem
            await update.callback_query.message.reply_text(title, reply_markup=reply_markup, parse_mode='Markdown')


async def mostrar_menu_status_jogo(update: Update, context: ContextTypes.DEFAULT_TYPE, aba_code: str):
    """
    Segundo menu: Escolhe entre Jogos AO VIVO e Próximos Jogos (Future).
    """

    title = f"**{aba_code}** - Escolha o Tipo de Partida:"

    keyboard = [
        [InlineKeyboardButton("🔴 AO VIVO (API)", callback_data=f"STATUS|LIVE|{aba_code}")],
        [InlineKeyboardButton("📅 PRÓXIMOS JOGOS (Planilha)", callback_data=f"STATUS|FUTURE|{aba_code}")],
        [InlineKeyboardButton("⬅️ Voltar para Ligas", callback_data="VOLTAR_LIGA")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await update.callback_query.edit_message_text(title, reply_markup=reply_markup, parse_mode='Markdown')
    except Exception as e:
        logging.error(f"ERRO ao editar mensagem em mostrar_menu_status_jogo (c|{aba_code}): {e}")
        await update.callback_query.message.reply_text(
            f"**{aba_code}** - Escolha o Tipo de Partida:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )


async def listar_jogos(update: Update, context: ContextTypes.DEFAULT_TYPE, aba_code: str, status: str):
    """Terceira tela: Lista jogos futuros (GSheets) ou ao vivo (API)."""
    jogos_a_listar = []
    
    # Define a chave de cache (memória) baseada no status
    cache_key = f"{aba_code}_jogos_{status.lower()}" # ex: 'BL1_jogos_future'

    if status == "FUTURE":
        try:
            await update.callback_query.edit_message_text(
                f"⏳ Buscando os próximos **{MAX_GAMES_LISTED}** jogos em **{aba_code}** (Planilha)...", 
                parse_mode='Markdown'
            )
        except Exception as e:
            logging.error(f"Erro ao editar mensagem de loading FUTURE: {e}")
            pass 

        jogos_agendados = get_sheet_data_future(aba_code)

        jogos_futuros_filtrados = []
        agora_utc = datetime.now(timezone.utc).replace(tzinfo=None)

        for jogo in jogos_agendados:
            try:
                data_utc = datetime.strptime(jogo['Data_Hora'][:16], '%Y-%m-%dT%H:%M').replace(tzinfo=timezone.utc)
                # BRT = UTC - 3h. O fuso local do Render será UTC, mas ajustamos para o fuso brasileiro para o display
                data_local = data_utc - timedelta(hours=3)
                if data_utc > agora_utc:
                    jogos_futuros_filtrados.append(jogo)
            except Exception as e:
                logging.warning(f"Erro ao parsear data de jogo futuro: {e}")
                continue

        jogos_agendados = jogos_futuros_filtrados

        if not jogos_agendados:
            await update.callback_query.edit_message_text(
                f"⚠️ **Nenhum jogo agendado futuro** encontrado em **{aba_code}**.\n"
                f"O Bot de atualização roda a cada 1 hora.", 
                parse_mode='Markdown'
            )
            keyboard = [[InlineKeyboardButton("⬅️ Voltar para Status", callback_data=f"VOLTAR_LIGA_STATUS|{aba_code}")]]
            await update.effective_message.reply_text("Opções:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
            return

        jogos_a_listar = jogos_agendados[:MAX_GAMES_LISTED]
        total_jogos_encontrados = len(jogos_agendados)
        matchday_label = f"Próximos {len(jogos_a_listar)} jogos (de {total_jogos_encontrados} no cache):"

    elif status == "LIVE":
        try:
            await update.callback_query.edit_message_text(
                f"⏳ Buscando jogos **AO VIVO** em **{aba_code}** (API)...", 
                parse_mode='Markdown'
            )
        except Exception as e:
            logging.error(f"Erro ao editar mensagem de loading LIVE: {e}")
            pass 

        jogos_a_listar = buscar_jogos_live(aba_code)

        if not jogos_a_listar:
            await update.callback_query.edit_message_text(
                f"⚠️ **Nenhum jogo AO VIVO** encontrado em **{aba_code}** no momento.", 
                parse_mode='Markdown'
            )
            keyboard = [[InlineKeyboardButton("⬅️ Voltar para Status", callback_data=f"VOLTAR_LIGA_STATUS|{aba_code}")]]
            await update.effective_message.reply_text("Opções:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
            return

        matchday_label = f"Jogos AO VIVO ({len(jogos_a_listar)}):"

    else:
        await update.callback_query.edit_message_text(f"Status desconhecido: {status}", parse_mode='Markdown')
        return

    text = f"**{aba_code}**\n\n{matchday_label}\n"
    keyboard = []
    
    for jogo in jogos_a_listar:
        time_m = escape_markdown(jogo['Mandante_Nome'])
        time_v = escape_markdown(jogo['Visitante_Nome'])
        
        if status == "FUTURE":
            # Extrai apenas a data e hora do UTC date (ignora segundos) e converte para horário local (BRT/GMT-3)
            data_utc = datetime.strptime(jogo['Data_Hora'][:16], '%Y-%m-%dT%H:%M').replace(tzinfo=timezone.utc)
            # BRT = UTC - 3h. O fuso local do Render será UTC, mas ajustamos para o fuso brasileiro para o display
            data_local = data_utc - timedelta(hours=3)
            data_label = data_local.strftime('%d/%m %H:%M')
            
            # Formato do callback: MATCH|ABA_CODE|STATUS|TIME_M|TIME_V
            callback_data = f"MATCH|{aba_code}|FUTURE|{jogo['Mandante_Nome']}|{jogo['Visitante_Nome']}"
            label = f"📅 {data_label} | {time_m} x {time_v}"
            
        elif status == "LIVE":
            tempo = jogo['Tempo_Jogo']
            placar_m = jogo['Placar_Mandante']
            placar_v = jogo['Placar_Visitante']
            
            # Formato do callback: MATCH|ABA_CODE|STATUS|TIME_M|TIME_V
            callback_data = f"MATCH|{aba_code}|LIVE|{jogo['Mandante_Nome']}|{jogo['Visitante_Nome']}"
            
            # Define o emoji para o status
            if tempo == 'HALF_TIME': emoji = '⏸️'
            elif tempo in ['PAUSED']: emoji = '🟡'
            elif tempo in ['1ºT', '2ºT'] or (isinstance(tempo, str) and tempo.isdigit()): emoji = '🔥'
            else: emoji = '⚽' # Default

            label = f"{emoji} {tempo} | {time_m} {placar_m} x {placar_v} {time_v}"
        
        keyboard.append([InlineKeyboardButton(label, callback_data=callback_data)])
        
    # Adicionar botão de Voltar
    keyboard.append([InlineKeyboardButton("⬅️ Voltar para Status", callback_data=f"VOLTAR_LIGA_STATUS|{aba_code}")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    except Exception as e:
        # Fallback se a mensagem não puder ser editada
        logging.error(f"Erro ao editar mensagem em listar_jogos: {e}")
        await update.effective_message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')


async def mostrar_filtros_confronto(update: Update, context: ContextTypes.DEFAULT_TYPE, aba_code: str, status: str, time_m: str, time_v: str):
    """Quarta tela: Filtros para Estatísticas ou Resultados."""
    
    # Coloca os times no contexto para serem usados no próximo callback (necessário para persistir dados)
    context.user_data['confronto'] = {
        'aba': aba_code,
        'status': status,
        'time_m': time_m,
        'time_v': time_v
    }
    
    if status == "LIVE":
        # Se for LIVE, buscamos os dados na API novamente para garantir o placar mais recente
        jogos_live = buscar_jogos_live(aba_code)
        jogo_atual = next((j for j in jogos_live if j['Mandante_Nome'] == time_m and j['Visitante_Nome'] == time_v), None)
        
        if jogo_atual:
            tempo = jogo_atual['Tempo_Jogo']
            placar_m = jogo_atual['Placar_Mandante']
            placar_v = jogo_atual['Placar_Visitante']
            
            if tempo == 'HALF_TIME': emoji = '⏸️'
            elif tempo in ['PAUSED']: emoji = '🟡'
            elif tempo in ['1ºT', '2ºT'] or (isinstance(tempo, str) and tempo.isdigit()): emoji = '🔥'
            else: emoji = '⚽'

            title = (f"**{aba_code}** | {time_m} x {time_v}\n\n"
                     f"🔴 **AO VIVO** | {emoji} {tempo}\n"
                     f"Placar Atual: **{placar_m} x {placar_v}**\n\n"
                     f"Escolha o filtro de histórico para o confronto:")
        else:
            title = (f"**{aba_code}** | {time_m} x {time_v}\n\n"
                     f"⚠️ Partida ao vivo encerrada ou não encontrada na API.\n"
                     f"Escolha o filtro de histórico para o confronto:")

    else:
        title = (f"**{aba_code}** | {time_m} x {time_v}\n\n"
                 f"📅 **PRÓXIMO JOGO**\n\n"
                 f"Escolha o filtro de histórico para o confronto:")


    keyboard = []
    
    # Botões para Estatísticas e Resultados (com filtros)
    for label, tipo, ultimos_n, cond_m, cond_v in CONFRONTO_FILTROS:
        # Formato do callback: RESULTADO|TIPO|ULTIMOS|COND_M|COND_V
        callback_data = f"RESULTADO|{tipo}|{ultimos_n}|{cond_m}|{cond_v}"
        keyboard.append([InlineKeyboardButton(label, callback_data=callback_data)])
        
    # Botão de Voltar para a lista de jogos
    keyboard.append([InlineKeyboardButton("⬅️ Voltar para Jogos", callback_data=f"VOLTAR_JOGOS_LIST|{aba_code}|{status}")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        await update.callback_query.edit_message_text(title, reply_markup=reply_markup, parse_mode='Markdown')
    except Exception as e:
        logging.error(f"Erro ao editar mensagem em mostrar_filtros_confronto: {e}")
        await update.callback_query.message.reply_text(title, reply_markup=reply_markup, parse_mode='Markdown')


async def exibir_confronto(update: Update, context: ContextTypes.DEFAULT_TYPE, filtro_tipo: str, ultimos: str, cond_m: str, cond_v: str):
    """Quinta tela: Exibe as estatísticas ou resultados finais."""
    
    # 1. Recuperar dados do confronto
    confronto_data = context.user_data.get('confronto')
    if not confronto_data:
        await update.callback_query.edit_message_text("❌ Erro: Dados do confronto perdidos. Por favor, reinicie com /stats.")
        return

    aba_code = confronto_data['aba']
    status = confronto_data['status']
    time_m = confronto_data['time_m']
    time_v = confronto_data['time_v']
    
    # 2. Atualizar mensagem para 'processando'
    try:
        await update.callback_query.edit_message_text(
            f"⏳ Calculando {filtro_tipo.split('_')[0]} para **{time_m}** e **{time_v}**...", 
            parse_mode='Markdown'
        )
    except Exception as e:
        logging.error(f"Erro ao editar mensagem de loading em exibir_confronto: {e}")
        pass

    # 3. Determinar o filtro a ser aplicado
    # O filtro de condição 'geral' é aplicado apenas na função de cálculo individual.
    # Aqui, a condição é aplicada ao time Mandante e Visitante para o histórico.
    filtro_mandante = cond_m if cond_m != 'None' else None
    filtro_visitante = cond_v if cond_v != 'None' else None
    
    ultimos_n = int(ultimos) if ultimos != 'None' else None
    
    # 4. Calcular/Listar Resultados
    
    # Resultado para o Mandante
    if filtro_tipo == "STATS_FILTRO":
        estats_m = calcular_estatisticas_time(time_m, aba_code, ultimos=ultimos_n, casa_fora=filtro_mandante)
        estats_v = calcular_estatisticas_time(time_v, aba_code, ultimos=ultimos_n, casa_fora=filtro_visitante)
        
        texto_m = formatar_estatisticas(estats_m)
        texto_v = formatar_estatisticas(estats_v)

        texto_final = f"**{aba_code} | Confronto {time_m} x {time_v}**\n\n"
        texto_final += f"========================\n\n{texto_m}\n\n"
        texto_final += f"========================\n\n{texto_v}"
    
    elif filtro_tipo == "RESULTADOS_FILTRO":
        
        texto_m = listar_ultimos_jogos(time_m, aba_code, ultimos=ultimos_n, casa_fora=filtro_mandante)
        texto_v = listar_ultimos_jogos(time_v, aba_code, ultimos=ultimos_n, casa_fora=filtro_visitante)
        
        texto_final = f"**{aba_code} | Confronto {time_m} x {time_v}**\n"
        texto_final += f"📅 **ÚLTIMOS {ultimos_n} JOGOS ({time_m})**\n(Filtro: {'Geral' if filtro_mandante is None else ('Casa' if filtro_mandante == 'casa' else 'Fora')})\n"
        texto_final += "========================\n"
        texto_final += f"{texto_m}\n\n"
        
        texto_final += f"📅 **ÚLTIMOS {ultimos_n} JOGOS ({time_v})**\n(Filtro: {'Geral' if filtro_visitante is None else ('Casa' if filtro_visitante == 'casa' else 'Fora')})\n"
        texto_final += "========================\n"
        texto_final += f"{texto_v}"
        
    else:
        texto_final = "❌ Erro de filtro."


    # 5. Enviar Resultados e Botões de Ação
    
    keyboard = [
        [InlineKeyboardButton("⬅️ Voltar para Filtros", callback_data=f"VOLTAR_FILTROS|{aba_code}|{status}|{time_m}|{time_v}")],
        [InlineKeyboardButton("🔄 Novo Confronto", callback_data="VOLTAR_LIGA")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # O resultado deve ser enviado como nova mensagem, pois é muito grande para edição.

    # Dividir a mensagem se for maior que o limite (Telegram V1)
    if len(texto_final) > 4096:
         
         # Tenta dividir a mensagem
         part1_len = texto_final.find('========================\n\n')
         if part1_len == -1: part1_len = 4000 # Fallback 
         
         part1 = texto_final[:part1_len]
         part2 = texto_final[part1_len:]

         try:
            # Envia a primeira parte (substituindo o loading)
            await update.callback_query.edit_message_text(
                part1, 
                parse_mode='Markdown'
            )
         except:
            # Se não puder editar, envia como nova mensagem
            await update.effective_message.reply_text(
                part1, 
                parse_mode='Markdown'
            )
            
         # Envia a segunda parte e a lista de botões
         await update.effective_message.reply_text(
             part2, 
             parse_mode='Markdown'
         )
         
         await update.effective_message.reply_text("Opções:", reply_markup=reply_markup, parse_mode='Markdown')

    else:
        # Envia como edição de mensagem única
        try:
             await update.callback_query.edit_message_text(texto_final, reply_markup=reply_markup, parse_mode='Markdown')
        except:
             # Se a edição falhar, envia como nova mensagem
             await update.effective_message.reply_text(texto_final, reply_markup=reply_markup, parse_mode='Markdown')


async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gerencia todos os cliques de botões Inline."""
    query = update.callback_query
    await query.answer() # Fecha o aviso de "Carregando..."
    data = query.data.split('|')
    
    try:
        if data[0] == "c":
            await mostrar_menu_status_jogo(update, context, data[1])
            
        elif data[0] == "STATUS":
            # STATUS|LIVE|ABA_CODE ou STATUS|FUTURE|ABA_CODE
            await listar_jogos(update, context, data[2], data[1])
            
        elif data[0] == "MATCH":
            # MATCH|ABA_CODE|STATUS|TIME_M|TIME_V
            await mostrar_filtros_confronto(update, context, data[1], data[2], data[3], data[4])

        elif data[0] == "RESULTADO":
            # RESULTADO|TIPO|ULTIMOS|COND_M|COND_V
            await exibir_confronto(update, context, data[1], data[2], data[3], data[4])
            
        # NAVEGAÇÃO
        elif data[0] == "VOLTAR_LIGA":
            # Volta para a primeira tela
            await listar_competicoes(update, context)

        elif data[0] == "VOLTAR_LIGA_STATUS":
            # Volta para a segunda tela
            await mostrar_menu_status_jogo(update, context, data[1])

        elif data[0] == "VOLTAR_JOGOS_LIST":
            # Volta para a terceira tela (lista de jogos)
            # data: VOLTAR_JOGOS_LIST|ABA_CODE|STATUS
            await listar_jogos(update, context, data[1], data[2])

        elif data[0] == "VOLTAR_FILTROS":
            # Volta para a quarta tela (filtros do confronto)
            # data: VOLTAR_FILTROS|ABA_CODE|STATUS|TIME_M|TIME_V
            await mostrar_filtros_confronto(update, context, data[1], data[2], data[3], data[4])
            
        else:
             await update.callback_query.edit_message_text("❌ Opção não reconhecida.")

    except Exception as e:
         logging.error(f"Erro no callback handler {data}: {e}")
         try:
              await update.callback_query.edit_message_text("❌ Ocorreu um erro interno. Tente novamente iniciando com /stats.")
         except:
              await update.effective_message.reply_text("❌ Ocorreu um erro interno. Tente novamente iniciando com /stats.")


# =================================================================================
# 🚀 ARQUITETURA WEBHOOK (FLASK) - CORRIGIDA
# =================================================================================

app_flask = Flask(__name__)
application = None # Vai segurar o objeto ApplicationBuilder

@app_flask.route(f"/{BOT_TOKEN}", methods=["POST"])
async def telegram_webhook():
    """Recebe atualizações do Telegram e as processa."""
    # Garante que o Application object foi construído antes de processar
    if application is None:
        return "Bot application not ready", 503

    if request.method == "POST":
        # Usa o método process_update diretamente para lidar com o webhook
        update = Update.de_json(request.get_json(force=True), application.bot)
        await application.process_update(update)
        # O retorno imediato é crucial para o Webhook
    return "ok"


def setup_webhook_sync(application_instance):
    """Configura o Webhook na API do Telegram (Síncrono/Requests)."""
    
    webhook_url = os.environ.get("RENDER_EXTERNAL_URL")
    if not webhook_url:
        logging.error("Variável RENDER_EXTERNAL_URL não encontrada. O Webhook não será configurado.")
        return

    full_url = f"{webhook_url}/{BOT_TOKEN}"
    
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook",
            json={"url": full_url}
        )
        response.raise_for_status()
        if response.json().get('ok'):
             logging.info(f"✅ Webhook configurado com sucesso (via Requests) para: {full_url}")
        else:
            logging.error(f"❌ Erro ao configurar webhook: {response.text}")
    except Exception as e:
        # Se falhar aqui, o JobQueue e a App não iniciam, mas o Flask tenta rodar
        logging.error(f"❌ Erro CRÍTICO ao configurar o webhook: {e}")


def main():
    global application

    if not BOT_TOKEN or BOT_TOKEN == "SEU_TOKEN_AQUI":
        logging.error("O token do bot não está configurado. Verifique a variável de ambiente BOT_TOKEN.")
        sys.exit(1)
        
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # 1. Adicionar Handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("stats", listar_competicoes))
    application.add_handler(CallbackQueryHandler(callback_query_handler))
    
    # 2. Configurar JobQueue
    if client:
        job_queue: JobQueue = application.job_queue
        
        # VERIFICAÇÃO DE ROBUSTEZ: Verifica se o JobQueue foi criado (depende da instalação do [job-queue])
        if job_queue:
            # CORREÇÃO: Agenda a pré-carga de cache para rodar na primeira oportunidade (when=0)
            job_queue.run_once(pre_carregar_cache_sheets, when=0, name="PreCarregarCache") 
            # Agenda a atualização periódica (primeira rodada após 3600s/1h)
            job_queue.run_repeating(atualizar_planilhas, interval=3600, first=3600, name="AtualizacaoPlanilhas")
            logging.info("Job Queue de atualização programado.")
        else:
            # AVISO CRÍTICO: Informa ao usuário que a instalação falhou
            logging.error("❌ ERRO NO JOB QUEUE: Falha ao inicializar o JobQueue. Verifique se 'python-telegram-bot[job-queue]' está no requirements.txt.")
    else:
        logging.warning("Job Queue de atualização e cache desativados: Conexão com GSheets não estabelecida.")

    # 3. Configurar Webhook e Iniciar a Application/JobQueue
    
    # 3.1 Configura o Webhook na API do Telegram (Chamada SÍNCRONA, antes de tudo)
    setup_webhook_sync(application)
    
    # 3.2 Inicia a Application e o JobQueue de forma não bloqueante para o Flask
    try:
        loop = asyncio.get_event_loop()
        # Inicializa a App e o JobQueue de forma não bloqueante
        loop.run_until_complete(application.initialize())
        # Cria a Task para iniciar a App (e o JobQueue) no loop de eventos
        loop.create_task(application.start())
        logging.info("Application e JobQueue iniciados em segundo plano (Task).")
    except Exception as e:
        logging.error(f"❌ Erro CRÍTICO ao iniciar Application e JobQueue: {e}")


# =================================================================================
# 💻 INÍCIO DO SERVIDOR FLASK
# =================================================================================

if __name__ == '__main__':
    # Roda a função principal (que configura JobQueue e App)
    main()
    
    # Inicia o servidor Flask, que passa a atender as requisições
    try:
        logging.info("Iniciando o servidor Flask...")
        # Usa host 0.0.0.0 e a porta fornecida pelo Render
        app_flask.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=False)
    except Exception as e:
        logging.error(f"❌ Erro ao iniciar o servidor Flask: {e}")
