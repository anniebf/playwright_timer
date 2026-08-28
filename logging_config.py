import logging
import os
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