import logging
import os
import re
import threading
from datetime import datetime
from pathlib import Path

# Configuração do diretório de log
BASE_DIR = Path(__file__).resolve().parent
log_dir = BASE_DIR / "log_playwright"
if not log_dir.exists():
    log_dir.mkdir(parents=True, exist_ok=True)

# Nome do arquivo de log unificado
log_file = log_dir / f"{datetime.now().strftime('%Y-%m-%d_%H-%M')}_protheus_timer.log"

# Configuração do logger raiz
def setup_logging():
    """Configura o logging unificado para todo o projeto."""
    
    # Cria o formatter com timestamp
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Configura o handler para arquivo (modo 'w' para sobrescrever a cada execução)
    file_handler = logging.FileHandler(str(log_file), mode='w', encoding='utf-8')
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)
    
    # Configura o handler para console (opcional)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)
    
    # Configura o logger raiz
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    
    # Remove handlers existentes para evitar duplicação
    root_logger.handlers.clear()
    
    # Adiciona os handlers
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
    
    return root_logger

# Inicializa o logging automaticamente
logger = setup_logging()

def get_logger(name: str = None):
    """Retorna um logger configurado para o módulo específico."""
    return logging.getLogger(name)

# ---------------------------------------------------------------------------
# Log separado por rotina
# ---------------------------------------------------------------------------
# Cada rotina roda em uma thread própria. Para ter um arquivo só dela, adicionamos
# ao logger raiz um FileHandler com um filtro que aceita apenas registros gerados
# pela thread atual. O log geral (log_file) continua recebendo tudo.
# Os arquivos ficam em log_playwright/rotinas/ (subpasta) para não confundir o
# LogBanco.salvar(), que lê o .log mais recente direto de log_playwright/.
rotinas_log_dir = log_dir / "rotinas"


class _FiltroDaThread(logging.Filter):
    """Deixa passar só os registros emitidos pela thread que criou o filtro."""

    def __init__(self, thread_id: int):
        super().__init__()
        self.thread_id = thread_id

    def filter(self, record: logging.LogRecord) -> bool:
        return record.thread == self.thread_id


def iniciar_log_da_rotina(indice: int, id_execucao: int, nome: str) -> logging.Handler:
    """Cria log_playwright/rotinas/<data>_r<indice>_<id>_<nome>.log para a thread atual."""
    rotinas_log_dir.mkdir(parents=True, exist_ok=True)
    nome_arquivo = re.sub(r"[^A-Za-z0-9_-]+", "_", nome).strip("_")[:60] or "rotina"
    arquivo = rotinas_log_dir / (
        f"{datetime.now().strftime('%Y-%m-%d_%H-%M')}_r{indice:02d}_{id_execucao}_{nome_arquivo}.log"
    )
    handler = logging.FileHandler(str(arquivo), mode="w", encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    ))
    handler.setLevel(logging.INFO)
    handler.addFilter(_FiltroDaThread(threading.get_ident()))
    logging.getLogger().addHandler(handler)
    return handler


def encerrar_log_da_rotina(handler: logging.Handler) -> None:
    """Remove e fecha o handler da rotina (chamar no finally)."""
    logging.getLogger().removeHandler(handler)
    handler.close()
