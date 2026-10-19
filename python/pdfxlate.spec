# PyInstaller spec for the pdfxlate sidecar.
#
# Build with:  pyinstaller --clean pdfxlate.spec
# Output:      dist/pdfxlate-sidecar/  (onedir)
#
# onedir rather than onefile: onnxruntime, opencv and PyMuPDF ship large
# native libraries, and onefile would extract them to a temp directory on
# every launch — slow, and prone to antivirus interference on Windows.

from PyInstaller.utils.hooks import (
    collect_all,
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)

import importlib.util
from pathlib import Path


def _collect_if_installed(*packages):
    """collect_submodules for each package that is actually installed.

    The mcp SDK is an optional extra (see pyproject.toml), so a bundle built
    without it must still build — it just ships without the MCP entry point.

    ``mcp.cli`` is excluded: it imports ``typer`` (the SDK's ``cli`` extra),
    which the server does not need, and collect_submodules imports every
    submodule to discover its children. ``on_error="ignore"`` covers other
    optional-dependency modules the same way.
    """
    found = []
    for package in packages:
        if importlib.util.find_spec(package) is not None:
            found += collect_submodules(
                package,
                filter=lambda name: not name.startswith("mcp.cli"),
                on_error="ignore",
            )
    return found


def _collect_optional_all(package):
    """Collect data, binaries and hidden imports for an installed package.

    ``pdf-inspector`` is an optional extra and ``cryptography`` is imported
    lazily by the WorkBuddy desktop backend.  Both have native extensions or
    data that static analysis can miss; collecting them explicitly makes the
    packaged app behave like the development environment.  A missing package
    is not fatal (the UI/backend reports the missing optional feature).
    """
    if importlib.util.find_spec(package) is None:
        return [], [], []
    try:
        return collect_all(package)
    except Exception:
        return [], [], []


_pdf_inspector_datas, _pdf_inspector_binaries, _pdf_inspector_hidden = (
    _collect_optional_all("pdf_inspector")
)
_cryptography_datas, _cryptography_binaries, _cryptography_hidden = (
    _collect_optional_all("cryptography")
)


hiddenimports = [
    # pdfminer resolves its submodules dynamically; the ported interpreter
    # touches internals that static analysis does not see.
    *collect_submodules("pdfminer"),
    # Loaded lazily inside functions in our code.
    "pdfxlate.pdf.ultrafast",
    "pdfxlate.pdf.ocr",
    "pdfxlate.models.openai_compat",
    # The other two adapters are imported only for their @register side effect,
    # so static analysis would drop them and the packaged app would report an
    # unknown provider for both.
    "pdfxlate.models.openai_responses",
    "pdfxlate.models.anthropic_messages",
    # The agent CLI adapter is also imported only for its @register side effect.
    "pdfxlate.models.agent_cli",
    # Imported lazily / through the adapter; naming them explicitly keeps the
    # packaged Agents panel and WorkBuddy desktop backend complete even if the
    # import chain changes later.
    "pdfxlate.agents.workbuddy_desktop",
    "pdfxlate.agents.zcode_app_server",
    # WorkBuddy desktop auth decrypts AES-GCM fields with cryptography; the
    # import is inside a function, so collect its native extension explicitly.
    *_cryptography_hidden,
    # The optional ultrafast layout path imports pdf_inspector lazily.
    *_pdf_inspector_hidden,
    # The OpenAI SDK lazily resolves resource/type modules through
    # ``__getattr__``; static analysis cannot see that dispatch. Collect the
    # package so a packaged adapter never dies on a missing openai.* module.
    *collect_submodules("openai", on_error="ignore"),
    # PyMuPDF imports this from inside a method when Document.subset_fonts()
    # runs. It is a direct dependency, but naming it here removes any doubt.
    "fontTools.subset",
    # The MCP server is an alternative entry point into the same bundle, so a
    # packaged build can serve an agent as well as the desktop app.  It is a
    # hidden import because nothing in the sidecar entry point references it, so
    # static analysis would drop it.
    #
    # Launched as ``pdfxlate-sidecar --mcp``: an onedir bundle has no separate
    # interpreter, so the binary itself has to be the entry point and the flag
    # selects which one.  The reader lives in sidecar.py's main().
    "pdfxlate.mcp_server",
    # The mcp SDK and its separate ``mcp_types`` package import transport and
    # type modules lazily, which static analysis does not follow.  Both are
    # optional, hence the guard.
    *_collect_if_installed("mcp", "mcp_types"),
]

# Native libraries for the ONNX runtime, OpenCV, fontTools, and the optional
# pdf-inspector / cryptography extensions.
binaries = [*_pdf_inspector_binaries, *_cryptography_binaries]
for package in ("onnxruntime", "cv2", "numpy", "pymupdf", "fontTools"):
    try:
        binaries += collect_dynamic_libs(package)
    except Exception:
        pass

datas = [
    # pdfminer loads its CMap tables from pdfminer/cmap/*.pickle.gz at runtime
    # via os.path.dirname(__file__). collect_submodules() alone does not bring
    # those files, and without them CID-keyed PDFs (common for Chinese and
    # Japanese documents) fail to parse in the packaged app.
    *collect_data_files("pdfminer"),
    *_pdf_inspector_datas,
    *_cryptography_datas,
]

# Fonts staged by scripts/stage_assets.py. Bundling the default Chinese face
# removes the only first-run download that was reliably unreachable (GitHub
# release assets); the layout model still downloads from the HF mirror.
_bundle_assets = Path("bundle_assets")
if _bundle_assets.is_dir():
    datas.append((str(_bundle_assets), "bundle_assets"))

a = Analysis(
    ["pdfxlate/sidecar.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # Keep the bundle lean: none of these are used at runtime.
        "tkinter",
        "matplotlib",
        "pytest",
        "IPython",
        "PIL.ImageQt",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="pdfxlate-sidecar",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX corrupts some onnxruntime DLLs on Windows.
    console=True,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="pdfxlate-sidecar",
)
