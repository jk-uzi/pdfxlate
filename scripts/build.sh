#!/usr/bin/env bash
#
# Build the distributable desktop app.
#
# Steps:
#   1. Build the Python sidecar with PyInstaller (onedir).
#   2. Compile the TypeScript (main, preload, renderer).
#   3. Package with electron-builder for the current platform.
#
# Cross-compiling is not supported by electron-builder for the sidecar: the
# PyInstaller output is platform-specific, so each target must be built on its
# own OS (or in CI with a matrix).

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY_DIR="$ROOT/python"

# The venv layout differs by platform: Scripts/python.exe on Windows,
# bin/python elsewhere. Hard-coding bin/ made this script fail in Git Bash on
# Windows, which is the primary development platform.
if [[ -x "$PY_DIR/.venv/Scripts/python.exe" ]]; then
  VENV_PY="$PY_DIR/.venv/Scripts/python.exe"
elif [[ -x "$PY_DIR/.venv/bin/python" ]]; then
  VENV_PY="$PY_DIR/.venv/bin/python"
else
  echo "error: Python venv not found under $PY_DIR/.venv" >&2
  echo "create it with:" >&2
  echo "  cd python && python -m venv .venv" >&2
  echo "  .venv/Scripts/pip install -e '.[dev]'      # Windows" >&2
  echo "  .venv/bin/pip install -e '.[dev]'          # macOS / Linux" >&2
  exit 1
fi

# electron-builder downloads NSIS and winCodeSign (and WiX for MSI) on the
# first run. Those come from GitHub releases, which are unreachable from some
# networks. This is the same mirror Electron's own download uses; override it
# with ELECTRON_BUILDER_BINARIES_MIRROR if you have another.
export ELECTRON_BUILDER_BINARIES_MIRROR="${ELECTRON_BUILDER_BINARIES_MIRROR:-https://npmmirror.com/mirrors/electron-builder-binaries/}"

echo "==> [1/4] Staging bundled assets"
"$VENV_PY" "$ROOT/scripts/stage_assets.py"

echo "==> [2/4] Building Python sidecar"
cd "$PY_DIR"
"$VENV_PY" -m PyInstaller --clean --noconfirm pdfxlate.spec

echo "==> [3/4] Building TypeScript"
cd "$ROOT"
npm run build

echo "==> [4/4] Packaging with electron-builder"
# --no-install: fail loudly if it is missing instead of silently downloading a
# different version from the network mid-build.
npx --no-install electron-builder --publish never

echo
echo "Done. Installers are in $ROOT/dist"
