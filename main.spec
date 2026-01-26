# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import (
    collect_submodules,
    collect_dynamic_libs,
    collect_data_files,
)
import sys
from PyInstaller.utils.hooks import collect_submodules
import os

# Set icon based on platform
if sys.platform.startswith("win"):
    app_icon = r"assets/icons/TrueEditor.ico"
elif sys.platform == "darwin":
    app_icon = r"assets/icons/TrueEditor.icns"
else:  # Linux/others
    app_icon = None  # Linux usually ignores PyInstaller icon

block_cipher = None

# -------------------------------
# Hidden imports (PURE PYTHON ONLY)
# -------------------------------
hiddenimports = (
    collect_submodules('PySide6')
    + collect_submodules('whisper')
    + collect_submodules('pysubs2')
    + collect_submodules('tqdm')
    + collect_submodules('df')
)

# -------------------------------
# Native binaries (C / CUDA / DSP)
# -------------------------------
binaries = (
    collect_dynamic_libs('torch')
    + collect_dynamic_libs('torchaudio')
    + collect_dynamic_libs('numpy')
    + collect_dynamic_libs('scipy')
)

# -------------------------------
# Data files
# -------------------------------
datas = (
    collect_data_files('whisper') + collect_data_files('assets') + collect_data_files('Core')
    + [
        ('assets', 'assets'),
        ('Core', 'Core'),
        ('Audio', 'Audio'),
    ]
)


# -------------------------------
# Analysis
# -------------------------------
a = Analysis(
    ['main.py'],
    pathex=[os.path.abspath(".")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

# -------------------------------
# PYZ
# -------------------------------
pyz = PYZ(
    a.pure,
    a.zipped_data,
    cipher=block_cipher,
)

# -------------------------------
# Executable
# -------------------------------
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='TrueEditor',
    debug=False,
    bootloader_ignore_signals=False,

    strip=False,      # REQUIRED for torch/numpy
    upx=False,        # safer OFF for ML stacks

    console=False,    # GUI app
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    copyright='Copyright (c) 2026 KLJ Enterprises, LLC.',
    icon=app_icon,
)
