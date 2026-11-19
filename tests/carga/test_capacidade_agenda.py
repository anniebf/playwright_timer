"""
test_capacidade_agenda.py - A execução cabe na janela de 1 hora da DAG?

LÓGICA GERAL
------------
A DAG roda de hora em hora com dagrun_timeout=3600 s e max_active_runs=1.
Se uma execução passar de 1 h, o Airflow mata a task e a próxima hora atrasa
(ou, se o container continuar vivo, duas sessões disputam licença do Protheus).

Três verificações, usando dados REAIS (somente leitura):
1. Tempo medido: p95 das execuções em log_playwright/tempos_rotinas_*.json
   precisa usar no máximo MARGEM_AGENDA (50%) da hora.
2. Pior caso pela configuração: soma dos timeouts do config.json, como se cada
   espera estourasse, incluindo as novas tentativas do login (ExecutionRetries).
3. Projeção: com ROTINAS_ALVO rotinas e o p95 real por rotina, ainda cabe?

Nada aqui abre navegador ou banco; só lê config.json e os JSONs de tempos.
"""
import json
import statistics
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import perfil_producao as P  # noqa: E402

RAIZ = Path(__file__).resolve().parents[2]


def ler_config() -> dict:
    """config.json real (ou o modelo, se o real não existir nesta máquina)."""
    arquivo = RAIZ / "config.json" if (RAIZ / "config.json").exists() else RAIZ / "config.example.json"
    return json.loads(arquivo.read_text(encoding="utf-8"))


def ler_execucoes_reais() -> list[dict]:
    execucoes = []
    for arquivo in (RAIZ / "log_playwright").glob("tempos_rotinas_*.json"):
        try:
            execucoes.append(json.loads(arquivo.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return execucoes


def p95(valores: list[float]) -> float:
    if len(valores) < 2:
        return valores[0] if valores else 0.0
    return statistics.quantiles(valores, n=20, method="inclusive")[-1]


def pior_caso_segundos(config: dict, quantidade_rotinas: int | None = None) -> tuple[float, float, float]:
    """(login, por rotina, total) somando os timeouts como se todos estourassem.

    Conservador: cada etapa do caminho é contada pelo MAIOR custo possível entre
    etapa de menu (MenuStepTimeout x MenuRetries) e ação (pausa + RotinaTimeout +
    AcaoEfeitoTimeout x AcaoRetries), já que o código só decide isso na tela.
    """
    g = lambda chave, padrao: float(config.get(chave, padrao)) / 1000  # ms -> s
    login = float(config.get("TimeOut", 30)) + g("ParametrosTimeout", 60000) + g("EmpresaTimeout", 15000) \
        + g("EntrarTimeout", 15000) + g("MfaTimeout", 60000) + g("MenuTimeout", 60000)
    tentativas_login = 1 + int(config.get("ExecutionRetries", 2))

    etapa_menu = g("MenuStepTimeout", 15000) * int(config.get("MenuRetries", 2))
    etapa_acao = g("PausaAntesAcao", 2000) + g("RotinaTimeout", 90000) \
        + g("AcaoEfeitoTimeout", 15000) * int(config.get("AcaoRetries", 2))
    voltar = g("FecharRotinaTimeout", 10000) + g("VoltarTelaTimeout", 15000) * int(config.get("VoltarTelaTentativas", 3))

    caminhos = [r["Fluxo"] if isinstance(r, dict) else r for r in config.get("Rotinas", [config.get("Fluxo", "")])]
    etapas = [len([e for e in c.split(">") if e.strip()]) for c in caminhos] or [6]
    if quantidade_rotinas is not None:  # projeção: repete o caminho médio
        etapas = [round(statistics.mean(etapas))] * quantidade_rotinas
    rotinas = sum(g("RotinaAbrirTimeout", 60000) + n * max(etapa_menu, etapa_acao) + voltar for n in etapas)
    por_rotina = rotinas / len(etapas)
    return login * tentativas_login, por_rotina, login * tentativas_login + rotinas


class TestCapacidadeAgenda(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = ler_config()
        cls.execucoes = ler_execucoes_reais()

    def test_p95_real_usa_no_maximo_metade_da_hora(self):
        totais = [sum(float(r.get("segundos") or 0) for r in e.get("rotinas", [])) for e in self.execucoes]
        if not totais:
            self.skipTest("sem tempos_rotinas_*.json em log_playwright/ nesta máquina")
        valor = p95(totais)
        P.medir(f"execução real: p95 de {len(totais)} execuções", round(valor, 1), "s")
        self.assertLessEqual(valor, P.JANELA_AGENDA_S * P.MARGEM_AGENDA)

    def test_pior_caso_da_configuracao_atual_cabe_no_dagrun_timeout(self):
        login, por_rotina, total = pior_caso_segundos(self.config)
        P.medir("pior caso: login (com todas as novas tentativas)", round(login), "s")
        P.medir("pior caso: por rotina", round(por_rotina), "s")
        P.medir("pior caso: execução inteira", round(total), "s")
        self.assertLessEqual(
            total, P.JANELA_AGENDA_S,
            f"Se tudo estourar o timeout, a execução leva {total / 60:.0f} min e passa do dagrun_timeout.",
        )

    def test_projecao_com_rotinas_alvo(self):
        if not self.execucoes:
            self.skipTest("sem histórico real para projetar")
        logins = [r["segundos"] for e in self.execucoes for r in e["rotinas"] if r["nome"] == "Login"]
        rotinas = [r["segundos"] for e in self.execucoes for r in e["rotinas"] if r["nome"] != "Login"]
        # +volta à tela principal e prints: medido no log ~ 10 s por rotina fora do tempo da rotina
        projetado = p95(logins) + P.ROTINAS_ALVO * (p95(rotinas) + 10)
        cabem = int((P.JANELA_AGENDA_S * P.MARGEM_AGENDA - p95(logins)) // (p95(rotinas) + 10))
        _, _, pior_alvo = pior_caso_segundos(self.config, P.ROTINAS_ALVO)
        P.medir(f"projeção p95 com {P.ROTINAS_ALVO} rotinas", round(projetado), "s")
        P.medir(f"pior caso com {P.ROTINAS_ALVO} rotinas", round(pior_alvo), "s")
        P.medir("rotinas que cabem em 50% da hora (p95 real)", cabem)
        self.assertLessEqual(projetado, P.JANELA_AGENDA_S * P.MARGEM_AGENDA)


if __name__ == "__main__":
    unittest.main()
