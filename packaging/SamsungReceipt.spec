# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build for the single-user Windows distribution."""

from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
    copy_metadata,
)


SPEC_LOCATION = Path(SPECPATH).resolve()
SPEC_DIR = SPEC_LOCATION if SPEC_LOCATION.is_dir() else SPEC_LOCATION.parent
ROOT = SPEC_DIR.parent

datas = [
    (str(ROOT / "templates"), "templates"),
    (str(ROOT / "static"), "static"),
    (str(ROOT / "数据" / "ground_truth.json"), "数据"),
    (str(ROOT / "数据" / "document_ground_truth.json"), "数据"),
]
binaries = []
hiddenimports = []

# PaddleOCR loads model and pipeline modules lazily.  Collecting the package
# modules here avoids a first-run failure that only appears after packaging.
for package in ("paddle", "paddleocr", "paddlex"):
    datas += collect_data_files(package, include_py_files=False)
    binaries += collect_dynamic_libs(package)
    hiddenimports += collect_submodules(package)

# PaddleOCR/PaddleX inspect distribution metadata at runtime when they verify
# the ``ocr-core`` extra. PyInstaller does not bundle ``*.dist-info`` folders
# by default, so the frozen app can contain every module yet still report a
# dependency error while creating the pipeline.
for distribution in (
    "paddlepaddle",
    "paddleocr",
    "paddlex",
    "imagesize",
    "opencv-contrib-python",
    "pyclipper",
    "pypdfium2",
    "python-bidi",
    "shapely",
):
    datas += copy_metadata(distribution)

datas += collect_data_files("cv2", include_py_files=False)
binaries += collect_dynamic_libs("cv2")

a = Analysis(
    [str(ROOT / "app.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SamsungReceipt",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="SamsungReceipt",
)
