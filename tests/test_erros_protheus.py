"""Testes de erros_protheus.py. Rodar: python -m unittest discover -s tests -v"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from erros_protheus import LicencaExcedidaError, eh_erro_licenca  # noqa: E402


class TestErroLicenca(unittest.TestCase):
    def test_reconhece_variacoes_da_mensagem(self):
        for texto in [
            "TOTVS\nExcedeu numero de licenças\nFechar",
            "Excedeu número de licencas",
            "EXCEDEU   NUMERO DE LICENÇAS",
        ]:
            self.assertTrue(eh_erro_licenca(texto), texto)

    def test_ignora_outros_textos(self):
        for texto in ["", None, "Pedidos de Compra", "Licenças disponíveis: 3"]:
            self.assertFalse(eh_erro_licenca(texto), texto)

    def test_nao_e_erro_de_playwright(self):
        # Não pode herdar de PlaywrightError, senão entraria nas novas tentativas.
        self.assertEqual(LicencaExcedidaError.__mro__[1], Exception)


if __name__ == "__main__":
    unittest.main()
