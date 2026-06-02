# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


# patchright / playwright: collect all submodules + data
playwright_hiddenimports = []
playwright_datas = []
for package in ("playwright", "patchright"):
    playwright_hiddenimports += collect_submodules(package)
    playwright_datas += collect_data_files(package)

# 显式收集含二进制 .pyd 的包，避免被遗漏进 PYZ
extra_hiddenimports = []
extra_datas = []
for pkg in ("PIL", "lxml", "cryptography", "bcrypt",
            "pydantic_core", "charset_normalizer", "certifi",
            "fitz", "pymupdf", "docx2pdf", "openai", "httpx", "pydantic",
            "win32com", "win32api", "win32con", "pythoncom", "pywintypes"):
    extra_hiddenimports += collect_submodules(pkg)
    extra_datas += collect_data_files(pkg)

all_hiddenimports = playwright_hiddenimports + extra_hiddenimports
all_datas = [('app_icon.png', '.')] + playwright_datas + extra_datas


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=all_datas,
    hiddenimports=all_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ReCreate AI',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ReCreate AI',
)
