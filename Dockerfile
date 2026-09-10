FROM python:3.12-slim AS builder

WORKDIR /app

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright


# ============================================================
# 1. Dependências do sistema
# ============================================================
RUN apt-get update && apt-get install -y --no-install-recommends \
    supervisor \
    libcap2-bin \
    libglib2.0-0 \
    libnss3 \
    curl \
    wget \
    netcat-openbsd \
    xvfb \
    xauth \
    dbus \
    dbus-x11 \
    libgtk-3-0 \
    libnotify4 \
    libxss1 \
    libxtst6 \
    libgbm1 \
    libx11-xcb1 \
    libxcb-dri3-0 \
    libappindicator3-1 \
    && rm -rf /var/lib/apt/lists/*


# ============================================================
# 2. Virtualenv do Python
# ============================================================
RUN python3 -m venv /venv

ENV PATH="/venv/bin:$PATH"


# ============================================================
# 3. Dependências Python
# ============================================================
RUN --mount=type=cache,target=/root/.cache/pip \
    --mount=type=bind,source=requirements.txt,target=requirements.txt \
    pip install -r requirements.txt


# ============================================================
# 4. Firefox do Playwright
# ============================================================
RUN playwright install --with-deps firefox


# ============================================================
# 5. Copia projeto
# ============================================================
COPY . .


# ============================================================
# 6. Instala TOTVS WebAgent
# ============================================================
RUN dpkg -i ./webagent/linux-x64-release.deb || true

RUN apt-get update && apt-get install -f -y \
    && rm -rf /var/lib/apt/lists/*


# ============================================================
# 7. Cria link para o executável real do WebAgent
#
# Executável instalado pelo pacote:
# /opt/web-agent/web-agent
# ============================================================
RUN ln -sf /opt/web-agent/web-agent /usr/local/bin/totvs-webagent \
    && ln -sf /opt/web-agent/web-agent /usr/bin/totvs-webagent \
    && ls -lah /opt/web-agent/web-agent \
    && ls -lah /usr/local/bin/totvs-webagent


# ============================================================
# 8. Supervisor
# ============================================================
RUN mkdir -p /var/log/supervisor


COPY <<EOF /etc/supervisor/conf.d/supervisord.conf

[supervisord]
user=root
nodaemon=true
logfile=/var/log/supervisor/supervisord.log


; ==========================================================
; TOTVS WEBAGENT
; ==========================================================

[program:webagent]
command=dbus-run-session -- xvfb-run -a --server-args="-screen 0 1920x1080x24" /usr/local/bin/totvs-webagent
autostart=true
autorestart=false
startsecs=2
startretries=5
priority=10

stdout_logfile=/dev/stdout
stdout_logfile_maxbytes=0

stderr_logfile=/dev/stderr
stderr_logfile_maxbytes=0


; ==========================================================
; APLICAÇÃO PLAYWRIGHT
; ==========================================================

[program:app]
command=/bin/bash -c "until nc -z 127.0.0.1 21021; do echo 'Aguardando TOTVS WebAgent subir na porta 21021...'; sleep 1; done; echo 'TOTVS WebAgent disponível na porta 21021!'; /venv/bin/python3 main_playwright.py; status=\$?; kill -TERM 1; exit \$status"
directory=/app
autostart=true
autorestart=false
autorexit=true
priority=20

stdout_logfile=/dev/stdout
stdout_logfile_maxbytes=0

stderr_logfile=/dev/stderr
stderr_logfile_maxbytes=0

EOF


# ============================================================
# 9. Inicialização
# ============================================================
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]