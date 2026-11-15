# -*- mode: python ; coding: utf-8 -*-
"""Spec PyInstaller — build : pyinstaller packaging/tradehelper.spec
Produit un exécutable autonome dist/tradeHelper[.exe] (GUI Tkinter + matplotlib).
Les extras GPU (cupy/xgboost) et Parquet (pyarrow) restent optionnels et ne sont
pas embarqués : installer les requirements correspondants dans l'environnement cible.
"""

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

hiddenimports = []
hiddenimports += collect_submodules("tradehelper")
hiddenimports += collect_submodules("sklearn")
hiddenimports += ["yfinance", "requests", "PIL"]

datas = []
datas += collect_data_files("matplotlib")

a = Analysis(
    ["run.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["cupy", "xgboost", "pyarrow", "torch", "tkinter.scrolledtext"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="tradeHelper",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # GUI : pas de console (mettre True pour déboguer)
    icon=None,
)
