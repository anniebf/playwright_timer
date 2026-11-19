"""
test_carga_execucao.py - O laço de rotinas sob carga (muitas rotinas, muitas execuções).

LÓGICA GERAL
------------
ProtheusPlaywrightMainFlow.executar_rotinas() é o coração de cada execução:
para cada rotina abre um log próprio (FileHandler), cronometra, grava o tempo no
banco, volta à tela principal e fecha o log. Se algo vazar a cada rotina
(handler não removido, arquivo não fechado, memória), em produção o efeito soma
rotina após rotina.

Para testar SÓ essa lógica, sem navegador, usamos um "fluxo falso": um objeto
que tem os métodos que executar_rotinas() chama (executar_rotina,
voltar_tela_principal, verificar_licenca, _fechar_dialogo), e chamamos o
método REAL da classe passando esse objeto como self. O tratamento de falha
(_tratar_falha_rotina) também é o real. Banco e e-mail são dublês.
"""
import os
import sys
import time
import tracemalloc
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import perfil_producao as P  # noqa: E402
from ambiente_isolado import obter_ambiente  # noqa: E402


class FluxoFalso:
    """Faz o papel da tela do Protheus. `falhar_em` = índices (1..N) de rotinas que dão erro."""

    def __init__(self, main, falhar_em=(), perder_tela_apos=None):
        self.main = main
        self.falhar_em = set(falhar_em)
        self.perder_tela_apos = perder_tela_apos
        self.executadas = 0
        Classe = main.ProtheusPlaywrightMainFlow
        # métodos reais da classe, usando este objeto como self
        self.executar_rotinas = Classe.executar_rotinas.__get__(self)
        self._tratar_falha_rotina = Classe._tratar_falha_rotina.__get__(self)

    def executar_rotina(self, cronometro, rotina, indice, total):
        self.executadas += 1
        if indice in self.falhar_em:
            raise self.main.PlaywrightTimeoutError(f"Elemento 'Visualizar' não foi encontrado (rotina {indice})")

    def voltar_tela_principal(self, cronometro, rotina, ja_fechou):
        return not (self.perder_tela_apos and self.executadas >= self.perder_tela_apos)

    def verificar_licenca(self):
        pass

    def _fechar_dialogo(self):
        pass


def arquivos_abertos() -> int | None:
    """Quantidade de descritores abertos (Linux). No Windows retorna None e o item é pulado."""
    pasta = Path("/proc/self/fd")
    return len(os.listdir(pasta)) if pasta.exists() else None


class TestCargaExecucao(unittest.TestCase):
    def setUp(self):
        self.amb = obter_ambiente()
        self.amb.banco.resetar()
        self.amb.emails.chamadas.clear()
        self.main = self.amb.main_playwright
        self.Rotina = self.amb.rotinas.Rotina

    def _rotinas(self, quantidade):
        return [self.Rotina(f"Rotina {i:02d}", ("Compras", "Movimento", f"Rotina {i:02d}", "Visualizar", "sair"))
                for i in range(1, quantidade + 1)]

    def _cronometro(self):
        return self.amb.LogBanco.CronometroExecucao()  # sem iniciar(): sem thread, só registrar_tempo

    # ------------------------------------------------------------------
    def test_muitas_execucoes_sem_vazar_handlers_memoria_ou_arquivos(self):
        """Simula 1 dia de produção com as rotinas-alvo e confere que nada se acumula."""
        import logging
        raiz = logging.getLogger()
        rotinas = self._rotinas(P.ROTINAS_ALVO)
        execucoes = P.EXECUCOES_POR_DIA

        # aquecimento (primeira execução cria pastas, caches de regex etc.)
        FluxoFalso(self.main).executar_rotinas(self._cronometro(), rotinas)
        handlers_antes, fds_antes = len(raiz.handlers), arquivos_abertos()
        tracemalloc.start()
        memoria_antes = tracemalloc.take_snapshot()

        inicio = time.perf_counter()
        for _ in range(execucoes):
            resultados = FluxoFalso(self.main).executar_rotinas(self._cronometro(), rotinas)
            self.assertEqual(len(resultados), P.ROTINAS_ALVO)
            self.assertTrue(all(r.sucesso for r in resultados))
        duracao = time.perf_counter() - inicio

        memoria_depois = tracemalloc.take_snapshot()
        tracemalloc.stop()
        crescimento_mb = sum(s.size_diff for s in memoria_depois.compare_to(memoria_antes, "filename")) / 1024 / 1024

        P.medir(f"{execucoes} execuções x {P.ROTINAS_ALVO} rotinas: overhead do código", round(duracao, 2), "s")
        P.medir("crescimento de memória", round(crescimento_mb, 2), "MB")
        P.medir("handlers de log (antes/depois)", f"{handlers_antes}/{len(raiz.handlers)}")
        self.assertEqual(len(raiz.handlers), handlers_antes, "handler de log de rotina não foi removido")
        self.assertLessEqual(crescimento_mb, P.ORCAMENTO_MEMORIA_MB)
        if fds_antes is not None:
            P.medir("arquivos abertos (antes/depois)", f"{fds_antes}/{arquivos_abertos()}")
            self.assertLessEqual(arquivos_abertos() - fds_antes, 3, "arquivos de log ficaram abertos")
        # 1 INSERT de tempo por rotina por execução
        self.assertEqual(len(self.amb.banco.inserts), (execucoes + 1) * P.ROTINAS_ALVO)

    def test_falhas_no_meio_nao_param_as_proximas(self):
        rotinas = self._rotinas(P.ROTINAS_ALVO)
        falhas = {2, 7, P.ROTINAS_ALVO}
        resultados = FluxoFalso(self.main, falhar_em=falhas).executar_rotinas(self._cronometro(), rotinas)
        self.assertEqual([i for i, r in enumerate(resultados, 1) if not r.sucesso], sorted(falhas))
        self.assertTrue(all("Visualizar" in resultados[i - 1].erro for i in falhas))
        P.medir("e-mails de falha disparados em 1 execução", len(self.amb.emails.chamadas))
        self.assertEqual(self.amb.emails.chamadas.count("enviar_erro_execucao"), len(falhas))

    def test_tela_principal_perdida_pula_o_resto_rapido(self):
        """Sem tela principal as rotinas seguintes viram 'NAO EXECUTADA' sem esperar timeouts."""
        rotinas = self._rotinas(P.ROTINAS_ALVO)
        fluxo = FluxoFalso(self.main, perder_tela_apos=1)
        inicio = time.perf_counter()
        resultados = fluxo.executar_rotinas(self._cronometro(), rotinas)
        duracao = time.perf_counter() - inicio
        self.assertEqual(fluxo.executadas, 1)
        self.assertTrue(all(r.erro.startswith("NAO EXECUTADA") for r in resultados[1:]))
        self.assertLess(duracao, 1)

    def test_falha_em_todas_as_rotinas_dispara_um_email_por_rotina(self):
        """Documenta o volume de alertas num dia ruim: hoje é 1 e-mail por rotina com erro."""
        rotinas = self._rotinas(P.ROTINAS_ALVO)
        FluxoFalso(self.main, falhar_em=range(1, P.ROTINAS_ALVO + 1)).executar_rotinas(self._cronometro(), rotinas)
        por_hora = self.amb.emails.chamadas.count("enviar_erro_execucao")
        P.medir(f"e-mails/hora com o Protheus fora ({P.ROTINAS_ALVO} rotinas)", por_hora)
        P.medir("e-mails/dia nesse cenário", por_hora * P.EXECUCOES_POR_DIA)
        self.assertEqual(por_hora, P.ROTINAS_ALVO)


if __name__ == "__main__":
    unittest.main()
