import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv
from html import escape
import os
from pathlib import Path
import logging_config

logger = logging_config.get_logger('MandaEmail')

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent

with (BASE_DIR / 'config.json').open('r', encoding='utf-8') as file:
    dados = json.load(file)

url = dados["Url"]
User = dados.get("User", "Não informado")
Environment = dados.get("Environment", "Não informado")

destinatarios = ["WILSON.PALHARES@bomfuturo.com.br", "hianny.urt@bomfuturo.com.br",
                 "MARCEL.RODRIGUES@bomfuturo.com.br","sherman.vendramini@bomfuturo.com.br"]

#destinatarios = ["hianny.urt@bomfuturo.com.br"]

def enviar_warning(DESC):

    try:
        server_smtp = os.getenv('server_smtp')
        port = int(os.getenv('port', '587'))
        sender_mail = os.getenv('sender_mail')
        password = os.getenv('password')

        subject = "Alerta de falha na execução do RPA Playwright Timer"
        body = f"""\
        <h2>Alerta de falha na execução</h2>
        <p>Foi identificado um aviso de falha durante a execução do RPA Playwright Timer.</p>
        <h3>Detalhes da execução</h3>
        <ul>
            <li><strong>Mensagem registrada:</strong> {escape(str(DESC))}</li>
            <li><strong>Usuário do Protheus:</strong> {escape(str(User))}</li>
            <li><strong>Ambiente:</strong> {escape(str(Environment))}</li>
            <li><strong>URL:</strong> {escape(str(url))}</li>
        </ul>
        <h3>Possíveis causas</h3>
        <ul>
            <li>O código MFA não foi recebido ou não pôde ser validado.</li>
            <li>A senha do usuário pode estar expirada ou inválida.</li>
            <li>A base do Protheus pode estar indisponível.</li>
            <li>Algum elemento esperado da tela pode ter sido alterado.</li>
        </ul>
        <p><strong>Ação recomendada:</strong> verificar os logs da execução, a disponibilidade da URL e as credenciais do usuário antes de executar o RPA novamente.</p>"""

        message = MIMEMultipart()
        message["From"] = sender_mail
        message["To"] = ", ".join(destinatarios)  # Concatena os destinatários em uma string separada por vírgulas
        message["Subject"] = subject
        message.attach(MIMEText(body, "html"))

        server = smtplib.SMTP(server_smtp, port)
        server.starttls()
        server.login(sender_mail, password)
        server.sendmail(sender_mail, destinatarios, message.as_string())  # Passa a lista de destinatários
        logger.info('Email warning enviado com sucesso')

        server.quit()
    except Exception as e:
        logger.error(f'Ocorreu um erro ao enviar email warning: {e}')

    return

def enviar_erro():
    try:
        server_smtp = os.getenv('server_smtp')
        port = int(os.getenv('port', '587'))
        sender_mail = os.getenv('sender_mail')
        password = os.getenv('password')

        subject = "Erro na execução do RPA Playwright Timer"
        body = f"""\
        <h2>Erro durante a execução</h2>
        <p>O RPA Playwright Timer encontrou um erro durante o processamento.</p>
        <h3>Detalhes da execução</h3>
        <ul>
            <li><strong>Usuário do Protheus:</strong> {escape(str(User))}</li>
            <li><strong>Ambiente:</strong> {escape(str(Environment))}</li>
            <li><strong>URL:</strong> {escape(str(url))}</li>
        </ul>
        <p>O impacto da falha deve ser confirmado no registro da execução no banco de dados.</p>
        <p><strong>Ação recomendada:</strong> consultar os logs da aplicação e verificar se a execução foi registrada como concluída na tabela de controle antes de iniciar uma nova tentativa.</p>"""


        message = MIMEMultipart()
        message["From"] = sender_mail
        message["To"] = ", ".join(destinatarios)  # Concatena os destinatários em uma string separada por vírgulas
        message["Subject"] = subject
        message.attach(MIMEText(body, "html"))

        server = smtplib.SMTP(server_smtp, port)
        server.starttls()
        server.login(sender_mail, password)
        server.sendmail(sender_mail, destinatarios, message.as_string())  # Passa a lista de destinatários
        logger.info('Email erro enviado com sucesso')

        server.quit()
    except Exception as e:
        logger.error(f'Ocorreu um erro ao enviar email erro: {e}')
    return

def enviar_temp_exc(tempo_executado, media_resultado):
    try:
        server_smtp = os.getenv('server_smtp')
        port = int(os.getenv('port', '587'))
        sender_mail = os.getenv('sender_mail')
        password = os.getenv('password')

        subject = "Tempo de execução acima da média no RPA Playwright Timer"
        body = f"""\
        <h2>Tempo de execução acima do esperado</h2>
        <p>A execução atual ultrapassou em mais de 30% a média das últimas execuções bem-sucedidas.</p>
        <h3>Indicadores</h3>
        <ul>
            <li><strong>Tempo da execução atual:</strong> {tempo_executado} segundos</li>
            <li><strong>Média das últimas execuções:</strong> {media_resultado:.2f} segundos</li>
            <li><strong>Limite de alerta:</strong> {media_resultado * 1.3:.2f} segundos</li>
            <li><strong>Usuário do Protheus:</strong> {escape(str(User))}</li>
            <li><strong>Ambiente:</strong> {escape(str(Environment))}</li>
            <li><strong>URL:</strong> {escape(str(url))}</li>
        </ul>
        <p><strong>Ação recomendada:</strong> analisar os logs para identificar etapas lentas, indisponibilidade da base, lentidão de rede ou demora na autenticação/MFA.</p>"""


        message = MIMEMultipart()
        message["From"] = sender_mail
        message["To"] = ", ".join(destinatarios)  # Concatena os destinatários em uma string separada por vírgulas
        message["Subject"] = subject
        message.attach(MIMEText(body, "html"))

        server = smtplib.SMTP(server_smtp, port)
        server.starttls()
        server.login(sender_mail, password)
        server.sendmail(sender_mail, destinatarios, message.as_string())  # Passa a lista de destinatários
        logger.info('Email tempo execução enviado com sucesso')

        server.quit()
    except Exception as e:
        logger.error(f'Ocorreu um erro ao enviar email tempo: {e}')
    return


def enviar_erro_base_selenium():
    try:
        server_smtp = os.getenv('server_smtp')
        port = int(os.getenv('port', '587'))
        sender_mail = os.getenv('sender_mail')
        password = os.getenv('password')

        subject = "Base do Protheus indisponível para o RPA Playwright Timer"
        body = f"""\
        <h2>Não foi possível acessar a base do Protheus</h2>
        <p>O RPA Playwright Timer não conseguiu acessar a URL configurada.</p>
        <h3>Detalhes da conexão</h3>
        <ul>
            <li><strong>URL:</strong> {escape(str(url))}</li>
            <li><strong>Ambiente:</strong> {escape(str(Environment))}</li>
            <li><strong>Usuário do Protheus:</strong> {escape(str(User))}</li>
        </ul>
        <p><strong>Ação recomendada:</strong> verificar se o serviço do Protheus está ativo, se a URL está acessível a partir do container e se há conectividade de rede.</p>"""

        message = MIMEMultipart()
        message["From"] = sender_mail
        message["To"] = ", ".join(destinatarios)  # Concatena os destinatários em uma string separada por vírgulas
        message["Subject"] = subject
        message.attach(MIMEText(body, "html"))

        server = smtplib.SMTP(server_smtp, port)
        server.starttls()
        server.login(sender_mail, password)
        server.sendmail(sender_mail, destinatarios, message.as_string())  # Passa a lista de destinatários
        logger.info('Email erro enviado com sucesso')

        server.quit()
    except Exception as e:
        logger.error(f'Ocorreu um erro ao enviar email erro: {e}')
    return

if __name__ == "__main__":
    logger.info('Testando funções de email')
    enviar_warning('')
    enviar_erro()
    enviar_temp_exc('','')
    enviar_erro_base_selenium()