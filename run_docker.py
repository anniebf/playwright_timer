import sys
import subprocess

# Comando Docker a ser executado
DOCKER_CMD = [
    "docker", "run",
    "--rm",
  #  "-it",
    "-e", "TZ=America/Cuiaba",
    "-v", "/etc/localtime:/etc/localtime:ro",
    "-v", "/python_bf/playwright_timer/config.json:/app/config.json:ro",
    "-v", "/python_bf/playwright_timer/.env:/app/.env:ro",
    "-v", "/python_bf/playwright_timer/log_playwright:/app/log_playwright",
    "hiannyurt/playwright-webagent:latest"
]

def run_container():
    try:
        # Executa o comando mantendo a interatividade no terminal (-it)
        subprocess.run(DOCKER_CMD, check=True)
    except subprocess.CalledProcessError as e:
        print(f"\n[ERRO] O container encerrou com código de saída: {e.returncode}")
        sys.exit(e.returncode)
    except FileNotFoundError:
        print("\n[ERRO] O utilitário 'docker' não foi encontrado no PATH do sistema.")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nExecução cancelada pelo usuário.")

if __name__ == "__main__":
    run_container()