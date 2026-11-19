"""
Gera linhas no MESMO formato que o robô grava no RPA.TIMER, para testes e demonstração.

Cada linha: (DESCRICAO, ERRO, TEMPO_EXECUCAO, HORARIO_EXECUCAO)
- registro por segundo do cronômetro: "[0042s] - <ação>", ERRO 'NAO', TEMPO 1
- tempo de cada item:                 "[0050s] - Tempo Rotina 'X': 12.3s", ERRO 'NAO'/'ERRO'
- linhas do log (LogBanco.salvar):    "[0041s] - <mensagem>", ERRO 'OK'/'WARN'/'ERRO', TEMPO 1
- encerramento:                       "[0123s] - Finalizando execução", TEMPO = total
"""
from datetime import datetime, timedelta


def linhas_execucao(inicio: datetime, passos_login, rotinas, avisos=()) -> list[tuple]:
    """passos_login: [(ação, segundos)]; rotinas: [(nome, [(ação, segundos)], erro_ou_None)];
    avisos: [(segundo, 'WARN'|'ERRO', texto)]."""
    linhas = [("[0000s] - Início do cronômetro Playwright", "NAO", 0, inicio)]
    segundo = 0

    def ticks(acao, duracao):
        nonlocal segundo
        for _ in range(duracao):
            linhas.append((f"[{segundo:04d}s] - {acao}", "NAO", 1, inicio + timedelta(seconds=segundo)))
            segundo += 1

    for acao, dur in passos_login:
        ticks(acao, dur)
    linhas.append((f"[{segundo:04d}s] - Tempo Login: {segundo:.1f}s", "NAO", segundo, inicio + timedelta(seconds=segundo)))

    total_rotinas = len(rotinas)
    for indice, (nome, passos, erro) in enumerate(rotinas, start=1):
        comeco = segundo
        ticks(f"Rotina {indice}/{total_rotinas} iniciada: {nome}", 1)
        for acao, dur in passos:
            ticks(f"Rotina {indice}/{total_rotinas} - {acao}", dur)
        dur_rotina = segundo - comeco
        sufixo = f" (com erro: {erro})" if erro else ""
        linhas.append((f"[{segundo:04d}s] - Tempo Rotina '{nome}': {dur_rotina:.1f}s{sufixo}",
                       "ERRO" if erro else "NAO", dur_rotina, inicio + timedelta(seconds=segundo)))
        ticks(f"Voltando para a tela principal após '{nome}'", 2)

    status_final = "SIM" if any(r[2] for r in rotinas) else "NAO"
    texto_final = "ERRO EM ROTINA(S): " + ", ".join(r[0] for r in rotinas if r[2]) if status_final == "SIM" else "Finalizando execução"
    linhas.append((f"[{segundo:04d}s] - {texto_final}", status_final, segundo, inicio + timedelta(seconds=segundo)))

    # linhas do log gravadas pelo LogBanco.salvar() depois do fim (horário original de cada linha)
    for seg, nivel, texto in avisos:
        linhas.append((f"[{seg:04d}s] - {texto}", nivel, 1, inicio + timedelta(seconds=seg)))
    linhas.append(("[0000s] - Versao do fluxo Playwright: teste", "OK", 1, inicio))
    return sorted(linhas, key=lambda l: l[3])


def execucao_padrao(inicio: datetime, visualizar_s: int = 6, erro: str | None = None) -> list[tuple]:
    """Uma execução típica: login + 'Pedidos de Compra' + 'Documento de entrada'."""
    return linhas_execucao(
        inicio,
        passos_login=[("Acessando URL do Protheus", 3), ("Iniciando processo de login para o usuário: bf.rpa01", 8),
                      ("Aguardando e-mail com o código MFA desta sessão", 12), ("Preenchendo empresa/filial", 4)],
        rotinas=[
            ("Pedidos de Compra", [("Etapa 1/6: procurando 'Compras' no menu", 1),
                                   ("Etapa 3/6: procurando 'Pedidos de Compra' no menu", 5),
                                   ("Etapa 4/6: procurando 'Visualizar'", visualizar_s),
                                   ("Etapa 5/6: procurando 'Cancelar'", 3),
                                   ("Etapa 6/6: procurando 'sair'", 2)], erro),
            ("Documento de entrada", [("Etapa 1/6: procurando 'Fiscal' no menu", 1),
                                      ("Etapa 3/6: procurando 'Documento Entrada' no menu", 4),
                                      ("Etapa 4/6: procurando 'Visualizar'", 7),
                                      ("Etapa 5/6: procurando 'Cancelar'", 3)], None),
        ],
        avisos=[(30, "WARN", "Clique em 'Visualizar' não teve efeito ('Cancelar' não apareceu em 15s); clicando de novo (1 de 2)")]
        if visualizar_s > 15 else [],
    )


# ---------------------------------------------------------------------------
# Formatos ANTIGOS (histórico anterior a 18/11/2026)
# ---------------------------------------------------------------------------
def execucao_playwright_antiga(inicio: datetime, login_s: int, rotina_s: int, erro: str | None = None) -> list[tuple]:
    """Playwright de set-nov/2026: registros "[0042s] - ...", marca "Acessando menu lateral" e
    linha final "FINALIZANDO EXECUCAO" (ou "ERRO DURANTE EXECUCAO PLAYWRIGHT: ...")."""
    linhas = [("[0000s] - Início do cronômetro Playwright", "NAO", 0, inicio)]
    for s in range(login_s):
        linhas.append((f"[{s:04d}s] - Iniciando processo de login para o usuário: bf.rpa01", "NAO", 1, inicio + timedelta(seconds=s)))
    linhas.append((f"[{login_s:04d}s] - Acessando menu lateral: Compras > Movimento > Pedidos de Compra",
                   "NAO", 1, inicio + timedelta(seconds=login_s)))
    for s in range(login_s + 1, login_s + rotina_s):
        linhas.append((f"[{s:04d}s] - Executando rotina de validacao", "NAO", 1, inicio + timedelta(seconds=s)))
    total = login_s + rotina_s
    if erro:
        linhas.append((f"[{total:04d}s] - ERRO DURANTE EXECUCAO PLAYWRIGHT: {erro}", "SIM", total, inicio + timedelta(seconds=total)))
    else:
        linhas.append(("FINALIZANDO EXECUCAO", "NAO", total, inicio + timedelta(seconds=total)))
    return linhas


def execucao_tir(inicio: datetime, total: int, sucesso: bool = True) -> list[tuple]:
    """Robô TIR (até set/2026): só início/fim com o total; o log entrava com a diferença entre linhas."""
    linhas = [("INICIANDO PROTHEUS_TIR", "OK", 0, inicio),
              ("Abrindo o Protheus", "OK", 12, inicio + timedelta(seconds=12)),
              ("Executando teste", "OK", 30, inicio + timedelta(seconds=42))]
    final = ("FINALIZANDO PROTHEUS_TIR", "OK") if sucesso else ("FINALIZANDO PROTHEUS_TIR COM ERRO", "ERRO")
    linhas.append((final[0], final[1], total, inicio + timedelta(seconds=total)))
    return linhas


def resumo_como_no_oracle(execucoes: dict) -> list[tuple]:
    """Reproduz em Python o SQL_RESUMO_EXECUCOES (uma linha por ID + dia), para os testes.
    execucoes: {(id, dia): [linhas]} com linhas (DESCRICAO, ERRO, TEMPO, HORARIO)."""
    resumo = []
    for (id_execucao, _), linhas in execucoes.items():
        final = max(linhas, key=lambda l: (l[2], l[3]))           # KEEP DENSE_RANK LAST ORDER BY TEMPO, HORARIO
        menu = [l for l in linhas if any(m in l[0] for m in ("Acessando menu lateral:", "Executando fluxo configurado:"))
                or ("Rotina 1/" in l[0] and "iniciada" in l[0])]
        caminho = [l[0] for l in linhas if "Acessando menu lateral:" in l[0] or "Executando fluxo configurado:" in l[0]]
        resumo.append((
            id_execucao,
            min(l[3] for l in linhas),
            max(l[2] for l in linhas),
            final[1], final[0][:300],
            sum(1 for l in linhas if "- Tempo Login:" in l[0]),
            1 if any("PROTHEUS_TIR" in l[0] for l in linhas) else 0,
            min((l[3] for l in menu), default=None),
            max(caminho, default=None),
        ))
    return resumo


def tempos_como_no_oracle(execucoes: dict) -> list[tuple]:
    """Reproduz o SQL_TEMPOS: (ID_EXECUCAO, DESCRICAO, HORARIO) das linhas "- Tempo "."""
    return sorted(((id_, d, h) for (id_, _), ls in execucoes.items() for d, _, _, h in ls if "- Tempo " in d),
                  key=lambda l: (l[2], l[0]))
