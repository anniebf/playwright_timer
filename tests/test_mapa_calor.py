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
sys.path.insert(0, str(Path(__file__).resolve().parent))  # linhas_exemplo.py

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


class TestLinhasDoBanco(unittest.TestCase):
    def test_converte_linhas_tempo(self):
        from datetime import datetime as dt
        linhas = [
            (10, "[0070s] - Tempo Login: 70.9s", dt(2026, 11, 19, 7, 50, 0)),
            (10, "[0135s] - Tempo Rotina 'Pedidos de Compra': 64.5s (com erro: Elemento 'Visualizar' não foi encontrado)",
             dt(2026, 11, 19, 7, 51, 5)),
            (10, "[0136s] - Tempo da rotina 'Pedidos de Compra': 64.5s (log, deve ser ignorado)", dt(2026, 11, 19, 7, 51, 6)),
            (9, "[0040s] - Tempo Login: 38.1s", dt(2026, 11, 18, 17, 44, 0)),
            (9, "[0060s] - Tempo Rotina 'Pedidos de Compra': 18.4s", dt(2026, 11, 18, 17, 45, 0)),
            (9, "[0061s] - Tempo Rotina 'Doc (antigo)': 3.0s (com erro)", dt(2026, 11, 18, 17, 45, 1)),
        ]
        execs = mapa_calor.linhas_para_execucoes(linhas)
        self.assertEqual([e["id_execucao"] for e in execs], [9, 10])          # ordem por data
        self.assertEqual(execs[1]["gerado_em"], "2026-11-19 07:50:00")
        rotinas = {r["nome"]: r for r in execs[1]["rotinas"]}
        self.assertEqual(set(rotinas), {"Login", "Pedidos de Compra"})       # linha de log ignorada
        self.assertFalse(rotinas["Pedidos de Compra"]["sucesso"])
        self.assertIn("Visualizar", rotinas["Pedidos de Compra"]["erro"])
        self.assertEqual(execs[0]["rotinas"][2]["erro"], "erro")              # formato antigo, sem mensagem

    def test_fallback_para_arquivos_quando_banco_falha(self):
        original = mapa_calor.carregar_execucoes_banco
        mapa_calor.carregar_execucoes_banco = lambda dias: (_ for _ in ()).throw(RuntimeError("sem banco"))
        try:
            with tempfile.TemporaryDirectory() as pasta:
                html = mapa_calor.gerar_mapa_calor(Path(pasta), fonte="banco").read_text(encoding="utf-8")
        finally:
            mapa_calor.carregar_execucoes_banco = original
        self.assertIn("arquivos locais", html)


class TestDetalheExecucao(unittest.TestCase):
    """montar_detalhe: transforma as linhas de UMA execução no painel de detalhe."""

    def setUp(self):
        from datetime import datetime as dt
        from linhas_exemplo import execucao_padrao
        self.detalhe = mapa_calor.montar_detalhe(
            42, execucao_padrao(dt(2026, 11, 19, 8, 0, 0), visualizar_s=18, erro="Elemento 'Cancelar' não foi encontrado"))

    def test_tempos_e_final(self):
        nomes = [t["nome"] for t in self.detalhe["tempos"]]
        self.assertEqual(nomes, ["Login", "Pedidos de Compra", "Documento de entrada"])
        self.assertEqual(self.detalhe["tempos"][0]["segundos"], 27.0)
        self.assertFalse(self.detalhe["tempos"][1]["sucesso"])
        self.assertIn("Cancelar", self.detalhe["tempos"][1]["erro"])
        self.assertTrue(self.detalhe["final"]["texto"].startswith("ERRO EM ROTINA(S)"))

    def test_linha_do_tempo_por_secao(self):
        secoes = {s["nome"]: s["passos"] for s in self.detalhe["secoes"]}
        self.assertEqual(list(secoes), ["Login", "Pedidos de Compra", "Documento de entrada"])
        # registros por segundo repetidos viram UM passo com a duração somada
        visualizar = [p for p in secoes["Pedidos de Compra"] if "Visualizar" in p["acao"]]
        self.assertEqual(len(visualizar), 1)
        self.assertEqual(visualizar[0]["duracao_s"], 18)
        self.assertEqual(secoes["Login"][2]["duracao_s"], 12)   # espera do MFA

    def test_eventos_so_avisos_e_erros(self):
        niveis = {e["nivel"] for e in self.detalhe["eventos"]}
        self.assertTrue(niveis <= {"WARN", "ERRO"})
        self.assertTrue(any("clicando de novo" in e["texto"] for e in self.detalhe["eventos"]))

    def test_execucao_inexistente(self):
        vazio = mapa_calor.montar_detalhe(99, [])
        self.assertEqual((vazio["tempos"], vazio["secoes"]), ([], []))


class TestArquivos(unittest.TestCase):
    def test_gera_html_e_ignora_json_invalido(self):
        with tempfile.TemporaryDirectory() as pasta:
            pasta = Path(pasta)
            (pasta / "tempos_rotinas_1.json").write_text(json.dumps(
                _execucao(1, "2026-11-19 07:00:00", [("Rotina </script>", 5, True, "")])), encoding="utf-8")
            (pasta / "tempos_rotinas_2.json").write_text("{corrompido", encoding="utf-8")
            arquivo = mapa_calor.gerar_mapa_calor(pasta, fonte="arquivos")
            html = arquivo.read_text(encoding="utf-8")
        self.assertNotIn("/*__DADOS__*/null", html)          # dados injetados
        self.assertIn("Rotina <\\/script>", html)            # não fecha o <script> antes da hora
        self.assertEqual(html.count("</script>"), 1)


if __name__ == "__main__":
    unittest.main()
