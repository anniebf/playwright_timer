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
2. Este módulo transforma essas linhas em uma grade:
       linhas  = Login + rotinas
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
5. Gera um único arquivo HTML autocontido (sem internet), com filtros, tooltip e
   visão em tabela: log_playwright/mapa_calor.html. Ele é regenerado ao final de
   cada execução; basta abrir/atualizar no navegador.

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
from datetime import datetime
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
    A data da execução é o horário da primeira linha de tempo daquele ID.
    """
    por_id: dict = {}
    for id_execucao, descricao, horario in linhas:
        achado = LINHA_TEMPO.match(str(descricao or "").strip())
        if not achado:
            continue
        execucao = por_id.setdefault(id_execucao, {
            "id_execucao": id_execucao,
            "gerado_em": horario.strftime("%Y-%m-%d %H:%M:%S"),
            "rotinas": [],
        })
        sucesso = achado.group("falha") is None
        execucao["rotinas"].append({
            "nome": "Login" if achado.group("login") else achado.group("rotina"),
            "caminho": "",
            "segundos": float(achado.group("segundos")),
            "sucesso": sucesso,
            "erro": "" if sucesso else (achado.group("erro") or "erro"),
        })
    return sorted(por_id.values(), key=lambda e: (e["gerado_em"], e["id_execucao"]))


def carregar_execucoes_banco(dias: int = 60, sistema: str = "PROTHEUS") -> list[dict]:
    """Lê as linhas "Tempo ..." do RPA.TIMER dos últimos `dias` (mesmas credenciais do LogBanco)."""
    import oracledb  # importado aqui para os testes e o modo "arquivos" não dependerem do Oracle
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env")
    oracledb.defaults.fetch_lobs = False  # DESCRICAO pode ser CLOB: devolve como texto
    conexao = oracledb.connect(user=os.getenv("usernamedb"), password=os.getenv("passworddb"), dsn=os.getenv("dsn"))
    try:
        with conexao.cursor() as cursor:
            cursor.execute(SQL_TEMPOS, {"sistema": sistema, "dias": dias})
            return linhas_para_execucoes(cursor.fetchall())
    finally:
        conexao.close()


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
    # Ordem das linhas: Login primeiro, depois as rotinas na ordem em que aparecem.
    rotinas: list[str] = []
    for execucao in execucoes:
        for item in execucao["rotinas"]:
            if item.get("nome") and item["nome"] not in rotinas:
                rotinas.append(item["nome"])
    if "Login" in rotinas:
        rotinas.remove("Login")
        rotinas.insert(0, "Login")

    historico: dict[str, list[float]] = {nome: [] for nome in rotinas}  # tempos sem erro
    celulas: dict[str, list[dict | None]] = {nome: [] for nome in rotinas}
    colunas = []

    for execucao in execucoes:
        colunas.append({"id": execucao.get("id_execucao"), "data": execucao["gerado_em"]})
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
