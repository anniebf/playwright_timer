from airflow.decorators import dag, task
from airflow.operators.empty import EmptyOperator
import pendulum
import os
import sys

# --- Caminhos de Configuração ---
os.environ['PATH'] = '/bin:' + os.environ['PATH']
PATH_TO_PYTHON_BINARY = "/python_bf/playwright_timer/.venv/bin/python"
# Variável com o caminho do diretório que o script precisa
DIRETORIO_DO_SCRIPT = "/python_bf/playwright_timer/" 
# ------------------------------

default_args = {
    'owner': 'HiannyUrt',
    'email': ['hianny.urt@bomfuturo.com.br'],
    'email_on_failure': True,
    "retries": 1,
    "retry_delay": pendulum.duration(seconds=60)
}

@dag(
    dag_id='Docker_playwright', 
    description= "cronmetro rotina do protheus",
    start_date= pendulum.datetime(2024, 1, 1, tz='America/Cuiaba'),
    default_args= default_args,
    schedule= "30 * * * *", 
    max_active_runs= 1,
    dagrun_timeout= 3600,
    catchup= False,
    tags=['Jobs', 'Rpa','docker']
)
def playwright():
    
    inicio = EmptyOperator(task_id="inicio")
    fim = EmptyOperator(task_id="fim")

    @task.external_python(
        task_id="executar_docker_playwright",
        python=PATH_TO_PYTHON_BINARY
    )
    def call_job_playwright(diretorio_script_path: str):
        
        import sys
        import os # Importamos 'os' para manipulação do sistema de arquivos
        
        try:
            os.chdir(diretorio_script_path)
            # Opcional: Para logar e confirmar o novo diretório de trabalho
            # print(f"CWD alterado para: {os.getcwd()}") 
        except OSError as e:
            # Lançar exceção se não conseguir mudar de diretório
            raise Exception(f"Erro fatal: Não foi possível mudar o diretório de trabalho para {diretorio_script_path}. Detalhe: {e}") 
            
        # Adiciona o caminho ao sys.path para que o 'from download_faturas import iniciar_job' funcione
        sys.path.append(diretorio_script_path) 
        
        # Importa e executa a função principal
        from run_docker import run_container
        run_container()

    # Passa o caminho do diretório como argumento para a função da task
    task_docker = call_job_playwright(diretorio_script_path=DIRETORIO_DO_SCRIPT)

    inicio >> task_docker >> fim
    
playwright()
