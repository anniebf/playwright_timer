"""
Testes do serviço api_mapa.py (FastAPI), com o banco SIMULADO.

Precisam de fastapi + httpx (estão no requirements.txt); sem eles, são pulados.
Rodar: python -m unittest discover -s tests -v
"""
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from fastapi.testclient import TestClient
    import api_mapa
    TEM_FASTAPI = True
except ImportError:
    TEM_FASTAPI = False

import mapa_calor  # noqa: E402
from linhas_exemplo import execucao_padrao  # noqa: E402


@unittest.skipUnless(TEM_FASTAPI, "fastapi/httpx não instalados (pip install -r requirements.txt)")
class TestApiMapa(unittest.TestCase):
    def setUp(self):
        # Banco simulado: 2 execuções do robô antigo + 5 atuais (a última com 'Visualizar' lento).
        from linhas_exemplo import execucao_playwright_antiga, resumo_como_no_oracle, tempos_como_no_oracle
        inicio = datetime(2026, 11, 19, 8, 0, 0)
        self.execucoes = {
            (90, "2026-11-10"): execucao_playwright_antiga(datetime(2026, 11, 10, 8, 30), 40, 25),
            (91, "2026-11-11"): execucao_playwright_antiga(datetime(2026, 11, 11, 8, 30), 42, 24),
        }
        for i in range(5):
            h = inicio + timedelta(hours=i)
            self.execucoes[(100 + i, h.strftime("%Y-%m-%d"))] = execucao_padrao(h, visualizar_s=30 if i == 4 else 6)

        def consultar(sql, p):
            if sql is mapa_calor.SQL_RESUMO_EXECUCOES:
                return resumo_como_no_oracle(self.execucoes)
            if sql is mapa_calor.SQL_TEMPOS:
                return tempos_como_no_oracle(self.execucoes)
            # SQL_EXECUCAO: todas as linhas do ID dentro da janela (pode trazer outra execução
            # com o mesmo número - quem separa é o mapa_calor.recortar_sessao)
            return [l for (id_, _), ls in self.execucoes.items() if id_ == p["id_execucao"]
                    for l in ls if p["de"] <= l[3] <= p["ate"]]

        self._original = mapa_calor._consultar
        mapa_calor._consultar = consultar
        api_mapa._cache.clear()
        self.cliente = TestClient(api_mapa.app)

    def tearDown(self):
        mapa_calor._consultar = self._original

    def test_pagina_em_modo_servico(self):
        r = self.cliente.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("let DADOS = null", r.text)   # sem dados embutidos: busca na API

    def test_mapa_classifica_a_ultima_como_lenta(self):
        dados = self.cliente.get("/api/mapa?dias=30").json()
        self.assertEqual([c["id"] for c in dados["colunas"]], [90, 91, 100, 101, 102, 103, 104])
        self.assertEqual(dados["rotinas"][0], "Execução completa")
        self.assertTrue(dados["celulas"]["Login"][0]["estimado"])          # histórico antigo
        self.assertEqual(dados["celulas"]["Pedidos de Compra"][-1]["status"], "lenta")
        self.assertIn("banco RPA.TIMER", dados["fonte"])

    def test_detalhe_e_404(self):
        d = self.cliente.get("/api/execucoes/104?inicio=2026-11-19T12:00:00").json()
        self.assertEqual(d["id_execucao"], 104)
        antiga = self.cliente.get("/api/execucoes/90?inicio=2026-11-10T08:30:00").json()
        self.assertEqual([s["nome"] for s in antiga["secoes"]], ["Login", "Pedidos de Compra"])
        self.assertEqual(self.cliente.get("/api/execucoes/104?inicio=ontem").status_code, 422)
        self.assertEqual(self.cliente.get("/api/execucoes/999?inicio=2026-11-19T08:00:00").status_code, 404)

    def test_banco_fora_do_ar_vira_503(self):
        mapa_calor._consultar = lambda sql, p: (_ for _ in ()).throw(RuntimeError("ORA-12541: TNS:no listener\nmais detalhes"))
        r = self.cliente.get("/api/mapa?dias=7")
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json()["detail"], "Banco indisponível: ORA-12541: TNS:no listener")

    def test_validacao_de_parametro(self):
        self.assertEqual(self.cliente.get("/api/mapa?dias=0").status_code, 422)
        self.assertEqual(self.cliente.get("/health").json(), {"status": "ok"})


if __name__ == "__main__":
    unittest.main()
