#!/usr/bin/env bash
# Install the MrFoX-MeM core API as an always-on per-user service.
#
# macOS: a launchd LaunchAgent (RunAtLoad + KeepAlive). Portable — derives all
# paths from THIS repo's location, so it works on any machine after `make setup`.
# Logs go to /tmp: a repo under ~/Documents/~/Desktop is TCC-protected and
# launchd cannot open a log file there (fails with EX_CONFIG 78).
#
# Usage:   bash scripts/install-service.sh
# Uninstall: launchctl bootout gui/$(id -u)/com.mrfox.mem && rm ~/Library/LaunchAgents/com.mrfox.mem.plist
#
# Linux (systemd --user) equivalent is printed at the end for reference.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PY="$REPO/.venv/bin/python"
PORT="${MRFOX_PORT:-8077}"
LABEL="com.mrfox.mem"

[ -x "$PY" ] || { echo "error: $PY missing — run 'make setup' (or 'python cli.py setup') first"; exit 1; }

case "$(uname -s)" in
  Darwin)
    PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
    mkdir -p "$HOME/Library/LaunchAgents"
    cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string><string>-lc</string>
    <string>cd '$REPO' &amp;&amp; exec '$PY' -m uvicorn core.api:app --host 127.0.0.1 --port $PORT</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/tmp/mrfox-mem.log</string>
  <key>StandardErrorPath</key><string>/tmp/mrfox-mem.log</string>
</dict></plist>
PLIST
    UID_N="$(id -u)"
    launchctl bootout "gui/$UID_N/$LABEL" 2>/dev/null || true
    launchctl bootstrap "gui/$UID_N" "$PLIST"
    launchctl kickstart "gui/$UID_N/$LABEL"
    echo "installed launchd service: $LABEL  (repo=$REPO port=$PORT log=/tmp/mrfox-mem.log)"
    echo "verify: curl http://127.0.0.1:$PORT/health"
    ;;
  Linux)
    UNIT="$HOME/.config/systemd/user/mrfox-mem.service"
    mkdir -p "$(dirname "$UNIT")"
    cat > "$UNIT" <<UNITEOF
[Unit]
Description=MrFoX-MeM core API
[Service]
WorkingDirectory=$REPO
ExecStart=$PY -m uvicorn core.api:app --host 127.0.0.1 --port $PORT
Restart=always
[Install]
WantedBy=default.target
UNITEOF
    systemctl --user daemon-reload
    systemctl --user enable --now mrfox-mem.service
    echo "installed systemd --user service: mrfox-mem  (repo=$REPO port=$PORT)"
    echo "verify: curl http://127.0.0.1:$PORT/health   |   logs: journalctl --user -u mrfox-mem -f"
    ;;
  *)
    echo "Unsupported OS for auto-service. Run manually: (cd '$REPO' && '$PY' -m uvicorn core.api:app --host 127.0.0.1 --port $PORT)"
    ;;
esac
