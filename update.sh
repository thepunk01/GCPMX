#!/usr/bin/env bash
set -euo pipefail
APP_DIR="${APP_DIR:-/opt/gcp-ip-panel}"
cd "$APP_DIR"

git fetch --all --prune
git reset --hard origin/"${GIT_BRANCH:-main}"
sed -i 's/\r$//' run.sh install.sh update.sh deploy/update-from-github.sh
chmod 755 run.sh install.sh update.sh deploy/update-from-github.sh
.venv/bin/pip install -r requirements.txt
install -m 644 deploy/gcp-ip-panel.service /etc/systemd/system/gcp-ip-panel.service
systemctl daemon-reload
systemctl restart gcp-ip-panel
echo "更新完成，当前版本：$(git rev-parse --short HEAD)"
