"""
erros_protheus.py - Erros conhecidos que o Protheus mostra na tela.

LÓGICA GERAL
------------
Alguns erros não são falha do robô (elemento não encontrado, timeout), e sim uma
recusa do próprio Protheus. O principal é a janela TOTVS
"Excedeu numero de licenças", que aparece quando não há licença livre para
abrir mais uma sessão (comum ao rodar várias rotinas em paralelo).

Para esses casos usamos uma exceção própria (LicencaExcedidaError), que:
- NÃO herda de PlaywrightError, então não dispara as novas tentativas
  automáticas (tentar de novo na hora não libera licença);
- é tratada à parte: descrição específica no RPA.TIMER e e-mail próprio.

O texto é comparado por expressão regular, tolerando acentos e espaços
("numero"/"número", "licenças"/"licencas").
"""
from __future__ import annotations

import re

# Mensagem da janela TOTVS de licenças esgotadas.
LICENCA_REGEX = re.compile(r"excedeu\s+n[uú]mero\s+de\s+licen[cç]as", re.IGNORECASE)

MENSAGEM_LICENCA = "TOTVS: Excedeu numero de licenças"
DESCRICAO_LICENCA = "ERRO LICENCA PROTHEUS: Excedeu numero de licenças"


class LicencaExcedidaError(Exception):
    """O Protheus recusou a sessão por falta de licença disponível."""

    def __init__(self, mensagem: str = MENSAGEM_LICENCA):
        super().__init__(mensagem)


def eh_erro_licenca(texto: str | None) -> bool:
    """True se o texto contém a mensagem de licenças excedidas."""
    return bool(texto) and LICENCA_REGEX.search(texto) is not None
