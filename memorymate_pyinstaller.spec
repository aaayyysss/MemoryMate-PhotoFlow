# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for MemoryMate-PhotoFlow
Includes proper packaging of InsightFace models and dependencies
"""

import os
import sys
from pathlib import Path

block_cipher = None

# Get the InsightFace models directory
insightface_models_dir = os.path.expanduser('~/.insightface/models/buffalo_l')

# Collect all model files
model_datas = []
if os.path.exists(insightface_models_dir):
    for root, dirs, files in os.walk(insightface_models_dir):
        for file in files:
            src = os.path.join(root, file)
            # Map to insightface/models/buffalo_l in the bundle
            rel_path = os.path.relpath(src, os.path.dirname(insightface_models_dir))
            dst = os.path.join('insightface', 'models', os.path.dirname(rel_path))
            model_datas.append((src, dst))
    print(f"Found {len(model_datas)} model files in {insightface_models_dir}")
else:
    print(f"WARNING: InsightFace models not found at {insightface_models_dir}")
    print("Please run face detection once to download models before packaging")

# Additional data files
datas = [
    # Add your project data files here
    ('reference_data.db', '.'),  # if you want to bundle the database
    # Add any other resources (icons, config files, etc.)
]

# Add model files
datas.extend(model_datas)

# Hidden imports for ML libraries
hiddenimports = [
    'insightface',
    'insightface.app',
    'insightface.model_zoo',
    'onnxruntime',
    'onnxruntime.capi',
    'onnxruntime.capi.onnxruntime_pybind11_state',
    'numpy',
    'cv2',
    'PIL',
    'PIL.Image',
    'PIL.ImageOps',
    'sklearn',
    'sklearn.cluster',
    'sklearn.preprocessing',
    'PySide6',
    'PySide6.QtCore',
    'PySide6.QtGui',
    'PySide6.QtWidgets',
]

a = Analysis(
    ['main_qt.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['pyi_rth_insightface.py'],  # Hook to set InsightFace model paths
    excludes=[
        'matplotlib',  # Exclude if not needed
        'tkinter',     # Exclude if not needed
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MemoryMate-PhotoFlow',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,  # Set to False for windowed app (no console)
    disable_windowing_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,  # Add path to .ico file if you have one
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='MemoryMate-PhotoFlow'
)
