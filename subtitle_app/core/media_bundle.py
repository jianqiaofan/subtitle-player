"""每个视频旁边的配套文件夹：视频全名.data。

字幕、标签和哈希文件放在这里。视频文件仍留在原来的文件夹。
转录工具仍会把新字幕写在视频旁边，打开时再收进配套文件夹。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.subtitle_tags import subtitle_belongs_to_media

DATA_SUFFIX = ".data"
_SUBTITLE_EXTENSIONS = {".srt", ".vtt"}
TIMELINE_SUFFIX = "_时间线.srt"


@dataclass(frozen=True)
class SubtitleSpot:
    path: Path
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class SubtitleConflict:
    name: str
    bundled: SubtitleSpot
    legacy: SubtitleSpot


def bundle_dir(media_path: Path) -> Path:
    return media_path.parent / f"{media_path.name}{DATA_SUFFIX}"


def ensure_bundle_dir(media_path: Path) -> Path:
    folder = bundle_dir(media_path)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def is_bundle_dir(path: Path) -> bool:
    return path.name.endswith(DATA_SUFFIX)


def timeline_subtitle_name(media_path: Path) -> str:
    return f"{media_path.stem}{TIMELINE_SUFFIX}"


def timeline_virtual_path(media_path: Path) -> Path:
    """时间线不落成字幕文件，这个路径只用来给标签文件起名。"""
    return bundle_dir(media_path) / timeline_subtitle_name(media_path)


def timeline_tag_path(media_path: Path) -> Path:
    virtual = timeline_virtual_path(media_path)
    return virtual.with_name(virtual.name + ".tags.json")


def subtitle_write_path(media_path: Path, suffix: str) -> Path:
    return bundle_dir(media_path) / f"{media_path.stem}{suffix}"


def subtitle_read_path(media_path: Path, suffix: str) -> Path:
    """已有字幕优先读配套文件夹，否则读视频旁边的旧位置。都没有时返回将要写入的路径。"""
    name = f"{media_path.stem}{suffix}"
    bundled = bundle_dir(media_path) / name
    if bundled.is_file():
        return bundled
    legacy = media_path.parent / name
    if legacy.is_file():
        return legacy
    return bundled


def tag_write_path(media_path: Path, suffix: str) -> Path:
    subtitle = subtitle_write_path(media_path, suffix)
    return subtitle.with_name(subtitle.name + ".tags.json")


def tag_read_path(media_path: Path, suffix: str) -> Path:
    write_path = tag_write_path(media_path, suffix)
    if write_path.is_file():
        return write_path
    legacy = media_path.parent / f"{media_path.stem}{suffix}.tags.json"
    if legacy.is_file():
        return legacy
    return write_path


def hash_cache_file(media_path: Path) -> Path:
    return bundle_dir(media_path) / f"{media_path.name}.videohash.json"


def legacy_hash_cache_file(media_path: Path) -> Path:
    return media_path.with_name(media_path.name + ".videohash.json")


def hash_search_dirs(subtitle_path: Path) -> list[Path]:
    """字幕可能还在视频旁边，哈希文件可能已经在配套文件夹里。两处都要找。"""
    folder = subtitle_path.parent
    found = [folder]
    if is_bundle_dir(folder):
        parent = folder.parent
        if parent not in found:
            found.append(parent)
        return found
    try:
        children = list(folder.iterdir())
    except OSError:
        return found
    for item in children:
        if item.is_dir() and is_bundle_dir(item):
            found.append(item)
    return found


def media_for_subtitle(subtitle_path: Path) -> Path | None:
    """配套文件夹里的字幕，对应上一级同名视频。视频旁边的字幕仍在同一层找。"""
    from core.config import MEDIA_EXTENSIONS

    parent = subtitle_path.parent
    if is_bundle_dir(parent):
        media = parent.parent / parent.name[: -len(DATA_SUFFIX)]
        if media.is_file() and media.suffix.lower() in MEDIA_EXTENSIONS:
            if subtitle_belongs_to_media(subtitle_path, media):
                return media
        return None
    try:
        entries = list(parent.iterdir())
    except OSError:
        return None
    for item in entries:
        if not item.is_file() or item.suffix.lower() not in MEDIA_EXTENSIONS:
            continue
        if subtitle_belongs_to_media(subtitle_path, item):
            return item
    return None


def _spot(path: Path) -> SubtitleSpot:
    stat = path.stat()
    return SubtitleSpot(path=path, created_at=stat.st_ctime, updated_at=stat.st_mtime)


def _owned_subtitles(folder: Path, media_path: Path) -> dict[str, Path]:
    if not folder.is_dir():
        return {}
    found: dict[str, Path] = {}
    try:
        entries = list(folder.iterdir())
    except OSError:
        return {}
    for item in entries:
        if not item.is_file() or item.suffix.lower() not in _SUBTITLE_EXTENSIONS:
            continue
        if item.name.endswith(".tags.json"):
            continue
        if not subtitle_belongs_to_media(item, media_path):
            continue
        found[item.name] = item
    return found


def _tag_sidecar(subtitle_path: Path) -> Path:
    return subtitle_path.with_name(subtitle_path.name + ".tags.json")


def _move_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()
    source.replace(destination)


def _delete_file(path: Path) -> None:
    if path.is_file():
        path.unlink()


def adopt_hash_cache(media_path: Path) -> None:
    legacy = legacy_hash_cache_file(media_path)
    if not legacy.is_file():
        return
    destination = hash_cache_file(media_path)
    if destination.is_file():
        legacy.unlink()
        return
    _move_file(legacy, destination)


def settle_media_sidecars(media_path: Path) -> list[SubtitleConflict]:
    """把只出现在视频旁边的字幕和标签收进配套文件夹。同名两边都有的留给用户选择。"""
    adopt_hash_cache(media_path)
    bundled = _owned_subtitles(bundle_dir(media_path), media_path)
    legacy = _owned_subtitles(media_path.parent, media_path)
    conflicts: list[SubtitleConflict] = []
    for name, source in legacy.items():
        current = bundled.get(name)
        if current is not None:
            conflicts.append(SubtitleConflict(name=name, bundled=_spot(current), legacy=_spot(source)))
            continue
        destination = _move_file_with_tag(source, ensure_bundle_dir(media_path) / name)
        bundled[name] = destination
    _adopt_orphan_tags(media_path, bundled)
    return conflicts


def _move_file_with_tag(source: Path, destination: Path) -> Path:
    tag_source = _tag_sidecar(source)
    _move_file(source, destination)
    tag_destination = _tag_sidecar(destination)
    if tag_source.is_file() and not tag_destination.exists():
        _move_file(tag_source, tag_destination)
    return destination


def _adopt_orphan_tags(media_path: Path, bundled: dict[str, Path]) -> None:
    for name, subtitle in bundled.items():
        legacy_tag = media_path.parent / f"{name}.tags.json"
        if not legacy_tag.is_file():
            continue
        destination = _tag_sidecar(subtitle)
        if destination.exists():
            continue
        _move_file(legacy_tag, destination)


def apply_subtitle_choice(media_path: Path, conflict: SubtitleConflict, winner: str) -> Path:
    """winner 为 bundle 或 legacy。胜出者留在配套文件夹，另一份字幕及其标签删除。"""
    folder = ensure_bundle_dir(media_path)
    destination = folder / conflict.name
    bundled = conflict.bundled.path
    legacy = conflict.legacy.path
    bundled_tag = _tag_sidecar(bundled)
    legacy_tag = _tag_sidecar(legacy)
    if winner == "legacy":
        _delete_file(bundled)
        _delete_file(bundled_tag)
        _move_file(legacy, destination)
        if legacy_tag.is_file():
            _move_file(legacy_tag, _tag_sidecar(destination))
        return destination
    _delete_file(legacy)
    _delete_file(legacy_tag)
    return bundled if bundled.is_file() else destination


def locate_subtitle_file(media_path: Path, subtitle_name: str) -> Path | None:
    bundled = bundle_dir(media_path) / subtitle_name
    if bundled.is_file() and subtitle_belongs_to_media(bundled, media_path):
        return bundled
    legacy = media_path.parent / subtitle_name
    if legacy.is_file() and subtitle_belongs_to_media(legacy, media_path):
        return legacy
    return None


def remove_legacy_subtitle(media_path: Path, subtitle_name: str) -> None:
    legacy = media_path.parent / subtitle_name
    bundled = bundle_dir(media_path) / subtitle_name
    if legacy.is_file() and legacy.resolve() != bundled.resolve():
        legacy.unlink()
    legacy_tag = media_path.parent / f"{subtitle_name}.tags.json"
    bundled_tag = bundle_dir(media_path) / f"{subtitle_name}.tags.json"
    if (
        legacy_tag.is_file()
        and bundled_tag.is_file()
        and legacy_tag.resolve() != bundled_tag.resolve()
    ):
        legacy_tag.unlink()
