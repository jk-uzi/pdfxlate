"""Stage runtime assets that must ship inside the installer.

The layout model is downloaded from the HuggingFace mirror on first run, which
works on networks where the official host is blocked. Fonts are different:
they only exist as GitHub *release* assets, and that host was unreachable from
the network this project was developed on (8/8 probes timed out). Shipping the
default Chinese face inside the bundle is therefore what makes a fresh install
usable without asking the user to find a font themselves.

Run by scripts/build.sh before PyInstaller. The output directory
(``python/bundle_assets``) is gitignored and picked up by pdfxlate.spec.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "python" / "bundle_assets"

# The package is installed editable in the venv the build script selected, but
# adding the source root keeps this working from a plain checkout too.
sys.path.insert(0, str(ROOT / "python"))

from pdfxlate import assets  # noqa: E402


def stage(lang: str, *, required: bool) -> None:
    try:
        source = assets.ensure_font(lang)
    except Exception as exc:  # noqa: BLE001 - the message is what matters
        if required:
            raise SystemExit(
                f"error: could not obtain the {lang!r} font for bundling: {exc}\n"
                "A release must not ship without it: on networks where GitHub "
                "release assets are blocked, the app cannot translate to that "
                "language at all."
            )
        print(
            f"warning: {lang!r} font not bundled (it will be downloaded on "
            f"first use if the network allows): {exc}"
        )
        return

    target = DEST / "fonts" / source.name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    print(f"staged {target.relative_to(ROOT)} ({target.stat().st_size} bytes)")


def main() -> int:
    if DEST.exists():
        shutil.rmtree(DEST)
    DEST.mkdir(parents=True)

    # zh is the default target and the one whose download was verified broken.
    stage("zh", required=True)
    # GoNoto covers non-CJK targets. It is not the default, and its release
    # asset is only intermittently reachable, so do not fail the build over it.
    stage("ru", required=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
