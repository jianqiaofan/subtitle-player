from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from core.app_paths import ffmpeg_executable


def find_ffmpeg() -> str:
    bundled = ffmpeg_executable("ffmpeg.exe")
    if bundled is not None:
        return str(bundled)
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        return ffmpeg
    raise RuntimeError("未找到 ffmpeg。请将 ffmpeg.exe 放到程序目录的 ffmpeg 文件夹，或加入 PATH。")


def find_ffprobe() -> str:
    bundled = ffmpeg_executable("ffprobe.exe")
    if bundled is not None:
        return str(bundled)
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        return ffprobe
    raise RuntimeError("未找到 ffprobe。请将 ffprobe.exe 放到程序目录的 ffmpeg 文件夹，或加入 PATH。")


def get_media_duration(media_path: Path) -> float:
    """获取媒体时长（秒）。"""
    cmd = [
        find_ffprobe(),
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(media_path),
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(f"无法读取媒体时长：\n{result.stderr[-1000:]}")
    try:
        return float(result.stdout.strip())
    except ValueError as exc:
        raise RuntimeError("无法解析媒体时长") from exc


def extract_audio(media_path: Path, work_dir: Path) -> Path:
    """将音视频转为 16kHz 单声道 WAV，供 Whisper 使用。"""
    work_dir.mkdir(parents=True, exist_ok=True)
    output = work_dir / f"{media_path.stem}.wav"
    if media_path.suffix.lower() == ".wav":
        return media_path

    cmd = [
        find_ffmpeg(),
        "-y",
        "-i", str(media_path),
        "-vn",
        "-ar", "16000",
        "-ac", "1",
        "-c:a", "pcm_s16le",
        str(output),
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(f"音频提取失败：\n{result.stderr[-2000:]}")
    if not output.exists() or output.stat().st_size < 1024:
        raise RuntimeError(
            f"提取的音频过小或为空（{output.stat().st_size if output.exists() else 0} 字节），"
            f"请检查源文件是否包含音轨。"
        )
    return output
