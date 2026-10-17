# PyInstaller spec for the pdfxlate sidecar.
#
# Build with:  pyinstaller --clean pdfxlate.spec
# Output:      dist/pdfxlate-sidecar/  (onedir)
#
# onedir rather than onefile: onnxruntime, opencv and PyMuPDF ship large
# native libraries, and onefile would extract them to a temp directory on
# every launch — slow, and prone to antivirus interference on Windows.

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules

import importlib.util


def _collect_if_installed(*packages):
    """collect_submodules for each package that is actually installed.

    The mcp SDK is an optional extra (see pyproject.toml), so a bundle built
    without it must still build — it just ships without the MCP entry point.
    """
    found = []
    for package in packages:
        if importlib.util.find_spec(package) is not None:
            found += collect_submodules(package)
    return found


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

# Native libraries for the ONNX runtime, OpenCV and fontTools.
binaries = []
for package in ("onnxruntime", "cv2", "numpy", "pymupdf", "fontTools"):
    try:
        binaries += collect_dynamic_libs(package)
    except Exception:
        pass

datas = []

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
