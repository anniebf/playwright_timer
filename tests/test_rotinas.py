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


class TestFormatoMenuAcoes(unittest.TestCase):
    """Formato padrão: {"Nome", "Menu", "Acoes": [{"clicar": ...}, {"tecla": ...}, ...]}."""

    CONFIG = {"Rotinas": [
        {"Nome": "Pedido de Compra - pesquisa",
         "Menu": "Compras > Movimento > Pedidos de Compra",
         "Acoes": [{"clicar": "Visualizar"}, {"tecla": "Enter"}, {"esperar": "000123"},
                   {"duplo": "000123"}, "Cancelar", {"clicar": "sair"}]},
        {"Nome": "Documento de entrada",
         "Fluxo": "Fiscal > Movimento > Documento Entrada > Visualizar > Cancelar > sair"},
    ]}

    def test_menu_e_acoes(self):
        pesquisa, documento = rotinas.carregar_rotinas(self.CONFIG)
        self.assertEqual(pesquisa.menu, ("Compras", "Movimento", "Pedidos de Compra"))
        self.assertEqual([(a.tipo, a.alvo) for a in pesquisa.acoes],
                         [("clicar", "Visualizar"), ("tecla", "Enter"), ("esperar", "000123"),
                          ("duplo", "000123"), ("clicar", "Cancelar"), ("clicar", "sair")])
        self.assertTrue(pesquisa.termina_com_saida)
        self.assertEqual(pesquisa.modulo, "Compras")
        self.assertEqual(pesquisa.caminho,
                         "Compras > Movimento > Pedidos de Compra > Visualizar > tecla Enter > "
                         "esperar 000123 > duplo 000123 > Cancelar > sair")
        # o formato antigo continua: menu vazio e tudo como "clicar"
        self.assertEqual(documento.menu, ())
        self.assertEqual(documento.modulo, "Fiscal")
        self.assertEqual({a.tipo for a in documento.acoes}, {"clicar"})

    def test_nome_padrao_e_o_ultimo_item_do_menu(self):
        (rotina,) = rotinas.carregar_rotinas({"Rotinas": [{"Menu": "Compras > Movimento > Pedidos de Compra"}]})
        self.assertEqual(rotina.nome, "Pedidos de Compra")
        self.assertEqual(rotina.acoes, ())

    def test_erros_claros_de_configuracao(self):
        casos = {
            "não está disponível": {"Menu": "A > B", "Acoes": [{"digitar": "000123"}]},
            "desconhecida": {"Menu": "A > B", "Acoes": [{"apertar": "Enter"}]},
            "tecla inválida": {"Menu": "A > B", "Acoes": [{"tecla": "Enter já"}]},
            "UMA ação": {"Menu": "A > B", "Acoes": [{"clicar": "X", "tecla": "Enter"}]},
            "precisa de um texto": {"Menu": "A > B", "Acoes": [{"esperar": ""}]},
            "não os dois": {"Menu": "A > B", "Fluxo": "A > B"},
            "sem a chave 'Menu'": {"Nome": "X", "Acoes": [{"clicar": "Visualizar"}]},
            "vai em Acoes": {"Menu": "A > B > sair"},
            "deve ser uma lista": {"Menu": "A > B", "Acoes": {"clicar": "X"}},
        }
        for trecho, rotina in casos.items():
            with self.subTest(trecho):
                with self.assertRaises(ValueError) as erro:
                    rotinas.carregar_rotinas({"Rotinas": [rotina]})
                self.assertIn(trecho, str(erro.exception))
                self.assertIn("Rotina #1", str(erro.exception))


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
