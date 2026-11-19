"""
test_carga_banco.py - Carga que UMA execução do robô coloca no Oracle (RPA.TIMER).

LÓGICA GERAL
------------
O LogBanco grava no banco em três momentos:
  1. cronômetro: 1 INSERT por segundo durante toda a execução (thread de fundo);
  2. registrar_tempo: 1 INSERT por item (Login + cada rotina);
  3. salvar(): no fim, 1 INSERT por linha do log geral.
Cada INSERT chama registrar_timer(), que ABRE e FECHA uma conexão nova.

Estes testes rodam o LogBanco de verdade contra o BancoFalso (ver ambiente_isolado.py)
e medem: quantas conexões uma execução de produção abre, quanto tempo o salvar()
leva com a latência de rede do Oracle, e o que acontece se o banco recusar conexões
(ex.: ORA-12519 quando o listener atinge o limite de sessões).

Rodar (na raiz do projeto):  python -m unittest discover -s tests/carga -v
"""
import os
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import perfil_producao as P  # noqa: E402
from ambiente_isolado import obter_ambiente  # noqa: E402


def escrever_log_sintetico(pasta: Path, linhas: int) -> Path:
    """Cria um log no formato do logging_config, com o mtime mais novo da pasta.

    O salvar() grava o .log MAIS RECENTE de log_playwright/, então este arquivo
    precisa ser o último modificado.
    """
    arquivo = pasta / "9999-12-31_23-59_protheus_timer.log"
    conteudo = "\n".join(
        f"2026-11-19 09:{(i // 60) % 60:02d}:{i % 60:02d} - main_playwright_from_main - "
        f"{'WARNING' if i % 50 == 0 else 'INFO'} - Rotina 1/2 - Etapa {i}: procurando 'Visualizar'"
        for i in range(linhas)
    )
    arquivo.write_text(conteudo + "\n", encoding="utf-8")
    futuro = time.time() + 3600
    os.utime(arquivo, (futuro, futuro))
    return arquivo


class TestCargaBanco(unittest.TestCase):
    def setUp(self):
        self.amb = obter_ambiente()
        self.amb.banco.resetar()
        self.log_criado: Path | None = None

    def tearDown(self):
        self.amb.banco.resetar()
        if self.log_criado and self.log_criado.exists():
            self.log_criado.unlink()

    # ------------------------------------------------------------------
    def test_salvar_abre_uma_conexao_por_linha_de_log(self):
        """Comportamento atual: N linhas de log -> N conexões (medido, não estimado)."""
        self.log_criado = escrever_log_sintetico(self.amb.LogBanco.LOG_DIR, P.LINHAS_LOG_POR_EXECUCAO)
        self.amb.LogBanco.salvar()
        P.medir("salvar(): conexões abertas", self.amb.banco.conexoes)
        self.assertEqual(self.amb.banco.conexoes, P.LINHAS_LOG_POR_EXECUCAO)
        self.assertEqual(len(self.amb.banco.inserts), P.LINHAS_LOG_POR_EXECUCAO)
        self.assertEqual(self.amb.banco.abertas, 0, "alguma conexão ficou aberta")

    def test_conexoes_por_execucao_de_producao_dentro_do_orcamento(self):
        """Uma execução real (≈242 s, ≈620 linhas de log) deve caber no orçamento de conexões."""
        banco = self.amb.banco

        # 1) Cronômetro de verdade por ~3 s para medir a taxa de INSERTs por segundo.
        cronometro = self.amb.LogBanco.CronometroExecucao()
        cronometro.iniciar()
        time.sleep(3)
        cronometro.finalizar()
        # Ticks = INSERTs com TEMPO_EXECUCAO=1. Em 3 s saem 4 (t=0,1,2,3): a taxa é (ticks-1)/3.
        ticks = sum(1 for r in banco.inserts if r.get("tempo_execucao") == 1)
        taxa_por_segundo = (ticks - 1) / 3
        P.medir("cronômetro: conexões por segundo", round(taxa_por_segundo, 2))

        # 2) Projeção para uma execução de produção.
        cronometro_total = int(taxa_por_segundo * P.DURACAO_EXECUCAO_S) + 2
        itens = P.ROTINAS_ATUAIS + 1  # registrar_tempo: Login + rotinas
        total = cronometro_total + itens + P.LINHAS_LOG_POR_EXECUCAO + 1  # +1 = resultado()
        P.medir("conexões Oracle por execução (projeção)", total)
        P.medir("conexões Oracle por dia", total * P.EXECUCOES_POR_DIA)

        self.assertLessEqual(
            total, P.ORCAMENTO_CONEXOES_POR_EXECUCAO,
            f"Uma execução abre ~{total} conexões no Oracle (orçamento {P.ORCAMENTO_CONEXOES_POR_EXECUCAO}). "
            "registrar_timer() conecta a cada INSERT; reutilizar uma conexão (ou um pool) "
            "e gravar o log com executemany resolveria.",
        )

    def test_tempo_do_salvar_com_latencia_de_rede(self):
        """Mede o salvar() com latência REAL por conexão e projeta para o log de produção."""
        amostra, latencia = 60, 0.02
        self.amb.banco.latencia_conexao = latencia
        self.log_criado = escrever_log_sintetico(self.amb.LogBanco.LOG_DIR, amostra)

        inicio = time.perf_counter()
        self.amb.LogBanco.salvar()
        medido = time.perf_counter() - inicio

        # Se é sequencial, o tempo é ~ amostra x latência. Confirma antes de projetar.
        self.assertGreaterEqual(medido, amostra * latencia * 0.9, "salvar() não é sequencial como esperado")
        por_linha = medido / amostra - latencia  # custo do código além da conexão
        projetado = P.LINHAS_LOG_POR_EXECUCAO * (P.LATENCIA_CONEXAO_ORACLE_S + max(por_linha, 0))
        P.medir("salvar(): medido com 60 linhas e 20 ms", round(medido, 2), "s")
        P.medir(f"salvar(): projetado ({P.LINHAS_LOG_POR_EXECUCAO} linhas, "
                f"{int(P.LATENCIA_CONEXAO_ORACLE_S * 1000)} ms/conexão)", round(projetado, 1), "s")

        self.assertLessEqual(
            projetado, P.ORCAMENTO_SALVAR_LOG_S,
            f"salvar() levaria ~{projetado:.0f}s no fim de cada execução (orçamento {P.ORCAMENTO_SALVAR_LOG_S}s).",
        )

    def test_banco_recusando_conexao_nao_derruba_o_robo(self):
        """Com o Oracle recusando conexões, gravar log não pode quebrar a execução.

        registrar_timer() já tem try/except para o INSERT ('Erro ao inserir no banco'),
        então a intenção é tolerar falha do banco. Este teste confere se o connect()
        também está protegido.
        """
        self.amb.banco.falhar_conexao = True
        LogBanco = self.amb.LogBanco
        try:
            LogBanco.registrar_timer(1, "teste", "PROTHEUS", "rpa", "NAO", 0, "2026-11-19 09:00:00")
        except Exception as erro:
            self.fail(
                "registrar_timer() propagou a falha de conexão "
                f"({type(erro).__name__}: {erro}). Com o banco fora, cronometro.iniciar() "
                "derruba a execução antes de abrir o navegador e a thread do cronômetro morre."
            )

    def test_cronometro_com_banco_lento_perde_registros_por_segundo(self):
        """Com o connect() mais lento que 1 s, o 'a cada segundo' deixa de ser verdade."""
        self.amb.banco.latencia_conexao = 1.5
        cronometro = self.amb.LogBanco.CronometroExecucao()
        cronometro.iniciar()           # o INSERT inicial já leva 1,5 s
        time.sleep(4.5)
        cronometro.finalizar()
        ticks = sum(1 for r in self.amb.banco.inserts if r.get("tempo_execucao") == 1)
        P.medir("cronômetro: registros em ~4,5 s com connect de 1,5 s", ticks)
        # Documenta o comportamento (não é orçamento): ~1 registro a cada 2,5 s.
        self.assertLess(ticks, 4)


if __name__ == "__main__":
    unittest.main()
