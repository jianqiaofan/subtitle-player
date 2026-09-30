"""从本机视频抽出一帧，或把原图压成上传用的 WebP。"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from core.audio import find_ffmpeg

_MAX_EDGE = 1280
_WEBP_QUALITY = "40"


def extract_video_frame(media_path: Path, seconds: float, dest: Path) -> bool:
    """按播放时间抽出一帧，存成 PNG。抽不出时返回 False。"""
    if seconds < 0 or not media_path.is_file():
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    temporary = dest.with_name(dest.name + ".part.png")
    command = [
        find_ffmpeg(),
        "-y",
        "-ss",
        f"{float(seconds):.3f}",
        "-i",
        str(media_path),
        "-frames:v",
        "1",
        "-f",
        "image2",
        str(temporary),
    ]
    if not _run(command) or not temporary.is_file() or temporary.stat().st_size <= 0:
        temporary.unlink(missing_ok=True)
        return False
    temporary.replace(dest)
    return dest.is_file() and dest.stat().st_size > 0


def compress_png_to_webp(png_path: Path) -> bytes:
    """长边不超过 1280，不放大小图，WebP 质量 40。"""
    if not png_path.is_file():
        raise OSError("没有可压缩的原图")
    dest = png_path.with_name(png_path.stem + ".upload.webp")
    command = [
        find_ffmpeg(),
        "-y",
        "-i",
        str(png_path),
        "-vf",
        f"scale={_MAX_EDGE}:{_MAX_EDGE}:force_original_aspect_ratio=decrease",
        "-c:v",
        "libwebp",
        "-quality",
        _WEBP_QUALITY,
        str(dest),
    ]
    try:
        if not _run(command) or not dest.is_file() or dest.stat().st_size <= 0:
            raise OSError("无法生成压缩截图")
        data = dest.read_bytes()
    finally:
        dest.unlink(missing_ok=True)
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise OSError("无法生成压缩截图")
    return data


def _run(command: list[str]) -> bool:
    try:
        kwargs: dict = {"capture_output": True}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        result = subprocess.run(command, **kwargs)
    except OSError:
        return False
    return result.returncode == 0
