#!/usr/bin/env bash
set -euo pipefail
cd /opt/gcp-ip-panel
ARGS=(app.main:app --host 0.0.0.0 --port "${PORT:-8443}")
if [[ -n "${TLS_CERTFILE:-}" && -n "${TLS_KEYFILE:-}" && -f "$TLS_CERTFILE" && -f "$TLS_KEYFILE" ]]; then
  ARGS+=(--ssl-certfile "$TLS_CERTFILE" --ssl-keyfile "$TLS_KEYFILE")
fi
exec /opt/gcp-ip-panel/.venv/bin/uvicorn "${ARGS[@]}"
