# ubuntu-server-hub — imagem do painel.
# A versão do Ubuntu deve ser a mesma do servidor: o apt daqui lê o dpkg/apt do host.
# O ./install.sh grava UBUNTU_VERSION no .env a partir do /etc/os-release do servidor.
ARG UBUNTU_VERSION=26.04
FROM ubuntu:${UBUNTU_VERSION}

ARG HUB_UID=1000
ARG HUB_GID=1000

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/app/.venv/bin:$PATH

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Usuário "hub" com o mesmo UID/GID do seu usuário no host,
# para que a pasta data/ continue editável por você.
RUN set -eu; \
    [ "$HUB_UID" != 0 ] || { echo "HUB_UID=0: rode o install.sh com um usuário comum, não root" >&2; exit 1; }; \
    existing_user="$(getent passwd "$HUB_UID" | cut -d: -f1 || true)"; \
    [ -z "$existing_user" ] || userdel -r "$existing_user" 2>/dev/null || userdel "$existing_user"; \
    getent group "$HUB_GID" >/dev/null || groupadd -g "$HUB_GID" hub; \
    useradd -u "$HUB_UID" -g "$HUB_GID" -M -d /app -s /usr/sbin/nologin hub

WORKDIR /app
COPY requirements.txt .
RUN python3 -m venv /app/.venv && pip install --no-cache-dir -r requirements.txt

COPY app.py collectors.py alertas.py ./
COPY templates ./templates
COPY static ./static

USER hub
EXPOSE 8084
# 1 worker de propósito: o coletor de métricas roda numa thread desse processo
CMD ["sh", "-c", "exec gunicorn -w 1 --threads 8 --no-control-socket -b ${HUB_BIND:-127.0.0.1}:${HUB_PORT:-8084} --access-logfile - app:app"]
