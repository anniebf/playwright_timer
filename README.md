# Playwright Timer com TOTVS WebAgent

Imagem Docker para executar uma automacao Playwright no Protheus, usando o TOTVS WebAgent, autenticacao com MFA via Microsoft Graph e registro do andamento da execucao em Oracle.

O container foi desenhado para executar **uma rotina por inicializacao**. Quando o script termina, o Supervisor encerra o WebAgent e o container tambem termina.

## 1. O que a imagem faz

Ao iniciar, o container:

1. Sobe o TOTVS WebAgent dentro de uma sessao D-Bus e de um display virtual Xvfb.
2. Aguarda a porta `21021` do WebAgent ficar disponivel.
3. Inicia `main_playwright.py`.
4. Abre a URL do Protheus usando o navegador configurado.
5. Preenche a rotina e o ambiente iniciais.
6. Faz login no Protheus.
7. Procura o codigo MFA recente no Microsoft Graph, quando a tela de MFA aparece.
8. Preenche a empresa/filial, se `Setup_emp` estiver definido.
9. Executa o menu configurado no codigo:

   `Compras > Movimento > Pedidos de Compra`

10. Executa a validacao `Visualizar` e `Cancelar`.
11. Fecha o navegador, grava o encerramento no banco e encerra o container.

## 2. Pre-requisitos

- Docker Desktop ou Docker Engine com suporte a Linux containers.
- Acesso de rede a:
  - URL do Protheus;
  - Microsoft Graph, se o MFA for usado;
  - banco Oracle;
  - servidor SMTP, se os alertas por e-mail forem usados.
- Um arquivo `config.json` valido.
- Um arquivo `.env` com os segredos e parametros de ambiente.
- Permissao no Microsoft Graph para ler a caixa de e-mail usada pelo MFA.
- O usuario do banco deve ter permissao para inserir e consultar `RPA.TIMER`.

## 3. Estrutura esperada

No host, mantenha pelo menos:

```text
playwright_timer/
|-- config.json
|-- .env
|-- Dockerfile
|-- main_playwright.py
|-- graph.py
|-- LogBanco.py
|-- MandaEmail.py
|-- logging_config.py
|-- requirements.txt
`-- webagent/
    `-- linux-x64-release.deb
```

`config.json` e `.env` sao montados no container por volume. Eles nao precisam ser publicados dentro da imagem.

## 4. Configuracao do `config.json`

Crie o arquivo com esta estrutura. Substitua os valores pelos dados do ambiente:

```json
{
  "Url": "https://protheus.exemplo.com.br/webapp/",
  "Browser": "firefox",
  "Environment": "bf",
  "Language": "pt-br",
  "User": "usuario_do_protheus",
  "Password": "senha_do_protheus",
  "DebugLog": false,
  "TimeOut": 30,
  "Headless": true,
  "POUILogin": true,
  "LogFolder": "Log",
  "LogFile": true,
  "StartProgram": true
}
```

### Parametros do `config.json`

| Parametro | Tipo | Obrigatorio | Funcao |
|---|---|---:|---|
| `Url` | string | Sim | URL de entrada do Protheus. Sem ela o programa encerra na inicializacao. |
| `Browser` | string | Nao | Navegador Playwright. A imagem instala Firefox. O padrao no codigo e `chromium`, mas use `firefox` nesta imagem. |
| `Environment` | string | Nao | Ambiente inicial do Protheus. Padrao: `bf`. |
| `Language` | string | Nao | Mantido no arquivo de configuracao; o fluxo atual nao usa esse valor diretamente. |
| `User` | string | Sim | Usuario usado no formulario de login do Protheus. |
| `Password` | string | Sim | Senha usada no formulario de login do Protheus. |
| `DebugLog` | booleano | Nao | Mantido para compatibilidade; o fluxo atual nao usa esse valor diretamente. |
| `TimeOut` | inteiro | Nao | Timeout base em segundos. O codigo converte esse valor para milissegundos. Padrao: `30`. |
| `Headless` | booleano | Nao | Define se o navegador abre sem interface. Padrao: `true`. |
| `POUILogin` | booleano | Nao | Mantido para compatibilidade; nao controla diretamente o fluxo atual. |
| `LogFolder` | string | Nao | Mantido para compatibilidade; os logs atuais sao gravados em `log_playwright`. |
| `LogFile` | booleano | Nao | Mantido para compatibilidade; o logging atual sempre cria arquivo e console. |
| `StartProgram` | booleano | Nao | Mantido para compatibilidade; nao controla diretamente o fluxo atual. |

O codigo aceita valores booleanos reais no JSON, por exemplo `true` e `false`. Para `Headless`, tambem sao reconhecidos textos como `true`, `false`, `1`, `0`, `yes` e `no`.

## 5. Variaveis do arquivo `.env`

Crie o `.env` no host. Nunca publique esse arquivo no Git ou no registry.

### 5.1 Parametros opcionais do Protheus

```dotenv
Setup_rotina=SIGAADV
Setup_ambiente=bf
Setup_emp=01
```

- `Setup_rotina`: rotina inicial. Se ausente, usa `SIGAADV`.
- `Setup_ambiente`: ambiente inicial. Se ausente, usa `Environment` do `config.json` ou `bf`.
- `Setup_emp`: empresa/grupo/filial. Se ausente, a tela de empresa/filial e ignorada.

### 5.2 Microsoft Graph e MFA

```dotenv
GRAPH_CLIENT_ID=seu-client-id
GRAPH_TENANT_ID=seu-tenant-id
GRAPH_CLIENT_SECRET=seu-client-secret
BOT_USER_EMAIL=caixa.mfa@exemplo.com
FOLDER_ID=inbox
```

Esses valores sao usados para obter um token de aplicacao e consultar a pasta de e-mail indicada.

O fluxo procura uma mensagem recebida nos ultimos cinco minutos e extrai somente os digitos encontrados em uma `div` cuja classe contenha `code`, como `x_code`.

O aplicativo do Microsoft Graph precisa ter permissao de aplicacao para leitura de e-mails e consentimento administrativo, conforme a politica do tenant.

### 5.3 Oracle

```dotenv
usernamedb=usuario_oracle
passworddb=senha_oracle
dsnhomol=host:1521/service_name
usuario=usuario_responsavel
```

O modulo `LogBanco.py` usa esses valores para:

- registrar inicio da execucao;
- registrar cada atualizacao do cronometro;
- registrar erros e avisos;
- registrar o fechamento do navegador;
- consultar o ultimo tempo de execucao para avaliar duracao elevada.

A tabela esperada e `RPA.TIMER`, com as colunas usadas pelo codigo:

`ID_EXECUCAO`, `DESCRICAO`, `SISTEMA`, `USUARIO`, `ERRO`, `TEMPO_EXECUCAO` e `HORARIO_EXECUCAO`.

### 5.4 SMTP

```dotenv
server_smtp=smtp.exemplo.com.br
port=587
sender_mail=robot@exemplo.com.br
password=senha_smtp
```

Os dados SMTP sao usados para enviar alertas quando:

- a URL do Protheus nao responde;
- ocorre erro no fluxo;
- aparece uma linha `FAILED` durante o processamento de logs;
- o tempo da rotina ultrapassa o limite comparado com a ultima execucao.

## 6. Construir a imagem localmente

Execute na pasta que contem o `Dockerfile`:

```powershell
cd C:\scripts_python\playwright_timer

docker build -t playwright-webagent:latest .
```

O build instala:

- Python 3.12;
- dependencias de `requirements.txt`;
- Firefox do Playwright;
- bibliotecas Linux necessarias ao navegador;
- Supervisor;
- Xvfb e D-Bus;
- pacote `webagent/linux-x64-release.deb`.

O arquivo `.dockerignore` impede que `config.json` e `.env` sejam enviados para o contexto do build.

## 7. Executar a imagem
No Windows PowerShell:

```powershell
Esse exemplo preserva os logs. As screenshots sao gravadas na raiz de trabalho `/app`; para preserva-las, use um container sem `--rm`, copie os arquivos com `docker cp` antes de remove-lo e depois execute `docker rm`. Evite montar todo `/app` sobre a imagem, pois isso pode esconder os arquivos do projeto.
  -v "C:\scripts_python\playwright_timer\config.json:/app/config.json:ro" `
  -v "C:\scripts_python\playwright_timer\.env:/app/.env:ro" `
  hiannyurt/playwright-webagent:latest
```

No Linux:

```bash
docker run --rm -it \
  -v "$PWD/config.json:/app/config.json:ro" \
  -v "$PWD/.env:/app/.env:ro" \
  hiannyurt/playwright-webagent:1.0.0
```

### Por que usar os volumes

O script procura os arquivos em:

```text
/app/config.json
/app/.env
```

O sufixo `:ro` monta os arquivos como somente leitura. Assim, as credenciais ficam no host e nao sao gravadas na imagem.

Se o container reclamar que `config.json` nao existe, confira se o caminho do host esta correto. O mesmo vale para `.env`.

## 8. Publicar em um registry

### Docker Hub

1. Autentique-se:

```powershell
docker login
```

2. Gere a imagem, se necessario:

```powershell
docker build -t playwright-webagent:latest .
```

3. Crie uma tag com seu usuario:

```powershell
docker tag playwright-webagent:latest SEU_USUARIO/playwright-webagent:1.0.0
docker tag playwright-webagent:latest SEU_USUARIO/playwright-webagent:latest
```

4. Publique as tags:

```powershell
docker push SEU_USUARIO/playwright-webagent:1.0.0
docker push SEU_USUARIO/playwright-webagent:latest
```

5. Em outro servidor, execute com a imagem publicada:

```powershell
docker pull SEU_USUARIO/playwright-webagent:1.0.0
docker run --rm -it `
  -v "C:\caminho\config.json:/app/config.json:ro" `
  -v "C:\caminho\.env:/app/.env:ro" `
  SEU_USUARIO/playwright-webagent:1.0.0
```

Prefira tags fixas, como `1.0.0`, em processos agendados. A tag `latest` pode mudar sem aviso.

## 9. Funcionamento do encerramento

O `supervisord` e o processo principal do container. Ele gerencia dois programas:

- `webagent`: executado com `dbus-run-session` e `xvfb-run`;
- `app`: aguarda a porta `21021` e executa o Python.

Quando `main_playwright.py` termina, o comando do `app` salva o status, envia `SIGTERM` para o processo 1 (`supervisord`) e retorna o status original. O Supervisor encerra o WebAgent e o Docker finaliza o container.

Por isso, ao usar `--rm`, o container e removido depois da execucao.

## 10. Logs e arquivos gerados

Os logs de aplicacao sao criados em:

```text
/app/log_playwright/
```

O fluxo tambem pode gerar screenshots no diretorio de trabalho do container, por exemplo:

```text
tela_login.png
Visualizar_tentativa_1.png
Cancelar.png
sair.png
```

Como o container e removido com `--rm`, esses arquivos desaparecem ao final se nao forem montados em um diretorio do host. Para preserva-los:

```powershell
docker run --rm -it `
  -v "C:\scripts_python\playwright_timer\config.json:/app/config.json:ro" `
  -v "C:\scripts_python\playwright_timer\.env:/app/.env:ro" `
  -v "C:\scripts_python\playwright_timer\saida:/app/log_playwright" `
  -v "C:\scripts_python\playwright_timer\screenshots:/app" `
  playwright-webagent:latest
```

Evite montar todo `/app` sobre a imagem, pois isso pode esconder os arquivos do projeto. Prefira montar somente pastas de saida especificas.

## 11. Execucao manual fora do Supervisor

Para testar o script diretamente em um ambiente Python que ja tenha as dependencias instaladas:

```powershell
python main_playwright.py --headless
```

Para abrir o navegador com interface:

```powershell
python main_playwright.py --headed
```

Esses argumentos alteram o modo do navegador. No container, o comando padrao do Supervisor nao passa argumentos extras; o modo usado normalmente vem de `Headless` no `config.json`.

## 12. Diagnostico rapido

### `app (exit status 0; expected)`

Essa mensagem significa que o Python terminou normalmente. Ela nao e um erro. O container deve finalizar em seguida quando o Supervisor encerra o WebAgent.

### O container fica aguardando a porta `21021`

Verifique:

- se `webagent/linux-x64-release.deb` esta presente antes do build;
- se o WebAgent iniciou sem erro;
- se a imagem foi reconstruida depois de alterar o Dockerfile;
- se a porta `21021` esta sendo aberta pelo WebAgent dentro do container.

### Erro ao carregar `config.json`

Confira o volume e o arquivo no host:

```powershell
test-path C:\scripts_python\playwright_timer\config.json
```

O arquivo precisa ser JSON valido e conter pelo menos `Url`, `User` e `Password`.

### MFA nao encontrado

Confira:

- credenciais do Microsoft Graph;
- permissao de leitura da caixa de e-mail;
- `BOT_USER_EMAIL`;
- `FOLDER_ID`;
- horario da mensagem, que precisa estar dentro dos ultimos cinco minutos;
- se o corpo do e-mail possui uma `div` com classe contendo `code`.

### Falha de conexao com Oracle

Confira `usernamedb`, `passworddb` e `dsnhomol`, alem da rota de rede do container ate o banco. A conexao e feita durante a inicializacao do cronometro, portanto o erro pode aparecer antes do login no Protheus.

### E-mail de erro nao foi enviado

Confira os quatro parametros SMTP e a conectividade com o servidor. O envio usa STARTTLS e porta padrao `587` quando `port` nao e informado.

## 13. Seguranca

- Nao publique `config.json` com usuario e senha reais.
- Nao publique `.env`, tokens, client secrets ou senhas SMTP/Oracle.
- Mantenha `config.json` e `.env` fora do Git, usando o `.gitignore`.
- Se credenciais reais ja foram versionadas ou enviadas ao Docker Hub, troque-as imediatamente.
- Use tags versionadas e controle quem pode fazer pull da imagem.
- Evite colocar segredos em argumentos de `docker build`, pois eles podem ficar registrados no historico da imagem.

## 14. Fluxo resumido

```text
Docker
  -> supervisord
      -> WebAgent + Xvfb + D-Bus
      -> aguarda TCP 21021
      -> main_playwright.py
          -> Protheus
          -> Microsoft Graph/MFA
          -> Oracle/RPA.TIMER
          -> SMTP em caso de alerta
      -> fecha navegador
      -> encerra supervisord
  -> container finalizado
```
