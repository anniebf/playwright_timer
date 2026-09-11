from datetime import datetime
import oracledb
import MandaEmail
import json
from dotenv import load_dotenv
import glob
import os
import re
from pathlib import Path
from threading import Event, Lock, Thread
import time
from time import sleep
import logging_config

logger = logging_config.get_logger('LogBanco')

BASE_DIR = Path(__file__).resolve().parent
ID_EXEC_PATH = BASE_DIR / 'idExec.json'
LOG_DIR = BASE_DIR / 'log_playwright'

def get_latest_log_file(log_folder=None, log_prefix='log'):
    if log_folder is None:
        log_folder = str(LOG_DIR)
    if Path(log_folder).resolve() == LOG_DIR.resolve():
        log_files = glob.glob(os.path.join(log_folder, '*.log'))
    else:
        log_files = glob.glob(os.path.join(log_folder, f'{log_prefix}*.txt'))
    if not log_files:
        log_files = glob.glob(f'{log_prefix}*.txt')
    log_files.sort(key=os.path.getmtime, reverse=True)
    return log_files[0] if log_files else None

load_dotenv(BASE_DIR / '.env')
username = os.getenv('usernamedb')
password = os.getenv('passworddb')
dsn = os.getenv('dsnhomol')
usuario = os.getenv('usuario')


def _next_execution_id() -> int:
    if not ID_EXEC_PATH.exists():
        ID_EXEC_PATH.write_text('{"id_execucao": 1}', encoding='utf-8')

    with ID_EXEC_PATH.open('r', encoding='utf-8') as fileid:
        dadosid = json.load(fileid)

    id_atual = int(dadosid.get('id_execucao', 1))
    dadosid['id_execucao'] = id_atual + 1

    with ID_EXEC_PATH.open('w', encoding='utf-8') as fileid:
        json.dump(dadosid, fileid, indent=4)

    return id_atual


id_execucao = _next_execution_id()
    
    
sistema = 'PROTHEUS'
erro_tratado_erro = False
erro_tratado_war = False
tempo_executado = 0  # Inicializa a variável
erro = False


def formatar_descricao( nivel: str, mensagem: str) -> str:
    return f'[{nivel.strip()}] {mensagem.strip()}'


class CronometroExecucao:
    """Registra no banco o andamento da execução a cada segundo."""

    def __init__(self, acao_inicial: str = 'ABRINDO NAVEGADOR', status_padrao: str = 'OK'):
        self.id_execucao = id_execucao
        self.status_padrao = status_padrao
        self._status_atual = status_padrao
        self._acao_atual = acao_inicial
        self._inicio_monotonic: float | None = None
        self._stop_event = Event()
        self._lock = Lock()
        self._thread: Thread | None = None

    def atualizar_acao(self, acao: str) -> None:
        with self._lock:
            self._acao_atual = acao

    def marcar_erro(self) -> None:
        with self._lock:
            self._status_atual = 'ERRO'

    def _descricao_tick(self, segundos: int, acao: str) -> str:
        return formatar_descricao(
            self._status_atual,
            f'{segundos:04d}s- {acao}',
        )

    def iniciar(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return

        self._inicio_monotonic = time.monotonic()
        self._stop_event.clear()
        registrar_timer(
            self.id_execucao,
            formatar_descricao( self.status_padrao, 'INICIO CRONOMETRO PLAYWRIGHT'),
            sistema,
            usuario,
            self.status_padrao,
            0,
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        )

        self._thread = Thread(target=self._executar_ticks, name='cronometro-playwright', daemon=True)
        self._thread.start()

    def _executar_ticks(self) -> None:
        if self._inicio_monotonic is None:
            return

        while not self._stop_event.is_set():
            segundos = int(time.monotonic() - self._inicio_monotonic)
            with self._lock:
                acao = self._acao_atual
                status = self._status_atual

            registrar_timer(
                self.id_execucao,
                self._descricao_tick(segundos, acao),
                sistema,
                usuario,
                status,
                1,
                datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            )

            self._stop_event.wait(1)

    def finalizar(self, status_final: str = 'OK', descricao_final: str = 'FINALIZANDO EXECUCAO') -> int:
        self._stop_event.set()
        with self._lock:
            self._status_atual = status_final
        if self._thread is not None:
            self._thread.join(timeout=2)

        total = 0
        if self._inicio_monotonic is not None:
            total = int(time.monotonic() - self._inicio_monotonic)

        registrar_timer(
            self.id_execucao,
            formatar_descricao( status_final, descricao_final),
            sistema,
            usuario,
            status_final,
            total,
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        )
        return total


def is_valid_timestamp(timestamp_str):
    try:
        datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S')
        return True
    except ValueError:
        return False

def resultado(id_execucao_atual: int | None = None):
    connection = oracledb.connect(user=username, password=password, dsn=dsn)
    cursor = connection.cursor()
    try:
        filtro_execucao_atual = ""
        parametros = {}
        if id_execucao_atual is not None:
            filtro_execucao_atual = "AND ID_EXECUCAO <> :id_execucao_atual"
            parametros["id_execucao_atual"] = id_execucao_atual

        cursor.execute(
            f"""
            SELECT AVG(TEMPO_EXECUCAO)
            FROM (
                SELECT TEMPO_EXECUCAO
                FROM RPA.TIMER
                WHERE DESCRICAO = 'FINALIZANDO PROTHEUS_TIR'
                  AND ERRO = 'OK'
                  {filtro_execucao_atual}
                ORDER BY HORARIO_EXECUCAO DESC, ID_EXECUCAO DESC
                FETCH FIRST 10 ROWS ONLY
            )
            """,
            parametros,
        )
        media_resultado = cursor.fetchone()
        return float(media_resultado[0]) if media_resultado and media_resultado[0] is not None else 0
    finally:
        connection.close()

def registrar_timer(id_execucao, descricao, sistema, usuario, erro, tempo_execucao, timestamp_str):
    if not is_valid_timestamp(timestamp_str):
        timestamp_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    connection = oracledb.connect(user=username, password=password, dsn=dsn)
    cursor = connection.cursor()
    try:
        cursor.execute("""
            INSERT INTO RPA.TIMER (ID_EXECUCAO,DESCRICAO, SISTEMA, USUARIO, ERRO, TEMPO_EXECUCAO, HORARIO_EXECUCAO)
            VALUES (:id_execucao, :descricao, :sistema, :usuario, :erro, :tempo_execucao, TO_TIMESTAMP(:timestamp_str, 'YYYY-MM-DD HH24:MI:SS'))
        """, {
            'id_execucao': id_execucao,
            'descricao': descricao,
            'sistema': sistema,
            'usuario': usuario,
            'erro': erro,
            'tempo_execucao': tempo_execucao,
            'timestamp_str': timestamp_str
        })
        connection.commit()
        logger.info(f'Registro inserido: {descricao} - Erro: {erro}')
    except Exception as e:
        logger.error(f'Erro ao inserir no banco: {e}')
    finally:
        connection.close()

def inicio():
    data_atual = datetime.now() 
    data_inicio = data_atual.strftime('%Y-%m-%d %H:%M:%S')
    
    registrar_timer(id_execucao, 'INICIANDO PROTHEUS_TIR', sistema, usuario, 'OK', 0, data_inicio)
    
    return data_atual
    
def finalizarErro(data_atual: datetime):
    data_finalizacao = datetime.now()

    diferenca = data_finalizacao - data_atual
    tempocorridos = int(diferenca.total_seconds()) 
    
    data_final = data_finalizacao.strftime('%Y-%m-%d %H:%M:%S')
    
    registrar_timer(id_execucao, 'FINALIZANDO PROTHEUS_TIR COM ERRO', sistema, usuario, 'ERRO', tempocorridos, data_final)
    logger.info('Finalização com erro registrada no banco')

def salvar():
    """Grava no banco os registros produzidos pelo logging do Playwright."""
    arquivo_log = get_latest_log_file()
    if not arquivo_log:
        logger.warning('Nenhum arquivo de log do Playwright encontrado em %s', LOG_DIR)
        return
    
    logger.info('Processando arquivo de log do Playwright: %s', arquivo_log)
    linha_log = re.compile(
        r'^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) - '
        r'(?P<logger>[^-]+) - (?P<level>INFO|WARNING|ERROR|CRITICAL) - '
        r'(?P<message>.*)$'
    )

    log_path = Path(arquivo_log)
    linhas = log_path.read_text(encoding='utf-8').splitlines()
    for linha in linhas:
        registro = linha_log.match(linha)
        if not registro or registro.group('logger').strip() == 'LogBanco':
            continue

        nivel = registro.group('level')
        status = 'ERRO' if nivel in {'ERROR', 'CRITICAL'} else 'WARN' if nivel == 'WARNING' else 'OK'
        descricao = formatar_descricao(
            status,
            registro.group('message'),
        )
        registrar_timer(
            id_execucao,
            descricao,
            sistema,
            usuario,
            status,
            1,
            registro.group('timestamp'),
        )

if __name__ == '__main__':
    inicio()
    salvar()
    