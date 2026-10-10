#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/gcpmx-probe}"
CONFIG_DIR="/etc/gcpmx-probe"
LOCATION="${PROBE_LOCATION:-overseas}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "请使用 root 运行" >&2
  exit 1
fi
if [[ ! -f app.py || ! -f requirements.txt || ! -f probe-agent.service ]]; then
  echo "请在 probe-agent 目录中运行此脚本" >&2
  exit 1
fi
if [[ "$LOCATION" != "mainland" && "$LOCATION" != "hk" && "$LOCATION" != "overseas" ]]; then
  echo "PROBE_LOCATION 必须是 mainland、hk 或 overseas" >&2
  exit 1
fi

apt-get update
apt-get install -y python3 python3-venv openssl
install -d -m 0755 "$APP_DIR" "$CONFIG_DIR"
install -m 0644 app.py requirements.txt "$APP_DIR"/
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

if [[ ! -f "$CONFIG_DIR/probe.env" ]]; then
  TOKEN="$(openssl rand -hex 32)"
  cat > "$CONFIG_DIR/probe.env" <<EOF
PROBE_TOKEN=$TOKEN
PROBE_LOCATION=$LOCATION
PROBE_PORT=9090
PROBE_MAX_CONCURRENT=50
EOF
  chmod 600 "$CONFIG_DIR/probe.env"
  echo "首次令牌已写入 $CONFIG_DIR/probe.env，请将 PROBE_TOKEN 保存到面板。"
fi

install -m 0644 probe-agent.service /etc/systemd/system/gcpmx-probe.service
systemctl daemon-reload
systemctl enable --now gcpmx-probe
echo "探针已启动：$APP_DIR，监听端口 9090，位置 $LOCATION"
