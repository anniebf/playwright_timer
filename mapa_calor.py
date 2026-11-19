"""
mapa_calor.py - Gera o mapa de calor (HTML interativo) com o tempo das rotinas.

LÓGICA GERAL
------------
1. Cada execução do cronômetro grava log_playwright/tempos_rotinas_<id>.json
   (ver rotinas.salvar_relatorio) com o tempo do Login e de cada rotina.
2. Este módulo lê todos esses JSONs e monta uma grade:
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

Uso manual (regera a partir dos JSONs existentes):
    python mapa_calor.py                 # usa ./log_playwright
    python mapa_calor.py C:\\caminho\\log_playwright
"""
from __future__ import annotations

import json
import statistics
import sys
from datetime import datetime
from pathlib import Path

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


def calcular_mapa(execucoes: list[dict]) -> dict:
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


def gerar_mapa_calor(pasta_logs: Path, destino: Path | None = None) -> Path:
    """Lê os JSONs da pasta e grava o HTML. Retorna o caminho do arquivo gerado."""
    pasta_logs = Path(pasta_logs)
    destino = destino or (pasta_logs / NOME_ARQUIVO_HTML)
    mapa = calcular_mapa(carregar_execucoes(pasta_logs))
    destino.write_text(gerar_html(mapa), encoding="utf-8")
    return destino


if __name__ == "__main__":
    pasta = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "log_playwright"
    print(f"Mapa de calor gerado em: {gerar_mapa_calor(pasta)}")
