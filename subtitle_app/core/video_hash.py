"""视频内容哈希。同一份字节改名或复制后哈希不变，内容变了才重算。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

_CHUNK = 1024 * 1024


def hash_cache_path(media_path: Path) -> Path:
    from core.media_bundle import adopt_hash_cache, hash_cache_file

    adopt_hash_cache(media_path)
    return hash_cache_file(media_path)


def video_content_hash(media_path: Path) -> str:
    stat = media_path.stat()
    cache = hash_cache_path(media_path)
    cached = _read_cache(cache)
    if (
        cached is not None
        and cached.get("size") == stat.st_size
        and cached.get("mtime_ns") == stat.st_mtime_ns
        and _is_hash(cached.get("hash"))
    ):
        return str(cached["hash"])
    digest = _sha256_file(media_path)
    payload = {"hash": digest, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError:
        return digest
    return digest


@dataclass(frozen=True)
class CachedVideoIdentity:
    video_hash: str
    video_stem: str
    media_name: str
    cache_path: Path


def cached_video_for_subtitle(subtitle_path: Path) -> tuple[CachedVideoIdentity | None, str]:
    """从字幕同目录的「视频文件名.videohash.json」取出合法视频哈希。失败时说明原因。"""
    from core.config import MEDIA_EXTENSIONS

    from core.media_bundle import hash_search_dirs

    subtitle_name = subtitle_path.name
    entries: list[Path] = []
    readable = False
    for folder in hash_search_dirs(subtitle_path):
        try:
            entries.extend(list(folder.iterdir()))
            readable = True
        except OSError:
            continue
    if not readable:
        return None, f"无法读取「{subtitle_name}」所在的文件夹，所以找不到视频哈希文件。"

    caches: list[tuple[Path, str, str]] = []
    raw_hash_files: list[str] = []
    for item in entries:
        if not item.is_file() or not item.name.endswith(".videohash.json"):
            continue
        raw_hash_files.append(item.name)
        media_name = item.name[: -len(".videohash.json")]
        media = Path(media_name)
        if media.suffix.lower() not in MEDIA_EXTENSIONS or not media.stem:
            continue
        caches.append((item, media_name, media.stem))

    if not caches:
        if not raw_hash_files:
            return None, (
                f"「{subtitle_name}」所在文件夹里没有视频哈希文件。"
                "请先用播放器打开对应视频，同目录会生成「视频文件名.videohash.json」。"
            )
        found = "、".join(raw_hash_files)
        return None, (
            f"「{subtitle_name}」旁边有哈希文件（{found}），但文件名不是「视频文件名.videohash.json」，"
            "无法从中得到视频哈希。"
        )

    subtitle_stem = subtitle_path.stem
    matched = [
        item
        for item in caches
        if subtitle_stem == item[2] or subtitle_stem.startswith(f"{item[2]}_")
    ]
    if not matched:
        found = "、".join(item[0].name for item in caches)
        return None, (
            f"「{subtitle_name}」对不上同目录里的视频哈希文件（{found}）。"
            "字幕名需要和视频主文件名相同，或只在后面加语言后缀。"
        )

    best_len = max(len(item[2]) for item in matched)
    best = [item for item in matched if len(item[2]) == best_len]
    identities: list[CachedVideoIdentity] = []
    invalid: list[str] = []
    for cache, media_name, media_stem in best:
        payload = _read_cache(cache)
        digest = payload.get("hash") if isinstance(payload, dict) else None
        if not _is_hash(digest):
            invalid.append(cache.name)
            continue
        identities.append(
            CachedVideoIdentity(
                video_hash=str(digest),
                video_stem=media_stem,
                media_name=media_name,
                cache_path=cache,
            )
        )
    if not identities:
        shown = "、".join(invalid)
        return None, (
            f"找到了哈希文件「{shown}」，但里面没有合法的视频哈希。"
            "请重新用播放器打开该视频，让程序重新生成哈希文件。"
        )
    if len({item.video_hash for item in identities}) > 1:
        shown = "、".join(item.cache_path.name for item in identities)
        return None, f"「{subtitle_name}」同时对上了多个不同的视频哈希，无法确定该上传到哪一部视频：{shown}。"
    identities.sort(key=lambda item: item.media_name.casefold())
    return identities[0], ""


def language_suffix(subtitle_name: str, video_stem: str) -> str:
    if video_stem and subtitle_name.startswith(video_stem):
        suffix = subtitle_name[len(video_stem) :]
        lower = suffix.lower()
        if suffix.startswith((".", "_")) and (lower.endswith(".srt") or lower.endswith(".vtt")):
            return suffix
    return ""


def _is_hash(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(ch in "0123456789abcdef" for ch in value)


def _read_cache(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(_CHUNK)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()
