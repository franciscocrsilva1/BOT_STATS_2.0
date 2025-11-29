# gsheets_api.py

import requests
import time
import logging
import asyncio
from datetime import datetime, timedelta
from gspread.exceptions import WorksheetNotFound

# Importa variáveis globais
from config import API_KEY, SHEET_URL, LIGAS_MAP, client, SHEET_CACHE

# =================================================================================
# 🎯 FUNÇÕES DE ESTATÍSTICAS E API
# =================================================================================
def buscar_jogos(league_code, status_filter):
    """Busca jogos na API com filtro de status (FINISHED ou ALL)."""
    if not API_KEY: return []
  
    try:
        url = f"https://api.football-data.org/v4/competitions/{league_code}/matches"
        if status_filter != "ALL": url += f"?status={status_filter}"

        r = requests.get(url, headers={"X-Auth-Token": API_KEY}, timeout=10)
        r.raise_for_status()
    except Exception as e:
        logging.error(f"❌ Erro ao buscar jogos {status_filter} para {league_code} na API: {e}")
        return []

    all_matches = r.json().get("matches", [])

    if status_filter == "ALL":
        # Retorna apenas jogos futuros
        return [m for m in all_matches if m.get('status') in ['SCHEDULED', 'TIMED']]

    else:
        # Lógica para jogos FINISHED (Histórico)
        jogos = []
        for m in all_matches:
            if m.get('status') == "FINISHED":
                try:
                    jogo_data = datetime.strptime(m['utcDate'][:10], "%Y-%m-%d")
                    ft = m.get("score", {}).get("fullTime", {}); ht = m.get("score", {}).get("halfTime", {})
                    if ft.get("home") is None: continue

                    gm, gv = ft.get("home",0), ft.get("away",0)
                    gm1, gv1 = ht.get("home",0), ht.get("away",0)
                    gm2 = gm - gm1; gv2 = gv - gv1

                    jogos.append({
                        "Mandante": m.get("homeTeam", {}).get("name", ""), "Visitante": m.get("awayTeam", {}).get("name", ""),
                        "Gols Mandante": gm, "Gols Visitante": gv,
                        "Gols Mandante 1T": gm1, "Gols Visitante 1T": gv1,
                        "Gols Mandante 2T": gm2, "Gols Visitante 2T": gv2,
                        "Data": jogo_data.strftime("%d/%m/%Y")
                    })
                except: continue
        return sorted(jogos, key=lambda x: datetime.strptime(x['Data'], "%d/%m/%Y"))


# =================================================================================
# 💾 FUNÇÕES DE CACHE E GSHEETS
# =================================================================================

def get_sheet_data(aba_code):
    """Obtém dados da aba de histórico (sheet_past) usando o cache global."""
    aba_name = LIGAS_MAP[aba_code]['sheet_past']

    if aba_name in SHEET_CACHE:
        return SHEET_CACHE[aba_name]['data'] 

    if not client: 
        logging.error("Cliente GSheets não autorizado. Impossível ler dados.")
        return []
    
    try:
        sh = client.open_by_url(SHEET_URL)
        linhas = sh.worksheet(aba_name).get_all_records()
        SHEET_CACHE[aba_name] = { 'data': linhas, 'timestamp': datetime.now() }
        return linhas
    except Exception as e:
        logging.error(f"❌ Erro ao ler dados da planilha {aba_name}: {e}")
        return []

async def pre_carregar_cache_sheets():
    """Pré-carrega o cache de dados para todas as ligas na inicialização."""
    logging.info("Iniciando pré-carregamento do cache de histórico.")
    for aba_code in LIGAS_MAP:
        await asyncio.to_thread(get_sheet_data, aba_code) 
    logging.info("✅ Pré-carregamento do cache finalizado.")


def atualizar_planilhas(context):
    """Executa a atualização automática das planilhas (JobQueue)."""
    if not client:
        logging.error("Atualização de planilhas ignorada: Cliente GSheets não autorizado.")
        return
        
    try: sh = client.open_by_url(SHEET_URL)
    except Exception as e:
        logging.error(f"❌ Erro ao abrir planilha para atualização: {e}.")
        return

    logging.info("Iniciando a atualização automática das planilhas...")

    for aba_code, aba_config in LIGAS_MAP.items():
        
        # 1. ATUALIZAÇÃO DO HISTÓRICO (ABA_PASSADO)
        aba_past = aba_config['sheet_past']
        try: ws_past = sh.worksheet(aba_past)
        except WorksheetNotFound: 
            logging.warning(f"⚠️ Aba '{aba_past}' não encontrada. Ignorando.")
            continue

        jogos_finished = buscar_jogos(aba_code, "FINISHED")
        
        # ✅ LOG DE DEBBUGING: QUANTIDADE DE JOGOS RETORNADOS PELA API
        if not jogos_finished:
            logging.error(f"❌ DEBUG: API retornou 0 jogos FINISHED para a liga {aba_code}. VERIFIQUE A API_KEY.")
        else:
            logging.info(f"✅ DEBUG: API retornou {len(jogos_finished)} jogos FINISHED para a liga {aba_code}.")
            
        time.sleep(10) # Pausa SÍNCRONA de 10s para respeitar limite da API

        if jogos_finished:
            try:
                exist = ws_past.get_all_records() 
                keys_exist = {(r['Mandante'], r['Visitante'], r['Data']) for r in exist}
                novas_linhas = []
            
                for j in jogos_finished:
                    key = (j["Mandante"], j["Visitante"], j["Data"])
                    if key not in keys_exist:
                        novas_linhas.append([j["Mandante"], j["Visitante"], j["Gols Mandante"], j["Gols Visitante"], j["Gols Mandante 1T"], j["Gols Visitante 1T"], j["Gols Mandante 2T"], j["Gols Visitante 2T"], j["Data"]])

                if novas_linhas:
                    ws_past.append_rows(novas_linhas)
                    logging.info(f"✅ {len(novas_linhas)} jogos adicionados ao histórico de {aba_past}.")
                    # Invalida o cache
                    if aba_past in SHEET_CACHE: del SHEET_CACHE[aba_past]
                else:
                    logging.info(f"⚠️ Nenhuma nova partida FINALIZADA para adicionar em {aba_past}.")
            
            except Exception as e:
                logging.error(f"❌ ERRO GRAVE DE ESCRITA em {aba_past}: {e}. Verifique cabeçalhos da coluna.")

        # 2. ATUALIZAÇÃO DO CACHE DE FUTUROS JOGOS (ABA_FUTURE)
        aba_future = aba_config['sheet_future']
        
        try: ws_future = sh.worksheet(aba_future)
        except WorksheetNotFound: continue

        jogos_future = buscar_jogos(aba_code, "ALL")
        logging.info(f"✅ DEBUG: API retornou {len(jogos_future)} jogos FUTURE para a liga {aba_code}.")

        time.sleep(10) # Pausa SÍNCRONA de 10s

        try:
            # Limpa a aba e reescreve os cabeçalhos
            ws_future.clear()
            ws_future.update(values=[['Mandante', 'Visitante', 'Data/Hora', 'Matchday']], range_name='A1:D1')

            if jogos_future:
                linhas_future = []
                for m in jogos_future:
                    utc_date = m.get('utcDate', '')
                    if utc_date:
                        try:
                            # Filtra jogos com até 90 dias de antecedência
                            data_utc = datetime.strptime(utc_date[:16], '%Y-%m-%dT%H:%M')
                            if data_utc < datetime.now() + timedelta(days=90):
                                linhas_future.append([m.get("homeTeam", {}).get("name"), m.get("awayTeam", {}).get("name"), utc_date, m.get("matchday")])
                        except: continue
             
                if linhas_future:
                    ws_future.append_rows(linhas_future, value_input_option='USER_ENTERED')
                    logging.info(f"✅ {len(linhas_future)} jogos futuros atualizados no cache de {aba_future}.")
                else:
                    logging.info(f"⚠️ Nenhuma partida agendada para {aba_code}.")

        except Exception as e:
            logging.error(f"❌ Erro ao atualizar cache de futuros jogos em {aba_future}: {e}.")

        time.sleep(3) # Pausa entre ligas

    logging.info("✅ Atualização automática finalizada.")
