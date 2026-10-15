# PyInstaller spec for the pdfxlate sidecar.
#
# Build with:  pyinstaller --clean pdfxlate.spec
# Output:      dist/pdfxlate-sidecar/  (onedir)
#
# onedir rather than onefile: onnxruntime, opencv and PyMuPDF ship large
# native libraries, and onefile would extract them to a temp directory on
# every launch — slow, and prone to antivirus interference on Windows.

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules

hiddenimports = [
    # pdfminer resolves its submodules dynamically; the ported interpreter
    # touches internals that static analysis does not see.
    *collect_submodules("pdfminer"),
    # Loaded lazily inside functions in our code.
    "pdfxlate.pdf.ultrafast",
    "pdfxlate.pdf.ocr",
    "pdfxlate.models.openai_compat",
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
