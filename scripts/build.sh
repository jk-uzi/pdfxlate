#!/usr/bin/env bash
#
# Build the distributable desktop app.
#
# Steps:
#   1. Restore the Python environment from uv.lock when uv is available.
#   2. Stage the bundled fonts.
#   3. Build the Python sidecar with PyInstaller (onedir).
#   4. Compile the TypeScript (main, preload, renderer).
#   5. Package with electron-builder for Windows.
#
# This project targets Windows. The PyInstaller output and the NSIS installer
# are produced on Windows (Git Bash); there is no cross-compilation.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY_DIR="$ROOT/python"

# Reproducible dependencies: when uv is available, restore exactly the versions
# in python/uv.lock (creating the venv if it is missing). --frozen makes a
# stale lock an error instead of silently resolving something else. Without
# uv, an existing pip-managed venv is used as before.
if command -v uv >/dev/null 2>&1; then
  echo "==> [1/5] Syncing the Python environment from uv.lock"
  (cd "$PY_DIR" && uv sync --frozen --extra dev)
else
  echo "==> [1/5] uv not found; keeping the existing Python environment"
fi

if [[ -x "$PY_DIR/.venv/Scripts/python.exe" ]]; then
  VENV_PY="$PY_DIR/.venv/Scripts/python.exe"
else
  echo "error: Python venv not found under $PY_DIR/.venv" >&2
  echo "create it with:" >&2
  echo "  cd python && uv sync --extra dev" >&2
  echo "or, without uv:" >&2
  echo "  cd python && python -m venv .venv" >&2
  echo "  .venv/Scripts/pip install -e '.[dev]'" >&2
  exit 1
fi

# electron-builder downloads NSIS and winCodeSign (and WiX for MSI) on the
# first run. Those come from GitHub releases, which are unreachable from some
# networks. This is the same mirror Electron's own download uses; override it
# with ELECTRON_BUILDER_BINARIES_MIRROR if you have another.
export ELECTRON_BUILDER_BINARIES_MIRROR="${ELECTRON_BUILDER_BINARIES_MIRROR:-https://npmmirror.com/mirrors/electron-builder-binaries/}"

echo "==> [2/5] Staging bundled assets"
"$VENV_PY" "$ROOT/scripts/stage_assets.py"

echo "==> [3/5] Building Python sidecar"
cd "$PY_DIR"
"$VENV_PY" -m PyInstaller --clean --noconfirm pdfxlate.spec

echo "==> [4/5] Building TypeScript"
cd "$ROOT"
npm run build

echo "==> [5/5] Packaging with electron-builder"
# --no-install: fail loudly if it is missing instead of silently downloading a
# different version from the network mid-build.
npx --no-install electron-builder --publish never

echo
echo "Done. Installers are in $ROOT/dist"
