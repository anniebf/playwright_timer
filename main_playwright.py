import argparse
import json
import os
from socket import timeout
import subprocess
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

from MandaEmail import enviar_erro_base_selenium
import graph
import LogBanco
import logging_config

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
MENU_LATERAL = "Compras > Movimento > Pedidos de Compra"

logger = logging_config.get_logger("main_playwright_from_main")

# Carrega o arquivo JSON com log de falha caso ocorra erro na leitura
try:
    with CONFIG_PATH.open("r", encoding="utf-8") as config_file:
        CONFIG = json.load(config_file)
except Exception as e:
    logger.exception("Erro crítico ao carregar arquivo de configuração (%s): %s", CONFIG_PATH, e)
    sys.exit(1)

load_dotenv(BASE_DIR / ".env")


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


def _build_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fluxo Playwright equivalente ao main.py")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--headless", action="store_true", help="Executa sem abrir a janela do navegador")
    mode.add_argument("--headed", action="store_true", help="Executa com a janela do navegador visivel")
    return parser.parse_args()


class ProtheusPlaywrightMainFlow:
    def __init__(self, page: Page):
        self.page = page
        self.timeout = int(CONFIG.get("TimeOut", 30)) * 1000

    def _frames(self) -> list[Frame]:
        return self.page.frames

    def _first_visible(self, selectors: list[str], timeout: int = 1000) -> Locator | None:
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

    def _click_text(self, text: str, timeout: int = 10000, exact: bool = True) -> None:
        deadline = monotonic() + timeout / 1000
        while monotonic() < deadline:
            for frame in self._frames():
                candidates = [
                    frame.get_by_role("button", name=text, exact=exact),
                    frame.get_by_role("menuitem", name=text, exact=exact),
                    frame.get_by_role("treeitem", name=text, exact=exact),
                    frame.get_by_text(text, exact=exact),
                ]
                for candidate in candidates:
                    try:
                        element = candidate.first
                        while element.is_visible(timeout=300):
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

    def _fill(self, selectors: list[str], value: str, timeout: int = 10000) -> None:
        deadline = monotonic() + timeout / 1000
        last_error: Exception | None = None

        while monotonic() < deadline:
            locator = self._first_visible(selectors, timeout=500)
            if locator is not None:
                try:
                    locator.fill(value, timeout=1500)
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

    def abrir(self) -> None:
        url = CONFIG.get("Url")
        if not url:
            logger.error("A chave 'Url' não está definida na configuração.")
            raise ValueError("URL não informada nas configurações.")
            
        logger.info("Iniciando automacao para URL: %s", url)
        self.page.goto(url, wait_until="domcontentloaded", timeout=self.timeout * 2)

    def selecionar_parametros_iniciais(self,cronometro) -> None:
        
        rotina = os.getenv("Setup_rotina") or "SIGAADV"
        ambiente = os.getenv("Setup_ambiente") or CONFIG.get("Environment", "bf")
        sleep(5)
        self._fill(["wa-combobox#selectStartProg input", "wa-combobox#selectStartProg"], rotina)
        self._fill(["wa-combobox#selectEnv input", "wa-combobox#selectEnv"], ambiente)

        botao_ok = self.page.locator("wa-dialog.startParameters wa-button button").last
        try:
            botao_ok.click(timeout=50000)
            logger.info("Acessou o SIGAADV")
            cronometro.atualizar_acao("Acessou o SIGAADV")
        except PlaywrightTimeoutError:
            logger.info("Tela de parametros iniciais nao apareceu")
            cronometro.atualizar_acao("Tela de parametros iniciais nao apareceu")
        except Exception as e:
            logger.exception("Erro inesperado na seleção dos parâmetros iniciais: %s", e)
            cronometro.atualizar_acao("Erro inesperado na seleção dos parâmetros iniciais")

    def fechar_tela_tamanho_se_existir(self,cronometro) -> None:
        try:
            botao_tamanho = self.page.locator("wa-button#COMP3012 button").first
            botao_tamanho.wait_for(state="visible", timeout=8000)
            botao_tamanho.click(timeout=5000)
        except PlaywrightTimeoutError:
            logger.info("Nao apareceu a tela de redimensionamento")
            cronometro.atualizar_acao("Nao apareceu a tela de redimensionamento")
        except Exception as e:
            logger.warning("Falha ao fechar a tela de tamanho: %s", e, exc_info=True)
            cronometro.atualizar_acao("Falha ao fechar a tela de tamanho")
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
        webview.wait_for(timeout=self.timeout)
        logger.info("Webview de login carregado")
        cronometro.atualizar_acao("Webview de login carregado")

        login_frame = webview.locator("iframe").content_frame
        if login_frame is None:
            err_msg = "Iframe de login nao encontrado"
            logger.error(err_msg)
            cronometro.atualizar_acao("Erro: Iframe de login nao encontrado")
            raise PlaywrightTimeoutError(err_msg)

        login_frame.locator('input[name="login"]').last.fill(username, timeout=200000)
        logger.info("Campo de usuário preenchido")
        cronometro.atualizar_acao("Campo de usuário preenchido")
        login_frame.locator('input[name="password"]').last.fill(password, timeout=200000)
        logger.info("Campo de senha preenchido")
        cronometro.atualizar_acao("Campo de senha preenchido")
        sleep(1)
        login_frame.get_by_role("button", name="Entrar", exact=True).click(timeout=200000)
        logger.info("Botão 'Entrar' clicado no formulário de login")
        cronometro.atualizar_acao("Botão 'Entrar' clicado no formulário de login")
        logger.info("Credenciais preenchidas")
        cronometro.atualizar_acao("Credenciais preenchidas")

        try:
            self._esperar_e_clicar("Entrar", timeout=200000)
            logger.info("Botao Entrar clicado")
            cronometro.atualizar_acao("Botao Entrar clicado")
        except PlaywrightTimeoutError:
            logger.info("Botao Entrar de confirmacao nao apareceu")
            cronometro.atualizar_acao("Botao Entrar de confirmacao nao apareceu")
        except Exception as e:
            logger.exception("Erro ao confirmar botão 'Entrar': %s", e)
            cronometro.atualizar_acao("Erro ao confirmar botão 'Entrar'")

    def _esperar_e_clicar(self,cronometro, texto_ou_seletor: str, timeout: int = 60000, duplo_clique: bool = False) -> None:
        logger.info("Aguardando elemento ou seletor: '%s'...", texto_ou_seletor)
        cronometro.atualizar_acao("Aguardando elemento ou seletor: '%s'..." % texto_ou_seletor)
        deadline = monotonic() + (timeout / 1000)

        def buscar_em_frame(frame: Frame) -> Locator | None:
            # 0. Caso seja enviado um ID ou seletor CSS direto (ex: '#COMP6014' ou 'wa-button#COMP6014')
            if texto_ou_seletor.startswith("#") or texto_ou_seletor.startswith("wa-"):
                loc_css = frame.locator(texto_ou_seletor)
                if loc_css.count() > 0 and loc_css.first.is_visible():
                    return loc_css.first

            # 1. Tenta por roles
            roles = ["button", "menuitem", "treeitem", "tab", "link"]
            for role in roles:
                loc = frame.get_by_role(role, name=texto_ou_seletor, exact=False)
                if loc.count() > 0:
                    for i in range(loc.count()):
                        cand = loc.nth(i)
                        if cand.is_visible():
                            return cand

            # 2. Tenta por texto direto
            loc_text = frame.get_by_text(texto_ou_seletor, exact=False)
            if loc_text.count() > 0:
                for i in range(loc_text.count()):
                    cand = loc_text.nth(i)
                    if cand.is_visible():
                        return cand

            # 3. Busca em sub-frames
            for child_frame in frame.child_frames:
                encontrado = buscar_em_frame(child_frame)
                if encontrado:
                    return encontrado

            return None

        while monotonic() < deadline:
            try:
                for frame in self.page.frames:
                    target = buscar_em_frame(frame)
                    if target:
                        target.wait_for(state="visible", timeout=2000)
                        
                        if duplo_clique:
                            target.dblclick(force=True, timeout=3000)
                        else:
                            target.click(force=True, timeout=3000)
                            
                        logger.info("Clique realizado com sucesso no elemento: '%s'", texto_ou_seletor)
                        return
            except (PlaywrightTimeoutError, PlaywrightError) as err:
                logger.debug("Tentando interagir com '%s'... (%s)", texto_ou_seletor, err)
            
            sleep(0.5)

        err_msg = f"Elemento '{texto_ou_seletor}' não foi encontrado dentro de {timeout}ms"
        logger.error(err_msg)
        raise PlaywrightTimeoutError(err_msg)
    
    def executar_rotina_teste(self,cronometro) -> None:
        logger.info("Acessando menu lateral: %s", MENU_LATERAL)
        for etapa in [parte.strip() for parte in MENU_LATERAL.split(">") if parte.strip()]:
            # No menu lateral do Protheus, usa-se duplo clique em alguns nós da árvore
            self._esperar_e_clicar(etapa, timeout=30000, duplo_clique=True)
            cronometro.atualizar_acao(f"Etapa do menu lateral concluída: {etapa}")

        logger.info("Executando rotina de validação")
        
        # Aguarda a ação 'Visualizar'
        self._esperar_e_clicar("Visualizar", timeout=60000, duplo_clique=False)
        self.page.screenshot(path="Visualizar.png", full_page=True)
        cronometro.atualizar_acao("Ação 'Clicar em Visualizar' concluída")


        # Aguarda a ação 'Cancelar' no modal/tela aberta
        self._esperar_e_clicar("Cancelar", timeout=100000, duplo_clique=False)
        self.page.screenshot(path="Cancelar.png", full_page=True)
        cronometro.atualizar_acao("Ação 'Clicar em Cancelar' concluída")

        # Para fechar/sair usando ID específico
        self._esperar_e_clicar("#COMP6014", timeout=90000)
        self.page.screenshot(path="sair.png", full_page=True)
        cronometro.atualizar_acao("Ação 'Clicar em Sair' concluída")

    def preencher_empresa_filial(self,cronometro) -> None:
        emp = os.getenv("Setup_emp")
        if not emp:
            logger.info("Setup_emp nao definido; pulando tela de empresa/filial")
            return

        try:
            self._fill(
                [
                    'input[id*="po-lookup"]',
                    'input[data-placeholder*="Grupo"]',
                    'input[placeholder*="Grupo"]',
                ],
                emp,
                timeout=10000,
            )
            self._esperar_e_clicar("Entrar", timeout=10000)
            logger.info("Empresa preenchida")
            cronometro.atualizar_acao("Empresa preenchida")
        except PlaywrightTimeoutError:
            logger.info("Tela de grupo/empresa nao apareceu")
            cronometro.atualizar_acao("Tela de grupo/empresa nao apareceu")
        except Exception as e:
            logger.exception("Erro durante o preenchimento da empresa/filial: %s", e)
            cronometro.atualizar_acao("Erro durante o preenchimento da empresa/filial")

    def confirmar_mfa(self,cronometro, wait_ms: int = 5000) -> bool:
        campo_mfa = self.page.locator("wa-text-input#COMP4506 input").first
        try:
            campo_mfa.wait_for(state="visible", timeout=wait_ms)
        except PlaywrightTimeoutError:
            cronometro.atualizar_acao("Campo MFA nao apareceu")
            return False

        try:
            codigo = str(graph.MicrosoftGraphClient().obter_ultimo_codigo_acesso()).strip()
            if not codigo or codigo.lower() == "none":
                logger.warning("Codigo de MFA retornado está vazio ou inválido")
                cronometro.atualizar_acao("Codigo de MFA retornado está vazio ou inválido")
                return False

            campo_mfa.fill(codigo, timeout=5000)
            self.page.locator("wa-button#COMP4507 button").first.click(timeout=5000)
            logger.info("MFA confirmado com sucesso")
            cronometro.atualizar_acao("MFA confirmado com sucesso")
            return True
        except Exception as error:
            logger.exception("Nao foi possivel confirmar MFA devido a um erro inesperado: %s", error)
            cronometro.atualizar_acao("Nao foi possivel confirmar MFA devido a um erro inesperado")
            return False

    def confirmar_mfa_se_existir(self,cronometro, etapa: str, wait_ms: int = 2500) -> bool:
        confirmado = self.confirmar_mfa(cronometro, wait_ms=wait_ms)
        if confirmado:
            logger.info("MFA confirmado %s", etapa)
            cronometro.atualizar_acao("MFA confirmado %s" % etapa)
        else:
            logger.info("MFA nao apareceu %s", etapa)
            cronometro.atualizar_acao("MFA nao apareceu %s" % etapa)
        return confirmado

    def executar_rotina_teste(self,cronometro, max_retries: int = 3) -> None:
        logger.info("Acessando menu lateral: %s", MENU_LATERAL)
        cronometro.atualizar_acao("Acessando menu lateral: %s" % MENU_LATERAL)
        for etapa in [parte.strip() for parte in MENU_LATERAL.split(">") if parte.strip()]:
            self._esperar_e_clicar(etapa, timeout=30000)

        logger.info("Executando rotina de validacao")
        cronometro.atualizar_acao("Executando rotina de validacao")

        # Tenta o fluxo Visualizar -> Cancelar com repetição em caso de timeout no Cancelar
        for tentativa in range(1, max_retries + 1):
            try:
                logger.info("Tentativa %d de %d: Clicando em 'Visualizar'", tentativa, max_retries)
                cronometro.atualizar_acao("Tentativa %d de %d: Clicando em 'Visualizar'" % (tentativa, max_retries))
                self._esperar_e_clicar("Visualizar", timeout=30000)
                self.page.screenshot(path=f"Visualizar_tentativa_{tentativa}.png", full_page=True)
                cronometro.atualizar_acao("Ação 'Clicar em Visualizar' concluída na tentativa %d" % tentativa)

                logger.info("Aguardando botão 'Cancelar'...")
                # Reduzimos levemente o timeout para detectar a falha mais rápido e tentar de novo
                self._esperar_e_clicar("Cancelar", timeout=20000) 
                cronometro.atualizar_acao("Ação 'Clicar em Cancelar' concluída na tentativa %d" % tentativa)

                self.page.screenshot(path="Cancelar.png", full_page=True)
                # Se chegou até aqui sem erros, o fluxo do Cancelar deu certo
                logger.info("Elemento 'Cancelar' encontrado e clicado com sucesso!")
                cronometro.atualizar_acao("Elemento 'Cancelar' encontrado e clicado com sucesso na tentativa %d" % tentativa)
                break

            except PlaywrightTimeoutError:
                logger.warning(
                    "O elemento 'Cancelar' não apareceu após clicar em 'Visualizar' (Tentativa %d/%d).",
                    tentativa, max_retries
                )
                cronometro.atualizar_acao("O elemento 'Cancelar' não apareceu após clicar em 'Visualizar' (Tentativa %d/%d)." % (tentativa, max_retries))
                if tentativa == max_retries:
                    logger.error("Número máximo de tentativas atingido. Lançando erro...")
                    cronometro.atualizar_acao("Número máximo de tentativas atingido. Lançando erro...")
                    raise
                logger.info("Re-tentando ação anterior ('Visualizar')...")
                cronometro.atualizar_acao("Re-tentando ação anterior ('Visualizar')...")
                sleep(2)  # Pausa breve para estabilização da tela antes de tentar novamente

        # Para fechar/sair após o sucesso do fluxo
        try:
            self._esperar_e_clicar("wa-button#COMP6014 button", timeout=30000)
            self.page.screenshot(path="sair.png", full_page=True)
            cronometro.atualizar_acao("Clicando no botão de fechar/sair (#COMP6014)")
        except Exception as e:
            logger.warning("Não foi possível clicar no botão de fechar/sair (#COMP6014): %s", e)
            cronometro.atualizar_acao("Não foi possível clicar no botão de fechar/sair (#COMP6014)")


def exec_playwright_from_main(headless_override: bool | None = None) -> bool:
    mfa_obtido = False
    cronometro = LogBanco.CronometroExecucao(acao_inicial="PREPARANDO ABERTURA DO NAVEGADOR")
    cronometro.iniciar()
    status_final = "OK"
    descricao_final = "FECHANDO NAVEGADOR"

    try:
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
                cronometro.atualizar_acao("ABRINDO NAVEGADOR")
                browser = browser_type.launch(
                    headless=headless,
                    firefox_user_prefs=firefox_prefs,
                )
                #browser = playwright.chromium.launch(headless=headless)
                context: BrowserContext = browser.new_context()
                page = context.new_page()
                flow = ProtheusPlaywrightMainFlow(page)

                cronometro.atualizar_acao("ACESSANDO URL DO PROTHEUS")
                try:
                    flow.abrir()
                except PlaywrightTimeoutError as error:
                    logger.error("Falha de conexao ou timeout ao acessar %s: %s", CONFIG.get("Url"), error, exc_info=True)
                    logger.info("Enviando email de base offline...")
                    cronometro.atualizar_acao("Falha de conexao ou timeout ao acessar URL do Protheus; enviando email de base offline")
                    
                    try:
                        enviar_erro_base_selenium()
                    except Exception as email_err:
                        logger.exception("Falha adicional ao tentar enviar o e-mail de base offline: %s", email_err)
                        cronometro.atualizar_acao("Falha adicional ao tentar enviar o e-mail de base offline")

                    status_final = "ERRO"
                    descricao_final = "ERRO AO ACESSAR URL DO PROTHEUS"
                    return False

                cronometro.atualizar_acao("Iniciando funcao para parâmetros iniciais...")
                flow.selecionar_parametros_iniciais(cronometro)

                cronometro.atualizar_acao("Iniciando funcao para fechar tela de tamanho se existir...")
                flow.fechar_tela_tamanho_se_existir(cronometro)

                cronometro.atualizar_acao("iniciando funcao de Login...")
                flow.fazer_login(cronometro)
                logger.info("Login realizado com sucesso")
                cronometro.atualizar_acao("Login realizado com sucesso")

                cronometro.atualizar_acao("Iniciando validacao de MFA antes de preencher grupo/filial...")
                mfa_obtido = flow.confirmar_mfa_se_existir(cronometro, "antes de preencher grupo/filial")

                cronometro.atualizar_acao("Iniciando funcao para preencher empresa/filial...")
                flow.preencher_empresa_filial(cronometro)

                if not mfa_obtido:
                    cronometro.atualizar_acao("Iniciando funcao de verificar MFA depois de preencher grupo/filial...")
                    mfa_obtido = flow.confirmar_mfa_se_existir(
                        cronometro,
                        "depois de preencher grupo/filial",
                        wait_ms=5000,
                    )

                cronometro.atualizar_acao(fr"Executando rotina {MENU_LATERAL}")
                cronometro.atualizar_acao(fr"Aguardando elemento atual... ")
                flow.executar_rotina_teste(cronometro)

                logger.info("Execução do fluxo principal do Playwright concluida")
                cronometro.atualizar_acao("Execução do fluxo principal do Playwright concluida")
                return mfa_obtido
            except Exception as e:
                status_final = "ERRO"
                erro_resumido = " ".join(str(e).split())
                descricao_final = f"ERRO DURANTE EXECUCAO PLAYWRIGHT: {erro_resumido}"
                logger.exception("Exceção não tratada capturada na execução do fluxo principal: %s", e)
                cronometro.marcar_erro()
                cronometro.atualizar_acao(
                    f"Exceção não tratada capturada na execução do fluxo principal: {erro_resumido}"
                )
                raise
            finally:
                if browser is not None:
                    browser.close()
                total = cronometro.finalizar(status_final=status_final, descricao_final=descricao_final)
                logger.info("Cronometro finalizado em %ss", total)
                LogBanco.salvar()
    except Exception as global_err:
        logger.exception("Erro crítico no gerenciador do Playwright: %s", global_err)
        enviar_erro_base_selenium()
        return False
    

if __name__ == "__main__":
    try:
        args = _build_args()
        override = None
        if args.headed:
            override = False
        elif args.headless:
            override = True

        exec_playwright_from_main(headless_override=override)
    except Exception as e:
        logger.critical("Ocorreu um erro fatal que interrompeu o script: %s", e, exc_info=True)
        sys.exit(1)