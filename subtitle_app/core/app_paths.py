"""开发目录与 onedir 安装包共用的路径。"""

from __future__ import annotations

import sys
from pathlib import Path

WHISPER_MODEL_FILES = (
    "win系统模型最小.bin",
    "win系统模型中等.bin",
    "win系统模型最大.bin",
)
_PREFERRED_MODEL_ORDER = (
    "win系统模型中等.bin",
    "win系统模型最大.bin",
    "win系统模型最小.bin",
)


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    """可写的程序目录：打包后是 exe 所在文件夹，开发时是 subtitle_app。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def bundle_dir() -> Path:
    """只读资源目录。PyInstaller onedir 下为 _internal。"""
    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return app_dir()
    return app_dir()


def repo_root() -> Path:
    if is_frozen():
        return app_dir()
    return app_dir().parent


def models_dir() -> Path:
    """三个 Whisper 模型的默认位置：程序目录下的 models 子目录。"""
    if is_frozen():
        return app_dir() / "models"
    return repo_root() / "models"


def ffmpeg_executable(name: str) -> Path | None:
    for folder in (app_dir() / "ffmpeg", bundle_dir() / "ffmpeg"):
        candidate = folder / name
        if candidate.is_file():
            return candidate
    return None


def default_model_files() -> list[Path]:
    folder = models_dir()
    return [folder / name for name in WHISPER_MODEL_FILES if (folder / name).is_file()]


def preferred_model_file() -> Path | None:
    folder = models_dir()
    for name in _PREFERRED_MODEL_ORDER:
        path = folder / name
        if path.is_file():
            return path
    return None


def resolve_model_path(configured: str) -> str:
    """已有有效路径则保留；否则用 models 目录中的默认模型。都没有则返回空字符串。"""
    text = (configured or "").strip()
    if text and Path(text).is_file():
        return str(Path(text))
    preferred = preferred_model_file()
    if preferred is not None:
        return str(preferred)
    return ""
