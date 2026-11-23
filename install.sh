#!/usr/bin/env bash
# ubuntu-server-hub — instala/atualiza o painel nesta máquina.
#   ./install.sh            cria/atualiza o .env, monta a imagem e sobe o container
#   ./install.sh --env-only só cria/atualiza o .env
# Pode rodar quantas vezes quiser (ex.: depois de um git pull).
set -euo pipefail
cd "$(dirname "$0")"

if [ "$(id -u)" = 0 ]; then
  echo "Rode como o seu usuário comum (que esteja no grupo docker), não como root." >&2
  exit 1
fi

[ -f .env ] || { cp .env.example .env; chmod 600 .env; echo "• .env criado a partir do .env.example"; }

# grava KEY=valor no .env (substitui a linha existente)
set_env() {
  local key=$1 val=$2
  if grep -q "^${key}=" .env; then
    sed -i "s|^${key}=.*|${key}=${val}|" .env
  else
    echo "${key}=${val}" >> .env
  fi
}

. /etc/os-release
set_env UBUNTU_VERSION "${VERSION_ID}"
set_env HUB_UID "$(id -u)"
set_env HUB_GID "$(id -g)"
set_env HUB_HOST_USER "$(id -un)"
if [ -S /var/run/docker.sock ]; then
  set_env DOCKER_GID "$(stat -c %g /var/run/docker.sock)"
fi

data_path=$(grep -E '^HUB_DATA_PATH=' .env | cut -d= -f2- || true)
mkdir -p "${data_path:-./data}"
echo "• Ubuntu ${VERSION_ID} · usuário $(id -un) · dados em ${data_path:-./data}"

[ "${1:-}" = "--env-only" ] && { echo "• .env atualizado"; exit 0; }

command -v docker >/dev/null || { echo "Docker não encontrado. Veja a seção 'Requisitos' do README." >&2; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "Plugin 'docker compose' não encontrado (pacote docker-compose-plugin)." >&2; exit 1; }
if ! docker info >/dev/null 2>&1; then
  echo "Sem acesso ao Docker. Rode: sudo usermod -aG docker \$USER  e entre de novo na sessão." >&2
  exit 1
fi

docker compose up -d --build --remove-orphans

bind=$(grep -E '^HUB_BIND=' .env | cut -d= -f2- || true); bind=${bind:-127.0.0.1}
port=$(grep -E '^HUB_PORT=' .env | cut -d= -f2- || true); port=${port:-8084}
host=$bind; case "$bind" in 0.0.0.0|::|"") host=127.0.0.1;; esac
for _ in $(seq 1 30); do
  if curl -fsS "http://${host}:${port}/healthz" >/dev/null 2>&1; then
    echo "✓ hub no ar em http://${host}:${port}"
    exit 0
  fi
  sleep 2
done
echo "O hub não respondeu. Últimos logs:" >&2
docker compose logs --tail 60 hub >&2
exit 1
