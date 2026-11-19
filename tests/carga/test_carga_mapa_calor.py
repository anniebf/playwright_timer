"""
test_carga_mapa_calor.py - Mapa de calor com o histórico que produção acumula.

LÓGICA GERAL
------------
No fim de CADA execução o robô regenera o mapa: lê do RPA.TIMER as linhas
"Tempo ..." dos últimos 60 dias, calcula o "normal" de cada rotina e grava
um HTML com todos os dados embutidos. Com 24 execuções/dia isso dá:
    60 dias x 24 = 1.440 execuções (colunas) x (Login + N rotinas) linhas.

Os testes geram esse histórico sintético (5% de execuções com erro, tempos
variando em torno do normal) e passam pelo caminho real do código:
gerar_mapa_calor() -> _consultar() [dublê] -> linhas_para_execucoes()
-> calcular_mapa() -> gerar_html(). Medem tempo e tamanho do HTML para a
lista de rotinas de hoje e para o crescimento previsto (ROTINAS_ALVO).

Também cobre o fallback (banco fora) com 1 ano de JSONs acumulados no
volume log_playwright/ e o detalhe de UMA execução longa (montar_detalhe).
"""
import json
import random
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import perfil_producao as P  # noqa: E402
from ambiente_isolado import obter_ambiente  # noqa: E402


def historico_banco(dias: int, por_dia: int, rotinas: int, semente: int = 7) -> list[tuple]:
    """Linhas (ID_EXECUCAO, DESCRICAO, HORARIO_EXECUCAO) como o SQL_TEMPOS devolve."""
    aleatorio = random.Random(semente)  # semente fixa = teste determinístico
    inicio = datetime(2026, 9, 20, 0, 30)
    linhas, id_exec = [], 1
    for dia in range(dias):
        for hora in range(por_dia):
            momento = inicio + timedelta(days=dia, hours=hora)
            segundos = 0
            itens = [("Login", 50)] + [(f"Rotina {i:02d}", 30 + i) for i in range(1, rotinas + 1)]
            for nome, base in itens:
                tempo = round(base * aleatorio.uniform(0.85, 1.45), 1)
                segundos += int(tempo)
                rotulo = "Login" if nome == "Login" else f"Rotina '{nome}'"
                erro = " (com erro: Elemento 'Visualizar' não foi encontrado dentro de 60000ms)" \
                    if aleatorio.random() < 0.05 else ""
                linhas.append((id_exec, f"[{segundos:04d}s] - Tempo {rotulo}: {tempo}s{erro}",
                               momento + timedelta(seconds=segundos)))
            id_exec += 1
    return linhas


class TestCargaMapaCalor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.amb = obter_ambiente()
        cls.mapa = cls.amb.mapa_calor
        cls._consultar_original = cls.mapa._consultar

    @classmethod
    def tearDownClass(cls):
        cls.mapa._consultar = cls._consultar_original

    def _gerar_pelo_banco(self, rotinas: int):
        """Roda gerar_mapa_calor(fonte='banco') com o _consultar trocado pelo histórico sintético."""
        linhas = historico_banco(P.DIAS_MAPA_CALOR, P.EXECUCOES_POR_DIA, rotinas)
        self.mapa._consultar = lambda sql, parametros: linhas
        with tempfile.TemporaryDirectory() as pasta:
            inicio = time.perf_counter()
            arquivo = self.mapa.gerar_mapa_calor(Path(pasta), fonte="banco", dias=P.DIAS_MAPA_CALOR)
            duracao = time.perf_counter() - inicio
            tamanho_mb = arquivo.stat().st_size / 1024 / 1024
            html = arquivo.read_text(encoding="utf-8")
        return linhas, duracao, tamanho_mb, html

    def _verificar(self, rotinas: int):
        linhas, duracao, tamanho_mb, html = self._gerar_pelo_banco(rotinas)
        P.medir(f"mapa ({rotinas} rotinas): linhas lidas do RPA.TIMER", len(linhas))
        P.medir(f"mapa ({rotinas} rotinas): tempo para gerar", round(duracao, 2), "s")
        P.medir(f"mapa ({rotinas} rotinas): tamanho do HTML", round(tamanho_mb, 2), "MB")
        self.assertIn("banco RPA.TIMER", html, "o mapa caiu no fallback em vez de usar o banco")
        self.assertLessEqual(duracao, P.ORCAMENTO_MAPA_CALOR_S, f"mapa levou {duracao:.1f}s")
        self.assertLessEqual(tamanho_mb, P.ORCAMENTO_HTML_MB, f"HTML com {tamanho_mb:.1f} MB")

    def test_mapa_60_dias_com_rotinas_atuais(self):
        self._verificar(P.ROTINAS_ATUAIS)

    def test_mapa_60_dias_com_rotinas_alvo(self):
        self._verificar(P.ROTINAS_ALVO)

    def test_classificacao_correta_sob_carga(self):
        """Com muitos dados, o 'normal' continua sendo a mediana das últimas 10 sem erro."""
        linhas = historico_banco(P.DIAS_MAPA_CALOR, P.EXECUCOES_POR_DIA, P.ROTINAS_ATUAIS)
        execucoes = self.mapa.linhas_para_execucoes(linhas)
        resultado = self.mapa.calcular_mapa(execucoes)
        self.assertEqual(len(resultado["colunas"]), P.DIAS_MAPA_CALOR * P.EXECUCOES_POR_DIA)
        # Recalcula à mão o normal da última coluna da Rotina 01 e compara.
        tempos_ok = [r["segundos"] for e in execucoes for r in e["rotinas"]
                     if r["nome"] == "Rotina 01" and r["sucesso"]]
        import statistics
        self.assertEqual(resultado["normal_atual"]["Rotina 01"], round(statistics.median(tempos_ok[-10:]), 1))

    def test_fallback_json_com_um_ano_acumulado(self):
        """Banco fora: o mapa lê os tempos_rotinas_*.json do volume (que não têm limpeza)."""
        execucoes_ano = 365 * P.EXECUCOES_POR_DIA
        with tempfile.TemporaryDirectory() as pasta:
            pasta = Path(pasta)
            inicio_ano = datetime(2025, 11, 19, 0, 30)
            for i in range(execucoes_ano):
                (pasta / f"tempos_rotinas_{i}.json").write_text(json.dumps({
                    "id_execucao": i,
                    "gerado_em": (inicio_ano + timedelta(hours=i)).strftime("%Y-%m-%d %H:%M:%S"),
                    "rotinas": [{"nome": "Login", "segundos": 50, "sucesso": True, "erro": ""},
                                {"nome": "Rotina 01", "segundos": 30, "sucesso": True, "erro": ""}],
                }), encoding="utf-8")
            self.mapa._consultar = lambda sql, p: (_ for _ in ()).throw(RuntimeError("banco fora (simulado)"))
            inicio = time.perf_counter()
            arquivo = self.mapa.gerar_mapa_calor(pasta, fonte="banco", dias=P.DIAS_MAPA_CALOR)
            duracao = time.perf_counter() - inicio
            dados = arquivo.read_text(encoding="utf-8")
        colunas = dados.count('"data":')
        P.medir("fallback: JSONs lidos", execucoes_ano)
        P.medir("fallback: tempo para gerar", round(duracao, 2), "s")
        P.medir("fallback: colunas no mapa", colunas)
        self.assertLessEqual(duracao, P.ORCAMENTO_MAPA_CALOR_S)
        limite = P.DIAS_MAPA_CALOR * P.EXECUCOES_POR_DIA
        self.assertLessEqual(
            colunas, limite,
            f"No fallback o mapa mostrou {colunas} execuções; o período de {P.DIAS_MAPA_CALOR} dias "
            f"(máx. {limite}) não é aplicado aos JSONs locais.",
        )

    def test_detalhe_de_uma_execucao_longa(self):
        """montar_detalhe() com 1 h de registros por segundo + log completo (pior caso)."""
        inicio = datetime(2026, 11, 19, 8, 30)
        linhas = [(f"[{s:04d}s] - Rotina {1 + s // 600}/6 - Etapa 3/6: procurando 'Visualizar'",
                   "NAO", 1, inicio + timedelta(seconds=s)) for s in range(P.JANELA_AGENDA_S)]
        linhas += [(f"[{s:04d}s] - linha de log {s}", "WARN" if s % 40 == 0 else "OK", 1,
                    inicio + timedelta(seconds=s)) for s in range(P.LINHAS_LOG_POR_EXECUCAO)]
        linhas.sort(key=lambda linha: linha[3])
        comeco = time.perf_counter()
        detalhe = self.mapa.montar_detalhe(1, linhas)
        duracao = time.perf_counter() - comeco
        P.medir("detalhe de 1 execução de 1 h", round(duracao, 3), "s")
        self.assertLessEqual(duracao, 2)
        self.assertLessEqual(len(detalhe["eventos"]), 50)


if __name__ == "__main__":
    unittest.main()
