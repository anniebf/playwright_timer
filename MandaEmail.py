import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv
import os
from pathlib import Path
import logging_config

logger = logging_config.get_logger('MandaEmail')

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent

with (BASE_DIR / 'config.json').open('r', encoding='utf-8') as file:
    dados = json.load(file)

url = dados["Url"]

#User = dados["User"]
User = "rpa"

Environment = dados["Environment"]
Environment = "protheus"

#destinatarios = ["WILSON.PALHARES@bomfuturo.com.br", "hianny.urt@bomfuturo.com.br",
#                 "MARCEL.RODRIGUES@bomfuturo.com.br","sherman.vendramini@bomfuturo.com.br"]

destinatarios = ["hianny.urt@bomfuturo.com.br"]

def enviar_warning(DESC):

    try:
        server_smtp = os.getenv('server_smtp')
        port = int(os.getenv('port', '587'))
        sender_mail = os.getenv('sender_mail')
        password = os.getenv('password')

        subject = "FALHA no ´Protheus_tir"
        body = f"""\
        <p>FALHA ENCONTRADA NO PROCESSO</p>
        <p>WARNING : {DESC}</p>
        <p>O USUARIO {User} ACESSOU A ACESSAR A BASE {url}</p>
        <p>POSSIVEIS CAUSAS: </p>
        <p>-MFA ativo para o usuario</p>
        <p>-Senha expirada</p>
        <p>-Base fora de ar</p>"""

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

        subject = "ERRO NO DOCKER_TIR"
        body = f"""\
        <p>NO MEIO DO PROCESSO OUVE UM ERRO</p>
        <p>O ERRO NAO AFETA DIRETAMENTE O FUNCIONAMENTE DO RPA</p>
        <p>VERIFIQUE SE O ROBO CONCLUIU A EXECUÇÃO NO BANCO</p>
        <p>A BASE USADO FOI: {url}</p>"""


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

def enviar_temp_exc(tempo_executado,ultimo_resultado):
    try:
        server_smtp = os.getenv('server_smtp')
        port = int(os.getenv('port', '587'))
        sender_mail = os.getenv('sender_mail')
        password = os.getenv('password')

        subject = "TEMPO EXECUÇÃO ELEVADO"
        body = f"""\
        <p>O TEMPO DE EXECUCAO DO ROBO EXCEDEU 40% DO TEMPO DA ULTIMA ROTINA </p>
        <p>TEMPO ATUAL: {tempo_executado}</p>
        <p>TEMPO DA ULTIMA ROTINA: {ultimo_resultado}</p>
        <p>A BASE USADO FOI: {url}</p>"""


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

        subject = "BASE PROTHEUS OFFLINE"
        body = f"""\
        <p>O rpa selenium não consegiu acessar a url abaixo</p>
        <p>URL: {url}</p>
        <p>Verifique se o servico esta online</p>"""

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