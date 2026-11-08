# -*- mode: python ; coding: utf-8 -*-
# The Windows app: one folder, two exes, one entry (evolvekit/app/entry.py).
# Built by `python tasks.py dist` (packaging/README.md), never by hand-picked
# PyInstaller flags. PyInstaller runs this file with SPECPATH, Analysis, PYZ,
# EXE and COLLECT defined.

import importlib.util
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent

# Loaded by its path: packaging/ has no __init__.py, so `import packaging`
# stays the PyPI library PyInstaller itself imports.
_spec = importlib.util.spec_from_file_location("evolvekit_bundle_files", ROOT / "packaging" / "bundle_files.py")
bundle_files = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bundle_files)

a = Analysis(
    [str(ROOT / "evolvekit" / "app" / "entry.py")],
    pathex=[str(ROOT)],
    datas=bundle_files.data_files(ROOT),
    # evolvekit imports much of itself lazily (commands, providers, jobs run as
    # `-m`), and a runner may import any standard module (D14); the file
    # picker's tkinter is code in a string (evolvekit/app/api.py).
    hiddenimports=collect_submodules("evolvekit") + bundle_files.stdlib_modules() + ["tkinter", "tkinter.filedialog"],
    excludes=["pytest", "_pytest", "pyvrp", "IPython"],
)
pyz = PYZ(a.pure)

# Unbuffered and UTF-8 mode: the frozen interpreter ignores PYTHONUNBUFFERED
# and PYTHONIOENCODING (which a run's launch sets), so a run's log would be
# buffered and cp1252 without them (D16).
options = [("u", None, "OPTION"), ("X utf8", None, "OPTION")]

exe_app = EXE(
    pyz,
    a.scripts,
    options,
    exclude_binaries=True,
    name="evolvekit-app",
    console=False,
    upx=False,
)
exe_cli = EXE(
    pyz,
    a.scripts,
    options,
    exclude_binaries=True,
    name="evolvekit-cli",
    console=True,
    upx=False,
)
coll = COLLECT(
    exe_app,
    exe_cli,
    a.binaries,
    a.datas,
    name="evolvekit-app",
    upx=False,
)
