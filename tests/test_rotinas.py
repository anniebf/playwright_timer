"""
Testes do módulo rotinas.py (não precisam de Playwright, Protheus nem banco).

Como rodar, na pasta do projeto:
    python -m unittest discover -s tests -v
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

# Permite importar rotinas.py a partir da raiz do projeto.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import rotinas  # noqa: E402
from rotinas import ResultadoRotina  # noqa: E402


class TestCarregarRotinas(unittest.TestCase):
    def test_fluxo_unico_formato_antigo(self):
        lista = rotinas.carregar_rotinas({"Fluxo": "Compras > Movimento >  Pedidos de Compra "})
        self.assertEqual(len(lista), 1)
        self.assertEqual(lista[0].etapas, ("Compras", "Movimento", "Pedidos de Compra"))

    def test_lista_rotinas_texto_e_objeto(self):
        config = {
            "Fluxo": "ignorado",
            "Rotinas": [
                "Compras > Movimento > Pedidos de Compra > sair",
                {"Nome": "Notas", "Fluxo": "Faturamento > Consultas > Notas > sair"},
            ],
        }
        lista = rotinas.carregar_rotinas(config)
        self.assertEqual([r.nome for r in lista],
                         ["Compras > Movimento > Pedidos de Compra > sair", "Notas"])
        self.assertTrue(lista[1].termina_com_saida)

    def test_cli_tem_prioridade(self):
        lista = rotinas.carregar_rotinas({"Rotinas": ["X > Y"]}, ["A > B", "C > D > Sair"])
        self.assertEqual([r.etapas for r in lista], [("A", "B"), ("C", "D", "Sair")])
        self.assertTrue(lista[1].termina_com_saida)
        self.assertFalse(lista[0].termina_com_saida)

    def test_erros_de_configuracao(self):
        with self.assertRaises(ValueError):
            rotinas.carregar_rotinas({})
        with self.assertRaises(ValueError):
            rotinas.carregar_rotinas({"Rotinas": []})
        with self.assertRaises(ValueError):
            rotinas.carregar_rotinas({"Rotinas": [{"Nome": "sem fluxo"}]})
        with self.assertRaises(ValueError):
            rotinas.carregar_rotinas({"Rotinas": [" > > "]})


class TestRelatorio(unittest.TestCase):
    def test_resumo_e_json(self):
        resultados = [
            ResultadoRotina("A", "A > sair", 12.34, True),
            ResultadoRotina("B", "B > sair", 5.0, False, "timeout"),
        ]
        resumo = rotinas.formatar_resumo(resultados)
        self.assertIn("A: 12.3s - OK", resumo)
        self.assertIn("ERRO (timeout)", resumo)
        with tempfile.TemporaryDirectory() as pasta:
            arquivo = rotinas.salvar_relatorio(resultados, Path(pasta), 99)
            dados = json.loads(arquivo.read_text(encoding="utf-8"))
        self.assertEqual(dados["id_execucao"], 99)
        self.assertEqual(dados["total_segundos"], 17.34)
        self.assertEqual(len(dados["rotinas"]), 2)


if __name__ == "__main__":
    unittest.main()
