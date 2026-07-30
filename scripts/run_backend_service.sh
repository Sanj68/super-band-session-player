#!/bin/zsh
set -euo pipefail

SCRIPT_DIR=${0:A:h}
REPO_ROOT=${SCRIPT_DIR:h}
BACKEND_ROOT="$REPO_ROOT/backend"
PYTHON="$BACKEND_ROOT/.venv/bin/python"

if [[ ! -x "$PYTHON" ]]; then
  print -u2 "Session Player backend venv is missing: $PYTHON"
  exit 78
fi

"$PYTHON" "$BACKEND_ROOT/tools/service_preflight.py"

export SESSION_PLAYER_ENABLE_GROOVE_BRIDGE=true
exec "$PYTHON" -m uvicorn app.main:app \
  --app-dir "$BACKEND_ROOT" \
  --host 127.0.0.1 \
  --port 8001
