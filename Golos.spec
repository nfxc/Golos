# Модели лежат отдельно в models, в exe их не зашиваем.
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

root = Path(SPECPATH)

datas = [(str(root / "assets"), "assets")]
binaries = []
hiddenimports = []

for package in ("onnx_asr", "faster_whisper", "ctranslate2", "tokenizers", "onnxruntime", "av"):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

excludes = [
    "torch",
    "torchvision",
    "torchaudio",
    "tensorflow",
    "transformers",
    "scipy",
    "sklearn",
    "pandas",
    "matplotlib",
    "cv2",
    "PIL",
    "tkinter",
    "IPython",
    "notebook",
    "jupyter",
    "pytest",
    "sentencepiece",
    "soundfile",
    "librosa",
    "numba",
    "llvmlite",
    "pyarrow",
]

a = Analysis(
    [str(root / "app" / "main.py")],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Golos",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(root / "assets" / "icon.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Golos",
)
