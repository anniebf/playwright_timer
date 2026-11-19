"""
test_producao_docker.py - Configuração do agendamento em produção (Airflow + Docker).

LÓGICA GERAL
------------
Cada hora o Airflow chama run_docker.py, que sobe um container NOVO a partir da
imagem. Tudo que precisa sobreviver entre execuções tem que vir por volume.
O ID_EXECUCAO sai do idExec.json: se ele não vier do volume, cada container
começa do valor gravado na imagem no dia do build, e TODAS as execuções da
hora passam a ter o mesmo ID (o mapa de calor e as consultas por ID_EXECUCAO
juntam execuções diferentes numa só).

Também testa o contador sob concorrência real (vários processos ao mesmo tempo,
como uma execução manual durante a do Airflow): o max_active_runs=1 só protege
execuções do próprio Airflow.
"""
import ast
import multiprocessing
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import perfil_producao as P  # noqa: E402

RAIZ = Path(__file__).resolve().parents[2]


def comando_docker() -> list[str]:
    """Lê a lista DOCKER_CMD do run_docker.py sem executá-lo (ast = só leitura do código)."""
    arvore = ast.parse((RAIZ / "run_docker.py").read_text(encoding="utf-8"))
    for no in arvore.body:
        if isinstance(no, ast.Assign) and any(getattr(a, "id", "") == "DOCKER_CMD" for a in no.targets):
            return ast.literal_eval(no.value)
    raise AssertionError("DOCKER_CMD não encontrado em run_docker.py")


def _gerar_ids(pasta: str, quantidade: int, fila) -> None:
    """Executado em OUTRO processo: importa o LogBanco da cópia isolada e gera IDs.

    Devolve (ids, erros). Um erro aqui = o processo leu o idExec.json enquanto outro
    escrevia (arquivo vazio/pela metade). Em produção isso derrubaria o import do LogBanco.
    """
    ids, erros = [], []
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import ambiente_isolado as A
        A.instalar_dubles(A.BancoFalso(), A.EmailsFalsos())
        sys.path.insert(0, pasta)
        import LogBanco  # noqa: E402  (o import já consome 1 ID)
        ids.append(LogBanco.id_execucao)
        for _ in range(quantidade):
            try:
                ids.append(LogBanco._next_execution_id())
            except Exception as erro:  # JSON lido pela metade
                erros.append(f"{type(erro).__name__}: {erro}")
    except Exception as erro:
        erros.append(f"{type(erro).__name__}: {erro}")
    fila.put((ids, erros))


class TestProducaoDocker(unittest.TestCase):
    def test_run_docker_monta_os_volumes_que_o_readme_exige(self):
        volumes = [comando_docker()[i + 1] for i, p in enumerate(comando_docker()) if p == "-v"]
        destinos = {v.split(":")[1] for v in volumes}
        P.medir("volumes montados pelo run_docker.py", sorted(destinos))
        for destino in ["/app/config.json", "/app/.env", "/app/log_playwright"]:
            self.assertIn(destino, destinos)
        self.assertIn(
            "/app/idExec.json", destinos,
            "run_docker.py (usado pelo Airflow) não monta o idExec.json: todo container começa "
            "do ID gravado na imagem e as execuções se repetem com o mesmo ID_EXECUCAO.",
        )

    def test_imagem_nao_leva_segredos(self):
        ignorados = (RAIZ / ".dockerignore").read_text(encoding="utf-8").split()
        self.assertIn("config.json", ignorados)
        self.assertIn(".env", ignorados)

    def test_contador_de_execucao_sem_ids_repetidos_em_paralelo(self):
        from ambiente_isolado import copiar_projeto
        processos, por_processo = 4, 150
        with tempfile.TemporaryDirectory() as pasta:
            copiar_projeto(Path(pasta))
            contexto = multiprocessing.get_context("spawn")  # igual no Windows e no Linux
            fila = contexto.Queue()
            filhos = [contexto.Process(target=_gerar_ids, args=(pasta, por_processo, fila)) for _ in range(processos)]
            for f in filhos:
                f.start()
            retornos = [fila.get(timeout=60) for _ in filhos]
            for f in filhos:
                f.join(timeout=30)
            ids = [i for r in retornos for i in r[0]]
            erros = [e for r in retornos for e in r[1]]
        # Com erros de leitura o arquivo pode ter sido reescrito do zero: "final" nem sempre existe.
        repetidos = len(ids) - len(set(ids))
        P.medir("IDs gerados em paralelo / repetidos", f"{len(ids)} / {repetidos}")
        P.medir("leituras do idExec.json que quebraram", f"{len(erros)} {erros[:1]}")
        self.assertEqual(erros, [], f"{len(erros)} leituras do idExec.json falharam durante a escrita de outro processo")
        self.assertEqual(repetidos, 0, f"{repetidos} ID_EXECUCAO repetidos com {processos} processos simultâneos")


if __name__ == "__main__":
    unittest.main()
