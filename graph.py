import msal
import os
import requests
import logging
from bs4 import BeautifulSoup
import re
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
import logging_config

load_dotenv()

# Configuração de Logging usando o módulo centralizado
logger = logging_config.get_logger('graph')



class MicrosoftGraphClient:
    def __init__(self):
        self.client_id = os.getenv('GRAPH_CLIENT_ID')
        self.tenant_id = os.getenv('GRAPH_TENANT_ID')
        self.client_secret = os.getenv('GRAPH_CLIENT_SECRET')
        self.user_email = os.getenv('BOT_USER_EMAIL') # Adicione no seu .env
        self.folder_id = os.getenv('FOLDER_ID')   # ID da pasta ou 'inbox'
        self.token = None

        self.obter_token()

    def obter_token(self) -> bool:
        if self.token:
            return True

        try:
            
            autoridade = f"https://login.microsoftonline.com/{self.tenant_id}"
            app = msal.ConfidentialClientApplication(
                self.client_id,
                authority=autoridade,
                client_credential=self.client_secret
            )
            
            # Escopo .default para permissões de aplicativo (Application Permissions)
            resultado = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
            
            if "access_token" in resultado:
                self.token = resultado['access_token']
                logger.info("Token Graph obtido com sucesso.")
                return True
            else:
                logger.error(f"Erro ao obter token: {resultado.get('error_description')}")
                return False
        except Exception as e:
            logger.exception("Erro excepcional ao obter token")
            return False

    def listar_emails(self):
        if not self.token and not self.obter_token():
            return

        # Endpoint para listar mensagens de uma pasta específica
        # Se for uma pasta personalizada, use o ID dela. Se for a principal, use 'inbox'
        url = f"https://graph.microsoft.com/v1.0/users/{self.user_email}/mailFolders/{self.folder_id}/messages"
        
        headers = {
            'Authorization': f'Bearer {self.token}',
            'Content-Type': 'application/json'
        }

        try:
            response = requests.get(url, headers=headers)
            if response.status_code == 200:
                emails = response.json().get('value', [])
                logger.info(f"Encontrados {len(emails)} e-mails na pasta.")
                
                for email in emails:
                    subject = email.get('subject')
                    # O Graph entrega o corpo do email em um dicionário 'body'
                    body_content = email.get('body', {}).get('content', '')

                    if body_content:
                        # Usamos o BeautifulSoup para parsear o HTML
                        soup = BeautifulSoup(body_content, 'html.parser')
                        
                        # Buscamos a div que contém a classe 'x_code'
                        # Dica: Às vezes o Outlook muda 'x_code' para 'code' ou vice-versa, 
                        # então buscamos por qualquer classe que contenha 'code'
                        elemento_codigo = soup.find('div', class_=re.compile(r'code'))
                        
                        if elemento_codigo:
                            # Extrai apenas os números (caso haja espaços ou caracteres invisíveis)
                            codigo_limpo = re.sub(r'\D', '', elemento_codigo.text)
                            
                            logger.info(f"Sucesso! Código encontrado no email '{subject}': {codigo_limpo}")
                            
                            # Agora você tem o código na variável para usar no seu RPA
                            minha_variavel_codigo = codigo_limpo
                            print(f"Código capturado: {minha_variavel_codigo}")
                        else:
                            logger.warning(f"A div de código não foi encontrada no email: {subject}")
                            
                            return emails
            else:
                logger.error(f"Erro na requisição: {response.status_code} - {response.text}")
        except Exception as e:
            logger.exception("Erro ao listar e-mails")
            
    def listar_emails_recentes(self):
        if not self.token and not self.obter_token():
            return

        # Calcula o tempo de 5 minutos atrás em formato ISO (UTC)
        cinco_minutos_atras = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")

        # Endpoint com FILTRO: receivedDateTime maior ou igual a 5 min atrás
        # Importante: O filtro deve estar entre aspas simples na URL
        url = (
            f"https://graph.microsoft.com/v1.0/users/{self.user_email}/mailFolders/{self.folder_id}/messages"
            f"?$filter=receivedDateTime ge {cinco_minutos_atras}"
        )
        
        headers = {
            'Authorization': f'Bearer {self.token}',
            'Content-Type': 'application/json'
        }

        try:
            logger.info(f"Buscando e-mails recebidos após: {cinco_minutos_atras}")
            response = requests.get(url, headers=headers)
            
            if response.status_code == 200:
                emails = response.json().get('value', [])
                logger.info(f"Encontrados {len(emails)} e-mails recentes.")
                
                for email in emails:
                    subject = email.get('subject')
                    # O Graph entrega o corpo do email em um dicionário 'body'
                    body_content = email.get('body', {}).get('content', '')

                    if body_content:
                        # Usamos o BeautifulSoup para parsear o HTML
                        soup = BeautifulSoup(body_content, 'html.parser')
                        
                        # Buscamos a div que contém a classe 'x_code'
                        # Dica: Às vezes o Outlook muda 'x_code' para 'code' ou vice-versa, 
                        # então buscamos por qualquer classe que contenha 'code'
                        elemento_codigo = soup.find('div', class_=re.compile(r'code'))
                        
                        if elemento_codigo:
                            # Extrai apenas os números (caso haja espaços ou caracteres invisíveis)
                            codigo_limpo = re.sub(r'\D', '', elemento_codigo.text)
                            
                            logger.info(f"Sucesso! Código encontrado no email '{subject}': {codigo_limpo}")
                            
                            # Agora você tem o código na variável para usar no seu RPA
                            minha_variavel_codigo = codigo_limpo
                            print(f"Código capturado: {minha_variavel_codigo}")
                        else:
                            logger.warning(f"A div de código não foi encontrada no email: {subject}")
                
                return emails
            else:
                logger.error(f"Erro {response.status_code}: {response.text}")
        except Exception as e:
            logger.exception("Erro ao filtrar e-mails recentes")        
            
    def obter_ultimo_codigo_acesso(self):
        if not self.token and not self.obter_token():
            return None

        # 1. Definir o tempo (5 minutos atrás em UTC)
        cinco_minutos_atras = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat().replace("+00:00", "Z")

        # 2. Construir a URL com Filtro, Ordenação e Top 1
        # Filtramos pela data, ordenamos pela data descrescente e pegamos o primeiro (top=1)
        url = (
            f"https://graph.microsoft.com/v1.0/users/{self.user_email}/mailFolders/{self.folder_id}/messages"
            f"?$filter=receivedDateTime ge {cinco_minutos_atras}"
            f"&$orderby=receivedDateTime desc"
            f"&$top=1"
            f"&$select=subject,receivedDateTime,body"
        )
        
        headers = {
            'Authorization': f'Bearer {self.token}',
            'Content-Type': 'application/json'
        }

        try:
            response = requests.get(url, headers=headers)
            if response.status_code == 200:
                dados = response.json().get('value', [])
                
                if not dados:
                    logger.info("Nenhum e-mail encontrado nos últimos 5 minutos.")
                    return None

                # Como usamos $top=1, pegamos o primeiro item da lista
                ultimo_email = dados[0]
                corpo_html = ultimo_email.get('body', {}).get('content', '')

                # 3. Extrair o código da <div class="x_code">
                soup = BeautifulSoup(corpo_html, 'html.parser')
                
                # Procura a div que contém 'code' na classe (lida com x_code ou code)
                elemento_codigo = soup.find('div', class_=re.compile(r'code'))

                if elemento_codigo:
                    # Remove qualquer coisa que não seja dígito (limpa o 8774)
                    codigo_acesso = re.sub(r'\D', '', elemento_codigo.text)
                    print(type)
                    logger.info(f"Código {codigo_acesso} extraído do e-mail: {ultimo_email.get('subject')}")
                    return codigo_acesso
                else:
                    logger.warning("E-mail encontrado, mas a <div class='x_code'> não estava no corpo.")
                    return None
            else:
                logger.error(f"Erro na API: {response.status_code} - {response.text}")
                return None
        except Exception as e:
            logger.exception("Erro ao processar último e-mail")
            return None

# Execução
if __name__ == "__main__":
    client = MicrosoftGraphClient()
    client.obter_ultimo_codigo_acesso()