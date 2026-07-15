#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="${AIQUANTTRADER_RUNTIME_DIR:-$ROOT/.runtime}"
WINEPREFIX_DIR="${AIQUANTTRADER_WINEPREFIX:-$RUNTIME_DIR/wineprefix}"
MT5_DIR="${AIQUANTTRADER_MT5_DIR:-$RUNTIME_DIR/mt5}"
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

SKIP_MT5="${AIQUANTTRADER_SKIP_MT5_INSTALL:-false}"
ALLOW_EMULATED="${AIQUANTTRADER_ALLOW_EMULATED_MT5:-false}"
WINE_CMD="${AIQUANTTRADER_WINE_CMD:-wine}"
WINEBOOT_TIMEOUT="${AIQUANTTRADER_WINEBOOT_TIMEOUT:-60s}"
MT5_INSTALL_TIMEOUT="${AIQUANTTRADER_MT5_INSTALL_TIMEOUT:-10m}"

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

ARCH=$(uname -m)

if [ "$ALLOW_EMULATED" = "true" ] && [ "$ARCH" != "x86_64" ] && [ "$ARCH" != "amd64" ] && [ -z "${AIQUANTTRADER_WINE_CMD:-}" ]; then
  if command -v wine >/dev/null 2>&1 && wine --version 2>/dev/null | grep -qi 'hangover'; then
    WINE_CMD="wine"
  else
    WINE_CMD="$ROOT/scripts/wine_box64.sh"
  fi
fi

read -r -a WINE_CMD_ARR <<< "$WINE_CMD"

run_wine() {
  "${WINE_CMD_ARR[@]}" "$@"
}

build_wineboot_cmd() {
  local last_index wine_bin wineboot_bin

  if [ -n "${AIQUANTTRADER_WINEBOOT_CMD:-}" ]; then
    read -r -a WINEBOOT_CMD_ARR <<< "$AIQUANTTRADER_WINEBOOT_CMD"
    WINEBOOT_CMD_ARR+=(-u)
    return
  fi

  last_index=$((${#WINE_CMD_ARR[@]} - 1))
  wine_bin="${WINE_CMD_ARR[$last_index]}"
  wineboot_bin="$(dirname "$wine_bin")/wineboot"

  if [ "$(basename "$wine_bin")" = "wine" ] && [ -x "$wineboot_bin" ]; then
    WINEBOOT_CMD_ARR=("${WINE_CMD_ARR[@]}")
    WINEBOOT_CMD_ARR[$last_index]="$wineboot_bin"
    WINEBOOT_CMD_ARR+=(-u)
    return
  fi

  WINEBOOT_CMD_ARR=("${WINE_CMD_ARR[@]}" wineboot -u)
}

# Determine whether MT5 installation is possible.
# MT5/Wine need x86_64; skip on other architectures.
MT5_CAPABLE=true
if [ "$SKIP_MT5" = "true" ]; then
  MT5_CAPABLE=false
elif [ "$ALLOW_EMULATED" != "true" ] && [ "$ARCH" != "x86_64" ] && [ "$ARCH" != "amd64" ]; then
  MT5_CAPABLE=false
fi

echo "Installing system packages..."

# Core packages (no nodejs/npm — handled separately to avoid NodeSource conflict)
PKGS=(ca-certificates curl python3 python3-pip python3-venv)
if $MT5_CAPABLE; then
  PKGS+=(xvfb)
  if [ "$ALLOW_EMULATED" != "true" ]; then
    PKGS+=(wine)
  fi
fi

sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y "${PKGS[@]}"

# nodejs/npm handled separately to avoid NodeSource vs Debian conflict
if ! command -v node >/dev/null 2>&1; then
  echo "Installing nodejs from Debian repository..."
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y nodejs
fi
if ! command -v npm >/dev/null 2>&1; then
  echo "Installing npm from Debian repository..."
  if ! sudo DEBIAN_FRONTEND=noninteractive apt-get install -y npm; then
    if command -v node >/dev/null 2>&1; then
      echo "Trying npm self-install via: sudo npm install -g npm"
      sudo npm install -g npm
    else
      echo "ERROR: Could not install npm. Install Node.js from https://nodejs.org" >&2
      exit 1
    fi
  fi
fi

if ! command -v pm2 >/dev/null 2>&1; then
  echo "Installing PM2 globally..."
  sudo npm install -g pm2
fi

echo "Creating Python virtualenv..."
python3 -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/python" -m pip install --upgrade pip
"$ROOT/.venv/bin/python" -m pip install -r "$ROOT/requirements.txt"

# --- MT5 / Wine installation ---
if ! $MT5_CAPABLE; then
  echo ""
  echo "Skipping MT5 installation ($ARCH is not x86_64, or AIQUANTTRADER_SKIP_MT5_INSTALL=true)."
  echo "Set AIQUANTTRADER_ALLOW_EMULATED_MT5=true and AIQUANTTRADER_WINE_CMD to use Wine via emulation."
  echo "Python venv, PM2, and non-MT5 tooling are ready anyway."
else
  mkdir -p "$DOWNLOAD_DIR" "$TERMINAL_ROOT" "$LOG_DIR"

  export WINEPREFIX="$WINEPREFIX_DIR"
  export WINEARCH=win64
  export WINEDEBUG="${WINEDEBUG:--all}"

  if [ ! -f "$INSTALLER" ]; then
    echo "Downloading MT5 installer..."
    curl -L --retry 3 --connect-timeout 20 -o "$INSTALLER" "$MT5_URL"
  fi

  echo "Initializing Wine prefix at $WINEPREFIX_DIR... (using: $WINE_CMD)"
  build_wineboot_cmd
  timeout "$WINEBOOT_TIMEOUT" xvfb-run -a "${WINEBOOT_CMD_ARR[@]}" >/dev/null 2>&1 || true

  echo "Installing MT5 into repo-local runtime..."
  timeout "$MT5_INSTALL_TIMEOUT" xvfb-run -a "${WINE_CMD_ARR[@]}" "$INSTALLER" /auto "/path:C:\\AIQuantTraderMT5" > "$LOG_DIR/mt5_install.log" 2>&1 || true

  FOUND=""
  for candidate in \
    "$WINEPREFIX_DIR/drive_c/AIQuantTraderMT5/terminal64.exe" \
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

  echo "Syncing AIQuantTrader EA..."
  "$ROOT/scripts/sync_mt5_ea.sh"

  mkdir -p "$TERMINAL_LINK/Config"
  cat > "$TERMINAL_LINK/Config/aiquanttrader-login.ini" <<INI
[Common]
Login=${MT5_LOGIN:-}
Password=${MT5_PASSWORD:-}
Server=${MT5_SERVER:-}
ProxyEnable=0
NewsEnable=0
CertInstall=0
INI
  chmod 600 "$TERMINAL_LINK/Config/aiquanttrader-login.ini"
  "$ROOT/.venv/bin/python" "$ROOT/scripts/mt5_configure_profile.py"
fi

echo ""
echo "Starting PM2 services..."
pm2 startOrReload "$ROOT/ecosystem.config.js" --update-env || true
pm2 save || true

if command -v systemctl >/dev/null 2>&1 && $MT5_CAPABLE; then
  echo "Registering PM2 startup with systemd..."
  sudo env PATH="$PATH" pm2 startup systemd -u "$USER" --hp "$HOME" || true
  pm2 save || true
fi

echo ""
echo "Install complete."
if $MT5_CAPABLE; then
  echo "MT5 terminal: ${FOUND:-not found}"
fi
echo "Runtime dir: $RUNTIME_DIR"
echo "Next checks:"
echo "  pm2 list"
echo "  $ROOT/.venv/bin/python scripts/mt5_status.py"
echo "  $ROOT/.venv/bin/python scripts/mt5_trade_report.py"
