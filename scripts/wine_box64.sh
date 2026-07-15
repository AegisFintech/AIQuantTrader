#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="${AIQUANTTRADER_RUNTIME_DIR:-$ROOT/.runtime}"
WINE_HOME="${AIQUANTTRADER_X86_WINE_HOME:-$RUNTIME_DIR/wine-x86_64/wine-11.10-amd64-wow64}"

if [ ! -x "$WINE_HOME/bin/wine" ]; then
  echo "x86_64 Wine not found at $WINE_HOME/bin/wine" >&2
  echo "Install or extract an x86_64 Wine build under $WINE_HOME, or set AIQUANTTRADER_X86_WINE_HOME." >&2
  exit 1
fi

if command -v box64 >/dev/null 2>&1; then
  RUNNER=(box64)
else
  RUNNER=()
fi

export PATH="$WINE_HOME/bin:$PATH"
export LD_LIBRARY_PATH="$WINE_HOME/lib:$WINE_HOME/lib64:${LD_LIBRARY_PATH:-}"
export WINEDLLPATH="$WINE_HOME/lib/wine/x86_64-windows:$WINE_HOME/lib/wine/i386-windows${WINEDLLPATH:+:$WINEDLLPATH}"
export BOX64_NOBANNER="${BOX64_NOBANNER:-1}"

HELPER=wine
case "${1:-}" in
  wine|wine64)
    shift
    HELPER=wine
    ;;
  wineboot|boot)
    shift
    HELPER=wineboot
    ;;
  winecfg)
    shift
    HELPER=winecfg
    ;;
  winepath)
    shift
    HELPER=winepath
    ;;
  wineserver|server)
    shift
    HELPER=wineserver
    ;;
esac

exec "${RUNNER[@]}" "$WINE_HOME/bin/$HELPER" "$@"
