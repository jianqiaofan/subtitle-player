# -*- mode: python ; coding: utf-8 -*-
"""CPU-only onedir build. Whisper models and ffmpeg are added by build_onedir.py."""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

APP = Path(SPECPATH).resolve().parent
VD = APP / "video_downloader"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))
if str(VD) not in sys.path:
    sys.path.insert(0, str(VD))

block_cipher = None

hidden = []
datas = [
    (str(APP / "config.json.example"), "."),
    (str(VD / "config" / "default_settings.json"), "video_downloader/config"),
]
binaries = []

for package in (
    "PyQt6",
    "pywhispercpp",
    "customtkinter",
    "yt_dlp",
    "PIL",
    "jieba",
    "janome",
    "jamdict",
    "jamdict_data",
    "nltk",
    "opencc",
    "jaconv",
    "pronouncing",
    "certifi",
):
    try:
        hidden += collect_submodules(package)
        datas += collect_data_files(package)
        binaries += collect_dynamic_libs(package)
    except Exception:
        pass

# Janome 把词典 .py 当二进制 mmap 打开，必须落在磁盘上，不能只打进 PYZ。
datas += collect_data_files("janome", include_py_files=True)

hidden += collect_submodules("app")
hidden += [
    "gui.player_window",
    "gui.main_window",
    "app.ui.main_window",
    "app.core.downloader",
    "app.plugins.ytdlp_plugin",
]

_CUDA_MARKERS = ("ggml-cuda", "cudart", "cublas", "cufft", "curand", "cusparse", "nvrtc", "nvcuda")


def _keep_binary(item) -> bool:
    name = str(item[0]).lower()
    return not any(marker in name for marker in _CUDA_MARKERS)


binaries = [item for item in binaries if _keep_binary(item)]

a = Analysis(
    [str(APP / "main.py")],
    pathex=[str(APP), str(VD)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["torch", "tensorflow", "numpy.tests", "scipy", "pandas", "matplotlib", "sklearn", "IPython", "pytest"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
a.binaries = [item for item in a.binaries if _keep_binary(item)]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="字幕播放器",
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
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="字幕播放器",
)
