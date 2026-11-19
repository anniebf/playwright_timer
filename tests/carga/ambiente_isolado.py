"""
ambiente_isolado.py - Monta um ambiente "de mentira" para os testes de carga.

LÓGICA GERAL
------------
Os testes de carga precisam rodar o código de verdade do robô MAS sem efeitos
colaterais em produção. Para isso:

1. CÓPIA DOS MÓDULOS: os .py do projeto são copiados para uma pasta temporária.
   Motivo: só de importar, o LogBanco incrementa o idExec.json (contador de
   ID_EXECUCAO) e o logging_config cria um .log em log_playwright/. Na cópia,
   o contador e os logs reais ficam intocados.

2. DUBLÊS (substituem dependências externas via sys.modules):
   - oracledb   -> BancoFalso: guarda os INSERTs em memória, conta conexões e
                   pode simular latência de rede ou banco fora do ar.
                   NUNCA conecta no Oracle real.
   - MandaEmail -> funções que só contam as chamadas (nenhum e-mail sai).
   - graph      -> stub (nenhuma leitura de e-mail/MFA).
   - playwright -> stub (nenhum navegador abre, nenhuma licença do Protheus é usada).
   - dotenv     -> stub só quando o pacote não estiver instalado.
   Sem o .env copiado, nenhuma credencial real chega aos módulos.

3. Os módulos são importados UMA vez por processo (obter_ambiente()) e cada
   teste zera o banco falso com amb.banco.resetar().
"""
from __future__ import annotations

import atexit
import importlib
import json
import logging
import shutil
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

RAIZ_PROJETO = Path(__file__).resolve().parents[2]

# Módulos do projeto que serão copiados e importados da pasta temporária.
MODULOS_PROJETO = ["logging_config", "erros_protheus", "rotinas", "mapa_calor", "LogBanco", "main_playwright"]
ARQUIVOS_EXTRAS = ["mapa_calor_template.html"]


# ---------------------------------------------------------------------------
# Dublê do Oracle
# ---------------------------------------------------------------------------
class ConexaoRecusada(Exception):
    """Erro que o BancoFalso levanta quando simula o banco fora do ar."""


class BancoFalso:
    """Imita o oracledb: connect() -> conexão -> cursor.execute()/fetch*/commit/close."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.resetar()

    def resetar(self) -> None:
        with self._lock:
            self.latencia_conexao = 0.0     # segundos de espera em cada connect()
            self.falhar_conexao = False     # True = connect() levanta ConexaoRecusada
            self.conexoes = 0               # total de connect() bem-sucedidos
            self.abertas = 0                # conexões abertas agora
            self.max_simultaneas = 0        # pico de conexões abertas ao mesmo tempo
            self.inserts: list[dict] = []   # parâmetros de cada INSERT, na ordem
            self.resultado_fetchone = (None,)
            self.resultado_fetchall: list[tuple] = []

    # -- API usada pelo código do projeto --
    def connect(self, **_credenciais):
        if self.falhar_conexao:
            raise ConexaoRecusada("ORA-12519: TNS:no appropriate service handler found (simulado)")
        if self.latencia_conexao:
            time.sleep(self.latencia_conexao)
        with self._lock:
            self.conexoes += 1
            self.abertas += 1
            self.max_simultaneas = max(self.max_simultaneas, self.abertas)
        return _ConexaoFalsa(self)

    def _fechou(self) -> None:
        with self._lock:
            self.abertas -= 1

    def _registrar(self, sql: str, parametros) -> None:
        if sql.lstrip().upper().startswith("INSERT"):
            with self._lock:
                self.inserts.append(dict(parametros or {}))

    def como_modulo(self) -> types.ModuleType:
        """Devolve um módulo 'oracledb' falso apontando para este banco."""
        modulo = types.ModuleType("oracledb")
        modulo.connect = self.connect
        modulo.defaults = types.SimpleNamespace(fetch_lobs=True)
        modulo.Error = ConexaoRecusada
        modulo.DatabaseError = ConexaoRecusada
        return modulo


class _CursorFalso:
    def __init__(self, banco: BancoFalso) -> None:
        self._banco = banco

    def execute(self, sql, parametros=None):
        self._banco._registrar(sql, parametros)

    def fetchone(self):
        return self._banco.resultado_fetchone

    def fetchall(self):
        return list(self._banco.resultado_fetchall)

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class _ConexaoFalsa:
    def __init__(self, banco: BancoFalso) -> None:
        self._banco = banco
        self._fechada = False

    def cursor(self):
        return _CursorFalso(self._banco)

    def commit(self):
        pass

    def close(self):
        if not self._fechada:
            self._fechada = True
            self._banco._fechou()


# ---------------------------------------------------------------------------
# Dublês de e-mail, Graph, Playwright e dotenv
# ---------------------------------------------------------------------------
class EmailsFalsos:
    """Substitui o MandaEmail: cada função só anota que foi chamada."""

    FUNCOES = ["enviar_erro_base_selenium", "enviar_erro_execucao", "enviar_erro_licenca", "enviar_temp_exc"]

    def __init__(self) -> None:
        self.chamadas: list[str] = []

    def como_modulo(self) -> types.ModuleType:
        modulo = types.ModuleType("MandaEmail")
        for nome in self.FUNCOES:
            setattr(modulo, nome, lambda *a, _nome=nome, **k: self.chamadas.append(_nome))
        return modulo


def _modulo_playwright_falso() -> tuple[types.ModuleType, types.ModuleType]:
    """playwright.sync_api mínimo: só os nomes que o main_playwright importa."""
    pacote = types.ModuleType("playwright")
    sync_api = types.ModuleType("playwright.sync_api")

    class PlaywrightError(Exception):
        pass

    class PlaywrightTimeoutError(PlaywrightError):
        pass

    def sync_playwright():
        raise RuntimeError("Navegador desabilitado nos testes de carga (dublê)")

    for nome in ["Browser", "BrowserContext", "Frame", "Locator", "Page"]:
        setattr(sync_api, nome, type(nome, (), {}))
    sync_api.Error = PlaywrightError
    sync_api.TimeoutError = PlaywrightTimeoutError
    sync_api.sync_playwright = sync_playwright
    pacote.sync_api = sync_api
    return pacote, sync_api


def _modulo_graph_falso() -> types.ModuleType:
    modulo = types.ModuleType("graph")

    def __getattr__(nome):  # qualquer função do graph vira "sem código MFA"
        return lambda *a, **k: None

    modulo.__getattr__ = __getattr__
    return modulo


def _modulo_dotenv_falso() -> types.ModuleType:
    modulo = types.ModuleType("dotenv")
    modulo.load_dotenv = lambda *a, **k: False
    return modulo


# ---------------------------------------------------------------------------
# Montagem do ambiente
# ---------------------------------------------------------------------------
class Ambiente:
    """Módulos importados da cópia temporária + dublês para inspecionar."""

    def __init__(self, pasta: Path, banco: BancoFalso, emails: EmailsFalsos) -> None:
        self.pasta = pasta
        self.banco = banco
        self.emails = emails
        for nome in MODULOS_PROJETO:
            setattr(self, nome, sys.modules[nome])


def copiar_projeto(destino: Path) -> None:
    """Copia só o necessário. NÃO copia .env (credenciais) nem o idExec.json real."""
    for nome in MODULOS_PROJETO:
        shutil.copy2(RAIZ_PROJETO / f"{nome}.py", destino / f"{nome}.py")
    for nome in ARQUIVOS_EXTRAS:
        shutil.copy2(RAIZ_PROJETO / nome, destino / nome)
    # O main_playwright lê o config.json ao importar: usa o modelo (sem senha real).
    shutil.copy2(RAIZ_PROJETO / "config.example.json", destino / "config.json")
    (destino / "idExec.json").write_text(json.dumps({"id_execucao": 1}), encoding="utf-8")


def instalar_dubles(banco: BancoFalso, emails: EmailsFalsos) -> None:
    """Registra os dublês em sys.modules ANTES de importar o projeto."""
    sys.modules["oracledb"] = banco.como_modulo()
    sys.modules["MandaEmail"] = emails.como_modulo()
    sys.modules["graph"] = _modulo_graph_falso()
    pacote, sync_api = _modulo_playwright_falso()
    sys.modules["playwright"] = pacote
    sys.modules["playwright.sync_api"] = sync_api
    try:
        import dotenv  # noqa: F401  (pacote real, se instalado)
    except ImportError:
        sys.modules["dotenv"] = _modulo_dotenv_falso()


def importar_de(pasta: Path) -> None:
    """Importa os módulos do projeto a partir da pasta temporária."""
    for nome in MODULOS_PROJETO:
        sys.modules.pop(nome, None)  # garante que não reaproveita uma importação da pasta real
    sys.path.insert(0, str(pasta))
    for nome in MODULOS_PROJETO:
        importlib.import_module(nome)
    # O logging_config manda INFO para o console; nos testes de carga os erros são
    # provocados de propósito (centenas de linhas). O log em arquivo continua completo.
    for handler in logging.getLogger().handlers:
        if type(handler) is logging.StreamHandler:
            handler.setLevel(logging.CRITICAL)


_AMBIENTE: Ambiente | None = None


def obter_ambiente() -> Ambiente:
    """Cria o ambiente na primeira chamada e reaproveita nas seguintes."""
    global _AMBIENTE
    if _AMBIENTE is None:
        temporario = tempfile.TemporaryDirectory(prefix="carga_timer_", ignore_cleanup_errors=True)
        atexit.register(temporario.cleanup)
        pasta = Path(temporario.name)
        copiar_projeto(pasta)
        banco, emails = BancoFalso(), EmailsFalsos()
        instalar_dubles(banco, emails)
        importar_de(pasta)
        _AMBIENTE = Ambiente(pasta, banco, emails)
    return _AMBIENTE
