"""
main_playwright.py - Cronômetro de rotinas do Protheus (Playwright + TOTVS WebAgent).

LÓGICA GERAL
------------
1. Lê as rotinas (uma ou mais) de --fluxo ou de "Rotinas"/"Fluxo" no config.json
   (ver rotinas.py). Trocar rotinas não exige alterar código.
2. Cada rotina roda em uma SESSÃO PRÓPRIA (navegador novo, login, MFA,
   empresa/filial, caminho da rotina, fecha navegador), dentro de uma THREAD.
   No máximo "MaxThreads" (config.json, padrão 3) rotinas rodam ao mesmo tempo.
3. Login + MFA + empresa/filial são feitos em fila (MFA_LOCK): o código MFA
   chega por e-mail e, em paralelo, uma thread poderia ler o código da outra.
   Além disso, só é aceito o e-mail recebido DEPOIS do clique em "Entrar".
4. O tempo de cada rotina mede só os cliques do caminho (sem login). Ele vai para:
   - o log (resumo no final);
   - a tabela RPA.TIMER (cada rotina com seu próprio ID_EXECUCAO);
   - log_playwright/tempos_rotinas_<id>.json.
   Cada rotina também tem um log só dela em log_playwright/rotinas/.
5. Falhas continuam com o comportamento antigo, agora POR ROTINA: novas tentativas
   (ExecutionRetries) e e-mail de erro. Uma rotina com falha não para as outras.
"""
import argparse
import json
import os
import re
from socket import timeout
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import sys
from pathlib import Path
from time import monotonic, sleep

from dotenv import load_dotenv
from playwright.sync_api import (
    Browser,
    BrowserContext,
    Error as PlaywrightError,
    Frame,
    Locator,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from MandaEmail import enviar_erro_base_selenium, enviar_erro_execucao, enviar_temp_exc
import graph
import LogBanco
import logging_config
import rotinas as rotinas_cfg
from rotinas import Rotina, ResultadoRotina

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"

logger = logging_config.get_logger("main_playwright_from_main")
BUILD_VERSION = "2026-11-18-rotinas-threads"

# Fila do login/MFA entre as threads: só uma sessão por vez faz login + MFA +
# empresa/filial. Sem isso, duas sessões pedem código ao mesmo tempo e uma pode
# preencher o código que pertence à outra.
MFA_LOCK = threading.Lock()

# Carrega o arquivo JSON com log de falha caso ocorra erro na leitura
try:
    with CONFIG_PATH.open("r", encoding="utf-8") as config_file:
        CONFIG = json.load(config_file)
except Exception as e:
    logger.exception("Erro crítico ao carregar arquivo de configuração (%s): %s", CONFIG_PATH, e)
    sys.exit(1)

load_dotenv(BASE_DIR / ".env")

#Normaliza o valor de Headless para booleano
def _parse_bool(value: object, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "f", "no", "n", "off"}:
        return False
    return default

# cria uma função para construir os argumentos de linha de comando
def _build_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fluxo Playwright equivalente ao main.py")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--headless", action="store_true", help="Executa sem abrir a janela do navegador")
    mode.add_argument("--headed", action="store_true", help="Executa com a janela do navegador visivel")
    # Cada --fluxo é uma rotina. Se informado, tem prioridade sobre o config.json.
    parser.add_argument(
        "--fluxo",
        action="append",
        metavar='"A > B > sair"',
        help="Caminho de uma rotina (pode repetir para várias rotinas)",
    )
    return parser.parse_args()


# classe principal que encapsula o fluxo do Playwright
class ProtheusPlaywrightMainFlow:
    def __init__(self, page: Page, cronometro: LogBanco.CronometroExecucao):
        self.page = page
        self.cronometro = cronometro
        self.timeout = int(CONFIG.get("TimeOut", 30)) * 1000
        self.mfa_confirmado = False
        # Horário (UTC) do clique em 'Entrar'; o e-mail do MFA precisa ser posterior a ele.
        self.mfa_solicitado_em: datetime | None = None

    # função para atualizar a ação no cronometro
    def _atualizar_acao(self, mensagem: str) -> None:
        self.cronometro.atualizar_acao(mensagem)

    # função para obter todos os frames da página
    def _frames(self) -> list[Frame]:
        return self.page.frames
    # função  para encontrar o primeiro seletor visível em uma lista de seletores
    def _first_visible(self, selectors: list[str], timeout: int = 1000) -> Locator | None:
        timeout = max(1, timeout)
        for frame in self._frames():
            for selector in selectors:
                locator = frame.locator(selector).first
                try:
                    if locator.is_visible(timeout=timeout):
                        return locator
                except PlaywrightTimeoutError:
                    continue
                except Exception as error:
                    logger.debug("Erro inesperado ao checar visibilidade do seletor '%s': %s", selector, error)
                    continue
        return None

    def _primeiro_elemento_visivel(
        self,
        textos: list[str],
        timeout: int,
    ) -> str | None:
        """Retorna o primeiro texto visível entre as opções informadas."""
        deadline = monotonic() + timeout / 1000
        while monotonic() < deadline:
            restante_ms = max(1, int((deadline - monotonic()) * 1000))
            for texto in textos:
                try:
                    self._aguardar_item_visivel(texto, timeout=min(500, restante_ms))
                    return texto
                except (PlaywrightTimeoutError, PlaywrightError):
                    continue
        return None
    
    # função para clicar em um elemento de texto específico, com timeout e opção de correspondência exata
    def _click_text(self, text: str, timeout: int = 10000, exact: bool = True) -> None:
        deadline = monotonic() + timeout / 1000
        while monotonic() < deadline:
            for frame in self._frames():
                # Tenta localizar o elemento por diferentes métodos
                candidates = [
                    frame.get_by_role("button", name=text, exact=exact),
                    frame.get_by_role("menuitem", name=text, exact=exact),
                    frame.get_by_role("treeitem", name=text, exact=exact),
                    frame.get_by_text(text, exact=exact),
                ]
                # Tenta clicar no primeiro elemento visível encontrado
                for candidate in candidates:
                    try:
                        element = candidate.first
                        while element.is_visible(timeout=9000):
                            # Executa o duplo clique diretamente
                            element.dblclick()
                            return
                    except (PlaywrightTimeoutError, PlaywrightError):
                        continue
                    except Exception as err:
                        logger.warning("Falha ao interagir com texto '%s': %s", text, err)
                        continue
            sleep(0.2)

        err_msg = f"Elemento com texto {text!r} não foi encontrado dentro do tempo limite de {timeout}ms"
        logger.error(err_msg)
        raise PlaywrightTimeoutError(err_msg)

    # função para preencher campos de entrada com um valor específico, com timeout
    def _fill(self, selectors: list[str], value: str, timeout: int = 10000) -> None:
        valor_log = "******" if "password" in " ".join(selectors).lower() else value
        self._atualizar_acao(f"Procurando campo {selectors} para preencher com '{valor_log}'")
        # Calcula o tempo limite absoluto para a operação
        deadline = monotonic() + timeout / 1000
        last_error: Exception | None = None

        # Tenta preencher o campo até que o tempo limite seja atingido
        while monotonic() < deadline:
            restante_ms = max(1, int((deadline - monotonic()) * 1000))
            locator = self._first_visible(selectors, timeout=min(300, restante_ms))
            if locator is not None:
                try:
                    locator.fill(value, timeout=min(1500, restante_ms))
                    self._atualizar_acao(f"Campo {selectors} encontrado e preenchido")
                    return
                except (PlaywrightTimeoutError, PlaywrightError) as error:
                    last_error = error
                except Exception as error:
                    logger.warning("Erro não mapeado durante preenchimento dos seletores %s: %s", selectors, error)
                    last_error = error

            sleep(0.2)

        err_msg = f"Campo não encontrado: {selectors}. Último erro registrado: {last_error}"
        logger.error(err_msg)
        raise PlaywrightTimeoutError(err_msg)

    # função para abrir URL
    def abrir(self) -> None:
        url = CONFIG.get("Url")
        if not url:
            logger.error("A chave 'Url' não está definida na configuração.")
            raise ValueError("URL não informada nas configurações.")

        logger.info("Iniciando automacao para URL: %s", url)
        timeout = self.timeout * 2
        deadline = monotonic() + timeout / 1000
        last_error: Exception | None = None
        # Tenta abrir a URL até 3 vezes antes de desistir
        for tentativa in range(1, 4):
            restante_ms = max(1, int((deadline - monotonic()) * 1000))
            try:
                self.page.goto(url, wait_until="domcontentloaded", timeout=restante_ms)
                logger.info("URL carregada na tentativa %d", tentativa)
                return
            except (PlaywrightTimeoutError, PlaywrightError) as error:
                last_error = error
                logger.warning(
                    "Falha ao acessar a URL na tentativa %d/3: %s",
                    tentativa,
                    error,
                )
                if tentativa < 3 and monotonic() < deadline:
                    sleep(min(2, max(0, deadline - monotonic())))

        raise PlaywrightTimeoutError(
            f"Nao foi possivel acessar {url!r} dentro do timeout global de "
            f"{timeout}ms: {last_error}"
        )

    #função para selecionar parâmetros iniciais, como rotina e ambiente
    def selecionar_parametros_iniciais(self,cronometro) -> None:
        
        rotina = os.getenv("Setup_rotina") or "SIGAADV"
        ambiente = os.getenv("Setup_ambiente") or CONFIG.get("Environment", "bf")
        self._atualizar_acao(f"Aguardando tela inicial; rotina='{rotina}', ambiente='{ambiente}'")
        parametros_timeout = int(CONFIG.get("ParametrosTimeout", 60000))
        self._fill(
            ["wa-combobox#selectStartProg input", "wa-combobox#selectStartProg"],
            rotina,
            timeout=parametros_timeout,
        )
        self._fill(
            ["wa-combobox#selectEnv input", "wa-combobox#selectEnv"],
            ambiente,
            timeout=parametros_timeout,
        )

        botao_ok = self.page.locator("wa-dialog.startParameters wa-button button").last
        try:
            self._atualizar_acao("Parâmetros iniciais preenchidos; clicando em OK")
            botao_ok.click(timeout=90000)
            logger.info("Acessou o SIGAADV")
            cronometro.atualizar_acao("Acessou o SIGAADV")
        except PlaywrightTimeoutError:
            logger.info("Tela de parametros iniciais nao apareceu")
            cronometro.atualizar_acao("Tela de parametros iniciais nao apareceu")
        except Exception as e:
            logger.exception("Erro inesperado na seleção dos parâmetros iniciais: %s", e)
            cronometro.atualizar_acao("Erro inesperado na seleção dos parâmetros iniciais")

    #função para fechar a tela de tamanho, caso ela exista
    def fechar_tela_tamanho_se_existir(self,cronometro) -> None:
        try:
            self._atualizar_acao("Verificando se a tela de redimensionamento existe")
            botao_tamanho = self.page.locator("wa-button#COMP3012 button").first
            botao_tamanho.wait_for(state="visible", timeout=8000)
            botao_tamanho.click(timeout=5000)
        except PlaywrightTimeoutError:
            logger.info("Nao apareceu a tela de redimensionamento")
            cronometro.atualizar_acao("Nao apareceu a tela de redimensionamento")
        except Exception as e:
            logger.warning("Falha ao fechar a tela de tamanho: %s", e, exc_info=True)
            cronometro.atualizar_acao("Falha ao fechar a tela de tamanho")

    # função para instalar e iniciar o WebAgent, caso ele não esteja rodando
    def instalar_e_iniciar_webagent(self):
        """Instala e executa o WebAgent no SO caso ele ainda não esteja rodando."""
        caminho_deb = "/tmp/totvs-webagent.deb"

        # 1. Pega a URL do botão sem abrir o fluxo de download do navegador
        try:
            url_download = self.page.get_by_role("button", name="INSTALAR PARA LINUX").get_attribute("href")
        except:
            pass

        if url_download:
            # 2. Baixa e instala via terminal do Linux
            subprocess.run(["wget", "-O", caminho_deb, url_download], check=True)
            subprocess.run(["sudo", "dpkg", "-i", caminho_deb], check=True)

            # 3. Inicia o serviço do WebAgent em background
            subprocess.run(["sudo", "systemctl", "start", "totvs-webagent"], check=True)

            # 4. Recarrega a página para o Protheus reconhecer o agente
            self.page.reload()

    # função para realizar o login no Protheus, preenchendo usuário e senha
    def fazer_login(self,cronometro) -> None:
                
        username = CONFIG.get("User")
        password = CONFIG.get("Password")

        self.page.screenshot(path="tela_login.png", full_page=True)
        # Verifica se o WebAgent está bloqueando a tela
        if self.page.get_by_role("button", name="INSTALAR PARA LINUX").is_visible():
            logger.info("WebAgent não detectado. Instalando...")
            self.instalar_e_iniciar_webagent()
            with self.page.expect_download() as download_info:
                # 2. Clica no botão para disparar o download
                self.page.get_by_role("button", name="INSTALAR PARA LINUX").click()

                # 3. Captura o objeto de download concluído
                download = download_info.value

                # 4. Salva o instalador no disco do servidor Linux
                caminho_arquivo = "/tmp/totvs-webagent.deb"
                download.save_as(caminho_arquivo)
        sleep(2) 
        logger.info("Iniciando processo de login para o usuário: %s", username)
        cronometro.atualizar_acao("Iniciando processo de login para o usuário: {username}".format(username=username))
        webview = self.page.locator("wa-webview#COMP3010").first
        self._atualizar_acao("Aguardando webview de login 'wa-webview#COMP3010'")
        webview.wait_for(timeout=self.timeout)
        logger.info("Webview de login carregado")
        cronometro.atualizar_acao("Webview de login carregado")

        # Obtém o frame de conteúdo do iframe de login
        login_frame = webview.locator("iframe").content_frame
        if login_frame is None:
            err_msg = "Iframe de login nao encontrado"
            logger.error(err_msg)
            cronometro.atualizar_acao("Erro: Iframe de login nao encontrado")
            raise PlaywrightTimeoutError(err_msg)

        self._atualizar_acao("Iframe de login encontrado; preenchendo usuário")
        login_frame.locator('input[name="login"]').last.fill(username, timeout=100000)
        logger.info("Campo de usuário preenchido")
        cronometro.atualizar_acao("Campo de usuário preenchido")
        login_frame.locator('input[name="password"]').last.fill(password, timeout=100000)
        logger.info("Campo de senha preenchido")
        cronometro.atualizar_acao("Campo de senha preenchido")
        sleep(1)
        self._atualizar_acao("Usuário e senha preenchidos; clicando em 'Entrar'")
        self.mfa_solicitado_em = datetime.now(timezone.utc)
        login_frame.get_by_role("button", name="Entrar", exact=True).click(timeout=200000)
        logger.info("Botão 'Entrar' clicado no formulário de login")
        cronometro.atualizar_acao("Botão 'Entrar' clicado no formulário de login")
        logger.info("Credenciais preenchidas")
        cronometro.atualizar_acao("Credenciais preenchidas")

        try:
            self._esperar_e_clicar("Entrar", timeout=500000)
            logger.info("Botao Entrar clicado")
            cronometro.atualizar_acao("Botao Entrar clicado")
        except PlaywrightTimeoutError:
            logger.info("Botao Entrar de confirmacao nao apareceu")
            cronometro.atualizar_acao("Botao Entrar de confirmacao nao apareceu")
        except Exception as e:
            logger.exception("Erro ao confirmar botão 'Entrar': %s", e)
            cronometro.atualizar_acao("Erro ao confirmar botão 'Entrar'")

    # função para esperar e clicar em um elemento com base em texto ou seletor, com timeout e opção de duplo clique
    def _esperar_e_clicar(
        self,
        texto_ou_seletor: str,
        timeout: int = 90000,
        duplo_clique: bool = False,
        pressionar_escape_ao_falhar: bool = True,
    ) -> None:
        logger.info("Aguardando elemento ou seletor: '%s'...", texto_ou_seletor)
        acao = "duplo clique" if duplo_clique else "clique"
        self._atualizar_acao(f"Procurando '{texto_ou_seletor}' para {acao}; timeout {timeout}ms")
        deadline = monotonic() + (timeout / 1000)
        texto_normalizado = r"\s+".join(re.escape(parte) for parte in texto_ou_seletor.split())
        texto_regex = re.compile(
            rf"^\s*{texto_normalizado}(?:\s*\(\d+\))?\s*$",
            re.IGNORECASE,
        )

        # Função recursiva para buscar o elemento em todos os frames e subframes
        def buscar_em_frame(frame: Frame) -> Locator | None:
            # 0. Caso seja enviado um ID ou seletor CSS direto (ex: '#COMP6014' ou 'wa-button#COMP6014')
            if texto_ou_seletor.startswith("#") or texto_ou_seletor.startswith("wa-"):
                loc_css = frame.locator(texto_ou_seletor)
                if loc_css.count() > 0 and loc_css.first.is_visible():
                    return loc_css.first

            # 1. Tenta por roles
            roles = ["button", "menuitem", "treeitem", "tab", "link"]
            for role in roles:
                loc = frame.get_by_role(role, name=texto_regex, exact=True)
                if loc.count() > 0:
                    for i in range(loc.count()):
                        cand = loc.nth(i)
                        try:
                            if cand.is_visible():
                                return cand
                        except PlaywrightError:
                            continue

            # 2. Tenta por texto direto
            loc_text = frame.get_by_text(texto_regex, exact=True)
            if loc_text.count() > 0:
                for i in range(loc_text.count()):
                    cand = loc_text.nth(i)
                    try:
                        if cand.is_visible():
                            return cand
                    except PlaywrightError:
                        continue

            # Alguns menus do Protheus sao renderizados como div/span sem role.
            loc_text_flexivel = frame.locator("text=" + texto_ou_seletor).filter(
                has_text=texto_regex,
            )
            for indice in range(loc_text_flexivel.count()):
                cand = loc_text_flexivel.nth(indice)
                try:
                    if cand.is_visible():
                        return cand
                except PlaywrightError:
                    continue

            # 3. Busca em sub-frames
            for child_frame in frame.child_frames:
                encontrado = buscar_em_frame(child_frame)
                if encontrado:
                    return encontrado

            return None
        # Loop principal para aguardar e clicar no elemento encontrado
        while monotonic() < deadline:
            try:
                frames = list(self.page.frames)
                for frame in frames:
                    target = buscar_em_frame(frame)
                    if target:
                        # Calcula o tempo restante para o timeout
                        restante_ms = max(1, int((deadline - monotonic()) * 1000))
                        target.wait_for(state="visible", timeout=min(1000, restante_ms))
                        self._atualizar_acao(f"Elemento '{texto_ou_seletor}' encontrado; executando {acao}")
                        
                        if duplo_clique:
                            target.dblclick(timeout=min(3000, restante_ms))
                        else:
                            target.click(timeout=min(3000, restante_ms))
                            
                        logger.info("Clique realizado com sucesso no elemento: '%s'", texto_ou_seletor)
                        self._atualizar_acao(f"{acao.capitalize()} realizado em '{texto_ou_seletor}'")
                        return
            except (PlaywrightTimeoutError, PlaywrightError) as err:
                logger.debug("Tentando interagir com '%s'... (%s)", texto_ou_seletor, err)

            sleep(0.5)

        err_msg = f"Elemento '{texto_ou_seletor}' não foi encontrado dentro de {timeout}ms"
        if pressionar_escape_ao_falhar:
            self.page.keyboard.press("Escape")
        logger.error(
            "%s URL atual: %s; frames: %d",
            err_msg,
            self.page.url,
            len(self.page.frames),
        )
        try:
            # Salva um screenshot do estado atual da página para análise de falha
            screenshot_name = (
                f"timeout_{re.sub(r'[^A-Za-z0-9_-]+', '_', texto_ou_seletor)}_"
                f"{int(monotonic() * 1000)}.png"
            )
            self.page.screenshot(
                path=str(LogBanco.LOG_DIR / screenshot_name),
                full_page=True,
            )
        except PlaywrightError as screenshot_error:
            logger.warning("Nao foi possivel salvar screenshot do timeout: %s", screenshot_error)
        self._atualizar_acao(f"Falha: elemento '{texto_ou_seletor}' não encontrado após {timeout}ms")
        logger.error(err_msg)
        raise PlaywrightTimeoutError(err_msg)

    # função para preencher o campo de empresa/filial, caso a variável de ambiente esteja definida
    def preencher_empresa_filial(self,cronometro) -> bool:
        emp = os.getenv("Setup_emp")
        if not emp:
            logger.warning(
                "Setup_emp nao definido no ambiente do processo; "
                "pulando preenchimento de empresa/filial e clique em Entrar"
            )
            cronometro.atualizar_acao(
                "Setup_emp nao definido; tela de empresa/filial ignorada"
            )
            return True

        logger.info("Setup_emp definido; preenchendo empresa/filial e aguardando Entrar")

        try:
            menu_disponivel = self._primeiro_elemento_visivel(
                ["Compras"],
                timeout=1000,
            )
            if menu_disponivel == "Compras":
                logger.info(
                    "Menu já está disponível; tela de empresa/filial não é necessária"
                )
                cronometro.atualizar_acao(
                    "Menu já disponível; etapa de empresa/filial ignorada"
                )
                return True

            # Preenche o campo de empresa/filial com base em uma lista de seletores possíveis
            self._atualizar_acao(
                f"Procurando campo de empresa/filial para preencher '{emp}'"
            )
            self._fill(
                [
                    'input[id*="po-lookup"]',
                    'input[data-placeholder*="Grupo"]',
                    'input[placeholder*="Grupo"]',
                    'input[placeholder*="empresa" i]',
                    'input[aria-label*="empresa" i]',
                ],
                emp,
                timeout=int(CONFIG.get("EmpresaTimeout", 15000)),
            )
            filial = os.getenv("Setup_filial")
            if filial:
                campo_filial = self._first_visible(
                    [
                        'input[placeholder*="Filial" i]',
                        'input[aria-label*="Filial" i]',
                        'input[data-placeholder*="Filial" i]',
                    ],
                    timeout=3000,
                )
                if campo_filial is not None:
                    campo_filial.fill(filial, timeout=5000)
                    logger.info("Filial '%s' preenchida; aguardando validação do formulário", filial)
                else:
                    logger.info("Campo de filial não localizado; mantendo valor apresentado pelo Protheus")

            sleep(3)
            logger.info("Campo de empresa/filial preenchido; procurando botão Entrar")
            self._atualizar_acao("Campo de empresa/filial preenchido; procurando botão Entrar")
            try:
                # Aguarda a tela seguinte sem impor uma pausa fixa de 10 segundos.
                proxima_tela = self._primeiro_elemento_visivel(
                    ["Entrar", "Compras"],
                    timeout=int(CONFIG.get("EntrarTimeout", 15000)),
                )
                if proxima_tela == "Entrar":
                    self._esperar_e_clicar(
                        "Entrar",
                        timeout=5000,
                        pressionar_escape_ao_falhar=False,
                    )
                elif proxima_tela == "Compras":
                    logger.info("Menu disponível após preencher empresa/filial")
                    return True
                else:
                    raise PlaywrightTimeoutError(
                        "Nem 'Entrar' nem 'Compras' apareceram após preencher empresa/filial"
                    )
            except PlaywrightTimeoutError:
                logger.warning(
                    "Entrar não apareceu após preencher empresa/filial; verificando MFA antes de tentar novamente"
                )
                cronometro.atualizar_acao(
                    "Entrar não apareceu; verificando MFA antes da segunda tentativa"
                )
                campo_mfa = self._first_visible(
                    ["wa-text-input#COMP4506 input"],
                    timeout=3000,
                )
                if campo_mfa is None:
                    menu_disponivel = self._primeiro_elemento_visivel(
                        ["Compras"],
                        timeout=5000,
                    )
                    if menu_disponivel == "Compras":
                        logger.info(
                            "Menu já está disponível após a falha em Entrar; MFA não foi necessário"
                        )
                        cronometro.atualizar_acao(
                            "Menu disponível após falha em Entrar"
                        )
                        return True
                    else:
                        logger.error("Entrar falhou e o MFA também não apareceu")
                        cronometro.atualizar_acao(
                            "Entrar falhou e o MFA não apareceu"
                        )
                        return False

                mfa_confirmado = self.confirmar_mfa_se_existir(
                    cronometro,
                    "depois de preencher grupo/filial",
                    wait_ms=3000,
                )
                if not mfa_confirmado:
                    logger.error("MFA apareceu, mas não foi confirmado")
                    cronometro.atualizar_acao(
                        "MFA apareceu após falha em Entrar, mas não foi confirmado"
                    )
                    return False

                logger.info("MFA confirmado; tentando Entrar novamente")
                self._atualizar_acao("MFA confirmado; tentando Entrar novamente")
                proxima_tela = self._primeiro_elemento_visivel(
                    ["Entrar", "Compras"],
                    timeout=30000,
                )
                if proxima_tela == "Entrar":
                    self._esperar_e_clicar("Entrar", timeout=30000)
                elif proxima_tela == "Compras":
                    logger.info(
                        "MFA confirmou a sessão e o menu já está disponível; novo Entrar não é necessário"
                    )
                    self._atualizar_acao(
                        "MFA confirmado; menu disponível, seguindo para a rotina"
                    )
                else:
                    logger.error("Após confirmar MFA, não apareceu Entrar nem Compras")
                    cronometro.atualizar_acao(
                        "Após confirmar MFA, nenhuma tela de continuação apareceu"
                    )
                    return False

            logger.info("Empresa preenchida")
            cronometro.atualizar_acao("Empresa preenchida")
            return True
        except PlaywrightTimeoutError:
            logger.exception(
                "Timeout na etapa de empresa/filial; o botão Entrar não será tentado "
                "porque o campo ou o próprio botão não foi localizado"
            )
            cronometro.atualizar_acao(
                "Timeout na etapa de empresa/filial; campo ou botão Entrar não localizado"
            )
            return False
        except Exception as e:
            logger.exception("Erro durante o preenchimento da empresa/filial: %s", e)
            cronometro.atualizar_acao("Erro durante o preenchimento da empresa/filial")
            return False

    # função para confirmar o MFA, caso o campo apareça na tela
    def confirmar_mfa(self,cronometro, wait_ms: int = 9000) -> bool:
        try:
            self._atualizar_acao(f"Verificando campo MFA 'COMP4506'; aguardando até {wait_ms}ms")
            campo_mfa = self._first_visible(
                ["wa-text-input#COMP4506 input"],
                timeout=wait_ms,
            )
            if campo_mfa is None:
                raise PlaywrightTimeoutError("Campo MFA 'COMP4506' não ficou visível")
        except PlaywrightTimeoutError:
            cronometro.atualizar_acao("Campo MFA nao apareceu")
            return False

        try:
            codigo = self._obter_codigo_mfa()
            if not codigo:
                logger.warning("Codigo de MFA retornado está vazio ou inválido")
                cronometro.atualizar_acao("Codigo de MFA retornado está vazio ou inválido")
                return False

            self._atualizar_acao("Código MFA encontrado; preenchendo campo de autenticação")
            campo_mfa.fill(codigo, timeout=9000)
            self._atualizar_acao("Código MFA preenchido; clicando em confirmar")
            botao_mfa = self._first_visible(
                ["wa-button#COMP4507 button"],
                timeout=9000,
            )
            if botao_mfa is None:
                raise PlaywrightTimeoutError("Botão de confirmação do MFA 'COMP4507' não ficou visível")
            botao_mfa.click(timeout=9000)
            self.mfa_confirmado = True
            logger.info("MFA confirmado com sucesso")
            cronometro.atualizar_acao("MFA confirmado com sucesso")
            return True
        except Exception as error:
            logger.exception("Nao foi possivel confirmar MFA devido a um erro inesperado: %s", error)
            cronometro.atualizar_acao("Nao foi possivel confirmar MFA devido a um erro inesperado")
            return False

    # busca (com novas consultas) o código MFA recebido depois do clique em 'Entrar' desta sessão
    def _obter_codigo_mfa(self) -> str | None:
        limite = monotonic() + int(CONFIG.get("MfaTimeout", 60000)) / 1000
        cliente = graph.MicrosoftGraphClient()
        while monotonic() < limite:
            codigo = cliente.obter_ultimo_codigo_acesso(recebido_apos=self.mfa_solicitado_em)
            if codigo:
                return str(codigo).strip()
            self._atualizar_acao("Aguardando e-mail com o código MFA desta sessão")
            sleep(3)
        return None

    # função para confirmar o MFA, caso ele apareça na tela, e registrar a ação no cronometro
    def confirmar_mfa_se_existir(self,cronometro, etapa: str, wait_ms: int = 12000) -> bool:
        confirmado = self.confirmar_mfa(cronometro, wait_ms=wait_ms)
        if confirmado:
            logger.info("MFA confirmado %s", etapa)
            cronometro.atualizar_acao("MFA confirmado %s" % etapa)
        else:
            logger.info("MFA nao apareceu %s", etapa)
            cronometro.atualizar_acao("MFA nao apareceu %s" % etapa)
        return confirmado

    # executa as etapas de UMA rotina, na ordem informada no caminho
    def executar_rotina(self, cronometro, rotina: Rotina, indice_rotina: int, total_rotinas: int) -> None:
        etapas_fluxo = list(rotina.etapas)
        menu_timeout = int(CONFIG.get("MenuTimeout", 60000))
        rotina_timeout = int(CONFIG.get("RotinaTimeout", 90000))

        for indice, etapa in enumerate(etapas_fluxo, start=1):
            # A primeira etapa (menu) usa MenuTimeout; as demais, RotinaTimeout.
            timeout = rotina_timeout if indice > 1 else menu_timeout
            prefixo = f"Rotina {indice_rotina}/{total_rotinas} - Etapa {indice}/{len(etapas_fluxo)}"
            logger.info("%s do fluxo: '%s'", prefixo, etapa)
            cronometro.atualizar_acao(f"{prefixo}: procurando '{etapa}'")

            if etapa.casefold() in rotinas_cfg.ETAPAS_SAIDA:
                # 'sair'/'fechar' = botão de fechar a tela da rotina
                self._esperar_e_clicar("wa-button#COMP6014 button", timeout=timeout)
            else:
                self._esperar_e_clicar(etapa, timeout=timeout)

            nome_etapa = re.sub(r"[^A-Za-z0-9_-]+", "_", etapa).strip("_") or f"etapa_{indice}"
            self.page.screenshot(
                path=str(LogBanco.LOG_DIR / f"fluxo_r{indice_rotina:02d}_{indice:02d}_{nome_etapa}.png"),
                full_page=True,
            )
            cronometro.atualizar_acao(f"{prefixo} concluída: '{etapa}'")
            sleep(1)

    # função auxiliar para garantir que a tela de pedidos esteja pronta antes de prosseguir
    def _acessar_menu_rotina(self, etapas_menu: list[str], menu_timeout: int) -> None:
        """Tenta abrir novamente cada etapa do menu antes de desistir da rotina."""
        tentativas_menu = int(CONFIG.get("MenuRetries", 3))
        transicao_timeout = min(
            menu_timeout,
            int(CONFIG.get("MenuStepTimeout", 15000)),
        )
        clique_timeout = transicao_timeout
        ultimo_erro: Exception | None = None

        for tentativa in range(1, tentativas_menu + 1):
            indice_retorno = 0
            try:
                logger.info(
                    "Tentativa %d de %d para acessar o menu: %s",
                    tentativa,
                    tentativas_menu,
                    " > ".join(etapas_menu),
                )
                self._atualizar_acao(
                    f"Tentativa {tentativa} de {tentativas_menu}: acessando menu da rotina"
                )
                for indice, etapa in enumerate(etapas_menu):
                    try:
                        self._esperar_e_clicar(etapa, timeout=clique_timeout)
                    except (PlaywrightTimeoutError, PlaywrightError):
                        indice_retorno = max(0, indice - 1)
                        raise
                    if indice < len(etapas_menu) - 1:
                        try:
                            self._aguardar_item_visivel(
                                etapas_menu[indice + 1],
                                timeout=transicao_timeout,
                            )
                        except (PlaywrightTimeoutError, PlaywrightError):
                            indice_retorno = indice
                            raise
                logger.info("Menu da rotina acessado com sucesso na tentativa %d", tentativa)
                return
            except (PlaywrightTimeoutError, PlaywrightError) as error:
                ultimo_erro = error
                logger.warning(
                    "Falha ao acessar o menu na tentativa %d; iniciando recuperação reversa: %s",
                    tentativa,
                    error,
                )

                screenshot_path = LogBanco.LOG_DIR / (
                    f"menu_inicio_{int(monotonic() * 1000)}.png"
                )
                self.page.screenshot(path=str(screenshot_path), full_page=True)
                self._atualizar_acao(
                    "Falha no menu; tentando novamente Movimento e Pedidos de Compra"
                )

                recuperado = False
                for indice_base in range(indice_retorno, -1, -1):
                    etapa_base = etapas_menu[indice_base]
                    try:
                        logger.info(
                            "Verificando predecessor do menu: '%s'",
                            etapa_base,
                        )
                        self._aguardar_item_visivel(
                            etapa_base,
                            timeout=min(menu_timeout, 3000),
                        )
                        logger.info(
                            "Predecessor '%s' visível; clicando novamente",
                            etapa_base,
                        )
                        self._esperar_e_clicar(
                            etapa_base,
                            timeout=clique_timeout,
                        )

                        for indice_retomada in range(indice_base + 1, len(etapas_menu)):
                            self._aguardar_item_visivel(
                                etapas_menu[indice_retomada],
                                timeout=transicao_timeout,
                            )
                            self._esperar_e_clicar(
                                etapas_menu[indice_retomada],
                                timeout=transicao_timeout,
                            )

                        logger.info(
                            "Menu recuperado a partir do predecessor '%s'",
                            etapa_base,
                        )
                        recuperado = True
                        break
                    except (PlaywrightTimeoutError, PlaywrightError) as recovery_error:
                        ultimo_erro = recovery_error
                        logger.warning(
                            "Predecessor '%s' não está disponível; tentando o anterior",
                            etapa_base,
                        )

                if recuperado:
                    return

                if tentativa == tentativas_menu:
                    break

        raise PlaywrightError(
            f"Nao foi possivel acessar o menu da rotina apos {tentativas_menu} tentativas: {ultimo_erro}"
        )
        

    # função auxiliar para recuperar a tela da rotina sem recarregar a aplicação Protheus
    def _recuperar_tela_rotina(self) -> None:
        """Prepara uma nova tentativa sem recarregar a aplicação Protheus."""
        self.page.keyboard.press("Escape")
        sleep(2)
        logger.info(
            "Tela da rotina mantida; próxima tentativa voltará por 'Visualizar' sem reload"
        )

    # função auxiliar para aguardar que o próximo item do menu esteja visível antes de clicar nele
    def _aguardar_item_visivel(self, texto: str, timeout: int) -> None:
        """Confirma que o proximo item do menu foi renderizado antes de clicar nele."""
        self._atualizar_acao(
            f"Aguardando próximo item do menu '{texto}' após a expansão anterior"
        )
        deadline = monotonic() + timeout / 1000
        texto_normalizado = r"\s+".join(re.escape(parte) for parte in texto.split())
        texto_regex = re.compile(
            rf"^\s*{texto_normalizado}(?:\s*\(\d+\))?\s*$",
            re.IGNORECASE,
        )

        while monotonic() < deadline:
            for frame in list(self.page.frames):
                for role in ("menuitem", "treeitem", "link", "button"):
                    candidatos = frame.get_by_role(role, name=texto_regex, exact=True)
                    for indice in range(candidatos.count()):
                        try:
                            if candidatos.nth(indice).is_visible(timeout=300):
                                logger.info("Próximo item de menu renderizado: '%s'", texto)
                                self._atualizar_acao(
                                    f"Próximo item do menu '{texto}' renderizado"
                                )
                                return
                        except (PlaywrightTimeoutError, PlaywrightError):
                            continue

                candidatos = frame.get_by_text(texto_regex, exact=True)
                for indice in range(candidatos.count()):
                    try:
                        if candidatos.nth(indice).is_visible(timeout=300):
                            logger.info("Próximo item de menu renderizado: '%s'", texto)
                            self._atualizar_acao(
                                f"Próximo item do menu '{texto}' renderizado"
                            )
                            return
                    except (PlaywrightTimeoutError, PlaywrightError):
                        continue
            sleep(0.5)

        raise PlaywrightTimeoutError(
            f"O item seguinte do menu '{texto}' não foi renderizado após o clique anterior"
        )

    # função auxiliar para registrar falhas na rotina, salvando um screenshot do estado atual da página
    def _registrar_falha_rotina(self, etapa: str, tentativa: int) -> None:
        """Registra o estado da tela sem executar cliques duplicados."""
        logger.error(
            "Etapa '%s' não apareceu na tentativa %d; URL atual: %s; frames: %d",
            etapa,
            tentativa,
            self.page.url,
            len(self.page.frames),
        )
        try:
            nome = re.sub(r"[^A-Za-z0-9_-]+", "_", etapa)
            self.page.screenshot(
                path=str(LogBanco.LOG_DIR / f"falha_rotina_{nome}_{tentativa}_{int(monotonic() * 1000)}.png"),
                full_page=True,
            )
        except PlaywrightError as error:
            logger.warning("Nao foi possivel salvar estado da falha da rotina: %s", error)

    # função auxiliar para registrar o estado inicial do menu, salvando um screenshot e os textos dos frames
    def _registrar_estado_menu(self) -> None:
        """Persiste o estado inicial do menu para comparar Docker e execução local."""
        try:
            LogBanco.LOG_DIR.mkdir(parents=True, exist_ok=True)
            screenshot_path = LogBanco.LOG_DIR / (
                f"menu_inicio_{int(monotonic() * 1000)}.png"
            )
            self.page.screenshot(path=str(screenshot_path), full_page=True)
            textos = []
            for frame in self.page.frames:
                try:
                    texto = " ".join(frame.locator("body").inner_text(timeout=1000).split())
                    if texto:
                        textos.append(texto[:1000])
                except PlaywrightError:
                    continue
            logger.info(
                "Estado do menu: URL=%s viewport=%s frames=%d textos=%s screenshot=%s",
                self.page.url,
                self.page.viewport_size,
                len(self.page.frames),
                textos,
                screenshot_path,
            )
        except (OSError, PlaywrightError) as error:
            logger.warning("Nao foi possivel registrar estado inicial do menu: %s", error)

    # função auxiliar para garantir que a tela de pedidos esteja pronta antes de prosseguir com a ação de "Visualizar"
    def _garantir_tela_pedidos(
        self,
        etapas_menu: list[str],
        menu_timeout: int,
        rotina_timeout: int,
    ) -> None:
        """Confirma que Visualizar existe antes de iniciar a próxima ação."""
        tentativas = int(CONFIG.get("MenuRetries", 3))
        ultimo_erro: Exception | None = None

        for tentativa in range(1, tentativas + 1):
            try:
                logger.info(
                    "Verificando tela de pedidos antes de Visualizar; tentativa %d de %d",
                    tentativa,
                    tentativas,
                )
                self._aguardar_item_visivel("Visualizar", timeout=rotina_timeout)
                logger.info("Tela de pedidos pronta; elemento 'Visualizar' está disponível")
                return
            except PlaywrightTimeoutError as error:
                ultimo_erro = error
                if tentativa == tentativas:
                    break

                logger.warning(
                    "Tela de pedidos não ficou disponível; reabrindo o menu da rotina"
                )
                self._atualizar_acao(
                    "Visualizar não apareceu; reabrindo Pedidos de Compra"
                )
                self.page.keyboard.press("Escape")
                sleep(2)
                self._acessar_menu_rotina(etapas_menu, menu_timeout)

        raise PlaywrightTimeoutError(
            f"A tela de pedidos não ficou disponível após {tentativas} tentativas: {ultimo_erro}"
        )
    
#########################################################################################################
#
# Executa UMA rotina em uma sessão própria: abre navegador, login, MFA, empresa/filial,
# caminho da rotina e fecha o navegador. Cada thread chama esta função.
#
#########################################################################################################
def executar_rotina_isolada(
    rotina: Rotina,
    indice_rotina: int,
    total_rotinas: int,
    headless_override: bool | None = None,
    _id_execucao: int | None = None,
    _login_retry_attempted: bool = False,
    _execution_retry_count: int = 0,
) -> ResultadoRotina:
    # Cada rotina tem seu próprio ID no RPA.TIMER; as novas tentativas reaproveitam o mesmo ID.
    id_execucao = _id_execucao or LogBanco.nova_execucao_id()
    logger.info(
        "Rotina %d/%d '%s' iniciando (id_execucao=%s, versao=%s)",
        indice_rotina, total_rotinas, rotina.nome, id_execucao, BUILD_VERSION,
    )
    cronometro = LogBanco.CronometroExecucao(
        acao_inicial=f"INICIANDO ROTINA {rotina.nome}",
        id_exec=id_execucao,
    )
    cronometro.iniciar()
    status_final = "NAO"
    descricao_final = "FINALIZANDO EXECUCAO"
    login_retry_requested = False
    inicio_rotina: float | None = None

    try:
        # Uma instância do Playwright por thread (a API sync não pode ser compartilhada entre threads).
        with sync_playwright() as playwright:

            firefox_prefs = {
                "network.protocol-handler.external.web-agent": True,
                "network.protocol-handler.expose.web-agent": False,
                "network.protocol-handler.warn-external.web-agent": False,
            }
            browser_type = getattr(playwright, CONFIG.get("Browser", "chromium").lower(), playwright.chromium)

            headless = headless_override
            if headless is None:
                headless = _parse_bool(CONFIG.get("Headless", True), default=True)

            browser: Browser | None = None
            try:
                cronometro.atualizar_acao("Abrindo navegador")
                browser = browser_type.launch(
                    headless=headless,
                    firefox_user_prefs=firefox_prefs,
                )
                context: BrowserContext = browser.new_context(
                    viewport={"width": 1920, "height": 1080},
                    screen={"width": 1920, "height": 1080},
                )
                page = context.new_page()
                flow = ProtheusPlaywrightMainFlow(page, cronometro)

                cronometro.atualizar_acao("Acessando URL do Protheus")
                try:
                    flow.abrir()
                except PlaywrightTimeoutError as error:
                    logger.error("Falha de conexao ou timeout ao acessar %s: %s", CONFIG.get("Url"), error, exc_info=True)
                    cronometro.atualizar_acao("Falha de conexao ou timeout ao acessar URL do Protheus; enviando email de base offline")
                    try:
                        enviar_erro_base_selenium()
                    except Exception as email_err:
                        logger.exception("Falha adicional ao tentar enviar o e-mail de base offline: %s", email_err)
                        cronometro.atualizar_acao("Falha adicional ao tentar enviar o e-mail de base offline")

                    status_final = "ERRO"
                    descricao_final = "ERRO AO ACESSAR URL DO PROTHEUS"
                    return ResultadoRotina(rotina.nome, rotina.caminho, 0.0, False, descricao_final)

                cronometro.atualizar_acao("Iniciando funcao para parâmetros iniciais...")
                flow.selecionar_parametros_iniciais(cronometro)

                cronometro.atualizar_acao("Iniciando funcao para fechar tela de tamanho se existir...")
                flow.fechar_tela_tamanho_se_existir(cronometro)

                # ---- Trecho em fila entre as threads: login -> MFA -> empresa/filial -> MFA ----
                cronometro.atualizar_acao("Aguardando vez para login/MFA (outras rotinas podem estar logando)")
                with MFA_LOCK:
                    cronometro.atualizar_acao("iniciando funcao de Login...")
                    flow.fazer_login(cronometro)
                    logger.info("Login realizado com sucesso")
                    cronometro.atualizar_acao("Login realizado com sucesso")

                    cronometro.atualizar_acao("Iniciando validacao de MFA antes de preencher grupo/filial...")
                    mfa_obtido = flow.confirmar_mfa_se_existir(cronometro, "antes de preencher grupo/filial")

                    cronometro.atualizar_acao("Iniciando funcao para preencher empresa/filial...")
                    empresa_filial_ok = flow.preencher_empresa_filial(cronometro)
                    if not empresa_filial_ok:
                        raise PlaywrightError(
                            "A etapa de empresa/filial não foi concluída; menu da rotina não será acessado"
                        )

                    mfa_obtido = mfa_obtido or flow.mfa_confirmado
                    if not mfa_obtido:
                        cronometro.atualizar_acao("Iniciando funcao de verificar MFA depois de preencher grupo/filial...")
                        flow.confirmar_mfa_se_existir(
                            cronometro,
                            "depois de preencher grupo/filial",
                            wait_ms=5000,
                        )
                # ---- fim do trecho em fila ----

                cronometro.atualizar_acao(f"Executando rotina: {rotina.caminho}")
                flow._registrar_estado_menu()
                # O tempo da rotina mede apenas os cliques do caminho (sem login).
                inicio_rotina = monotonic()
                flow.executar_rotina(cronometro, rotina, indice_rotina, total_rotinas)
                segundos = monotonic() - inicio_rotina
                cronometro.registrar_rotina(rotina.nome, segundos, True)

                logger.info("Rotina '%s' concluída em %.1fs", rotina.nome, segundos)
                cronometro.atualizar_acao("Execução da rotina concluida")
                return ResultadoRotina(rotina.nome, rotina.caminho, round(segundos, 2), True)
            except Exception as e:
                login_retry_requested = (
                    cronometro.obter_acao()
                    == "Iframe de login encontrado; preenchendo usuário"
                )
                status_final = "SIM"
                erro_resumido = " ".join(str(e).split())
                descricao_final = f"ERRO DURANTE EXECUCAO PLAYWRIGHT: {erro_resumido}"
                logger.exception("Exceção na rotina '%s': %s", rotina.nome, e)
                if inicio_rotina is not None:
                    cronometro.registrar_rotina(rotina.nome, monotonic() - inicio_rotina, False)
                cronometro.marcar_erro()
                cronometro.atualizar_acao(f"Exceção não tratada na rotina: {erro_resumido}")
                raise
            finally:
                if browser is not None:
                    browser.close()
                total = cronometro.finalizar(status_final=status_final, descricao_final=descricao_final)
                logger.info("Cronometro da rotina '%s' finalizado em %ss", rotina.nome, total)
                if status_final == "NAO":
                    try:
                        media_resultado = LogBanco.resultado(id_execucao)
                        limite_alerta = media_resultado * 1.3
                        if media_resultado > 0 and total > limite_alerta:
                            logger.warning(
                                "Tempo de execução acima da média: %ss; média %.2fs; limite %.2fs",
                                total,
                                media_resultado,
                                limite_alerta,
                            )
                            enviar_temp_exc(total, media_resultado)
                    except Exception:
                        logger.exception("Não foi possível calcular a média ou enviar o alerta de tempo")
    except Exception as global_err:
        logger.exception("Erro na rotina '%s': %s", rotina.nome, global_err)
        if login_retry_requested and not _login_retry_attempted:
            logger.warning("Rotina '%s' interrompida no login; reiniciando uma única vez", rotina.nome)
            return executar_rotina_isolada(
                rotina, indice_rotina, total_rotinas,
                headless_override=headless_override,
                _id_execucao=id_execucao,
                _login_retry_attempted=True,
            )
        execution_retries = int(CONFIG.get("ExecutionRetries", 2))
        if isinstance(global_err, PlaywrightError) and _execution_retry_count < execution_retries:
            proxima_tentativa = _execution_retry_count + 1
            logger.warning(
                "Falha Playwright na rotina '%s'; nova sessão (tentativa %d de %d)",
                rotina.nome, proxima_tentativa, execution_retries,
            )
            return executar_rotina_isolada(
                rotina, indice_rotina, total_rotinas,
                headless_override=headless_override,
                _id_execucao=id_execucao,
                _execution_retry_count=proxima_tentativa,
            )
        enviar_erro_execucao(global_err, cronometro.obter_acao())
        erro_resumido = " ".join(str(global_err).split())[:300]
        return ResultadoRotina(rotina.nome, rotina.caminho, 0.0, False, erro_resumido)


# ponto de entrada de cada thread: abre o log exclusivo da rotina e executa a rotina
def _executar_rotina_com_log(
    rotina: Rotina,
    indice_rotina: int,
    total_rotinas: int,
    headless_override: bool | None = None,
) -> ResultadoRotina:
    # O ID é gerado aqui para já aparecer no nome do arquivo de log;
    # as novas tentativas rodam na mesma thread e caem no mesmo arquivo.
    id_execucao = LogBanco.nova_execucao_id()
    handler = logging_config.iniciar_log_da_rotina(indice_rotina, id_execucao, rotina.nome)
    logger.info("Log da rotina '%s' em %s", rotina.nome, handler.baseFilename)
    try:
        return executar_rotina_isolada(
            rotina, indice_rotina, total_rotinas,
            headless_override=headless_override,
            _id_execucao=id_execucao,
        )
    finally:
        logging_config.encerrar_log_da_rotina(handler)


#########################################################################################################
#
# Função principal: distribui as rotinas em threads e consolida os tempos no final.
#
#########################################################################################################
def exec_playwright_from_main(
    headless_override: bool | None = None,
    fluxos_cli: list[str] | None = None,
) -> bool:
    # Valida as rotinas ANTES de abrir qualquer navegador (falha rápida se a config estiver errada).
    lista_rotinas = rotinas_cfg.carregar_rotinas(CONFIG, fluxos_cli)
    total = len(lista_rotinas)
    # Limita quantas sessões ficam abertas ao mesmo tempo (memória, licenças e WebAgent).
    max_threads = max(1, min(int(CONFIG.get("MaxThreads", 3)), total))
    logger.info("Rotinas a cronometrar (%d, até %d em paralelo): %s",
                total, max_threads, [r.nome for r in lista_rotinas])

    # Mantém os resultados na mesma ordem do config, mesmo terminando fora de ordem.
    resultados: list[ResultadoRotina | None] = [None] * total
    with ThreadPoolExecutor(max_workers=max_threads, thread_name_prefix="rotina") as executor:
        futuros = {
            executor.submit(_executar_rotina_com_log, rotina, indice, total, headless_override): indice - 1
            for indice, rotina in enumerate(lista_rotinas, start=1)
        }
        for futuro in as_completed(futuros):
            posicao = futuros[futuro]
            try:
                resultados[posicao] = futuro.result()
            except Exception as erro:  # proteção: erro inesperado que escapou da thread
                rotina = lista_rotinas[posicao]
                logger.exception("Erro inesperado na thread da rotina '%s': %s", rotina.nome, erro)
                resultados[posicao] = ResultadoRotina(
                    rotina.nome, rotina.caminho, 0.0, False, " ".join(str(erro).split())[:300]
                )

    # Entrega dos tempos: log + JSON. O log vai para o banco UMA vez, depois de todas as threads.
    logger.info(rotinas_cfg.formatar_resumo(resultados))
    relatorio = rotinas_cfg.salvar_relatorio(resultados, LogBanco.LOG_DIR, LogBanco.id_execucao)
    logger.info("Relatório de tempos salvo em %s", relatorio)
    LogBanco.salvar()
    return all(r.sucesso for r in resultados)

if __name__ == "__main__":
    try:
        args = _build_args()
        override = None
        if args.headed:
            override = False
        elif args.headless:
            override = True

        exec_playwright_from_main(headless_override=override, fluxos_cli=args.fluxo)
    except Exception as e:
        logger.critical("Ocorreu um erro fatal que interrompeu o script: %s", e, exc_info=True)
        sys.exit(1)