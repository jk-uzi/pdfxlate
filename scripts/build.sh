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
VENV_PY="$PY_DIR/.venv/bin/python"

if [[ ! -x "$VENV_PY" ]]; then
  echo "error: Python venv not found at $VENV_PY" >&2
  echo "create it with:" >&2
  echo "  cd python && python -m venv .venv && .venv/bin/pip install -e '.[dev]'" >&2
  exit 1
fi

echo "==> [1/3] Building Python sidecar"
cd "$PY_DIR"
"$VENV_PY" -m PyInstaller --clean --noconfirm pdfxlate.spec

echo "==> [2/3] Building TypeScript"
cd "$ROOT"
npm run build

echo "==> [3/3] Packaging with electron-builder"
npx electron-builder --publish never

echo
echo "Done. Installers are in $ROOT/dist"
