"""
rotinas.py - Leitura e relatório das rotinas a cronometrar no Protheus.

LÓGICA GERAL
------------
O cronômetro deixa de ter um único caminho fixo. Agora ele recebe UMA OU MAIS
rotinas e, depois de um login único, mede o tempo de cada uma separadamente.

FORMATO PADRÃO (config.json, chave "Rotinas"): menu e ações separados

    {
      "Nome": "Pedidos de Compra",
      "Menu": "Compras > Movimento > Pedidos de Compra",     <- caminho no menu até ABRIR a rotina
      "Acoes": [                                             <- o que fazer dentro da rotina, em ordem
        { "clicar": "Visualizar" },
        { "esperar": "Pedido de Compra" },
        { "clicar": "Cancelar" },
        { "clicar": "sair" }
      ]
    }

Tipos de ação (TIPOS_ACAO): clicar, duplo (clique duplo), tecla (ex.: Enter, Tab, F3),
esperar (aguarda um texto aparecer, sem clicar). "digitar" ainda não está disponível.
Uma ação também pode ser só um texto ("Visualizar" = clicar).

Formatos antigos, que continuam funcionando (tudo vira "clicar" e o menu é descoberto
na navegação):
    - {"Nome": "...", "Fluxo": "Compras > Movimento > Pedidos de Compra > Visualizar > sair"}
    - um texto com o caminho, direto na lista "Rotinas"
    - a chave "Fluxo" (texto único) na raiz do config.json
    - linha de comando: python main_playwright.py --fluxo "A > B > sair" (prioridade máxima)

Este módulo NÃO depende do Playwright: só transforma configuração em objetos
(Rotina) e resultados em relatório (ResultadoRotina). Isso permite testá-lo
isoladamente (ver tests/test_rotinas.py).
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

# Caractere que separa as etapas do caminho ("Compras > Movimento > ...").
SEPARADOR = ">"

# Etapas especiais que significam "fechar a tela da rotina" (botão de saída).
ETAPAS_SAIDA = {"sair", "fechar", "fechar tela"}

# Ações disponíveis dentro da rotina (chave do objeto em "Acoes").
TIPOS_ACAO = ("clicar", "duplo", "tecla", "esperar")
# Ações planejadas: dão um erro claro em vez de serem ignoradas.
ACOES_EM_BREVE = {"digitar": "a ação 'digitar' ainda não está disponível (depende de mapear os campos do Protheus)"}
# Nome de tecla aceito pelo Playwright: Enter, Tab, F3, Escape, ArrowDown, Control+A...
TECLA_VALIDA = re.compile(r"^[A-Za-z0-9]+(\+[A-Za-z0-9]+)*$")


@dataclass(frozen=True)
class Acao:
    """Uma ação dentro da rotina: o tipo (clicar, duplo, tecla, esperar) e o alvo."""

    tipo: str
    alvo: str

    @property
    def descricao(self) -> str:
        """Texto para logs e para o caminho: 'Visualizar', 'tecla Enter', 'esperar 000123'."""
        return self.alvo if self.tipo == "clicar" else f"{self.tipo} {self.alvo}"

    @property
    def e_saida(self) -> bool:
        """True para 'clicar sair/fechar' (botão de fechar a tela da rotina)."""
        return self.tipo == "clicar" and self.alvo.casefold() in ETAPAS_SAIDA

    @property
    def tem_alvo_na_tela(self) -> bool:
        """Ações cujo alvo é um texto/botão visível (usado para confirmar o efeito do clique anterior)."""
        return self.tipo in ("clicar", "duplo", "esperar")


@dataclass(frozen=True)
class Rotina:
    """Uma rotina: nome, caminho no menu e ações dentro dela.

    menu vazio = formato antigo (Fluxo): todas as etapas estão em `acoes` como "clicar"
    e a navegação descobre quais delas são do menu (as pastas da árvore).
    """

    nome: str
    menu: tuple[str, ...]
    acoes: tuple[Acao, ...]

    @property
    def etapas(self) -> tuple[str, ...]:
        """Todas as etapas como texto, na ordem (menu + ações)."""
        return self.menu + tuple(a.descricao for a in self.acoes)

    @property
    def modulo(self) -> str:
        """Primeiro item do menu (ex.: 'Compras'): visível quando se está na tela principal."""
        return self.menu[0] if self.menu else self.acoes[0].alvo

    @property
    def caminho(self) -> str:
        """Caminho completo, usado em logs e relatórios."""
        return f" {SEPARADOR} ".join(self.etapas)

    @property
    def termina_com_saida(self) -> bool:
        """True quando a última ação já fecha a tela (ex.: {"clicar": "sair"})."""
        return bool(self.acoes) and self.acoes[-1].e_saida


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


def _rotina_de_fluxo(nome: str | None, caminho: str) -> Rotina:
    """Formato antigo: todas as etapas viram "clicar"; o menu é descoberto ao navegar."""
    etapas = separar_etapas(caminho)
    acoes = tuple(Acao("clicar", etapa) for etapa in etapas)
    return Rotina(nome=nome or f" {SEPARADOR} ".join(etapas), menu=(), acoes=acoes)


def _criar_acao(item: object, rotina: int, posicao: int) -> Acao:
    """Converte {"clicar": "Visualizar"} (ou só "Visualizar") em Acao, validando."""
    onde = f"Rotina #{rotina}, ação #{posicao}"
    if isinstance(item, str) and item.strip():
        return Acao("clicar", item.strip())
    if not isinstance(item, dict) or len(item) != 1:
        raise ValueError(f"{onde}: use um objeto com UMA ação, ex.: {{\"clicar\": \"Visualizar\"}}; recebido {item!r}")

    tipo, alvo = next(iter(item.items()))
    tipo = str(tipo).strip().lower()
    if tipo in ACOES_EM_BREVE:
        raise ValueError(f"{onde}: {ACOES_EM_BREVE[tipo]}")
    if tipo not in TIPOS_ACAO:
        raise ValueError(f"{onde}: ação '{tipo}' desconhecida (use: {', '.join(TIPOS_ACAO)})")
    if not isinstance(alvo, str) or not alvo.strip():
        raise ValueError(f"{onde}: '{tipo}' precisa de um texto, recebido {alvo!r}")
    alvo = alvo.strip()
    if tipo == "tecla" and not TECLA_VALIDA.match(alvo):
        raise ValueError(f"{onde}: tecla inválida '{alvo}' (ex.: Enter, Tab, F3, Escape, Control+A)")
    return Acao(tipo, alvo)


def _criar_rotina(item: object, posicao: int) -> Rotina:
    """Aceita o formato padrão ({'Nome', 'Menu', 'Acoes'}) e os formatos antigos (Fluxo/texto)."""
    if isinstance(item, str):
        return _rotina_de_fluxo(None, item)  # sem nome: o próprio caminho identifica a rotina

    if not isinstance(item, dict):
        raise ValueError(f"Rotina #{posicao} em formato inválido (use um objeto com Menu e Acoes): {item!r}")

    nome = str(item.get("Nome") or "").strip() or None
    if item.get("Menu") and item.get("Fluxo"):
        raise ValueError(f"Rotina #{posicao}: use 'Menu' + 'Acoes' OU 'Fluxo', não os dois")

    if item.get("Fluxo"):
        return _rotina_de_fluxo(nome, item["Fluxo"])

    if not item.get("Menu"):
        raise ValueError(f"Rotina #{posicao} sem a chave 'Menu' (caminho no menu até abrir a rotina): {item!r}")
    menu = separar_etapas(item["Menu"])
    if any(etapa.casefold() in ETAPAS_SAIDA for etapa in menu):
        raise ValueError(f"Rotina #{posicao}: 'sair' vai em Acoes ({{\"clicar\": \"sair\"}}), não no Menu")

    acoes_cfg = item.get("Acoes", [])
    if not isinstance(acoes_cfg, list):
        raise ValueError(f"Rotina #{posicao}: 'Acoes' deve ser uma lista, ex.: [{{\"clicar\": \"Visualizar\"}}]")
    acoes = tuple(_criar_acao(acao, posicao, i) for i, acao in enumerate(acoes_cfg, start=1))
    # Sem nome: o último item do menu é a própria rotina (ex.: "Pedidos de Compra").
    return Rotina(nome=nome or menu[-1], menu=menu, acoes=acoes)


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
