
import argparse
import json
import os
import re
from socket import timeout
import subprocess
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

from MandaEmail import enviar_erro_base_selenium, enviar_erro_execucao, enviar_erro_licenca, enviar_temp_exc
from erros_protheus import DESCRICAO_LICENCA, LicencaExcedidaError, LICENCA_REGEX
import graph
import LogBanco
import logging_config
import mapa_calor
import rotinas as rotinas_cfg
from rotinas import Rotina, ResultadoRotina

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"

logger = logging_config.get_logger("main_playwright_from_main")
BUILD_VERSION = "2026-11-18-login-unico-rotinas"
# Resume uma exceção em uma linha (o Playwright inclui um "Call log" de dezenas de linhas).
def resumir_erro(erro: BaseException, limite: int = 200) -> str:
    primeira = next((linha.strip() for linha in str(erro).splitlines() if linha.strip()), type(erro).__name__)
    return primeira[:limite]


# Intervalo (s) entre verificações da janela de licença enquanto espera um elemento.
INTERVALO_VERIFICACAO_LICENCA = 3

# Lê a árvore do menu do WebApp e devolve o item `texto` que é filho DIRETO do item
# `escopoId` (ou da raiz do menu, se escopoId for null). Estrutura do WebApp:
#   <wa-menu> <wa-menu-item caption="Compras (7)"> <wa-menu-item caption="Movimento (26)"> ...
# - compara o texto ignorando maiúsculas, tags (<u>) e o contador "(26)";
# - "pasta": tem contador ou subitens; "aberta": algum subitem está visível;
# - subitens são criados só na primeira vez que a pasta abre (carga sob demanda).
JS_LOCALIZAR_ITEM_MENU = """
([escopoId, texto]) => {
  const norm = s => (s || '').replace(/<[^>]+>/g, '').replace(/\\(\\d+\\)\\s*$/, '')
                             .replace(/\\s+/g, ' ').trim().toLowerCase();
  const filhos = e => [...e.children].filter(c => c.tagName === 'WA-MENU-ITEM');
  // Visível de verdade: tem tamanho E não está escondido por CSS (o Protheus esconde o menu
  // com visibility:hidden quando a rotina abre - só o tamanho não basta).
  const visivel = e => {
    const r = e.getBoundingClientRect();
    if (!(r.width > 0 && r.height > 0)) return false;
    const alvo = (e.shadowRoot && e.shadowRoot.querySelector('span.caption')) || e;
    if (alvo.checkVisibility) {
      return alvo.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true, opacityProperty: true, visibilityProperty: true });
    }
    return getComputedStyle(alvo).visibility !== 'hidden';
  };
  const escopo = escopoId
    ? document.getElementById(escopoId)
    : [...document.querySelectorAll('wa-menu')].find(m => filhos(m).some(visivel));
  if (!escopo) return null;
  const item = filhos(escopo).find(e => visivel(e) && norm(e.getAttribute('caption')) === norm(texto));
  if (!item) return null;
  const sub = filhos(item);
  return {
    id: item.id,
    pasta: /\\(\\d+\\)\\s*$/.test(item.getAttribute('caption') || '') || sub.length > 0,
    aberta: sub.some(visivel),
  };
}
"""

# Marca o TEXTO do item (span.caption, dentro do shadow DOM do wa-menu-item) com um
# atributo, para o Playwright clicar exatamente nele - como fazia a busca por texto antiga.
# Clicar fora do texto às vezes só SELECIONA o item (fica azul) sem abrir.
JS_MARCAR_ALVO_MENU = """
(id) => {
  const item = document.getElementById(id);
  const alvo = item && item.shadowRoot && item.shadowRoot.querySelector('span.caption');
  if (!alvo) return false;
  alvo.setAttribute('data-rpa-alvo', id);
  return true;
}
"""

# Estado atual de um item do menu, usado para confirmar o efeito do clique:
# pasta -> "aberta" (subitens visíveis); rotina -> o menu some ("visivel" = false).
JS_ESTADO_ITEM_MENU = """
(id) => {
  const item = document.getElementById(id);
  if (!item) return { visivel: false, aberta: false };
  // Visível de verdade: tem tamanho E não está escondido por CSS (o Protheus esconde o menu
  // com visibility:hidden quando a rotina abre - só o tamanho não basta).
  const visivel = e => {
    const r = e.getBoundingClientRect();
    if (!(r.width > 0 && r.height > 0)) return false;
    const alvo = (e.shadowRoot && e.shadowRoot.querySelector('span.caption')) || e;
    if (alvo.checkVisibility) {
      return alvo.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true, opacityProperty: true, visibilityProperty: true });
    }
    return getComputedStyle(alvo).visibility !== 'hidden';
  };
  const sub = [...item.children].filter(c => c.tagName === 'WA-MENU-ITEM');
  return { visivel: visivel(item), aberta: sub.some(visivel) };
}
"""

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

    # verifica se a janela TOTVS "Excedeu numero de licenças" está na tela
    def verificar_licenca(self) -> None:
        """Levanta LicencaExcedidaError se a mensagem de licenças estiver visível em algum frame."""
        for frame in list(self.page.frames):
            try:
                mensagem = frame.get_by_text(LICENCA_REGEX).first
                if mensagem.count() and mensagem.is_visible():
                    logger.error("Protheus recusou a sessão: Excedeu numero de licenças")
                    self._atualizar_acao("Janela TOTVS: Excedeu numero de licenças")
                    try:
                        self.page.screenshot(
                            path=str(LogBanco.LOG_DIR / f"licenca_excedida_{int(monotonic() * 1000)}.png"),
                            full_page=True,
                        )
                    except PlaywrightError:
                        pass
                    raise LicencaExcedidaError()
            except PlaywrightError:
                # frame descarregado no meio da verificação: segue para o próximo
                continue

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
        proxima_verificacao_licenca = monotonic()
        ultimo_erro_clique: str | None = None  # motivo da última falha de clique (para o log)
        while monotonic() < deadline:
            # Se o Protheus mostrar "Excedeu numero de licenças", não adianta esperar o timeout.
            if monotonic() >= proxima_verificacao_licenca:
                self.verificar_licenca()
                proxima_verificacao_licenca = monotonic() + INTERVALO_VERIFICACAO_LICENCA
            target = None
            clique_tentado = False
            try:
                frames = list(self.page.frames)
                for frame in frames:
                    target = buscar_em_frame(frame)
                    if target:
                        # Calcula o tempo restante para o timeout
                        restante_ms = max(1, int((deadline - monotonic()) * 1000))
                        target.wait_for(state="visible", timeout=min(1000, restante_ms))
                        self._atualizar_acao(f"Elemento '{texto_ou_seletor}' encontrado; executando {acao}")

                        clique_tentado = True
                        if duplo_clique:
                            target.dblclick(timeout=min(3000, restante_ms))
                        else:
                            target.click(timeout=min(3000, restante_ms))
                            
                        logger.info("Clique realizado com sucesso no elemento: '%s'", texto_ou_seletor)
                        self._atualizar_acao(f"{acao.capitalize()} realizado em '{texto_ou_seletor}'")
                        return
            except (PlaywrightTimeoutError, PlaywrightError) as err:
                # O clique pode ter funcionado e o erro vir depois (ex.: "Cancelar" fecha a
                # janela enquanto o Playwright ainda confirma o clique). Se o elemento saiu
                # da tela, o clique teve efeito: segue o fluxo em vez de procurar de novo.
                if clique_tentado and target is not None and self._saiu_da_tela(target):
                    logger.info("Clique em '%s' confirmado: o elemento saiu da tela após o clique", texto_ou_seletor)
                    self._atualizar_acao(f"{acao.capitalize()} realizado em '{texto_ou_seletor}'")
                    return
                if clique_tentado:
                    motivo = resumir_erro(err)
                    if motivo != ultimo_erro_clique:  # registra cada motivo uma vez só
                        logger.warning("Clique em '%s' falhou (%s); tentando de novo", texto_ou_seletor, motivo)
                        ultimo_erro_clique = motivo
                else:
                    logger.debug("Tentando interagir com '%s'... (%s)", texto_ou_seletor, err)

            sleep(0.5)

        if ultimo_erro_clique:
            err_msg = (f"Elemento '{texto_ou_seletor}' encontrado, mas o clique falhou "
                       f"durante {timeout}ms: {ultimo_erro_clique}")
        else:
            err_msg = f"Elemento '{texto_ou_seletor}' não foi encontrado dentro de {timeout}ms"
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
        # Esc só DEPOIS do print: antes, o print mostrava a tela já alterada pelo Esc.
        if pressionar_escape_ao_falhar:
            self.page.keyboard.press("Escape")
        self._atualizar_acao(f"Falha: elemento '{texto_ou_seletor}' não encontrado após {timeout}ms")
        raise PlaywrightTimeoutError(err_msg)

    # depois de um clique com erro: o elemento sumiu/ficou invisível? (= o clique teve efeito)
    def _saiu_da_tela(self, target: Locator, espera_ms: int = 1500) -> bool:
        deadline = monotonic() + espera_ms / 1000
        while True:
            try:
                if not target.is_visible():
                    return True
            except PlaywrightError:
                return True  # elemento descartado junto com a janela
            if monotonic() >= deadline:
                return False
            sleep(0.25)

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

    # executa todas as rotinas na mesma sessão, cronometrando cada uma e voltando à tela principal
    def executar_rotinas(self, cronometro, lista_rotinas: list[Rotina]) -> list[ResultadoRotina]:
        resultados: list[ResultadoRotina] = []
        total = len(lista_rotinas)
        tela_principal_ok = True

        for indice, rotina in enumerate(lista_rotinas, start=1):
            # Sem a tela principal não há como abrir a próxima rotina pelo menu.
            if not tela_principal_ok:
                logger.error("Rotina '%s' não executada: tela principal não foi recuperada", rotina.nome)
                nao_executada = ResultadoRotina(
                    rotina.nome, rotina.caminho, 0.0, False,
                    "NAO EXECUTADA: tela principal não foi recuperada após a rotina anterior",
                )
                resultados.append(nao_executada)
                cronometro.registrar_tempo(f"Rotina '{rotina.nome}'", 0.0, False, nao_executada.erro)
                continue

            handler = logging_config.iniciar_log_da_rotina(indice, LogBanco.id_execucao, rotina.nome)
            try:
                logger.info("Rotina %d/%d '%s': %s", indice, total, rotina.nome, rotina.caminho)
                cronometro.atualizar_acao(f"Rotina {indice}/{total} iniciada: {rotina.nome}")

                # ---- tempo da rotina: só os cliques do caminho ----
                inicio = monotonic()
                try:
                    self.executar_rotina(cronometro, rotina, indice, total)
                    resultado = ResultadoRotina(rotina.nome, rotina.caminho, round(monotonic() - inicio, 2), True)
                except Exception as erro:
                    resultado = self._tratar_falha_rotina(cronometro, rotina, erro, monotonic() - inicio)
                # ---- fim do tempo da rotina ----

                logger.info("Tempo da rotina '%s': %.1fs (%s)", rotina.nome, resultado.segundos,
                            "OK" if resultado.sucesso else resultado.erro)
                cronometro.registrar_tempo(
                    f"Rotina '{rotina.nome}'", resultado.segundos, resultado.sucesso, resultado.erro
                )
                resultados.append(resultado)

                # Volta para a tela principal (fora do tempo medido).
                ja_fechou = resultado.sucesso and rotina.termina_com_saida
                tela_principal_ok = self.voltar_tela_principal(cronometro, rotina, ja_fechou)
            finally:
                logging_config.encerrar_log_da_rotina(handler)

        return resultados

    # registra a falha de uma rotina (erro de licença tem tratamento próprio) e devolve o resultado
    def _tratar_falha_rotina(self, cronometro, rotina: Rotina, erro: Exception, segundos: float) -> ResultadoRotina:
        # Um timeout qualquer pode ter sido causado pela janela de licença: confere a tela.
        if not isinstance(erro, LicencaExcedidaError):
            try:
                self.verificar_licenca()
            except LicencaExcedidaError as erro_licenca:
                erro = erro_licenca
            except Exception:
                pass

        if isinstance(erro, LicencaExcedidaError):
            logger.error("Rotina '%s': %s", rotina.nome, erro)
            enviar_erro_licenca(rotina.nome, cronometro.obter_acao())
            self._fechar_dialogo()  # botão "Fechar" da janela TOTVS
            return ResultadoRotina(rotina.nome, rotina.caminho, round(segundos, 2), False, DESCRICAO_LICENCA)

        erro_resumido = resumir_erro(erro)
        logger.error("Rotina '%s' falhou após %.1fs: %s", rotina.nome, segundos, erro_resumido)
        logger.debug("Detalhes técnicos da falha em '%s'", rotina.nome, exc_info=erro)
        enviar_erro_execucao(erro, cronometro.obter_acao())
        return ResultadoRotina(rotina.nome, rotina.caminho, round(segundos, 2), False, erro_resumido)

    # executa as etapas de UMA rotina, na ordem informada no caminho
    def executar_rotina(self, cronometro, rotina: Rotina, indice_rotina: int, total_rotinas: int) -> None:
        etapas_fluxo = list(rotina.etapas)
        rotina_timeout = int(CONFIG.get("RotinaTimeout", 90000))

        # 1) Etapas do menu: percorridas pela árvore (até abrir a rotina).
        etapas_menu = self._navegar_menu(cronometro, rotina, indice_rotina, total_rotinas)

        # 2) Demais etapas: ações dentro da tela da rotina (Visualizar, Cancelar, sair...).
        for indice in range(etapas_menu + 1, len(etapas_fluxo) + 1):
            etapa = etapas_fluxo[indice - 1]
            timeout = rotina_timeout
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

    # percorre as etapas do menu pela árvore; devolve quantas etapas do caminho eram do menu
    def _navegar_menu(self, cronometro, rotina: Rotina, indice_rotina: int, total_rotinas: int) -> int:
        """Ex.: "Fiscal > Movimento > Documento Entrada > Visualizar":
        - Fiscal: procurado na raiz; se fechado, clica (o acordeão fecha o módulo anterior);
        - Movimento: procurado SÓ dentro de Fiscal; se já aberto, não clica;
        - Documento Entrada: não é pasta -> clica e a rotina abre. Retorna 3.
        As etapas seguintes (Visualizar...) ficam para executar_rotina.
        """
        menu_timeout = int(CONFIG.get("MenuTimeout", 60000))
        passo_timeout = int(CONFIG.get("MenuStepTimeout", 15000))
        escopo_id: str | None = None  # None = raiz do menu
        total_etapas = len(rotina.etapas)

        for indice, etapa in enumerate(rotina.etapas, start=1):
            if etapa.casefold() in rotinas_cfg.ETAPAS_SAIDA:
                return indice - 1
            prefixo = f"Rotina {indice_rotina}/{total_rotinas} - Etapa {indice}/{total_etapas}"
            cronometro.atualizar_acao(f"{prefixo}: procurando '{etapa}' no menu")

            # A raiz pode demorar logo após o login; dentro de uma pasta, os subitens
            # aparecem depois do clique (carga sob demanda).
            timeout = menu_timeout if indice == 1 else passo_timeout
            achado = self._aguardar_item_menu(escopo_id, etapa, timeout)
            if achado is None:
                local = "na raiz do menu" if escopo_id is None else f"dentro de '{rotina.etapas[indice - 2]}'"
                raise PlaywrightTimeoutError(f"Item '{etapa}' não encontrado {local} após {timeout}ms")
            frame, item = achado

            if item["pasta"] and item["aberta"]:
                # Clicar numa pasta aberta a fecharia.
                logger.info("%s: pasta '%s' já está aberta; sem clique", prefixo, etapa)
            else:
                self._clicar_item_menu(frame, item, etapa, timeout)
                logger.info("%s: clique em '%s' (menu, %s)", prefixo, etapa, item["id"])
                nome_etapa = re.sub(r"[^A-Za-z0-9_-]+", "_", etapa).strip("_") or f"etapa_{indice}"
                self.page.screenshot(
                    path=str(LogBanco.LOG_DIR / f"fluxo_r{indice_rotina:02d}_{indice:02d}_{nome_etapa}.png"),
                    full_page=True,
                )
            cronometro.atualizar_acao(f"{prefixo} concluída: '{etapa}'")

            if not item["pasta"]:
                return indice  # a rotina foi aberta; o resto do caminho são ações na tela dela
            escopo_id = item["id"]

        return total_etapas

    # clica no item do menu e CONFIRMA que o clique teve efeito; repete se só selecionou
    def _clicar_item_menu(self, frame, item: dict, etapa: str, timeout: int) -> None:
        """Pasta: espera abrir (subitens visíveis). Rotina: espera o menu sumir (a tela da
        rotina ocupa a página). Se o clique só selecionou o item, clica de novo."""
        tentativas = max(1, int(CONFIG.get("MenuRetries", 2)))
        # Abrir uma rotina pode demorar bem mais que abrir uma pasta.
        espera_ms = (int(CONFIG.get("MenuStepTimeout", 15000)) if item["pasta"]
                     else int(CONFIG.get("RotinaAbrirTimeout", 60000)))
        item_id = item["id"]

        for tentativa in range(1, tentativas + 1):
            # Antes de repetir, confere de novo: a rotina pode ter aberto devagar.
            # Clicar de novo poderia abrir a rotina duas vezes.
            if tentativa > 1 and self._aguardar_efeito_clique(frame, item, 0):
                return
            try:
                if frame.evaluate(JS_MARCAR_ALVO_MENU, item_id):
                    frame.locator(f'[data-rpa-alvo="{item_id}"]').first.click(timeout=min(timeout, 10000))
                else:
                    # Sem o texto no shadow DOM: clica na linha do título do item.
                    frame.locator(f"wa-menu-item#{item_id}").click(position={"x": 60, "y": 10}, timeout=min(timeout, 10000))
            except PlaywrightTimeoutError:
                # O item ficou invisível antes do clique: a tela mudou (ex.: rotina abrindo).
                if self._aguardar_efeito_clique(frame, item, 2000):
                    return
                raise

            if self._aguardar_efeito_clique(frame, item, espera_ms):
                return
            logger.warning(
                "Clique em '%s' não teve efeito (%s); tentativa %d de %d",
                etapa, "pasta não abriu" if item["pasta"] else "rotina não abriu", tentativa, tentativas,
            )
            self.verificar_licenca()

        o_que = "a pasta não abriu" if item["pasta"] else "a rotina não abriu"
        raise PlaywrightTimeoutError(f"Item '{etapa}' do menu foi clicado {tentativas}x, mas {o_que}")

    # espera o efeito do clique: pasta aberta ou rotina aberta (menu escondido)
    def _aguardar_efeito_clique(self, frame, item: dict, espera_ms: int) -> bool:
        deadline = monotonic() + espera_ms / 1000
        while True:  # confere ao menos uma vez (espera_ms=0 = só conferir agora)
            try:
                estado = frame.evaluate(JS_ESTADO_ITEM_MENU, item["id"])
            except PlaywrightError:
                estado = {"visivel": False, "aberta": False}  # frame recarregou: a tela mudou
            if item["pasta"] and estado["aberta"]:
                return True
            if not item["pasta"] and not estado["visivel"]:
                return True
            if monotonic() >= deadline:
                return False
            sleep(0.3)

    # espera o item aparecer na árvore do menu (dentro do pai informado)
    def _aguardar_item_menu(self, escopo_id: str | None, texto: str, timeout: int):
        """Retorna (frame, {"id", "pasta", "aberta"}) ou None se não aparecer no tempo."""
        deadline = monotonic() + timeout / 1000
        proxima_verificacao_licenca = monotonic()
        while True:
            if monotonic() >= proxima_verificacao_licenca:
                self.verificar_licenca()
                proxima_verificacao_licenca = monotonic() + INTERVALO_VERIFICACAO_LICENCA
            for frame in list(self.page.frames):
                try:
                    item = frame.evaluate(JS_LOCALIZAR_ITEM_MENU, [escopo_id, texto])
                except PlaywrightError:
                    continue  # frame recarregando/descartado
                if item:
                    return frame, item
            if monotonic() >= deadline:
                return None
            sleep(0.3)

    # fecha a tela da rotina e confirma que o menu principal voltou a aparecer
    def voltar_tela_principal(self, cronometro, rotina: Rotina, ja_fechou: bool) -> bool:
        """Retorna True quando a 1ª etapa do caminho (ex.: o módulo 'Compras') está visível no menu."""
        tentativas = int(CONFIG.get("VoltarTelaTentativas", 3))
        espera_ms = int(CONFIG.get("VoltarTelaTimeout", 15000))
        item_menu = rotina.etapas[0]
        inicio = monotonic()
        cronometro.atualizar_acao(f"Voltando para a tela principal após '{rotina.nome}'")

        for tentativa in range(1, tentativas + 1):
            # Se o caminho já terminou em 'sair' com sucesso, a 1ª tentativa só confere o menu.
            if not (ja_fechou and tentativa == 1):
                self._fechar_dialogo()
                self._fechar_rotina_atual()
            if self._item_visivel_rapido(item_menu, timeout=espera_ms):
                logger.info("Tela principal recuperada em %.1fs (tentativa %d)", monotonic() - inicio, tentativa)
                cronometro.atualizar_acao("Tela principal recuperada")
                return True
            logger.warning("Menu '%s' ainda não visível; tentativa %d de %d", item_menu, tentativa, tentativas)

        logger.error("Não foi possível voltar para a tela principal após '%s'", rotina.nome)
        try:
            self.page.screenshot(
                path=str(LogBanco.LOG_DIR / f"tela_principal_perdida_{int(monotonic() * 1000)}.png"),
                full_page=True,
            )
        except PlaywrightError:
            pass
        return False

    # verifica rapidamente (sem esperar o timeout longo) se um item do menu está visível
    def _item_visivel_rapido(self, texto: str, timeout: int = 1000) -> bool:
        try:
            self._aguardar_item_visivel(texto, timeout=timeout)
            return True
        except (PlaywrightTimeoutError, PlaywrightError):
            return False

    # tenta fechar a tela da rotina atual (botão de saída); ignora se não existir
    def _fechar_rotina_atual(self) -> None:
        try:
            self._esperar_e_clicar(
                "wa-button#COMP6014 button",
                timeout=int(CONFIG.get("FecharRotinaTimeout", 10000)),
                pressionar_escape_ao_falhar=False,
            )
            sleep(1)
        except (PlaywrightTimeoutError, PlaywrightError):
            logger.info("Botão de saída da rotina não encontrado")

    # clica em "Fechar" de uma janela de mensagem do Protheus, se houver
    def _fechar_dialogo(self) -> None:
        for frame in list(self.page.frames):
            try:
                botao = frame.get_by_role("button", name="Fechar", exact=True).first
                if botao.count() and botao.is_visible():
                    botao.click(timeout=3000)
                    sleep(0.5)
                    return
            except PlaywrightError:
                continue

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
            logger.info("Estado do menu salvo em %s (frames=%d)", screenshot_path.name, len(self.page.frames))
            logger.debug("Textos dos frames do menu: %s", textos)
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
# Função principal: login único, depois cada rotina na mesma sessão, com tempos individuais.
#
#########################################################################################################
def exec_playwright_from_main(
    headless_override: bool | None = None,
    fluxos_cli: list[str] | None = None,
    _login_retry_attempted: bool = False,
    _execution_retry_count: int = 0,
) -> bool:
    # Valida as rotinas ANTES de abrir o navegador (falha rápida se a config estiver errada).
    lista_rotinas = rotinas_cfg.carregar_rotinas(CONFIG, fluxos_cli)
    logger.info("Versao do fluxo Playwright: %s", BUILD_VERSION)
    logger.info("Rotinas a cronometrar (%d): %s", len(lista_rotinas), [r.nome for r in lista_rotinas])

    cronometro = LogBanco.CronometroExecucao(acao_inicial="INICIANDO EXECUCAO PLAYWRIGHT")
    cronometro.iniciar()
    status_final = "NAO"
    descricao_final = "FINALIZANDO EXECUCAO"
    login_retry_requested = False
    resultados: list[ResultadoRotina] = []

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
            flow: ProtheusPlaywrightMainFlow | None = None
            inicio_login: float | None = None
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

                # ================= LOGIN (cronometrado como "Login") =================
                handler_login = logging_config.iniciar_log_da_rotina(0, LogBanco.id_execucao, "Login")
                inicio_login = monotonic()
                try:
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
                        return False

                    cronometro.atualizar_acao("Iniciando funcao para parâmetros iniciais...")
                    flow.selecionar_parametros_iniciais(cronometro)

                    cronometro.atualizar_acao("Iniciando funcao para fechar tela de tamanho se existir...")
                    flow.fechar_tela_tamanho_se_existir(cronometro)

                    cronometro.atualizar_acao("iniciando funcao de Login...")
                    flow.fazer_login(cronometro)
                    flow.verificar_licenca()  # a janela de licença costuma aparecer logo após o login
                    logger.info("Login realizado com sucesso")
                    cronometro.atualizar_acao("Login realizado com sucesso")

                    cronometro.atualizar_acao("Iniciando validacao de MFA antes de preencher grupo/filial...")
                    mfa_obtido = flow.confirmar_mfa_se_existir(cronometro, "antes de preencher grupo/filial")

                    cronometro.atualizar_acao("Iniciando funcao para preencher empresa/filial...")
                    empresa_filial_ok = flow.preencher_empresa_filial(cronometro)
                    flow.verificar_licenca()
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

                    segundos_login = monotonic() - inicio_login
                    logger.info("Tempo de login: %.1fs", segundos_login)
                finally:
                    logging_config.encerrar_log_da_rotina(handler_login)

                resultados.append(ResultadoRotina(
                    "Login", "URL > parâmetros > login > MFA > empresa/filial", round(segundos_login, 2), True
                ))
                cronometro.registrar_tempo("Login", segundos_login, True)
                # ================= fim do LOGIN =================

                # ============ ROTINAS (cada uma cronometrada, voltando à tela principal) ============
                flow._registrar_estado_menu()
                resultados.extend(flow.executar_rotinas(cronometro, lista_rotinas))

                falhas = [r.nome for r in resultados if not r.sucesso]
                if falhas:
                    status_final = "SIM"
                    descricao_final = "ERRO EM ROTINA(S): " + ", ".join(falhas)
                logger.info("Execução concluida (%d rotina(s), %d com erro)", len(lista_rotinas), len(falhas))
                cronometro.atualizar_acao("Execução do fluxo principal do Playwright concluida")
                return not falhas
            except Exception as e:
                # Um timeout no login pode ter sido causado pela janela de licença: confere a tela.
                erro = e
                if not isinstance(e, LicencaExcedidaError) and flow is not None:
                    try:
                        flow.verificar_licenca()
                    except LicencaExcedidaError as erro_licenca:
                        erro = erro_licenca
                    except Exception:
                        pass

                status_final = "SIM"
                erro_resumido = resumir_erro(erro)
                if isinstance(erro, LicencaExcedidaError):
                    descricao_final = DESCRICAO_LICENCA
                else:
                    login_retry_requested = (
                        cronometro.obter_acao()
                        == "Iframe de login encontrado; preenchendo usuário"
                    )
                    descricao_final = f"ERRO DURANTE EXECUCAO PLAYWRIGHT: {erro_resumido}"
                # Falhou durante o login: registra o tempo do login com erro (vai para o mapa de calor).
                if inicio_login is not None and not resultados:
                    segundos_login = monotonic() - inicio_login
                    resultados.append(ResultadoRotina(
                        "Login", "URL > parâmetros > login > MFA > empresa/filial",
                        round(segundos_login, 2), False, descricao_final[:300],
                    ))
                    cronometro.registrar_tempo("Login", segundos_login, False, descricao_final)
                logger.error("Falha na execução: %s", erro_resumido)
                logger.debug("Detalhes técnicos da falha na execução", exc_info=e)
                cronometro.marcar_erro()
                cronometro.atualizar_acao(
                    f"Exceção não tratada capturada na execução do fluxo principal: {erro_resumido}"
                )
                if erro is e:
                    raise
                raise erro from e
            finally:
                if browser is not None:
                    browser.close()
                total = cronometro.finalizar(status_final=status_final, descricao_final=descricao_final)
                logger.info("Cronometro finalizado em %ss", total)
                if status_final == "NAO":
                    try:
                        media_resultado = LogBanco.resultado(LogBanco.id_execucao)
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

                # Entrega dos tempos (Login, Rotina 1, ...): log + JSON, mesmo se algo falhou.
                if resultados:
                    logger.info(rotinas_cfg.formatar_resumo(resultados))
                    relatorio = rotinas_cfg.salvar_relatorio(resultados, LogBanco.LOG_DIR, LogBanco.id_execucao)
                    logger.info("Relatório de tempos salvo em %s", relatorio)
                    # Atualiza o mapa de calor com esta execução. Falha aqui não afeta o cronômetro.
                    try:
                        mapa = mapa_calor.gerar_mapa_calor(
                            LogBanco.LOG_DIR, dias=int(CONFIG.get("MapaCalorDias", 60))
                        )
                        logger.info("Mapa de calor atualizado em %s", mapa)
                    except Exception:
                        logger.exception("Não foi possível gerar o mapa de calor")
                LogBanco.salvar()
    except Exception as global_err:
        if isinstance(global_err, LicencaExcedidaError):
            # Sem licença não adianta tentar de novo na hora: avisa e encerra.
            logger.error("Execução não realizada: %s", global_err)
            enviar_erro_licenca("Login (nenhuma rotina executada)", cronometro.obter_acao())
            return False
        logger.debug("Detalhes técnicos do erro no gerenciador do Playwright", exc_info=global_err)
        if login_retry_requested and not _login_retry_attempted:
            logger.warning(
                "Fluxo interrompido no preenchimento do usuário do iframe; "
                "reiniciando uma única vez"
            )
            return exec_playwright_from_main(
                headless_override=headless_override,
                fluxos_cli=fluxos_cli,
                _login_retry_attempted=True,
            )
        execution_retries = int(CONFIG.get("ExecutionRetries", 2))
        if isinstance(global_err, PlaywrightError) and _execution_retry_count < execution_retries:
            proxima_tentativa = _execution_retry_count + 1
            logger.warning(
                "Falha Playwright transitória; reiniciando o fluxo completo "
                "(tentativa %d de %d)",
                proxima_tentativa,
                execution_retries,
            )
            return exec_playwright_from_main(
                headless_override=headless_override,
                fluxos_cli=fluxos_cli,
                _execution_retry_count=proxima_tentativa,
            )
        enviar_erro_execucao(
            global_err,
            cronometro.obter_acao(),
        )
        return False

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

"""
main_playwright.py - Cronômetro de rotinas do Protheus (Playwright + TOTVS WebAgent).

LÓGICA GERAL
------------
1. Lê as rotinas (uma ou mais) de --fluxo ou de "Rotinas"/"Fluxo" no config.json
   (ver rotinas.py). Trocar rotinas não exige alterar código.
2. LOGIN ÚNICO: abre o navegador, acessa a URL, faz login, MFA e empresa/filial
   uma única vez. Esse trecho é cronometrado como "Login".
3. Para cada rotina, na mesma sessão:
   a) executa o caminho (menu > rotina > ações) - cronometrado individualmente.
      O menu é percorrido LENDO A ÁRVORE do WebApp (wa-menu / wa-menu-item, que é
      aninhada): cada item é procurado só dentro do item pai (evita confundir
      "Compras > Movimento" com "Fiscal > Movimento") e pastas já abertas não são
      clicadas (clicar fecharia). O menu é um acordeão: abrir um módulo fecha o outro;
   b) volta para a tela principal (fora do tempo medido): fecha a tela da rotina
      e confirma que o menu (1ª etapa do caminho) está visível de novo.
4. Os tempos (Login, Rotina 1, Rotina 2, ...) vão para:
   - o log geral (resumo no final) e um log de cada item em log_playwright/rotinas/;
   - a tabela RPA.TIMER (linhas "Tempo Login: Ns", "Tempo Rotina 'X': Ns");
   - log_playwright/tempos_rotinas_<id>.json;
   - log_playwright/mapa_calor.html (mapa de calor lido do RPA.TIMER, ver mapa_calor.py).
5. Falhas:
   - no login: novas tentativas da execução inteira (ExecutionRetries) e e-mail;
   - em uma rotina: e-mail de erro, a rotina fica marcada com erro e a próxima segue;
     se não for possível voltar à tela principal, as rotinas restantes são marcadas
     como não executadas.
6. Erro conhecido "Excedeu numero de licenças" (janela TOTVS): reconhecido na tela,
   vira LicencaExcedidaError (erros_protheus.py), tem descrição e e-mail próprios e
   NÃO entra nas novas tentativas.
"""