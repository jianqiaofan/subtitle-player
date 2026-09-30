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
    """先找配套文件夹，再找视频旁边的旧位置。同名文件以配套文件夹为准。"""
    from core.media_bundle import bundle_dir

    media_stem = media_path.stem
    results: list[tuple[Path, str]] = []
    seen: set[str] = set()
    for folder in (bundle_dir(media_path), media_path.parent):
        if not folder.is_dir():
            continue
        for file_path in sorted(folder.iterdir()):
            if not file_path.is_file() or file_path.name in seen:
                continue
            if file_path.suffix.lower() not in SUBTITLE_EXTENSIONS:
                continue
            stem = file_path.stem
            if stem == media_stem or stem.startswith(f"{media_stem}_"):
                seen.add(file_path.name)
                label = subtitle_display_name(media_stem, file_path)
                results.append((file_path, label))
    return results


def find_valid_subtitles(media_path: Path) -> list[tuple[Path, str]]:
    """同目录下能成功加载、且含有字幕内容的文件。"""
    valid: list[tuple[Path, str]] = []
    for path, label in find_subtitles_for_media(media_path):
        try:
            segments = load_subtitle_file(path)
        except (OSError, ValueError):
            continue
        if segments:
            valid.append((path, label))
    return valid


def load_subtitles(path: Path) -> list[SubtitleSegment]:
    return load_subtitle_file(path)
