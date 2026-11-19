"""
Testes do serviço api_mapa.py (FastAPI), com o banco SIMULADO.

Precisam de fastapi + httpx (requirements-mapa.txt); sem eles, são pulados.
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


@unittest.skipUnless(TEM_FASTAPI, "fastapi/httpx não instalados (pip install -r requirements-mapa.txt)")
class TestApiMapa(unittest.TestCase):
    def setUp(self):
        # Banco simulado: 5 execuções; a última com 'Visualizar' bem mais lento.
        inicio = datetime(2026, 11, 19, 8, 0, 0)
        self.execucoes = {
            100 + i: execucao_padrao(inicio + timedelta(hours=i), visualizar_s=30 if i == 4 else 6)
            for i in range(5)
        }
        todas = [(id_, d, h) for id_, linhas in self.execucoes.items() for d, _, _, h in linhas]
        self._originais = (mapa_calor._consultar,)
        mapa_calor._consultar = lambda sql, p: (
            [l for l in todas] if "SYSDATE" in sql else self.execucoes.get(p["id_execucao"], [])
        )
        api_mapa._cache.clear()
        self.cliente = TestClient(api_mapa.app)

    def tearDown(self):
        mapa_calor._consultar = self._originais[0]

    def test_pagina_em_modo_servico(self):
        r = self.cliente.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("let DADOS = null", r.text)   # sem dados embutidos: busca na API

    def test_mapa_classifica_a_ultima_como_lenta(self):
        dados = self.cliente.get("/api/mapa?dias=30").json()
        self.assertEqual([c["id"] for c in dados["colunas"]], [100, 101, 102, 103, 104])
        self.assertEqual(dados["celulas"]["Pedidos de Compra"][-1]["status"], "lenta")
        self.assertIn("banco RPA.TIMER", dados["fonte"])

    def test_detalhe_e_404(self):
        d = self.cliente.get("/api/execucoes/104").json()
        self.assertEqual(d["id_execucao"], 104)
        self.assertEqual(self.cliente.get("/api/execucoes/999").status_code, 404)

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
