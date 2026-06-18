from __future__ import annotations

from pathlib import Path

from core.subtitle import SubtitleSegment, load_subtitle_file

SUBTITLE_EXTENSIONS = {".srt", ".vtt"}


def subtitle_display_name(media_stem: str, subtitle_path: Path) -> str:
    stem = subtitle_path.stem
    if stem == media_stem:
        return "默认"
    prefix = f"{media_stem}_"
    if stem.startswith(prefix):
        return stem[len(prefix) :]
    return subtitle_path.name


def find_subtitles_for_media(media_path: Path) -> list[tuple[Path, str]]:
    """在同目录下查找与视频前缀匹配的字幕文件。"""
    folder = media_path.parent
    media_stem = media_path.stem
    results: list[tuple[Path, str]] = []

    for file_path in sorted(folder.iterdir()):
        if not file_path.is_file():
            continue
        if file_path.suffix.lower() not in SUBTITLE_EXTENSIONS:
            continue
        stem = file_path.stem
        if stem == media_stem or stem.startswith(f"{media_stem}_"):
            label = subtitle_display_name(media_stem, file_path)
            results.append((file_path, label))

    return results


def load_subtitles(path: Path) -> list[SubtitleSegment]:
    return load_subtitle_file(path)
