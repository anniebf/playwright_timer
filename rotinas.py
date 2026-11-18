"""
rotinas.py - Leitura e relatório das rotinas a cronometrar no Protheus.

LÓGICA GERAL
------------
O cronômetro deixa de ter um único caminho fixo. Agora ele recebe UMA OU MAIS
rotinas e mede o tempo de cada uma separadamente, na mesma sessão logada.

As rotinas podem vir de três lugares (em ordem de prioridade):

1. Linha de comando:  python main_playwright.py --fluxo "A > B > sair" --fluxo "C > D > sair"
2. config.json, chave "Rotinas" (lista). Cada item pode ser:
       - um texto com o caminho:   "Compras > Movimento > Pedidos de Compra > sair"
       - um objeto com nome:       {"Nome": "Pedidos", "Fluxo": "Compras > ... > sair"}
3. config.json, chave "Fluxo" (texto único) - formato antigo, continua funcionando.

Este módulo NÃO depende do Playwright: só transforma configuração em objetos
(Rotina) e resultados em relatório (ResultadoRotina). Isso permite testá-lo
isoladamente (ver tests/test_rotinas.py).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

# Caractere que separa as etapas do caminho ("Compras > Movimento > ...").
SEPARADOR = ">"

# Etapas especiais que significam "fechar a tela da rotina" (botão de saída).
ETAPAS_SAIDA = {"sair", "fechar", "fechar tela"}


@dataclass(frozen=True)
class Rotina:
    """Uma rotina a ser executada: nome amigável + etapas na ordem de clique."""

    nome: str
    etapas: tuple[str, ...]

    @property
    def caminho(self) -> str:
        """Caminho no formato original, usado em logs e relatórios."""
        return f" {SEPARADOR} ".join(self.etapas)

    @property
    def termina_com_saida(self) -> bool:
        """True quando a última etapa já fecha a tela (ex.: '... > sair')."""
        return self.etapas[-1].casefold() in ETAPAS_SAIDA


@dataclass
class ResultadoRotina:
    """Tempo medido e status de uma rotina executada."""

    nome: str
    caminho: str
    segundos: float
    sucesso: bool
    erro: str = ""


def separar_etapas(caminho: str) -> tuple[str, ...]:
    """Converte 'A > B > C' em ('A', 'B', 'C'), ignorando espaços e etapas vazias."""
    if not isinstance(caminho, str):
        raise ValueError(f"Caminho da rotina deve ser texto, recebido: {caminho!r}")
    etapas = tuple(parte.strip() for parte in caminho.split(SEPARADOR) if parte.strip())
    if not etapas:
        raise ValueError(f"Caminho da rotina vazio ou inválido: {caminho!r}")
    return etapas


def _criar_rotina(item: object, posicao: int) -> Rotina:
    """Aceita um texto ('A > B') ou um objeto ({'Nome': ..., 'Fluxo': ...})."""
    if isinstance(item, str):
        etapas = separar_etapas(item)
        # Sem nome informado, o próprio caminho identifica a rotina.
        return Rotina(nome=f" {SEPARADOR} ".join(etapas), etapas=etapas)

    if isinstance(item, dict):
        fluxo = item.get("Fluxo")
        if not fluxo:
            raise ValueError(f"Rotina #{posicao} sem a chave 'Fluxo': {item!r}")
        etapas = separar_etapas(fluxo)
        nome = str(item.get("Nome") or "").strip() or f" {SEPARADOR} ".join(etapas)
        return Rotina(nome=nome, etapas=etapas)

    raise ValueError(f"Rotina #{posicao} em formato inválido (use texto ou objeto): {item!r}")


def carregar_rotinas(config: dict, fluxos_cli: Iterable[str] | None = None) -> list[Rotina]:
    """Monta a lista de rotinas a partir da linha de comando ou do config.json.

    Falha cedo (ValueError) se nada for informado, para não abrir o navegador
    e fazer login à toa.
    """
    fluxos_cli = [f for f in (fluxos_cli or []) if f and f.strip()]

    if fluxos_cli:
        itens: list[object] = list(fluxos_cli)
    elif "Rotinas" in config:
        itens = config["Rotinas"]
        if not isinstance(itens, list) or not itens:
            raise ValueError("'Rotinas' no config.json deve ser uma lista com pelo menos uma rotina.")
    elif config.get("Fluxo"):
        itens = [config["Fluxo"]]
    else:
        raise ValueError(
            "Nenhuma rotina informada. Use --fluxo, a lista 'Rotinas' ou a chave 'Fluxo' no config.json."
        )

    return [_criar_rotina(item, posicao) for posicao, item in enumerate(itens, start=1)]


def formatar_resumo(resultados: list[ResultadoRotina]) -> str:
    """Texto legível com o tempo de cada rotina, usado no log final."""
    linhas = ["Resumo dos tempos por rotina:"]
    for indice, r in enumerate(resultados, start=1):
        status = "OK" if r.sucesso else f"ERRO ({r.erro})"
        linhas.append(f"  {indice:02d}. {r.nome}: {r.segundos:.1f}s - {status}")
    total = sum(r.segundos for r in resultados)
    linhas.append(f"  Total das rotinas: {total:.1f}s")
    return "\n".join(linhas)


def salvar_relatorio(resultados: list[ResultadoRotina], pasta: Path, id_execucao: int) -> Path:
    """Grava um JSON com os tempos em log_playwright/ (pasta já montada no Docker)."""
    pasta.mkdir(parents=True, exist_ok=True)
    arquivo = pasta / f"tempos_rotinas_{id_execucao}.json"
    conteudo = {
        "id_execucao": id_execucao,
        "gerado_em": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_segundos": round(sum(r.segundos for r in resultados), 2),
        "rotinas": [asdict(r) for r in resultados],
    }
    arquivo.write_text(json.dumps(conteudo, ensure_ascii=False, indent=4), encoding="utf-8")
    return arquivo
