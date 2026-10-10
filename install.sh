#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/gcp-ip-panel}"
PYTHON="${PYTHON:-python3}"
REPO_URL="${REPO_URL:-}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "请使用 root 运行此脚本" >&2
  exit 1
fi

mkdir -p "$APP_DIR"
if [[ -n "$REPO_URL" ]]; then
  rm -rf "$APP_DIR"
  git clone --depth 1 "${REPO_URL}" "$APP_DIR"
else
  SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
  cp -a "$SCRIPT_DIR"/. "$APP_DIR"/
fi
cd "$APP_DIR"

apt-get update
apt-get install -y "$PYTHON" python3-venv git openssl
"$PYTHON" -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

mkdir -p /etc/gcp-ip-panel
if [[ ! -f /etc/gcp-ip-panel/fullchain.pem || ! -f /etc/gcp-ip-panel/privkey.pem ]]; then
  openssl req -x509 -nodes -newkey rsa:2048 -days 365 \
    -keyout /etc/gcp-ip-panel/privkey.pem -out /etc/gcp-ip-panel/fullchain.pem \
    -subj "/CN=$(hostname -f 2>/dev/null || hostname)" >/dev/null 2>&1
  chmod 600 /etc/gcp-ip-panel/privkey.pem
fi
if [[ ! -f .env ]]; then
  cat > .env <<EOF
GCP_MOCK=0
ADMIN_USERNAME=admin
ADMIN_PASSWORD=$(openssl rand -base64 18 | tr -dc 'A-Za-z0-9' | head -c 16)
SESSION_SECRET=$(openssl rand -hex 32)
GCP_CREDENTIALS_PATH=/etc/gcp-ip-panel/gcp-service-account.json
TLS_CERTFILE=/etc/gcp-ip-panel/fullchain.pem
TLS_KEYFILE=/etc/gcp-ip-panel/privkey.pem
PORT=8443
EOF
  echo "管理员账号：admin"
  grep '^ADMIN_PASSWORD=' .env
fi

install -m 0644 deploy/gcp-ip-panel.service /etc/systemd/system/gcp-ip-panel.service
systemctl daemon-reload
systemctl enable --now gcp-ip-panel
echo "安装完成：http://$(hostname -I | awk '{print $1}'):8080"
