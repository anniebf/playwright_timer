"""
Testes do mapa_calor.py (não precisam de Playwright, Protheus nem banco).

Rodar na pasta do projeto:
    python -m unittest discover -s tests -v
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mapa_calor  # noqa: E402


def _execucao(id_exec, data, rotinas):
    """Monta um relatório no mesmo formato de rotinas.salvar_relatorio."""
    return {
        "id_execucao": id_exec,
        "gerado_em": data,
        "rotinas": [
            {"nome": n, "caminho": f"{n} > sair", "segundos": s, "sucesso": ok, "erro": e}
            for n, s, ok, e in rotinas
        ],
    }


class TestClassificar(unittest.TestCase):
    def test_faixas(self):
        self.assertEqual(mapa_calor.classificar(11.4, 10, True, ""), "normal")    # +14%
        self.assertEqual(mapa_calor.classificar(12.0, 10, True, ""), "atencao")   # +20%
        self.assertEqual(mapa_calor.classificar(13.5, 10, True, ""), "lenta")     # +35%
        self.assertEqual(mapa_calor.classificar(5, 10, False, "timeout"), "erro")
        self.assertEqual(mapa_calor.classificar(0, 10, False, "NAO EXECUTADA: ..."), "pulada")
        self.assertEqual(mapa_calor.classificar(10, None, True, ""), "sem_base")


class TestCalcularMapa(unittest.TestCase):
    def setUp(self):
        # 4 execuções normais (10s) da rotina A, depois uma lenta e uma com erro.
        self.execs = [
            _execucao(i, f"2026-11-1{i} 08:00:00", [("Login", 30, True, ""), ("A", 10, True, "")])
            for i in range(1, 5)
        ]
        self.execs.append(_execucao(5, "2026-11-15 08:00:00", [("Login", 31, True, ""), ("A", 14, True, "")]))
        self.execs.append(_execucao(6, "2026-11-16 08:00:00", [("Login", 30, True, ""), ("A", 3, False, "timeout")]))

    def test_login_primeiro_e_status(self):
        mapa = mapa_calor.calcular_mapa(self.execs)
        self.assertEqual(mapa["rotinas"], ["Login", "A"])
        status_a = [c["status"] for c in mapa["celulas"]["A"]]
        # 3 primeiras sem histórico, 4ª normal, 5ª lenta (+40%), 6ª erro
        self.assertEqual(status_a, ["sem_base", "sem_base", "sem_base", "normal", "lenta", "erro"])
        self.assertEqual(mapa["celulas"]["A"][4]["var"], 40)

    def test_erro_nao_entra_no_normal(self):
        mapa = mapa_calor.calcular_mapa(self.execs)
        # histórico sem erro de A: 10,10,10,10,14 -> mediana 10
        self.assertEqual(mapa["normal_atual"]["A"], 10.0)

    def test_rotina_ausente_vira_celula_vazia(self):
        self.execs.append(_execucao(7, "2026-11-17 08:00:00", [("Login", 30, True, ""), ("B", 5, True, "")]))
        mapa = mapa_calor.calcular_mapa(self.execs)
        self.assertIsNone(mapa["celulas"]["A"][-1])
        self.assertIsNone(mapa["celulas"]["B"][0])


class TestArquivos(unittest.TestCase):
    def test_gera_html_e_ignora_json_invalido(self):
        with tempfile.TemporaryDirectory() as pasta:
            pasta = Path(pasta)
            (pasta / "tempos_rotinas_1.json").write_text(json.dumps(
                _execucao(1, "2026-11-19 07:00:00", [("Rotina </script>", 5, True, "")])), encoding="utf-8")
            (pasta / "tempos_rotinas_2.json").write_text("{corrompido", encoding="utf-8")
            arquivo = mapa_calor.gerar_mapa_calor(pasta)
            html = arquivo.read_text(encoding="utf-8")
        self.assertNotIn("/*__DADOS__*/null", html)          # dados injetados
        self.assertIn("Rotina <\\/script>", html)            # não fecha o <script> antes da hora
        self.assertEqual(html.count("</script>"), 1)


if __name__ == "__main__":
    unittest.main()
