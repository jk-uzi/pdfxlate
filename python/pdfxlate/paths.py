"""Cross-platform path resolution.

Three locations are resolved here:

* **config** — user-edited settings.  The desktop app overrides this to its
  ``app.getPath('userData')`` via ``--config-dir``.
* **cache** — the translation cache database.
* **assets** — the downloaded layout model, fonts and OCR data.

Only *config* honours the override.  Caches and assets always use the
platform-standard location so the CLI and the desktop app share one copy;
otherwise the 75 MB layout model would be downloaded once per entry point.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "pdfxlate"


def _override_root() -> Path | None:
    """Root directory forced by the host application, if any.

    Set by the CLI ``--config-dir`` flag or the ``PDFXLATE_CONFIG_DIR``
    environment variable.  Electron uses this to keep every artefact inside
    the OS-appropriate application data directory.
    """
    for source in (
        os.environ.get("PDFXLATE_CONFIG_DIR"),
        _config_dir_from_argv(),
    ):
        if source:
            return Path(source).expanduser().resolve()
    return None


def _config_dir_from_argv() -> str | None:
    argv = sys.argv
    for i, arg in enumerate(argv):
        if arg == "--config-dir" and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith("--config-dir="):
            return arg.split("=", 1)[1]
    return None


def config_dir() -> Path:
    """Directory holding ``config.yaml``."""
    if root := _override_root():
        return root
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return Path(base) / APP_NAME


def cache_dir() -> Path:
    """Directory holding the translation cache database."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache"))
    return Path(base) / APP_NAME


def assets_dir() -> Path:
    """Directory holding downloaded models, fonts and OCR data.

    Shared between the CLI and the desktop app; see the module docstring.
    """
    return cache_dir() / "assets"


def config_file() -> Path:
    return config_dir() / "config.yaml"


def cache_file() -> Path:
    return cache_dir() / "cache.v1.db"


def ensure_dirs() -> None:
    """Create every directory the application writes to."""
    for path in (config_dir(), cache_dir(), assets_dir()):
        path.mkdir(parents=True, exist_ok=True)
