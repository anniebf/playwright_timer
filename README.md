# Protheus Timer — cronômetro de rotinas do Protheus

Robô em Python + Playwright que **entra no Protheus pelo navegador (WebApp), abre as rotinas que você configurar e mede quanto tempo cada uma leva** — do jeito que um usuário faria. Os tempos vão para o banco (`RPA.TIMER`), para os logs e para um **mapa de calor** que mostra quais rotinas estão mais lentas que o normal ou com erro.

Trocar ou incluir rotinas **não exige mexer no código**: basta editar o `config.json`.

O projeto tem duas partes:

- **o robô** (`main_playwright.py`), que roda de hora em hora, cronometra o login e cada rotina e grava tudo no banco;
- **o mapa de calor** (`api_mapa.py`), um serviço web que lê o banco e mostra o histórico de tempos, quais rotinas estão lentas ou com erro e, clicando numa execução, onde o tempo foi gasto.

Comandos docker para subir a imagem 

```powershell
docker login  # Primeiro fazer login no docker

docker build --no-cache -t hiannyurt/playwright-webagent:versaoatual .  # Docker build para criar uma nova versao da imagem
docker pull hiannyurt/playwright-webagent:latest  #docker pull para subir a nova versao da imagem

#para o rodar na producao atualize o latest
#para fazer no servidor ou para usar a imagem ja existente no docker hub em outra maquina
docker login
docker push hiannyurt/playwright-webagent:latest

```
---

## Sumário

1. [Como funciona](#1-como-funciona)
2. [Estrutura do projeto](#2-estrutura-do-projeto)
3. [Pré-requisitos](#3-pré-requisitos)
4. [Configuração](#4-configuração) — `config.json`, rotinas e `.env`
5. [Como executar](#5-como-executar) — Windows, Docker e Airflow
6. [Onde ver os resultados](#6-onde-ver-os-resultados) — logs, prints e o que vai para o banco
7. [Mapa de calor](#7-mapa-de-calor) — como ler, histórico, detalhe e o serviço web
8. [Erros e alertas](#8-erros-e-alertas)
9. [Testes](#9-testes)
10. [Diagnóstico rápido](#10-diagnóstico-rápido)
11. [Segurança](#11-segurança)

---

## 1. Como funciona

Uma execução faz **um login só** e, na mesma sessão, passa por cada rotina, cronometrando cada uma separadamente:

```text
┌──────────────────────────────────────────────────────────────────────┐
│ LOGIN  (cronometrado como "Login")                                   │
│   abre o navegador → URL do WebApp → parâmetros iniciais (SIGAADV)   │
│   → usuário/senha → MFA (código lido do e-mail) → empresa/filial     │
├──────────────────────────────────────────────────────────────────────┤
│ ROTINA 1  (cronometrada)                                             │
│   Menu:  Compras > Movimento > Pedidos de Compra                     │
│   Acoes: clicar Visualizar, clicar Cancelar, clicar sair             │
│   volta para a tela principal (fora do tempo medido)                 │
├──────────────────────────────────────────────────────────────────────┤
│ ROTINA 2 ... ROTINA N  (idem)                                        │
├──────────────────────────────────────────────────────────────────────┤
│ FIM: fecha o navegador, grava os tempos no banco, gera o JSON e o    │
│      mapa de calor, compara com a média e manda alertas se preciso   │
└──────────────────────────────────────────────────────────────────────┘
```

Detalhes que garantem medições confiáveis:

- **Menu lido pela estrutura da página.** O menu do WebApp é uma árvore (`wa-menu-item` dentro de `wa-menu-item`). Cada etapa é procurada **só dentro da pasta anterior** — por isso `Compras > Movimento` e `Fiscal > Movimento` nunca se confundem. Pastas já abertas não são clicadas de novo (clicar as fecharia). O menu funciona como acordeão: abrir um módulo fecha o outro.
- **Todo clique é confirmado.** Pasta → espera os subitens aparecerem. Rotina → espera o menu sumir (a tela da rotina ocupa a página). Ação (ex.: `Visualizar`) → espera a **próxima etapa** aparecer. Se o Protheus ignorou o clique (tela ainda carregando), o robô clica de novo.
- **Pausa antes de cada ação** (`PausaAntesAcao`, 2 s): com a tela ainda "assentando", o Protheus ignora cliques. A pausa é fixa, então entra igual em todas as medições.
- **O tempo da rotina mede só o caminho dela** — não inclui o login nem a volta para a tela principal.

---

## 2. Estrutura do projeto

```text
playwright_timer/
├── main_playwright.py          # Fluxo principal: login, menu, ações, tempos, tentativas
├── rotinas.py                  # Lê as rotinas (config.json ou --fluxo) e monta o relatório JSON
├── erros_protheus.py           # Erros conhecidos do Protheus (ex.: "Excedeu numero de licenças")
├── LogBanco.py                 # Cronômetro e gravação no Oracle (tabela RPA.TIMER)
├── graph.py                    # Lê o código MFA no e-mail (Microsoft Graph)
├── MandaEmail.py               # E-mails de alerta (SMTP)
├── logging_config.py           # Log geral + um log por rotina
├── mapa_calor.py               # Regras do mapa de calor: lê o RPA.TIMER, calcula o "normal", detalhe da execução
├── mapa_calor_template.html    # Página do mapa (usada pelo serviço e pelo arquivo; não abrir direto)
├── api_mapa.py                 # SERVIÇO web do mapa de calor (FastAPI)
├── Dockerfile.mapa             # Imagem do serviço do mapa (separada da do robô)
│
├── config.example.json         # ⇢ MODELO do config.json (copie e preencha)
├── .env.example                # ⇢ MODELO do .env (copie e preencha)
├── config.json                 # Sua configuração (NÃO versionado)
├── .env                        # Seus segredos (NÃO versionado)
├── idExec.json                 # Contador do ID_EXECUCAO (atualizado a cada execução)
├── requirements.txt            # Todas as dependências (robô, serviço do mapa e testes)
│
├── Dockerfile                  # Imagem com Python, Firefox, Xvfb e TOTVS WebAgent
├── run_docker.py               # Executa o container (usado pelo Airflow)
├── BF_DockerPlaywright.py      # DAG do Airflow (agendamento)
├── webagent/                   # Instaladores do TOTVS WebAgent para Linux (.deb/.rpm)
│
├── tests/                      # Testes automáticos (sem Protheus/banco)
└── log_playwright/             # Saídas: logs, prints, JSONs e mapa_calor.html
    └── rotinas/                #   um log por item (r00 = Login, r01 = rotina 1, ...)
```

Responsabilidade de cada módulo, em uma frase:

| Módulo | O que faz |
|---|---|
| `main_playwright.py` | Orquestra tudo. A classe `ProtheusPlaywrightMainFlow` sabe "mexer" no Protheus (login, menu, cliques confirmados); `exec_playwright_from_main()` amarra login → rotinas → resultados. |
| `rotinas.py` | Lê as rotinas do `config.json` (`Menu` + `Acoes`, ou o `Fluxo` antigo), valida cada ação e grava `tempos_rotinas_<id>.json`. |
| `LogBanco.py` | `CronometroExecucao` grava no `RPA.TIMER` o andamento (1 linha/segundo), o tempo de cada item (`Tempo Login`, `Tempo Rotina`) e o fim da execução. |
| `mapa_calor.py` | Lê as linhas `Tempo ...` do banco, calcula o "normal" de cada rotina, monta o detalhe de uma execução e gera `mapa_calor.html`. |
| `api_mapa.py` | Serviço web (FastAPI) que entrega o mapa de calor lendo o banco a cada acesso. |
| `erros_protheus.py` | Reconhece a janela TOTVS de licenças esgotadas e a transforma num erro próprio (sem novas tentativas). |

---

## 3. Pré-requisitos

| Item | Para quê |
|---|---|
| Python 3.12+ | Rodar o robô fora do Docker |
| Firefox do Playwright | Navegador usado pelo robô (`python -m playwright install firefox`) |
| **TOTVS WebAgent** instalado e rodando | O WebApp do Protheus exige o WebAgent na máquina (no Docker ele já vem na imagem) |
| Acesso de rede ao Protheus, Oracle, Microsoft Graph e SMTP | Login, gravação dos tempos, MFA e alertas |
| Usuário do Protheus com acesso ao **SIGAADV** e às rotinas configuradas | O robô navega pelo menu desse usuário |
| Aplicativo no Azure AD com permissão de aplicação **Mail.Read** | Ler o código MFA na caixa de e-mail |
| Permissão de `INSERT`/`SELECT` em `RPA.TIMER` | Gravar e ler os tempos |

---

## 4. Configuração

Dois arquivos, ambos **fora do git**:

```powershell
copy config.example.json config.json
copy .env.example .env
```

### 4.1 `config.json` — comportamento do robô

Obrigatórios: `Url`, `User`, `Password` e as rotinas (`Rotinas` ou `Fluxo`). O resto tem padrão.

**Acesso**

| Chave | Padrão | O que faz |
|---|---|---|
| `Url` | — | Endereço do WebApp do Protheus. |
| `Environment` | `bf` | Ambiente do AppServer (usado se `Setup_ambiente` não estiver no `.env`). |
| `User` / `Password` | — | Usuário e senha do Protheus usados pelo robô. |
| `Browser` | `chromium` | Use **`firefox`** (é o instalado na imagem Docker e o testado). |
| `Headless` | `true` | `false` mostra o navegador. Pode ser trocado na linha de comando (`--headed`/`--headless`). |

**Rotinas** — ver [4.2](#42-rotinas--como-descrever-o-caminho)

| Chave | O que faz |
|---|---|
| `Rotinas` | Lista de rotinas a cronometrar, cada uma com `Nome`, `Menu` e `Acoes` (ver 4.2). |
| `Fluxo` | Formato antigo: uma rotina só, em texto (tudo é clique). Usado apenas se `Rotinas` não existir. |

**Login** (tempos em ms, exceto `TimeOut`)

| Chave | Padrão | O que faz |
|---|---|---|
| `TimeOut` | `30` | Tempo-base **em segundos** (carregar a URL, aparecer a tela de login). |
| `ParametrosTimeout` | `60000` | Espera dos campos da tela "Parâmetros iniciais". |
| `EmpresaTimeout` / `EntrarTimeout` | `15000` | Tela de empresa/filial e botão Entrar dela. |
| `MfaTimeout` | `60000` | Quanto tempo esperar o e-mail com o código MFA desta sessão. |

**Menu**

| Chave | Padrão | O que faz |
|---|---|---|
| `MenuTimeout` | `60000` | Espera do 1º item do menu (logo após o login o menu pode demorar). |
| `MenuStepTimeout` | `15000` | Espera de cada subitem e de uma pasta abrir após o clique. |
| `MenuRetries` | `2` | Quantas vezes clicar num item do menu que não respondeu. |
| `RotinaAbrirTimeout` | `60000` | Espera da rotina abrir depois do clique no menu. |

**Ações dentro da rotina** (Visualizar, Cancelar, ...)

| Chave | Padrão | O que faz |
|---|---|---|
| `PausaAntesAcao` | `2000` | Pausa entre o botão aparecer e o clique (evita clique ignorado). |
| `RotinaTimeout` | `90000` | Espera máxima de cada botão de ação aparecer. |
| `AcaoEfeitoTimeout` | `15000` | Espera da próxima etapa aparecer depois de um clique. |
| `AcaoRetries` | `2` | Quantas vezes clicar de novo numa ação que não teve efeito. |

**Voltar à tela principal e execução**

| Chave | Padrão | O que faz |
|---|---|---|
| `FecharRotinaTimeout` | `10000` | Procura do botão de fechar a rotina (quando o caminho não termina em `sair`). |
| `VoltarTelaTimeout` | `15000` | Espera do menu reaparecer após fechar a rotina. |
| `VoltarTelaTentativas` | `3` | Tentativas de voltar à tela principal. Sem tela principal, as rotinas seguintes ficam como "não executadas". |
| `ExecutionRetries` | `2` | Quantas vezes refazer a execução inteira se o **login** falhar (não vale para licença esgotada). |
| `MapaCalorDias` | `60` | Período de histórico lido do banco para o mapa de calor. |

> Chaves antigas como `Language`, `DebugLog`, `POUILogin`, `LogFolder`, `LogFile` e `StartProgram` podem continuar no arquivo, mas **não são usadas**.

### 4.2 Rotinas — como descrever o que o robô faz

Cada rotina tem duas partes, separadas:

- **`Menu`** — o caminho no menu do Protheus **até abrir a rotina**, com os itens separados por `>`;
- **`Acoes`** — a lista do que fazer **dentro da rotina**, uma ação por linha, na ordem.

```json
"Rotinas": [
    {
        "Nome": "Pedidos de Compra",
        "Menu": "Compras > Movimento > Pedidos de Compra",
        "Acoes": [
            { "clicar": "Visualizar" },
            { "clicar": "Cancelar" },
            { "clicar": "sair" }
        ]
    },
    {
        "Nome": "Documento de entrada",
        "Menu": "Fiscal > Movimento > Documento Entrada",
        "Acoes": [
            { "clicar": "Visualizar" },
            { "clicar": "Cancelar" },
            { "clicar": "sair" }
        ]
    }
]
```

**Tipos de ação:**

| Ação | Exemplo | O que o robô faz |
|---|---|---|
| `clicar` | `{ "clicar": "Visualizar" }` | Espera o botão/texto aparecer, faz a pausa `PausaAntesAcao` e clica. `"sair"` ou `"fechar"` = botão de fechar a tela da rotina. |
| `duplo` | `{ "duplo": "000123" }` | Igual, com clique duplo (ex.: abrir uma linha da lista). |
| `tecla` | `{ "tecla": "Enter" }` | Faz a pausa e aperta a tecla no que estiver em foco. Ex.: `Enter`, `Tab`, `Escape`, `F3`, `F5`, `ArrowDown`, `Control+A`. |
| `esperar` | `{ "esperar": "000123" }` | **Não clica**: espera aparecer na tela um texto que **contenha** esse valor. Serve para medir quanto o Protheus demora a responder (ex.: o resultado de uma pesquisa). |
| `digitar` | — | **Ainda não disponível** (depende de mapear como o WebApp identifica cada campo). Usar dá erro já na leitura do `config.json`. |

Atalho: uma ação pode ser só o texto — `"Visualizar"` é o mesmo que `{ "clicar": "Visualizar" }`.

**Como o robô executa:**

1. **Menu:** cada item é procurado **dentro do item anterior** na árvore do menu (assim `Compras > Movimento` e `Fiscal > Movimento` nunca se confundem). Pastas já abertas não são clicadas. O último item precisa **abrir a rotina** — se ele for uma pasta, ou se a rotina abrir antes do fim do `Menu`, o robô para com uma mensagem dizendo o que corrigir.
2. **Ações:** em ordem. Depois de um `clicar`/`duplo`, o robô confirma que a ação seguinte apareceu; se não apareceu e o botão continua na tela (clique ignorado pelo Protheus), clica de novo (`AcaoRetries`).
3. **Tempo da rotina:** do primeiro clique no menu até a última ação. A volta para a tela principal fica fora.

**Regras:**

1. **Use o texto exato do menu e dos botões, sem o contador.** No menu aparece `Compras (7)` → escreva `Compras`. Maiúsculas/minúsculas e espaços extras não importam.
2. **Termine as ações com `{ "clicar": "sair" }`** para fechar a tela da rotina. Se não terminar, o robô fecha sozinho, fora do tempo medido.
3. **`Nome` é opcional**: é como a rotina aparece no log, no banco e no mapa. Sem ele, vale o último item do `Menu` (ex.: `Pedidos de Compra`). **Não troque o nome de uma rotina já em uso** — o histórico (e o "normal" do mapa de calor) é agrupado por nome.
4. **Use só rotinas de consulta** (visualizar, pesquisar, cancelar). O robô roda de hora em hora em produção: nunca coloque ações que confirmem, salvem, incluam ou excluam dados.
5. A ordem da lista é a ordem de execução. `Acoes` pode ficar vazio: o robô só abre a rotina e fecha.

**Formato antigo (continua funcionando):** um caminho único em texto, em que tudo é clique e o robô descobre sozinho onde o menu termina:

```json
{ "Nome": "Documento de entrada", "Fluxo": "Fiscal > Movimento > Documento Entrada > Visualizar > Cancelar > sair" }
```

Para testar uma rotina sem editar o `config.json`, passe-a na linha de comando nesse formato (tem prioridade sobre o arquivo; repita `--fluxo` para várias):

```powershell
python main_playwright.py --headed --fluxo "Compras > Movimento > Pedidos de Compra > Visualizar > Cancelar > sair"
```

### 4.3 `.env` — segredos e dados do ambiente

| Variável | Obrigatória | O que faz |
|---|:-:|---|
| `Setup_rotina` | | Programa inicial do WebApp. Padrão `SIGAADV`. |
| `Setup_ambiente` | | Ambiente. Padrão: `Environment` do `config.json`. |
| `Setup_emp` / `Setup_filial` | | Grupo de empresas e filial. **Sem `Setup_emp` a tela de empresa/filial é pulada.** |
| `GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID`, `GRAPH_CLIENT_SECRET` | ✔ (se houver MFA) | Aplicativo do Azure AD usado para ler o e-mail. |
| `BOT_USER_EMAIL` | ✔ (se houver MFA) | Caixa que recebe o código MFA do usuário do robô. |
| `FOLDER_ID` | ✔ (se houver MFA) | Pasta do e-mail: `inbox` ou o ID de uma pasta. |
| `usernamedb`, `passworddb` | ✔ | Usuário e senha do Oracle. |
| `dsn` | ✔ | Conexão Oracle: `host:porta/service_name`. |
| `usuario` | | Valor gravado na coluna `USUARIO` do `RPA.TIMER`. |
| `server_smtp`, `port`, `sender_mail`, `password` | | E-mails de alerta (porta padrão `587`, STARTTLS). |

O código MFA só é aceito se o e-mail chegou **depois** do clique em "Entrar" da sessão atual — assim o robô nunca usa um código antigo.

---

## 5. Como executar

### 5.1 No Windows (desenvolvimento e testes)

```powershell
cd C:\scripts_python\playwright_timer
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install firefox

copy config.example.json config.json   # e preencha
copy .env.example .env                 # e preencha

python main_playwright.py --headed     # mostra o navegador (bom para acompanhar)
python main_playwright.py --headless   # sem janela
```

O TOTVS WebAgent precisa estar instalado e aberto no Windows (o mesmo que você usa para abrir o Protheus no navegador).

Para regerar o mapa de calor sem rodar o robô:

```powershell
python mapa_calor.py log_playwright               # lê o banco (últimos 60 dias)
python mapa_calor.py log_playwright --dias 90
python mapa_calor.py log_playwright --fonte arquivos   # só os JSONs locais
```

### 5.2 No Docker (produção)

O container sobe o **TOTVS WebAgent** num display virtual (Xvfb), espera a porta `21021`, roda **uma execução** e se encerra. `config.json` e `.env` entram por volume (não vão para a imagem).

```bash
# gerar e publicar a imagem
docker build --no-cache -t hiannyurt/playwright-webagent:latest .
docker push hiannyurt/playwright-webagent:latest

# rodar no servidor Linux
docker run --pull always --rm -it -e TZ=America/Cuiaba \
  -v /etc/localtime:/etc/localtime:ro \
  -v /python_bf/playwright_timer/config.json:/app/config.json:ro \
  -v /python_bf/playwright_timer/.env:/app/.env:ro \
  -v /python_bf/playwright_timer/idExec.json:/app/idExec.json \
  -v /python_bf/playwright_timer/log_playwright:/app/log_playwright \
  hiannyurt/playwright-webagent:latest
```

No Windows (PowerShell), troque os caminhos dos volumes, por exemplo `-v "C:\scripts_python\playwright_timer\config.json:/app/config.json:ro"`.

- O volume de `log_playwright` preserva logs, prints, JSONs e o `mapa_calor.html` no servidor.
- O volume de `idExec.json` mantém o contador de `ID_EXECUCAO` entre execuções.
- **Atualizar o WebAgent:** coloque o novo `.deb`/`.rpm` em `webagent/` e refaça o `docker build`.
- **Mudou código Python?** Refaça o `docker build`. **Mudou só rotinas/config?** Não precisa: o `config.json` vem do volume.

### 5.3 Agendamento (Airflow)

`BF_DockerPlaywright.py` é a DAG `Docker_playwright`: roda **de hora em hora, no minuto 30**, chamando `run_docker.py`, que executa o container acima. `max_active_runs = 1` impede duas execuções ao mesmo tempo.

---

## 6. Onde ver os resultados

| Onde | O que tem |
|---|---|
| **Mapa de calor (serviço)** — `http://<servidor>:8010` | Visão principal: histórico completo lido do banco e **passo a passo de cada execução**. Ver [seção 7](#7-mapa-de-calor). |
| Mapa de calor (arquivo) — `log_playwright/mapa_calor.html` | A mesma grade, sem servidor (regerada ao fim de cada execução). |
| Log geral — `log_playwright/<data>_protheus_timer.log` | Toda a execução, com o **resumo dos tempos** no final. |
| Log por item — `log_playwright/rotinas/` | Um arquivo para o login (`r00`) e um por rotina (`r01`, `r02`...). |
| Prints — `log_playwright/*.png` | `fluxo_rNN_EE_<etapa>.png` a cada clique; `timeout_*.png` quando algo não aparece (tirado antes de qualquer Esc). |
| JSON — `log_playwright/tempos_rotinas_<id>.json` | Tempos da execução (usado pelo mapa se o banco cair). |
| Banco — `RPA.TIMER` | Tudo, com o mesmo `ID_EXECUCAO` em todas as linhas de uma execução (ver abaixo). |

### 6.1 O que o robô grava no `RPA.TIMER`

Colunas: `ID_EXECUCAO, DESCRICAO, SISTEMA, USUARIO, ERRO, TEMPO_EXECUCAO, HORARIO_EXECUCAO` (`SISTEMA = 'PROTHEUS'`).

| Quando | `DESCRICAO` (exemplo) | `ERRO` | `TEMPO_EXECUCAO` |
|---|---|---|---|
| Início | `[0000s] - Início do cronômetro Playwright` | `NAO` | 0 |
| **A cada segundo** (cronômetro) | `[0042s] - Rotina 1/2 - Etapa 4/6: procurando 'Visualizar'` | `NAO` | 1 |
| Fim de cada item | `[0278s] - Tempo Login: 70.9s` | `NAO` | segundos do item |
| | `[0345s] - Tempo Rotina 'Pedidos de Compra': 64.5s (com erro: <motivo>)` | `ERRO` | segundos do item |
| Fim da execução | `[0400s] - Finalizando execução` ou `ERRO EM ROTINA(S): ...` | `NAO` / `SIM` | **tempo total** |
| Depois do fim (`LogBanco.salvar`) | uma linha para cada linha do log geral | `NAO` / `WARN` / `ERRO` | 1 |

Consultas úteis:

```sql
-- Tempo de cada item (Login e rotinas), mais recentes primeiro
SELECT ID_EXECUCAO, HORARIO_EXECUCAO, ERRO, TEMPO_EXECUCAO, DESCRICAO
  FROM RPA.TIMER
 WHERE SISTEMA = 'PROTHEUS'
   AND DESCRICAO LIKE '%- Tempo %'
 ORDER BY HORARIO_EXECUCAO DESC;

-- Passo a passo de uma execução (filtre também pelo horário: o mesmo ID pode se repetir, ver 7.4)
SELECT HORARIO_EXECUCAO, ERRO, DESCRICAO
  FROM RPA.TIMER
 WHERE ID_EXECUCAO = :id
   AND HORARIO_EXECUCAO BETWEEN :inicio AND :inicio + INTERVAL '2' HOUR
 ORDER BY HORARIO_EXECUCAO;
```

---

## 7. Mapa de calor

O mapa de calor responde, de relance: **quanto tempo cada rotina levou em cada execução, e se isso foi normal, lento ou deu erro**.

### 7.1 Como os dados chegam até a tela

```text
 main_playwright.py ──grava──▶ RPA.TIMER ──lê──▶ mapa_calor.py ──▶ api_mapa.py ──▶ navegador
   (robô, a cada               (Oracle)          (regras: normal,    (FastAPI,       (página do
    execução)                                     status, histórico,   cache 30 s)     mapa)
                                                  passo a passo)
```

- **`mapa_calor.py`** tem toda a regra: lê o banco, monta a grade, calcula o "normal" e classifica cada célula, monta o passo a passo de uma execução.
- **`api_mapa.py`** só entrega isso pela web (página + API JSON).
- **`mapa_calor_template.html`** é a página; ela funciona de dois jeitos:
  - **serviço** (recomendado): servida pelo `api_mapa.py`, busca os dados na API a cada carga;
  - **arquivo**: `log_playwright/mapa_calor.html`, com os dados embutidos, gerado pelo robô no fim de cada execução (abre offline). O `mapa_calor_template.html` da raiz é só o modelo — não abra direto.

### 7.2 Como ler a grade

- **Linhas**: `Execução completa` (tempo total), `Login` e cada rotina.
- **Colunas**: cada execução, da mais antiga (esquerda) para a mais recente (direita).
- **Célula**: o tempo daquele item naquela execução, com cor **e** ícone:

| Ícone | Status | Quando |
|---|---|---|
| ✓ | Normal | até **+15%** do normal da rotina |
| ▲ | Atenção | de +15% a **+30%** |
| ▲▲ | Lenta | acima de +30% (mesmo limite do e-mail de lentidão) |
| ✕ | Erro | o item falhou |
| – | Não executada | a rotina não rodou (ex.: não deu para voltar à tela principal) |
| · | Sem histórico | menos de 3 execuções anteriores para comparar |
| `~` antes do valor | Estimado | execução de uma versão antiga do robô (ver 7.3) |

**O "normal"** de cada célula é a **mediana das 10 execuções anteriores sem erro daquela mesma rotina**. A mediana não se deixa levar por um dia atípico (a média se deixaria), e só execuções *anteriores* entram — assim cada célula é comparada com o que era normal **naquele momento**. Se uma rotina fica lenta por vários dias seguidos, o novo tempo passa a ser o normal depois de umas 6 execuções (mais da metade da janela de 10).

Os limites (`LIMITE_ATENCAO`, `LIMITE_LENTA`, `JANELA_NORMAL`, `MINIMO_HISTORICO`) ficam no topo de `mapa_calor.py`.

**Na página:** cartões com o resumo da última execução; filtros de **Histórico** (7 dias a 2 anos, lido do banco), **Período** (últimas N execuções), nome da rotina e "só rotinas com problema"; valor em segundos ou em % do normal; visão em tabela; **Atualizar sozinho** (busca o banco de novo a cada minuto, sem recarregar).

### 7.3 Histórico: todas as versões do robô

O robô já gravou no `RPA.TIMER` em formatos diferentes. O mapa entende todos:

| Período | Versão | O que existe no banco | O que aparece no mapa |
|---|---|---|---|
| até 10/09/2026 | Robô TIR (Selenium) | `INICIANDO/FINALIZANDO PROTHEUS_TIR` com o tempo total | Só **Execução completa** |
| 11/09 a 17/11/2026 | Playwright antigo | registros por segundo + linha final com o total | Execução completa + **Login e rotina estimados** (`~`) |
| desde 18/11/2026 | Versão atual | + linhas `Tempo Login` / `Tempo Rotina` | Execução completa + Login e cada rotina **exatos** |

- **Execução completa** vem da linha de encerramento (o maior `TEMPO_EXECUCAO` da execução) e existe em todas as versões: é a série histórica mais longa.
- **Estimativa do Playwright antigo:** o Login vai do início até o registro `Acessando menu lateral: ...`; a rotina vai daí até o fim (por isso inclui também o fechamento da tela — pode ficar alguns segundos acima da medição atual). O nome vem do caminho (`... > Pedidos de Compra`), então o histórico antigo cai **na mesma linha** da rotina atual de mesmo nome.
- O histórico é **resumido dentro do Oracle** (`SQL_RESUMO_EXECUCOES`: uma linha por execução). Mesmo com 2 anos, o serviço não puxa os milhares de registros por segundo.

### 7.4 Como uma execução é identificada (ID + sessão)

O `ID_EXECUCAO` vem de um contador local (`idExec.json`). **O servidor e o PC de teste têm contadores separados**, então o mesmo número aparece em execuções diferentes (ex.: o mesmo número usado em setembro pelo servidor e hoje pelo PC).

Por isso o mapa identifica uma execução por **ID + sessão**: linhas com o mesmo ID separadas por **mais de 30 minutos sem registros** são de execuções diferentes (`INTERVALO_NOVA_EXECUCAO_MIN` em `mapa_calor.py`). Isso vale para a grade (o SQL usa `LAG` para detectar o intervalo) e para o detalhe, que é buscado pelo **ID + horário de início** da coluna.

### 7.5 Detalhe de uma execução (clique numa célula — modo serviço)

O painel mostra:

1. **Tempos** de todos os itens daquela execução (clique num deles para trocar o passo a passo).
2. **Passo a passo** do item escolhido — *onde o tempo foi gasto*:

   ```text
   08:30:29  Rotina 1/2 - Etapa 3/6: procurando 'Pedidos de Compra' no menu   ▇▇          5s
   08:30:34  Rotina 1/2 - Etapa 4/6: procurando 'Visualizar'                  ▇▇▇▇▇▇▇▇▇  22s  ← mais demorado
   08:30:56  Rotina 1/2 - Etapa 5/6: procurando 'Cancelar'                    ▇            3s
   ```

   Ele é montado com os registros que o cronômetro grava **a cada segundo** (`ERRO = 'NAO'`, `TEMPO_EXECUCAO = 1`): segundos seguidos com o mesmo texto viram um passo, e a duração do passo vai do **primeiro ao último registro dele** (um buraco nos dados nunca vira duração). Uma linha de log que cai no meio de um passo que continua é juntada a ele.
3. **Avisos e erros** do log daquela execução (`ERRO = 'WARN'`/`'ERRO'`), ex.: clique repetido, timeout.

Em execuções do TIR não há registros por segundo, então só aparecem os tempos.

### 7.6 Subir o serviço

**No Windows (para testar):**

```powershell
pip install -r requirements.txt   # se ainda não instalou
python api_mapa.py                 # http://localhost:8010
# ou: uvicorn api_mapa:app --host 0.0.0.0 --port 8010
```

O serviço não recarrega o código sozinho: depois de atualizar os arquivos, pare (Ctrl+C) e rode de novo.

**No servidor (Docker, sempre no ar)** — imagem leve e separada da do robô: o `Dockerfile.mapa` instala só FastAPI, Uvicorn, `oracledb` e `python-dotenv`, nas versões do `requirements.txt`:

```bash
docker build -f Dockerfile.mapa -t hiannyurt/protheus-timer-mapa:latest .
docker run -d --name mapa-protheus --restart unless-stopped -p 8010:8010 \
  -v /python_bf/playwright_timer/.env:/app/.env:ro \
  -v /etc/localtime:/etc/localtime:ro \
  hiannyurt/protheus-timer-mapa:latest
```

| Endereço | O que entrega |
|---|---|
| `/` | A página do mapa de calor |
| `/api/mapa?dias=60` | A grade já classificada (JSON) |
| `/api/execucoes/{id}?inicio=AAAA-MM-DDTHH:MM:SS` | O detalhe da execução que começou nesse horário (JSON) |
| `/health` | `{"status": "ok"}` — para monitoramento (o Docker usa no healthcheck) |
| `/docs` | Documentação interativa da API (Swagger) |

- Usa só `usernamedb`, `passworddb` e `dsn` do `.env`, e só **lê** o `RPA.TIMER`. O `oracledb` roda no modo *thin* (sem Oracle Instant Client; exige Oracle 12.1+).
- Variáveis opcionais: `MAPA_PORTA` (8010), `MAPA_HOST` (0.0.0.0), `MAPA_CACHE_SEGUNDOS` (30 — várias telas abertas não consultam o Oracle a cada segundo).
- Banco fora do ar → a página mostra o aviso e a API responde `503` com o motivo.
- O serviço **não tem login**: deixe a porta só na rede interna (ou atrás de um proxy com autenticação).

**Arquivo sem servidor:** `python mapa_calor.py log_playwright [--dias 90] [--fonte arquivos]` gera o `log_playwright/mapa_calor.html` (o robô já faz isso no fim de cada execução; `--fonte arquivos` usa só os JSONs locais).

---

## 8. Erros e alertas

| Situação | O que o robô faz | Alerta |
|---|---|---|
| URL do Protheus não responde | Encerra a execução | E-mail "Base do Protheus indisponível" |
| Falha no login (Playwright) | Refaz a execução inteira até `ExecutionRetries` vezes | E-mail de falha ao esgotar |
| Janela **"Excedeu numero de licenças"** | Reconhece a tela, registra `ERRO LICENCA PROTHEUS` e **não** tenta de novo (não libera licença) | E-mail "Licenças do Protheus esgotadas" |
| Item do menu ou ação não responde ao clique | Clica de novo (`MenuRetries` / `AcaoRetries`); depois marca a rotina com erro | — |
| Rotina com erro | Marca a rotina, fecha a tela e **segue para a próxima** | E-mail de falha |
| Não consegue voltar à tela principal | Rotinas restantes ficam "NAO EXECUTADA" | — |
| Execução sem erro, mas > 30% acima da média das últimas 10 | Conclui normalmente | E-mail "Tempo acima da média" |

Os destinatários de cada e-mail estão em `MandaEmail.py`.

---

## 9. Testes

Os testes não precisam de Protheus, Oracle nem navegador:

```powershell
python -m unittest discover -s tests -v
```

| Arquivo | Cobre |
|---|---|
| `tests/test_rotinas.py` | Leitura das rotinas (`Rotinas`, `Fluxo`, `--fluxo`) e relatório JSON |
| `tests/test_erros_protheus.py` | Reconhecimento da mensagem de licenças |
| `tests/test_mapa_calor.py` | Classificação normal/atenção/lenta/erro, leitura das linhas do banco, detalhe da execução e fallback |
| `tests/test_api_mapa.py` | Rotas do serviço com o banco simulado (pulado se `fastapi`/`httpx` não estiverem instalados — `pip install -r requirements.txt`) |
| `tests/linhas_exemplo.py` | Gera linhas no mesmo formato que o robô grava no `RPA.TIMER` (usado pelos testes) |

Para testar uma rotina nova de ponta a ponta, rode com `--headed` e `--fluxo` (ver 4.2) e confira os prints `fluxo_*.png`.

---

## 10. Diagnóstico rápido

| Sintoma no log | Causa provável / o que fazer |
|---|---|
| `O Menu terminou em 'X', que é uma pasta` | O último item do `Menu` precisa ser a rotina; complete o caminho. |
| `'X' já abriu a rotina; 'Y' deve ir em Acoes` | O `Menu` tem itens demais: mova os itens depois da rotina para `Acoes`. |
| `Rotina #N, ação #M: ...` (ao iniciar) | Erro de digitação no `config.json` (ação desconhecida, tecla inválida, `digitar`...). A mensagem diz o que corrigir. |
| `'X' não apareceu na tela dentro de Nms` | Uma ação `esperar` não encontrou o texto: a tela não respondeu ou o texto está diferente. Veja o `timeout_*.png`. |
| `Item 'X' não encontrado dentro de 'Y'` | O texto da etapa não bate com o menu (confira no Protheus, sem o contador) ou o usuário não tem acesso à rotina. |
| `Item 'X' do menu foi clicado 2x, mas a rotina não abriu` | Protheus lento ou travado; aumente `RotinaAbrirTimeout` / `MenuRetries`. |
| `Clique em 'Visualizar' não teve efeito ... clicando de novo` | Tela ainda carregando; o robô se recupera. Se for frequente, aumente `PausaAntesAcao`. |
| `Elemento 'X' não foi encontrado dentro de Nms` | O botão não apareceu. Veja o `timeout_X_*.png` para saber em que tela o robô estava. |
| `Codigo de MFA ... vazio ou inválido` | E-mail não chegou em `MfaTimeout`, ou credenciais/permissão do Graph, `BOT_USER_EMAIL` ou `FOLDER_ID` errados. |
| `(535, '5.7.3 Authentication unsuccessful')` | Senha do `sender_mail` no `.env` recusada pelo SMTP (expirada ou trocada). |
| Página do serviço mostra `Não foi possível carregar os dados: Banco indisponível ...` | O servidor do serviço não alcança o Oracle ou o `.env` montado está errado. |
| `Mapa de calor: banco indisponível ...; usando os JSONs locais` | Sem acesso ao Oracle naquela máquina; o mapa sai só com as execuções locais. |
| Container parado em `Aguardando TOTVS WebAgent subir na porta 21021` | WebAgent não iniciou: confira o `.deb` em `webagent/` e refaça o build. |
| `app (exit status 0; expected)` no Docker | Não é erro: o Python terminou normalmente e o container vai encerrar. |

Para ver também as linhas técnicas (cada `INSERT` no banco, tracebacks completos), ative o nível `DEBUG` em `logging_config.py`.

---

## 11. Segurança

- `config.json` e `.env` têm senhas: **nunca versione nem coloque na imagem**. Eles estão no `.gitignore` e no `.dockerignore`; use os arquivos `*.example` como modelo.
- Se uma credencial real já foi para o git ou para o Docker Hub, **troque-a**.
- Não passe segredos em `docker build --build-arg` (ficam no histórico da imagem).
- Prefira tags fixas da imagem (ex.: `1.0.0`) em agendamentos; `latest` pode mudar sem aviso.
