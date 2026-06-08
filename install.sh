#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="${FINROBOT_RUNTIME_DIR:-$ROOT/.runtime}"
WINEPREFIX_DIR="${FINROBOT_WINEPREFIX:-$RUNTIME_DIR/wineprefix}"
MT5_DIR="${FINROBOT_MT5_DIR:-$RUNTIME_DIR/mt5}"
DOWNLOAD_DIR="$RUNTIME_DIR/downloads"
INSTALLER="$DOWNLOAD_DIR/mt5setup.exe"
TERMINAL_ROOT="$MT5_DIR/terminal"
TERMINAL_LINK="$TERMINAL_ROOT/current"
LOG_DIR="$ROOT/logs"
MT5_URL="https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe"

if [ -f "$ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

if [ ! -f /etc/os-release ]; then
  echo "Unsupported OS: /etc/os-release not found" >&2
  exit 1
fi

. /etc/os-release
case "${ID:-}" in
  ubuntu|debian) ;;
  *)
    case "${ID_LIKE:-}" in
      *debian*) ;;
      *)
        echo "Unsupported OS: ${PRETTY_NAME:-unknown}. install.sh supports Debian/Ubuntu only." >&2
        exit 1
        ;;
    esac
    ;;
esac

if ! command -v sudo >/dev/null 2>&1; then
  echo "sudo is required to install system packages" >&2
  exit 1
fi

echo "Installing system packages..."
sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  ca-certificates \
  curl \
  python3 \
  python3-pip \
  python3-venv \
  xvfb \
  wine \
  nodejs \
  npm

if ! command -v pm2 >/dev/null 2>&1; then
  echo "Installing PM2 globally..."
  sudo npm install -g pm2
fi

echo "Creating Python virtualenv..."
python3 -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/python" -m pip install --upgrade pip
"$ROOT/.venv/bin/python" -m pip install -r "$ROOT/requirements.txt"

mkdir -p "$DOWNLOAD_DIR" "$TERMINAL_ROOT" "$LOG_DIR"

export WINEPREFIX="$WINEPREFIX_DIR"
export WINEARCH=win64
export WINEDEBUG="${WINEDEBUG:--all}"

if [ ! -f "$INSTALLER" ]; then
  echo "Downloading MT5 installer..."
  curl -L --retry 3 --connect-timeout 20 -o "$INSTALLER" "$MT5_URL"
fi

echo "Initializing Wine prefix at $WINEPREFIX_DIR..."
xvfb-run -a bash -lc 'wineboot -u >/dev/null 2>&1 || true'

echo "Installing MT5 into repo-local runtime..."
xvfb-run -a wine "$INSTALLER" /auto "/path:C:\\FinRobotMT5" > "$LOG_DIR/mt5_install.log" 2>&1 || true

FOUND=""
for candidate in \
  "$WINEPREFIX_DIR/drive_c/FinRobotMT5/terminal64.exe" \
  "$WINEPREFIX_DIR/drive_c/Program Files/MetaTrader 5/terminal64.exe" \
  "$WINEPREFIX_DIR/drive_c/Program Files (x86)/MetaTrader 5/terminal64.exe"; do
  if [ -f "$candidate" ]; then
    FOUND="$candidate"
    break
  fi
done

if [ -z "$FOUND" ]; then
  FOUND=$(find "$WINEPREFIX_DIR/drive_c" -iname terminal64.exe -print -quit 2>/dev/null || true)
fi

if [ -z "$FOUND" ]; then
  echo "terminal64.exe not found. See $LOG_DIR/mt5_install.log" >&2
  exit 1
fi

ln -sfn "$(dirname "$FOUND")" "$TERMINAL_LINK"

echo "Syncing FinRobot EA..."
"$ROOT/scripts/sync_mt5_ea.sh" --no-compile

mkdir -p "$TERMINAL_LINK/Config"
cat > "$TERMINAL_LINK/Config/finrobot-login.ini" <<INI
[Common]
Login=${MT5_LOGIN:-}
Password=${MT5_PASSWORD:-}
Server=${MT5_SERVER:-}
ProxyEnable=0
NewsEnable=0
CertInstall=0
INI
chmod 600 "$TERMINAL_LINK/Config/finrobot-login.ini"

echo "Starting PM2 services..."
pm2 startOrReload "$ROOT/ecosystem.config.js" --update-env
pm2 save || true

if command -v systemctl >/dev/null 2>&1; then
  echo "Registering PM2 startup with systemd..."
  sudo env PATH="$PATH" pm2 startup systemd -u "$USER" --hp "$HOME" || true
  pm2 save || true
fi

echo "Install complete."
echo "MT5 terminal: $FOUND"
echo "Runtime dir: $RUNTIME_DIR"
echo "Next checks:"
echo "  pm2 list"
echo "  $ROOT/.venv/bin/python scripts/mt5_status.py"
echo "  $ROOT/.venv/bin/python scripts/mt5_trade_report.py"
