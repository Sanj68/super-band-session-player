#!/bin/zsh
set -euo pipefail

LABEL=com.subone.session-player-backend
SCRIPT_DIR=${0:A:h}
REPO_ROOT=${SCRIPT_DIR:h}
BACKEND_ROOT="$REPO_ROOT/backend"
TEMPLATE="$REPO_ROOT/ops/launchagents/$LABEL.plist.in"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$HOME/Library/Logs/Session Player"
DOMAIN="gui/$(id -u)"
SERVICE="$DOMAIN/$LABEL"
PYTHON="$BACKEND_ROOT/.venv/bin/python"

wait_for_health() {
  local attempt
  for attempt in {1..30}; do
    if "$PYTHON" "$BACKEND_ROOT/tools/service_probe.py" >/dev/null 2>&1; then
      "$PYTHON" "$BACKEND_ROOT/tools/service_probe.py"
      return 0
    fi
    sleep 0.2
  done
  print -u2 "Session Player service did not become healthy."
  print -u2 "Inspect: $LOG_DIR/backend.stderr.log"
  return 1
}

install_agent() {
  mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR"
  local pending="$PLIST.pending"
  sed \
    -e "s|__REPO__|$REPO_ROOT|g" \
    -e "s|__HOME__|$HOME|g" \
    "$TEMPLATE" > "$pending"
  plutil -lint "$pending" >/dev/null
  mv "$pending" "$PLIST"
  print "Installed $PLIST"
}

case "${1:-status}" in
  install)
    install_agent
    if launchctl print "$SERVICE" >/dev/null 2>&1; then
      launchctl bootout "$SERVICE"
    fi
    launchctl bootstrap "$DOMAIN" "$PLIST"
    wait_for_health
    ;;
  start)
    [[ -f "$PLIST" ]] || install_agent
    if ! launchctl print "$SERVICE" >/dev/null 2>&1; then
      launchctl bootstrap "$DOMAIN" "$PLIST"
    else
      launchctl kickstart "$SERVICE"
    fi
    wait_for_health
    ;;
  stop)
    if launchctl print "$SERVICE" >/dev/null 2>&1; then
      launchctl bootout "$SERVICE"
    else
      print "Session Player service is already stopped."
    fi
    ;;
  restart)
    if launchctl print "$SERVICE" >/dev/null 2>&1; then
      launchctl kickstart -k "$SERVICE"
    else
      [[ -f "$PLIST" ]] || install_agent
      launchctl bootstrap "$DOMAIN" "$PLIST"
    fi
    wait_for_health
    ;;
  status|health)
    launchctl print "$SERVICE" >/dev/null
    "$PYTHON" "$BACKEND_ROOT/tools/service_probe.py"
    ;;
  logs)
    tail -n 80 "$LOG_DIR/backend.stderr.log"
    ;;
  *)
    print -u2 "Usage: $0 {install|start|stop|restart|status|health|logs}"
    exit 64
    ;;
esac
