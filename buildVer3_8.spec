# -*- mode: python ; coding: utf-8 -*-
#from PyInstaller.utils.crypto import PyInstallerCryptoKey
#block_cipher = PyInstallerCryptoKey(b'123456789')

block_cipher = None

a = Analysis(
    ['main_qt.py'],
    pathex=['.'],
    binaries=[],
	datas=[
        ('app_icon.ico', '.'),                        # icon
        ('MemoryMate-PhotoFlow-logo.jpg', '.'),       # images
        ('MemoryMate-PhotoFlow-logo.png', '.'), 
        ('photo_app_settings.json', '.'),
		# include meta_backfill scripts so they exist as files next to exe
        ('meta_backfill_pool.py', '.'),
        ('meta_backfill_single.py', '.'),  # if you have this file
#		('models/**/*', 'models'),
        ('workers/*.py', 'workers'),
#        ('resources/**/*', 'resources'),
#        ('settings/*.json', 'settings'),
    ],

    hiddenimports=[
        # Core PySide6
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
        'PySide6.QtXml',

        # App-specific modules
        'reference_db',
        'app_services',
        'sidebar_qt',
        'thumbnail_grid_qt',
        'main_window_qt',
        'settings_manager_qt',
        'splash_qt',
        'thumb_cache_db',
        'preview_panel_qt',
        'meta_backfill_pool',
        'meta_backfill_single',
        'db_writer',
		
		
        # Standard libs used dynamically
        'datetime',
        'time',
        'os',
        'pathlib',
        'sqlite3',
		'cv2',
		'numpy',
		'onnxruntime'
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='Memory-Mate Photo-Flow 3.8',
    debug=True,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
#    runtime_tmpdir=None,
    console=True,
	icon='app_icon.ico',       # App icon
	runtime_tmpdir='.',   # forces extraction in current folder
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='PhotoFlowApp'
)