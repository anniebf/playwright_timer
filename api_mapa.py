"""
api_mapa.py - Serviço web (FastAPI) do mapa de calor dos tempos das rotinas Protheus.

LÓGICA GERAL
------------
O robô (main_playwright.py) grava tudo no Oracle (tabela RPA.TIMER). Este serviço
lê o banco NO MOMENTO DO ACESSO e entrega:

    GET /                          -> página do mapa de calor (mapa_calor_template.html)
    GET /api/mapa?dias=60          -> grade Login/rotinas x execuções, já classificada
                                      (normal/atenção/lenta/erro) - mapa_calor.calcular_mapa
    GET /api/execucoes/{id}?inicio= -> detalhe completo de UMA execução: tempos, linha do
                                      tempo do que o robô fez e avisos/erros
                                      - mapa_calor.carregar_detalhe_execucao
                                      (o início separa execuções diferentes que
                                       tenham o mesmo ID, ex.: servidor x PC)
    GET /health                    -> verificação simples para monitoramento/Docker
    GET /docs                      -> documentação automática da API (Swagger)

Toda a regra de negócio fica em mapa_calor.py; aqui só há HTTP, cache e tratamento
de erros. Um cache curto (MAPA_CACHE_SEGUNDOS, padrão 30 s) evita que várias telas
abertas consultem o Oracle ao mesmo tempo.

Como subir:
    uvicorn api_mapa:app --host 0.0.0.0 --port 8010        (ou: python api_mapa.py)
    docker build -f Dockerfile.mapa -t protheus-timer-mapa .  (ver README)

Variáveis de ambiente (além das do banco no .env: usernamedb, passworddb, dsn):
    MAPA_HOST (0.0.0.0), MAPA_PORTA (8010), MAPA_CACHE_SEGUNDOS (30)
"""
from __future__ import annotations

import logging
import os
from threading import Lock
from time import monotonic
from datetime import datetime
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse

import mapa_calor

logger = logging.getLogger("api_mapa")

app = FastAPI(
    title="Mapa de calor - Rotinas Protheus",
    description="Tempos do Login e de cada rotina cronometrada pelo robô, lidos do RPA.TIMER.",
    version="1.0.0",
)

# ---------------------------------------------------------------------------
# Cache curto em memória: (chave) -> (momento, valor)
# ---------------------------------------------------------------------------
CACHE_SEGUNDOS = int(os.getenv("MAPA_CACHE_SEGUNDOS", "30"))
_cache: dict[Any, tuple[float, Any]] = {}
_cache_lock = Lock()  # o uvicorn atende requisições em threads diferentes


def _em_cache(chave: Any, gerar: Callable[[], Any]) -> Any:
    """Devolve o valor guardado se ainda for recente; senão gera e guarda."""
    with _cache_lock:
        item = _cache.get(chave)
        if item and monotonic() - item[0] < CACHE_SEGUNDOS:
            return item[1]
    valor = gerar()  # fora do lock: a consulta ao banco pode demorar
    with _cache_lock:
        _cache[chave] = (monotonic(), valor)
    return valor


def _erro_banco(erro: Exception) -> HTTPException:
    """Erro de banco vira 503 com mensagem curta (o detalhe técnico fica no log do serviço)."""
    logger.exception("Falha ao consultar o RPA.TIMER")
    primeira_linha = next((l for l in str(erro).splitlines() if l.strip()), type(erro).__name__)
    return HTTPException(status_code=503, detail=f"Banco indisponível: {primeira_linha[:200]}")


# ---------------------------------------------------------------------------
# Rotas
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse, summary="Página do mapa de calor")
def pagina() -> str:
    # Sem dados embutidos (null): a página busca /api/mapa sozinha (modo serviço).
    return mapa_calor.gerar_html(None)


@app.get("/api/mapa", summary="Grade do mapa de calor")
def api_mapa(dias: int = Query(60, ge=1, le=730, description="Período lido do banco (todo o histórico)")) -> dict:
    def gerar() -> dict:
        execucoes = mapa_calor.carregar_execucoes_banco(dias)
        return mapa_calor.calcular_mapa(execucoes, f"banco RPA.TIMER (últimos {dias} dias)")

    try:
        return _em_cache(("mapa", dias), gerar)
    except Exception as erro:
        raise _erro_banco(erro) from erro


@app.get("/api/execucoes/{id_execucao}", summary="Detalhe completo de uma execução")
def api_execucao(
    id_execucao: int,
    inicio: datetime = Query(..., description="Início da execução (AAAA-MM-DDTHH:MM:SS), como na coluna do mapa"),
) -> dict:
    try:
        detalhe = _em_cache(("execucao", id_execucao, inicio),
                            lambda: mapa_calor.carregar_detalhe_execucao(id_execucao, inicio))
    except Exception as erro:
        raise _erro_banco(erro) from erro
    if detalhe["inicio"] is None:  # nenhuma linha no banco
        raise HTTPException(status_code=404, detail=f"Execução {id_execucao} não encontrada")
    return detalhe


@app.get("/health", summary="Serviço no ar")
def health() -> dict:
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    uvicorn.run(
        "api_mapa:app",
        host=os.getenv("MAPA_HOST", "0.0.0.0"),
        port=int(os.getenv("MAPA_PORTA", "8010")),
    )
