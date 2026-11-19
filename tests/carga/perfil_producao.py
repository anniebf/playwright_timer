"""
perfil_producao.py - Números de PRODUÇÃO usados pelos testes de carga.

LÓGICA GERAL
------------
Um teste de carga só diz algo se a carga simulada for parecida com a real.
Por isso os valores abaixo vêm do que o robô faz hoje em produção:

- Agenda: a DAG `Docker_playwright` roda "30 * * * *" (1x por hora, 24x por dia),
  com max_active_runs=1 e dagrun_timeout=3600 s (BF_DockerPlaywright.py).
- Duração e volume de log: medidos em log_playwright/ em 19/11/2026
  (execuções 2457 a 2465: 107 s a 242 s, logs de 520 a 620 linhas, 2 rotinas).
- Mapa de calor: lê os últimos MapaCalorDias = 60 dias do RPA.TIMER.

Cada número pode ser trocado por variável de ambiente, sem editar o código
(ex.: CARGA_ROTINAS=40 para simular uma lista de rotinas maior).

ORÇAMENTOS = critérios de aceite. Se um teste passa do orçamento, ele FALHA e a
mensagem diz quanto passou. Ajuste aqui se o time combinar outro limite.
"""
import os


def _num(nome: str, padrao: float) -> float:
    """Lê um número de variável de ambiente (CARGA_*) ou usa o padrão."""
    return float(os.getenv(nome, padrao))


# ---------------------------------------------------------------------------
# Carga de produção (o que acontece hoje)
# ---------------------------------------------------------------------------
EXECUCOES_POR_DIA = int(_num("CARGA_EXECUCOES_DIA", 24))      # cron "30 * * * *"
DIAS_MAPA_CALOR = int(_num("CARGA_DIAS_MAPA", 60))            # MapaCalorDias
ROTINAS_ATUAIS = int(_num("CARGA_ROTINAS_ATUAIS", 2))         # config.json de hoje
DURACAO_EXECUCAO_S = int(_num("CARGA_DURACAO_S", 242))        # pior execução medida (id 2459)
LINHAS_LOG_POR_EXECUCAO = int(_num("CARGA_LINHAS_LOG", 620))  # maior log medido
JANELA_AGENDA_S = int(_num("CARGA_JANELA_S", 3600))           # intervalo da DAG = dagrun_timeout

# Latência para ABRIR uma conexão com o Oracle pela rede (login + sessão).
# 50 ms é um valor otimista para rede interna; use CARGA_LATENCIA_ORACLE=0.3 para um dia ruim.
LATENCIA_CONEXAO_ORACLE_S = _num("CARGA_LATENCIA_ORACLE", 0.05)

# ---------------------------------------------------------------------------
# Crescimento previsto (estresse): mais rotinas cadastradas no config.json
# ---------------------------------------------------------------------------
ROTINAS_ALVO = int(_num("CARGA_ROTINAS", 20))

# ---------------------------------------------------------------------------
# Orçamentos (critérios de aceite)
# ---------------------------------------------------------------------------
ORCAMENTO_CONEXOES_POR_EXECUCAO = int(_num("CARGA_ORC_CONEXOES", 50))
ORCAMENTO_SALVAR_LOG_S = _num("CARGA_ORC_SALVAR_S", 10)        # LogBanco.salvar() no fim
ORCAMENTO_MAPA_CALOR_S = _num("CARGA_ORC_MAPA_S", 10)          # gerar o mapa no fim da execução
ORCAMENTO_HTML_MB = _num("CARGA_ORC_HTML_MB", 10)              # tamanho do mapa_calor.html
ORCAMENTO_MEMORIA_MB = _num("CARGA_ORC_MEMORIA_MB", 5)         # crescimento de memória em loop
MARGEM_AGENDA = _num("CARGA_MARGEM_AGENDA", 0.5)               # usar no máx. 50% da hora no caso real


def medir(nome: str, valor, unidade: str = "") -> None:
    """Mostra uma medição no console com prefixo fácil de filtrar ([CARGA])."""
    print(f"\n[CARGA] {nome}: {valor}{(' ' + unidade) if unidade else ''}", flush=True)
