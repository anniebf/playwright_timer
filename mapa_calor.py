"""
mapa_calor.py - Gera o mapa de calor (HTML interativo) com o tempo das rotinas.

LÓGICA GERAL
------------
1. FONTE DOS DADOS (padrão: banco):
   - banco: tabela RPA.TIMER. Cada execução grava uma linha por item, no formato
       "[0278s] - Tempo Login: 70.9s"
       "[0345s] - Tempo Rotina 'Pedidos de Compra': 64.5s (com erro: <mensagem>)"
     (ver LogBanco.CronometroExecucao.registrar_tempo). Assim o mapa junta as
     execuções de qualquer máquina (servidor Docker, PC de teste...).
   - arquivos: log_playwright/tempos_rotinas_<id>.json (só da máquina local).
     Usado automaticamente se o banco estiver indisponível.
   HISTÓRICO: as linhas "Tempo ..." só existem desde 18/11/2026. Para as execuções
   anteriores (robô TIR e Playwright antigo) o mapa usa um resumo agregado no
   próprio Oracle (SQL_RESUMO_EXECUCOES): tempo total de cada execução e, no
   Playwright antigo, Login/rotina ESTIMADOS pelo registro "Acessando menu lateral"
   (ver montar_execucoes_banco). Execuções são identificadas por ID + SESSÃO: o
   servidor e o PC de teste têm contadores de ID separados e o mesmo número pode
   aparecer em execuções diferentes; mais de INTERVALO_NOVA_EXECUCAO_MIN minutos sem
   registros com o mesmo ID = outra execução.
2. Este módulo transforma essas linhas em uma grade:
       linhas  = Execução completa (tempo total) + Login + rotinas
       colunas = execuções (mais antiga -> mais recente)
       célula  = tempo daquela rotina naquela execução
3. Para cada célula calcula o "normal" da rotina: a MEDIANA dos tempos das
   últimas JANELA_NORMAL execuções SEM ERRO anteriores a ela. A mediana não é
   puxada por um dia atípico, como seria a média.
4. Classifica cada célula (status):
       normal   -> até +15% do normal
       atencao  -> entre +15% e +30% acima do normal
       lenta    -> mais de +30% acima do normal (mesmo limite do alerta por e-mail)
       erro     -> a rotina falhou
       pulada   -> a rotina não foi executada (ex.: tela principal não recuperada)
       sem_base -> ainda não há histórico suficiente para dizer o que é normal
5. Duas formas de ver o mapa (mesmo template mapa_calor_template.html):
   - SERVIÇO (recomendado): api_mapa.py (FastAPI) lê o banco a cada acesso e,
     ao clicar numa célula, mostra o detalhe completo da execução (montar_detalhe):
     linha do tempo do que o robô fez, avisos/erros e tempo total;
   - ARQUIVO: log_playwright/mapa_calor.html, autocontido (sem internet),
     regenerado ao final de cada execução.

Uso manual:
    python mapa_calor.py                         # banco, últimos 60 dias, grava em ./log_playwright
    python mapa_calor.py log_playwright --dias 90
    python mapa_calor.py log_playwright --fonte arquivos   # só os JSONs locais
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import statistics
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger("mapa_calor")

# ---------------------------------------------------------------------------
# Parâmetros da classificação (ajuste aqui se o critério de "lento" mudar)
# ---------------------------------------------------------------------------
JANELA_NORMAL = 10          # quantas execuções anteriores sem erro formam o "normal"
MINIMO_HISTORICO = 3        # abaixo disso a célula fica "sem_base"
LIMITE_ATENCAO = 1.15       # > +15% do normal (abaixo disso é variação comum do dia a dia)
LIMITE_LENTA = 1.30         # > +30% do normal (igual ao alerta de e-mail)

NOME_ARQUIVO_HTML = "mapa_calor.html"
PADRAO_JSON = "tempos_rotinas_*.json"


# ---------------------------------------------------------------------------
# 1) Leitura dos relatórios de cada execução
# ---------------------------------------------------------------------------
def carregar_execucoes(pasta: Path) -> list[dict]:
    """Lê os tempos_rotinas_*.json da pasta, ignorando arquivos inválidos.

    Retorna a lista ordenada da execução mais antiga para a mais recente.
    """
    execucoes = []
    for arquivo in Path(pasta).glob(PADRAO_JSON):
        try:
            dados = json.loads(arquivo.read_text(encoding="utf-8"))
            datetime.strptime(dados["gerado_em"], "%Y-%m-%d %H:%M:%S")  # valida a data
            if isinstance(dados.get("rotinas"), list):
                execucoes.append(dados)
        except (OSError, ValueError, KeyError, TypeError):
            continue  # arquivo corrompido/incompleto não derruba o mapa
    execucoes.sort(key=lambda e: (e["gerado_em"], e.get("id_execucao", 0)))
    return execucoes


# ---------------------------------------------------------------------------
# 1b) Leitura do banco (RPA.TIMER)
# ---------------------------------------------------------------------------
# "[0278s] - Tempo Login: 70.9s"  |  "[0345s] - Tempo Rotina 'X': 64.5s (com erro: msg)"
LINHA_TEMPO = re.compile(
    r"^\[\d+s\] - Tempo (?:(?P<login>Login)|Rotina '(?P<rotina>.+?)'): (?P<segundos>[\d.]+)s"
    r"(?P<falha> \(com erro(?:: (?P<erro>.*))?\))?\s*$",
    re.DOTALL,
)

SQL_TEMPOS = """
    SELECT ID_EXECUCAO, DESCRICAO, HORARIO_EXECUCAO
      FROM RPA.TIMER
     WHERE SISTEMA = :sistema
       AND DESCRICAO LIKE '%- Tempo %'
       AND HORARIO_EXECUCAO >= SYSDATE - :dias
     ORDER BY HORARIO_EXECUCAO, ID_EXECUCAO
"""


def linhas_para_execucoes(linhas) -> list[dict]:
    """Converte linhas (ID_EXECUCAO, DESCRICAO, HORARIO_EXECUCAO) no mesmo formato dos JSONs.

    Linhas que não são do formato "Tempo ..." são ignoradas (ex.: logs parecidos).
    A execução é identificada por ID + SESSÃO (IDs podem se repetir entre máquinas):
    linhas do mesmo ID separadas por mais de INTERVALO_NOVA_EXECUCAO_MIN = outra execução.
    A data dela é o horário da primeira linha de tempo.
    """
    execucoes: list[dict] = []
    ultima_por_id: dict = {}  # id -> (execução aberta, horário da última linha)
    for id_execucao, descricao, horario in sorted(linhas, key=lambda l: (l[2], str(l[0]))):
        achado = LINHA_TEMPO.match(str(descricao or "").strip())
        if not achado:
            continue
        aberta = ultima_por_id.get(id_execucao)
        if aberta and (horario - aberta[1]).total_seconds() <= INTERVALO_NOVA_EXECUCAO_MIN * 60:
            execucao = aberta[0]
        else:
            execucao = {
                "id_execucao": id_execucao,
                "dia": horario.strftime("%Y-%m-%d"),
                "gerado_em": horario.strftime("%Y-%m-%d %H:%M:%S"),
                "_primeira": horario,
                "rotinas": [],
            }
            execucoes.append(execucao)
        ultima_por_id[id_execucao] = (execucao, horario)
        sucesso = achado.group("falha") is None
        execucao["rotinas"].append({
            "nome": "Login" if achado.group("login") else achado.group("rotina"),
            "caminho": "",
            "segundos": float(achado.group("segundos")),
            "sucesso": sucesso,
            "erro": "" if sucesso else (achado.group("erro") or "erro"),
        })
    # "_primeira" (datetime) fica para montar_execucoes_banco casar com o resumo; ele remove depois.
    return sorted(execucoes, key=lambda e: (e["gerado_em"], str(e["id_execucao"])))


# ---------------------------------------------------------------------------
# 1a) Histórico completo: uma linha por execução, agregada no Oracle
# ---------------------------------------------------------------------------
NOME_TOTAL = "Execução completa"
# Mais que isso sem registros com o mesmo ID_EXECUCAO = começou outra execução
INTERVALO_NOVA_EXECUCAO_MIN = 30

# Para cada execução (ID + dia) o Oracle devolve só o essencial, em vez de trazer
# os milhares de registros por segundo:
#   TOTAL           maior TEMPO_EXECUCAO = linha de encerramento (todas as versões do robô)
#   STATUS_FINAL    ERRO dessa linha: NAO/OK = sucesso; SIM/ERRO = falha
#   LINHAS_TEMPO    > 0 -> versão atual (tem "Tempo Login"/"Tempo Rotina" exatos)
#   ERA_TIR         robô antigo (TIR/Selenium): só há o tempo total
#   INICIO_MENU     Playwright antigo: quando começou a navegação no menu (= fim do login)
#   CAMINHO         texto com o caminho da rotina antiga (para dar nome à linha)
# DESCRICAO pode ser CLOB: TO_CHAR(SUBSTR(...)) permite MAX/KEEP sem erro de tipo.
# Sessões: LAG compara cada linha com a anterior do mesmo ID; um intervalo maior que
# INTERVALO_NOVA_EXECUCAO_MIN abre uma nova sessão (CAST AS DATE funciona com DATE e TIMESTAMP).
SQL_RESUMO_EXECUCOES = f"""
    WITH marcadas AS (
        SELECT ID_EXECUCAO, DESCRICAO, ERRO, TEMPO_EXECUCAO, HORARIO_EXECUCAO,
               CASE WHEN (CAST(HORARIO_EXECUCAO AS DATE)
                          - CAST(LAG(HORARIO_EXECUCAO) OVER (PARTITION BY ID_EXECUCAO ORDER BY HORARIO_EXECUCAO) AS DATE))
                         * 1440 > {INTERVALO_NOVA_EXECUCAO_MIN}
                    THEN 1 ELSE 0 END AS NOVA_SESSAO
          FROM RPA.TIMER
         WHERE SISTEMA = :sistema
           AND HORARIO_EXECUCAO >= SYSDATE - :dias
    ),
    sessoes AS (
        SELECT m.*,
               SUM(NOVA_SESSAO) OVER (PARTITION BY ID_EXECUCAO ORDER BY HORARIO_EXECUCAO
                                      ROWS UNBOUNDED PRECEDING) AS SESSAO
          FROM marcadas m
    )
    SELECT ID_EXECUCAO,
           MIN(HORARIO_EXECUCAO) AS INICIO,
           MAX(TEMPO_EXECUCAO) AS TOTAL,
           MAX(ERRO) KEEP (DENSE_RANK LAST ORDER BY TEMPO_EXECUCAO, HORARIO_EXECUCAO) AS STATUS_FINAL,
           MAX(TO_CHAR(SUBSTR(DESCRICAO, 1, 300)))
               KEEP (DENSE_RANK LAST ORDER BY TEMPO_EXECUCAO, HORARIO_EXECUCAO) AS DESCRICAO_FINAL,
           SUM(CASE WHEN DESCRICAO LIKE '%- Tempo Login:%' THEN 1 ELSE 0 END) AS LINHAS_TEMPO,
           MAX(CASE WHEN DESCRICAO LIKE '%PROTHEUS_TIR%' THEN 1 ELSE 0 END) AS ERA_TIR,
           MIN(CASE WHEN DESCRICAO LIKE '%Acessando menu lateral:%'
                      OR DESCRICAO LIKE '%Executando fluxo configurado:%'
                      OR DESCRICAO LIKE '%Rotina 1/%iniciada%'
                    THEN HORARIO_EXECUCAO END) AS INICIO_MENU,
           MAX(CASE WHEN DESCRICAO LIKE '%Acessando menu lateral:%'
                      OR DESCRICAO LIKE '%Executando fluxo configurado:%'
                    THEN TO_CHAR(SUBSTR(DESCRICAO, 1, 300)) END) AS CAMINHO
      FROM sessoes
     GROUP BY ID_EXECUCAO, SESSAO
    HAVING MAX(TEMPO_EXECUCAO) > 0
"""
PREFIXO_REGISTRO = re.compile(r"^\[(?:\d+s|[A-Z]+)\]\s*-?\s*")   # "[0042s] - " ou "[OK] "


def _limpar_descricao(texto) -> str:
    """Tira o prefixo "[0042s] - " / "[OK] " e espaços repetidos."""
    return " ".join(PREFIXO_REGISTRO.sub("", str(texto or "").strip()).split())


def nome_rotina_legada(caminho: str | None, nomes_conhecidos: list[str]) -> str:
    """Dá nome à rotina de uma execução antiga a partir do texto do caminho.

    Ex.: "Acessando menu lateral: Compras > Movimento > Pedidos de Compra" -> "Pedidos de Compra".
    Se alguma etapa tem o nome de uma rotina já conhecida (das execuções atuais), usa esse
    nome, para o histórico antigo cair na MESMA linha do mapa.
    """
    if not caminho:
        return "Rotina (registro antigo)"
    texto = _limpar_descricao(caminho)
    trecho = texto.split(":", 1)[1] if ":" in texto else texto
    etapas = [e.strip() for e in trecho.split(">") if e.strip()]
    conhecidos = {n.casefold(): n for n in nomes_conhecidos}
    for etapa in etapas:
        if etapa.casefold() in conhecidos:
            return conhecidos[etapa.casefold()]
    if "menu lateral" in texto.lower() and etapas:
        return etapas[-1]          # o caminho antigo do menu terminava na rotina
    return etapas[2] if len(etapas) >= 3 else (trecho or "Rotina (registro antigo)")


def montar_execucoes_banco(resumos, linhas_tempo) -> list[dict]:
    """Junta o resumo de TODAS as execuções com os tempos exatos da versão atual.

    resumos: linhas do SQL_RESUMO_EXECUCOES; linhas_tempo: linhas do SQL_TEMPOS.
    Cada execução ganha o item "Execução completa" (tempo total). Além disso:
    - versão atual: Login e rotinas exatos (linhas "Tempo ...");
    - Playwright antigo: Login e rotina ESTIMADOS (estimado=True) pelo início do menu;
    - TIR: só o tempo total.
    """
    atuais: dict = {}  # id -> execuções atuais (tempos exatos) ainda não casadas com o resumo
    for e in linhas_para_execucoes(linhas_tempo):
        atuais.setdefault(e["id_execucao"], []).append(e)
    nomes_conhecidos = [r["nome"] for lista in atuais.values() for e in lista
                        for r in e["rotinas"] if r["nome"] != "Login"]

    def casar_atual(id_execucao, inicio, total):
        """A execução atual do mesmo ID cujas linhas "Tempo" caem dentro desta sessão."""
        limite = inicio + timedelta(seconds=total + INTERVALO_NOVA_EXECUCAO_MIN * 60)
        for e in atuais.get(id_execucao, []):
            if inicio <= e["_primeira"] <= limite:
                atuais[id_execucao].remove(e)
                return e
        return None

    execucoes = []
    for (id_execucao, inicio, total, status_final, descricao_final,
         linhas_tempo_qtd, era_tir, inicio_menu, caminho) in resumos:
        dia = inicio.strftime("%Y-%m-%d")
        total = float(total or 0)
        sucesso_total = str(status_final or "").upper() in ("NAO", "OK")
        erro_total = "" if sucesso_total else (_limpar_descricao(descricao_final) or "erro")
        atual = casar_atual(id_execucao, inicio, total)

        if atual:
            origem, itens = "atual", list(atual["rotinas"])
        elif era_tir:
            origem, itens = "TIR (robô antigo: só o tempo total)", []
        else:
            origem, itens = "Playwright antigo (Login/rotina estimados)", []
            if inicio_menu is not None:
                login_s = max(0.0, (inicio_menu - inicio).total_seconds())
                itens.append({"nome": "Login", "segundos": login_s, "sucesso": True, "erro": "", "estimado": True})
                itens.append({"nome": nome_rotina_legada(caminho, nomes_conhecidos),
                              "segundos": max(0.0, total - login_s), "sucesso": sucesso_total,
                              "erro": erro_total, "estimado": True})
            elif not sucesso_total:
                # falhou antes de chegar ao menu: o problema foi no login
                itens.append({"nome": "Login", "segundos": total, "sucesso": False,
                              "erro": erro_total, "estimado": True})

        itens.insert(0, {"nome": NOME_TOTAL, "segundos": total, "sucesso": sucesso_total, "erro": erro_total})
        execucoes.append({
            "id_execucao": id_execucao, "dia": dia, "origem": origem,
            "gerado_em": inicio.strftime("%Y-%m-%d %H:%M:%S"), "rotinas": itens,
        })

    # Execuções com tempos atuais mas fora do resumo (não deveria ocorrer): mantém mesmo assim.
    execucoes.extend(dict(e, origem="atual") for lista in atuais.values() for e in lista)
    for e in execucoes:
        e.pop("_primeira", None)
    return sorted(execucoes, key=lambda e: (e["gerado_em"], str(e["id_execucao"])))


def _consultar(sql: str, parametros: dict) -> list[tuple]:
    """Executa uma consulta no Oracle com as mesmas credenciais do LogBanco (.env)."""
    import oracledb  # importado aqui para os testes e o modo "arquivos" não dependerem do Oracle
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env")
    oracledb.defaults.fetch_lobs = False  # DESCRICAO pode ser CLOB: devolve como texto
    conexao = oracledb.connect(user=os.getenv("usernamedb"), password=os.getenv("passworddb"), dsn=os.getenv("dsn"))
    try:
        with conexao.cursor() as cursor:
            cursor.execute(sql, parametros)
            return cursor.fetchall()
    finally:
        conexao.close()


def carregar_execucoes_banco(dias: int = 60, sistema: str = "PROTHEUS") -> list[dict]:
    """Lê do RPA.TIMER o histórico completo dos últimos `dias` (todas as versões do robô)."""
    parametros = {"sistema": sistema, "dias": dias}
    return montar_execucoes_banco(_consultar(SQL_RESUMO_EXECUCOES, parametros),
                                  _consultar(SQL_TEMPOS, parametros))


# ---------------------------------------------------------------------------
# 1c) Detalhe completo de UMA execução (usado pelo serviço api_mapa.py)
# ---------------------------------------------------------------------------
# Todas as linhas que a execução gravou no RPA.TIMER:
#   - a cada 1 s, o cronômetro grava o que o robô está fazendo (ERRO = 'NAO'/'SIM');
#   - as linhas "Tempo ..." de cada item;
#   - ao final, o LogBanco.salvar() grava cada linha do log (ERRO = 'OK'/'WARN'/'ERRO').
SQL_EXECUCAO = """
    SELECT DESCRICAO, ERRO, TEMPO_EXECUCAO, HORARIO_EXECUCAO
      FROM RPA.TIMER
     WHERE ID_EXECUCAO = :id_execucao
       AND SISTEMA = :sistema
       AND HORARIO_EXECUCAO BETWEEN :de AND :ate
     ORDER BY HORARIO_EXECUCAO
"""
PREFIXO_SEGUNDOS = re.compile(r"^\[(\d+)s\] - (.*)$", re.DOTALL)
INICIO_ROTINA = re.compile(r"^Rotina (\d+)/\d+")
# Playwright antigo: o início da navegação no menu marca o fim do login
INICIO_ROTINA_ANTIGA = re.compile(r"^(Acessando menu lateral|Executando fluxo configurado):")


def montar_detalhe(id_execucao: int, linhas) -> dict:
    """Transforma as linhas de UMA execução em informações para o painel de detalhe.

    - tempos: Login e rotinas (iguais às células do mapa);
    - secoes: para o Login e cada rotina, a LINHA DO TEMPO do que o robô fez,
      juntando os registros por segundo repetidos em um passo com duração
      (ex.: "Procurando 'Visualizar'..." durante 18 s) - mostra onde o tempo foi gasto;
    - eventos: avisos e erros do log daquela execução;
    - final: a linha de encerramento (tempo total e situação).
    """
    linhas = [(str(d or ""), str(e or ""), t, h) for d, e, t, h in linhas]

    # 1) Tempos de cada item, na ordem em que aconteceram
    tempos = []
    for descricao, _, _, horario in linhas:
        achado = LINHA_TEMPO.match(descricao.strip())
        if achado:
            sucesso = achado.group("falha") is None
            tempos.append({
                "nome": "Login" if achado.group("login") else achado.group("rotina"),
                "segundos": float(achado.group("segundos")),
                "sucesso": sucesso,
                "erro": "" if sucesso else (achado.group("erro") or "erro"),
                "hora": horario.strftime("%H:%M:%S"),
            })
    nomes_rotinas = [t["nome"] for t in tempos if t["nome"] != "Login"]

    # 2) Linha do tempo (registros por segundo), separada por seção
    secoes: list[dict] = []
    eventos: list[dict] = []
    final = None
    secao_atual = None
    passo_atual = None  # passo aberto: mesmo texto em segundos seguidos
    for descricao, status, tempo, horario in linhas:
        achado = PREFIXO_SEGUNDOS.match(descricao.strip())
        if not achado or LINHA_TEMPO.match(descricao.strip()):
            continue
        texto = " ".join(achado.group(2).split())

        if tempo and int(tempo) > 1:
            # Só a linha de encerramento grava o tempo total (as demais gravam 0 ou 1)
            final = {"texto": texto, "total_segundos": int(tempo), "hora": horario.strftime("%H:%M:%S")}
            continue
        if status in ("WARN", "ERRO"):
            # Aviso/erro do log (ou do cronômetro já marcado com erro): lista de eventos, sem repetir
            if not eventos or eventos[-1]["texto"] != texto:
                eventos.append({"hora": horario.strftime("%H:%M:%S"), "nivel": status, "texto": texto[:400]})
            continue
        if status not in ("NAO", "SIM") or tempo == 0:
            continue  # linhas OK do log e a linha de início: o detalhe usa os registros por segundo

        # Muda de seção quando o robô começa uma rotina ("Rotina 2/3 ...")
        rotina = INICIO_ROTINA.match(texto)
        nome_secao = ("Login" if secao_atual is None else secao_atual["nome"])
        if rotina:
            indice = int(rotina.group(1))
            nome_secao = nomes_rotinas[indice - 1] if indice <= len(nomes_rotinas) else f"Rotina {indice}"
        elif INICIO_ROTINA_ANTIGA.match(texto) and nome_secao == "Login":
            nome_secao = nome_rotina_legada(texto, nomes_rotinas)
        if secao_atual is None or secao_atual["nome"] != nome_secao:
            secao_atual = {"nome": nome_secao, "passos": []}
            secoes.append(secao_atual)
            passo_atual = None

        passos = secao_atual["passos"]
        if passo_atual and passo_atual["acao"] == texto[:300]:
            passo_atual["fim"] = horario
        elif (len(passos) >= 2 and passos[-2]["acao"] == texto[:300] and passos[-1]["registros"] == 1
              and (horario - passos[-2]["fim"]).total_seconds() <= 3):
            # Uma linha de log caiu no meio de um passo que continua: junta os dois pedaços
            passo_atual = passos[-2]
            passo_atual["fim"] = horario
        else:
            passo_atual = {"acao": texto[:300], "inicio": horario, "fim": horario, "registros": 0}
            passos.append(passo_atual)
        passo_atual["registros"] += 1

    # 3) Duração de cada passo = do seu primeiro ao seu último registro (+1 s, um registro por
    #    segundo). Nunca até o passo seguinte: um buraco nos dados não vira duração.
    for passo in (p for s in secoes for p in s["passos"]):
        passo["duracao_s"] = max(1, int(round((passo["fim"] - passo["inicio"]).total_seconds())) + 1)
        passo["hora"] = passo.pop("inicio").strftime("%H:%M:%S")
        passo.pop("fim")
        passo.pop("registros")

    return {
        "id_execucao": id_execucao,
        "inicio": linhas[0][3].strftime("%Y-%m-%d %H:%M:%S") if linhas else None,
        "tempos": tempos,
        "secoes": secoes,
        "eventos": eventos[-50:],
        "final": final,
    }


def recortar_sessao(linhas, inicio: datetime) -> list:
    """Das linhas do ID, fica só com a sessão (execução) que contém o `inicio`.

    Linhas do mesmo ID separadas por mais de INTERVALO_NOVA_EXECUCAO_MIN são de outra
    execução (ex.: o servidor e o PC usaram o mesmo número em dias diferentes).
    """
    sessoes: list[list] = []
    for linha in sorted(linhas, key=lambda l: l[3]):
        if not sessoes or (linha[3] - sessoes[-1][-1][3]).total_seconds() > INTERVALO_NOVA_EXECUCAO_MIN * 60:
            sessoes.append([])
        sessoes[-1].append(linha)
    if not sessoes:
        return []
    # a sessão que contém o início; senão, a que começa mais perto dele
    return min(sessoes, key=lambda s: 0 if s[0][3] <= inicio <= s[-1][3]
               else abs((s[0][3] - inicio).total_seconds()))


def carregar_detalhe_execucao(id_execucao: int, inicio: datetime, sistema: str = "PROTHEUS") -> dict:
    """Busca no RPA.TIMER as linhas da execução que começou em `inicio` e monta o detalhe."""
    janela = timedelta(minutes=INTERVALO_NOVA_EXECUCAO_MIN)
    linhas = _consultar(SQL_EXECUCAO, {
        "id_execucao": id_execucao, "sistema": sistema,
        "de": inicio - janela, "ate": inicio + timedelta(hours=6),
    })
    return montar_detalhe(id_execucao, recortar_sessao(linhas, inicio))


# ---------------------------------------------------------------------------
# 2) Cálculo do "normal" e classificação de cada célula
# ---------------------------------------------------------------------------
def classificar(segundos: float, normal: float | None, sucesso: bool, erro: str) -> str:
    """Devolve o status de uma célula a partir do tempo e do normal da rotina."""
    if not sucesso:
        return "pulada" if erro.upper().startswith("NAO EXECUTADA") else "erro"
    if normal is None or normal <= 0:
        return "sem_base"
    proporcao = segundos / normal
    if proporcao > LIMITE_LENTA:
        return "lenta"
    if proporcao > LIMITE_ATENCAO:
        return "atencao"
    return "normal"


def calcular_mapa(execucoes: list[dict], fonte: str = "arquivos") -> dict:
    """Monta a estrutura usada pelo HTML: colunas, linhas e células classificadas."""
    # Ordem das linhas: Execução completa, Login e as rotinas na ordem em que aparecem.
    rotinas: list[str] = []
    for execucao in execucoes:
        for item in execucao["rotinas"]:
            if item.get("nome") and item["nome"] not in rotinas:
                rotinas.append(item["nome"])
    for fixo in ("Login", NOME_TOTAL):
        if fixo in rotinas:
            rotinas.remove(fixo)
            rotinas.insert(0, fixo)

    historico: dict[str, list[float]] = {nome: [] for nome in rotinas}  # tempos sem erro
    celulas: dict[str, list[dict | None]] = {nome: [] for nome in rotinas}
    colunas = []

    for execucao in execucoes:
        colunas.append({
            "id": execucao.get("id_execucao"),
            "data": execucao["gerado_em"],
            "dia": execucao.get("dia") or execucao["gerado_em"][:10],
            "origem": execucao.get("origem", "atual"),
        })
        por_nome = {item.get("nome"): item for item in execucao["rotinas"]}

        for nome in rotinas:
            item = por_nome.get(nome)
            if item is None:
                celulas[nome].append(None)  # rotina não fazia parte dessa execução
                continue

            segundos = float(item.get("segundos") or 0)
            sucesso = bool(item.get("sucesso"))
            erro = str(item.get("erro") or "")
            anteriores = historico[nome][-JANELA_NORMAL:]
            normal = statistics.median(anteriores) if len(anteriores) >= MINIMO_HISTORICO else None
            status = classificar(segundos, normal, sucesso, erro)

            celulas[nome].append({
                "s": round(segundos, 1),
                "normal": round(normal, 1) if normal else None,
                "var": round((segundos / normal - 1) * 100) if (normal and sucesso) else None,
                "status": status,
                "erro": erro[:300],
                "caminho": item.get("caminho", ""),
                "estimado": bool(item.get("estimado")),  # registro antigo: tempo estimado
            })
            # Só tempos sem erro entram no histórico que define o "normal".
            if sucesso:
                historico[nome].append(segundos)

    normal_atual = {
        nome: (round(statistics.median(historico[nome][-JANELA_NORMAL:]), 1)
               if len(historico[nome]) >= MINIMO_HISTORICO else None)
        for nome in rotinas
    }
    return {
        "gerado_em": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "fonte": fonte,
        "parametros": {
            "janela": JANELA_NORMAL,
            "minimo": MINIMO_HISTORICO,
            "atencao_pct": round((LIMITE_ATENCAO - 1) * 100),
            "lenta_pct": round((LIMITE_LENTA - 1) * 100),
        },
        "colunas": colunas,
        "rotinas": rotinas,
        "celulas": celulas,
        "normal_atual": normal_atual,
    }


# ---------------------------------------------------------------------------
# 3) Geração do HTML (template em mapa_calor_template.html)
# ---------------------------------------------------------------------------
TEMPLATE_PATH = Path(__file__).resolve().parent / "mapa_calor_template.html"


def gerar_html(mapa: dict) -> str:
    """Injeta os dados no template. '</' é escapado para não fechar o <script>."""
    dados_json = json.dumps(mapa, ensure_ascii=False).replace("</", "<\\/")
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    return template.replace("/*__DADOS__*/null", dados_json)


def gerar_mapa_calor(
    pasta_logs: Path,
    destino: Path | None = None,
    fonte: str = "banco",
    dias: int = 60,
) -> Path:
    """Gera o HTML a partir do banco (padrão) ou dos JSONs da pasta.

    Se o banco falhar (rede, credencial...), usa os JSONs locais para não ficar sem mapa.
    """
    pasta_logs = Path(pasta_logs)
    destino = destino or (pasta_logs / NOME_ARQUIVO_HTML)
    execucoes = None
    if fonte == "banco":
        try:
            execucoes = carregar_execucoes_banco(dias)
            fonte_usada = f"banco RPA.TIMER (últimos {dias} dias)"
        except Exception as erro:
            logger.warning("Mapa de calor: banco indisponível (%s); usando os JSONs locais", erro)
    if execucoes is None:
        execucoes = carregar_execucoes(pasta_logs)
        fonte_usada = "arquivos locais (tempos_rotinas_*.json)"
    destino.write_text(gerar_html(calcular_mapa(execucoes, fonte_usada)), encoding="utf-8")
    return destino


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Gera o mapa de calor dos tempos das rotinas")
    parser.add_argument("pasta", nargs="?", default=str(Path(__file__).resolve().parent / "log_playwright"),
                        help="pasta log_playwright (destino do HTML e dos JSONs)")
    parser.add_argument("--fonte", choices=["banco", "arquivos"], default="banco")
    parser.add_argument("--dias", type=int, default=60, help="período lido do banco")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")
    print(f"Mapa de calor gerado em: {gerar_mapa_calor(Path(args.pasta), fonte=args.fonte, dias=args.dias)}")
