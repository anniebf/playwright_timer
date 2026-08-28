from datetime import datetime
import oracledb
import MandaEmail
import json
from dotenv import load_dotenv
import glob
import os
from pathlib import Path
from threading import Event, Lock, Thread
import time
from time import sleep
import logging_config

logger = logging_config.get_logger('LogBanco')

BASE_DIR = Path(__file__).resolve().parent
ID_EXEC_PATH = BASE_DIR / 'idExec.json'
LOG_DIR = BASE_DIR / 'log'

def get_latest_log_file(log_folder=None, log_prefix='log'):
    if log_folder is None:
        log_folder = str(LOG_DIR)
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


class CronometroExecucao:
    """Registra no banco o andamento da execução a cada segundo."""

    def __init__(self, acao_inicial: str = 'ABRINDO NAVEGADOR', status_padrao: str = 'OK'):
        self.id_execucao = id_execucao
        self.status_padrao = status_padrao
        self._acao_atual = acao_inicial
        self._inicio_monotonic: float | None = None
        self._stop_event = Event()
        self._lock = Lock()
        self._thread: Thread | None = None

    def atualizar_acao(self, acao: str) -> None:
        with self._lock:
            self._acao_atual = acao

    def _descricao_tick(self, segundos: int, acao: str) -> str:
        return f'CRONOMETRO [{segundos:04d}s] - {acao}'

    def iniciar(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return

        self._inicio_monotonic = time.monotonic()
        self._stop_event.clear()
        registrar_timer(
            self.id_execucao,
            'INICIO CRONOMETRO PLAYWRIGHT',
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

            registrar_timer(
                self.id_execucao,
                self._descricao_tick(segundos, acao),
                sistema,
                usuario,
                self.status_padrao,
                1,
                datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            )

            self._stop_event.wait(1)

    def finalizar(self, status_final: str = 'OK', descricao_final: str = 'FECHANDO NAVEGADOR') -> int:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

        total = 0
        if self._inicio_monotonic is not None:
            total = int(time.monotonic() - self._inicio_monotonic)

        registrar_timer(
            self.id_execucao,
            descricao_final,
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

def resultado():
    connection = oracledb.connect(user=username, password=password, dsn=dsn)
    cursor = connection.cursor()
    cursor.execute("""
        SELECT TEMPO_EXECUCAO FROM RPA.TIMER 
        WHERE DESCRICAO = 'FINALIZANDO PROTHEUS_TIR' 
        FETCH FIRST ROW ONLY
    """)
    ultimo_resultados = cursor.fetchone()
    ultimo_resultado = ultimo_resultados[0] if ultimo_resultados else 0
    connection.close()
    return ultimo_resultado

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
    
def finalizarErro(data_atual):
    
    print(data_atual)
    data_finalizacao = datetime.now()
    print(data_finalizacao)
    
    diferenca = data_finalizacao - data_atual
    tempocorridos = int(diferenca.total_seconds()) 
    
    data_final = data_finalizacao.strftime('%Y-%m-%d %H:%M:%S')
    
    registrar_timer(id_execucao, 'FINALIZANDO PROTHEUS_TIR COM ERRO', sistema, usuario, 'ERRO', tempocorridos, data_final)
    logger.info('Finalização com erro registrada no banco')

def salvar():
    logger.info('Registrando processo de log no banco')
    global erro_tratado_erro, erro_tratado_war, tempo_executado, erro
    
    sleep(10)
    arquivo_log = get_latest_log_file()
    if not arquivo_log:
        logger.warning('Nenhum arquivo de log encontrado!')
        return
    
    logger.info(f'Processando arquivo: {arquivo_log}')
    
    last_timestamp = None
    log_path = Path(arquivo_log)
    with log_path.open('r', encoding='utf-8') as arquivo:
        for linha in arquivo:
            linha = linha.strip()  # Remove espaços e quebras de linha
            
            # Processa linhas especiais (Ran/OK) mesmo sem timestamp
            if linha.startswith("Ran") and " in " in linha:
                try:
                    tempo_str = linha.split(" in ")[1].replace("s", "").split(".")[0]
                    tempo_executado = int(tempo_str)
                    logger.info(f'Tempo de execução capturado: {tempo_executado}s')
                    continue
                except (ValueError, IndexError) as e:
                    logger.warning(f'Erro ao capturar tempo: {e}')
                    continue
            
            if linha.lower() == "ok":
                if tempo_executado:
                    logger.info('Registro final OK gravado no banco')
                    data_atual = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    registrar_timer(
                        id_execucao,
                        'FINALIZANDO PROTHEUS_TIR', 
                        sistema, 
                        usuario, 
                        'OK', 
                        tempo_executado,
                        data_atual,
                        
                    )
                continue
            elif "FAILED" in linha:
                logger.warning('Processo falhou, registrando erro no banco e enviando email')
                
                MandaEmail.enviar_warning('Foi Encontrada a linha FAILED no log, a execução nmao foi bem sucedida')
                data_inicio = inicio()
                erro = True
                finalizarErro(data_inicio)
            
            # Processa apenas linhas com timestamp
            if len(linha) < 19:
                continue
                
            timestamp_str = linha[:19]
            
            if is_valid_timestamp(timestamp_str):
                new_timestamp = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S')
                tempo_execucao = 1  # Valor padrão
                
                if last_timestamp:
                    delta = new_timestamp - last_timestamp
                    tempo_execucao = delta.total_seconds()
                
                last_timestamp = new_timestamp
            else:
                timestamp_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                tempo_execucao = 1
            
            try:
                if "INFO" in linha:
                    descricao = linha[29:-1].strip().replace("'", "''")
                    registrar_timer(id_execucao, descricao, sistema, usuario, 'OK', tempo_execucao, timestamp_str)
                
                elif "WARNING:" in linha and not erro_tratado_war:
                    descricao = linha[34:].strip().replace("'", "''")
                    registrar_timer(id_execucao, descricao, sistema, usuario, 'WARN', tempo_execucao, timestamp_str)
                    erro_tratado_war = True
                    
            except Exception as e:
                logger.error(f'Erro ao processar linha: {linha} - Erro: {e}')
    
    # Verificação final
    ultimo_resultado = resultado()
    if erro:
        pass
    else:
        logger.info('Sem erro fatal, avaliando tempo de execução')
        if ultimo_resultado and tempo_executado and ultimo_resultado >= tempo_executado * 1.3:
            MandaEmail.enviar_temp_exc(tempo_executado,ultimo_resultado)
        else:
            logger.info('Tempo de execução dentro do esperado')

if __name__ == '__main__':
    inicio()
    salvar()
    finalizarErro('')
    