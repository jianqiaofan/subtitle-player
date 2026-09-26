"""字幕标签：每个字幕文件对应同目录下的同名标签文件。

`lesson_中文.srt` 配对 `lesson_中文.srt.tags.json`。
标签文件只认同一文件夹里文件名相同的那一份字幕。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

from core.subtitle import SubtitleSegment

TAG_FILE_SUFFIX = ".tags.json"
TAG_DOCUMENT_VERSION = 1

PRESET_TAGS = ("重点", "难点", "易错", "跟读", "已掌握")
# 左侧色条只取一个标签，按复习优先级。
TAG_BAR_PRIORITY = ("难点", "重点", "易错", "跟读", "已掌握")

_TIME_TOLERANCE_MS = 1


@dataclass
class SubtitleTagEntry:
    id: str
    index: int
    start: float
    end: float
    text: str
    tags: list[str] = field(default_factory=list)
    note: str = ""

    def has_content(self) -> bool:
        return bool(self.tags) or bool(self.note.strip())


@dataclass
class SubtitleTagDocument:
    subtitle_file: str = ""
    entries: list[SubtitleTagEntry] = field(default_factory=list)
    version: int = TAG_DOCUMENT_VERSION


@dataclass
class TagAssignment:
    """row -> 已挂上的标签；unmatched 是对不上当前字幕的记录。"""

    by_row: dict[int, SubtitleTagEntry]
    unmatched: list[SubtitleTagEntry]


def tag_path_for_subtitle(subtitle_path: Path) -> Path:
    """标签文件与字幕同目录，文件名为「字幕文件名 + .tags.json」。"""
    return subtitle_path.parent / f"{subtitle_path.name}{TAG_FILE_SUFFIX}"


def subtitle_name_for_tag_path(tag_path: Path) -> str | None:
    name = tag_path.name
    if not name.endswith(TAG_FILE_SUFFIX):
        return None
    subtitle_name = name[: -len(TAG_FILE_SUFFIX)]
    if not subtitle_name:
        return None
    return subtitle_name


def find_subtitle_for_tag_file(tag_path: Path) -> Path | None:
    """只在标签文件所在文件夹查找同名字幕。"""
    subtitle_name = subtitle_name_for_tag_path(tag_path)
    if not subtitle_name:
        return None
    candidate = tag_path.parent / subtitle_name
    if candidate.is_file():
        return candidate
    return None


def time_ms(seconds: float) -> int:
    return int(round(float(seconds) * 1000 + 1e-9))


def normalize_tag_text(text: str) -> str:
    return " ".join((text or "").replace("\r\n", "\n").replace("\n", " ").split())


def text_similarity(left: str, right: str) -> float:
    a = normalize_tag_text(left)
    b = normalize_tag_text(right)
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def text_close(left: str, right: str) -> bool:
    a = normalize_tag_text(left)
    b = normalize_tag_text(right)
    if a == b:
        return True
    if min(len(a), len(b)) < 8:
        return False
    return text_similarity(left, right) >= 0.82


def times_close(left: float, right: float) -> bool:
    return abs(time_ms(left) - time_ms(right)) <= _TIME_TOLERANCE_MS


def custom_tag_names(entries: list[SubtitleTagEntry]) -> list[str]:
    names: list[str] = []
    for entry in entries:
        for tag in entry.tags:
            if tag not in PRESET_TAGS and tag not in names:
                names.append(tag)
    return names


def custom_tags_for_media(
    media_path: Path | None,
    current_subtitle: Path | None,
    current_entries: list[SubtitleTagEntry],
) -> list[str]:
    """只收集当前视频自己的自定义标签，不带上其它视频里用过的名字。"""
    from core.subtitle_loader import find_subtitles_for_media

    names = custom_tag_names(current_entries)
    if media_path is None:
        return names
    current = current_subtitle.resolve() if current_subtitle is not None else None
    for path, _label in find_subtitles_for_media(media_path):
        if current is not None and path.resolve() == current:
            continue
        document = load_tag_document(tag_path_for_subtitle(path), path.name)
        for tag in custom_tag_names(document.entries):
            if tag not in names:
                names.append(tag)
    return names


def order_tags(tags: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for name in PRESET_TAGS:
        if name in tags and name not in seen:
            ordered.append(name)
            seen.add(name)
    for name in tags:
        cleaned = name.strip()
        if cleaned and cleaned not in seen:
            ordered.append(cleaned)
            seen.add(cleaned)
    return ordered


def primary_tag(tags: list[str]) -> str | None:
    if not tags:
        return None
    for name in TAG_BAR_PRIORITY:
        if name in tags:
            return name
    return tags[0]


def new_entry_id() -> str:
    return uuid.uuid4().hex[:12]


def entry_from_segment(
    segment: SubtitleSegment,
    tags: list[str],
    note: str,
    entry_id: str | None = None,
) -> SubtitleTagEntry:
    return SubtitleTagEntry(
        id=entry_id or new_entry_id(),
        index=int(segment.index),
        start=float(segment.start),
        end=float(segment.end),
        text=segment.text or "",
        tags=order_tags(tags),
        note=note.strip(),
    )


def snapshot_entry(entry: SubtitleTagEntry, segment: SubtitleSegment) -> None:
    entry.index = int(segment.index)
    entry.start = float(segment.start)
    entry.end = float(segment.end)
    entry.text = segment.text or ""


def load_tag_document(tag_path: Path, subtitle_name: str) -> SubtitleTagDocument:
    """读取标签。文件名已经配对时才使用；内部记录的字幕名不一致则忽略内容。"""
    document = SubtitleTagDocument(subtitle_file=subtitle_name)
    if not tag_path.is_file():
        return document
    try:
        payload = json.loads(tag_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return document
    if not isinstance(payload, dict):
        return document
    recorded = str(payload.get("subtitle_file") or "").strip()
    if recorded and recorded != subtitle_name:
        return document
    entries: list[SubtitleTagEntry] = []
    raw_entries = payload.get("entries") or []
    if isinstance(raw_entries, list):
        for item in raw_entries:
            parsed = _parse_entry(item)
            if parsed is not None and parsed.has_content():
                entries.append(parsed)
    document.entries = entries
    return document


def read_tag_document_for_import(tag_path: Path) -> SubtitleTagDocument | None:
    """读取待移植的标签文件。文件名、内部字幕名或内容无效时返回 None。"""
    subtitle_name = subtitle_name_for_tag_path(tag_path)
    if not subtitle_name or not tag_path.is_file():
        return None
    try:
        payload = json.loads(tag_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    recorded = str(payload.get("subtitle_file") or "").strip()
    if recorded != subtitle_name:
        return None
    raw_entries = payload.get("entries") or []
    if not isinstance(raw_entries, list):
        return None
    entries: list[SubtitleTagEntry] = []
    for item in raw_entries:
        parsed = _parse_entry(item)
        if parsed is not None and parsed.has_content():
            entries.append(parsed)
    if not entries:
        return None
    return SubtitleTagDocument(subtitle_file=subtitle_name, entries=entries)


def _clone_entry(entry: SubtitleTagEntry, entry_id: str | None = None) -> SubtitleTagEntry:
    return SubtitleTagEntry(
        id=entry_id or entry.id,
        index=int(entry.index),
        start=float(entry.start),
        end=float(entry.end),
        text=entry.text or "",
        tags=order_tags(list(entry.tags)),
        note=(entry.note or "").strip(),
    )


def _merge_entry_into(target: SubtitleTagEntry, incoming: SubtitleTagEntry) -> None:
    target.tags = order_tags(list(target.tags) + list(incoming.tags))
    extra = (incoming.note or "").strip()
    current = (target.note or "").strip()
    if extra and extra not in current:
        target.note = f"{current}\n{extra}".strip() if current else extra


def _find_merge_target(
    incoming: SubtitleTagEntry,
    local_entries: list[SubtitleTagEntry],
    claimed: set[str],
) -> SubtitleTagEntry | None:
    pool = [entry for entry in local_entries if entry.id not in claimed]
    for entry in pool:
        if entry.id == incoming.id:
            return entry
    time_hits = [
        entry
        for entry in pool
        if times_close(entry.start, incoming.start) and text_close(entry.text, incoming.text)
    ]
    if len(time_hits) == 1:
        return time_hits[0]
    if len(time_hits) > 1:
        return max(time_hits, key=lambda entry: text_similarity(entry.text, incoming.text))
    index_hits = [
        entry
        for entry in pool
        if int(entry.index) == int(incoming.index) and text_close(entry.text, incoming.text)
    ]
    if len(index_hits) == 1:
        return index_hits[0]
    key = normalize_tag_text(incoming.text)
    if not key:
        return None
    text_hits = [entry for entry in pool if normalize_tag_text(entry.text) == key]
    if len(text_hits) == 1:
        return text_hits[0]
    return None


def merge_tag_entries(
    local_entries: list[SubtitleTagEntry],
    incoming_entries: list[SubtitleTagEntry],
) -> list[SubtitleTagEntry]:
    """把外来标签并入本地记录。对得上的合并标签和备注，对不上的追加。"""
    merged = [_clone_entry(entry) for entry in local_entries if entry.has_content()]
    claimed: set[str] = set()
    for incoming in incoming_entries:
        if not incoming.has_content():
            continue
        target = _find_merge_target(incoming, merged, claimed)
        if target is not None:
            _merge_entry_into(target, incoming)
            claimed.add(target.id)
            continue
        entry_id = incoming.id if incoming.id and incoming.id not in {item.id for item in merged} else new_entry_id()
        merged.append(_clone_entry(incoming, entry_id))
    return merged


@dataclass(frozen=True)
class TagVideoMatch:
    source_tag: Path
    video_path: Path
    subtitle_name: str
    has_existing_tag: bool


def subtitle_matches_video(subtitle_name: str, video_path: Path) -> bool:
    """字幕名与视频同名，或是「视频名_语种」这种配套字幕。"""
    subtitle = Path(subtitle_name)
    if subtitle.suffix.lower() not in {".srt", ".vtt"}:
        return False
    stem = subtitle.stem
    media_stem = video_path.stem
    return stem == media_stem or stem.startswith(f"{media_stem}_")


def find_batch_tag_matches(
    tag_files: list[Path],
    video_folder: Path,
) -> list[TagVideoMatch]:
    """用选中的标签文件，匹配视频文件夹（含子目录）中的同名视频。

    只保留视频旁边已经有对应字幕文件的结果，否则无法写入标签。
    """
    from core.config import MEDIA_EXTENSIONS

    videos = sorted(
        path
        for path in video_folder.rglob("*")
        if path.is_file() and path.suffix.lower() in MEDIA_EXTENSIONS
    )
    matches: list[TagVideoMatch] = []
    for tag_path in tag_files:
        subtitle_name = subtitle_name_for_tag_path(tag_path)
        if not subtitle_name or read_tag_document_for_import(tag_path) is None:
            continue
        for video_path in videos:
            if not subtitle_matches_video(subtitle_name, video_path):
                continue
            subtitle_path = video_path.parent / subtitle_name
            if not subtitle_path.is_file():
                continue
            destination = tag_path_for_subtitle(subtitle_path)
            try:
                if tag_path.resolve() == destination.resolve():
                    continue
            except OSError:
                pass
            matches.append(
                TagVideoMatch(
                    source_tag=tag_path,
                    video_path=video_path,
                    subtitle_name=subtitle_name,
                    has_existing_tag=destination.is_file(),
                )
            )
    return matches


def sync_tag_file_into_folder(source: Path, folder: Path) -> tuple[str, str]:
    """把一个标签文件同步到视频所在文件夹。返回 (copied|merged|skipped, 说明)。"""
    subtitle_name = subtitle_name_for_tag_path(source)
    if not subtitle_name:
        return "skipped", f"{source.name}：不是标签文件"
    subtitle_path = folder / subtitle_name
    if not subtitle_path.is_file():
        return "skipped", f"{source.name}：当前视频文件夹中没有 {subtitle_name}"
    incoming = read_tag_document_for_import(source)
    if incoming is None:
        return "skipped", f"{source.name}：标签文件无效，或与字幕文件名不一致"
    destination = tag_path_for_subtitle(subtitle_path)
    try:
        if source.resolve() == destination.resolve():
            return "skipped", f"{source.name}：已经在当前视频文件夹中"
    except OSError:
        pass
    local = load_tag_document(destination, subtitle_name)
    incoming.subtitle_file = subtitle_name
    if not local.entries:
        save_tag_document(destination, incoming)
        return "copied", f"{subtitle_name}：已复制标签文件"
    local.entries = merge_tag_entries(local.entries, incoming.entries)
    local.subtitle_file = subtitle_name
    save_tag_document(destination, local)
    return "merged", f"{subtitle_name}：已与现有标签合并"


def save_tag_document(tag_path: Path, document: SubtitleTagDocument) -> None:
    kept = [entry for entry in document.entries if entry.has_content()]
    document.entries = kept
    if not kept:
        if tag_path.is_file():
            tag_path.unlink()
        return
    tag_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": TAG_DOCUMENT_VERSION,
        "subtitle_file": document.subtitle_file,
        "entries": [asdict(entry) for entry in kept],
    }
    tag_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def assign_tags(
    segments: list[SubtitleSegment],
    entries: list[SubtitleTagEntry],
) -> TagAssignment:
    pool = [entry for entry in entries if entry.has_content()]
    by_row: dict[int, SubtitleTagEntry] = {}

    def claim(entry: SubtitleTagEntry, row: int) -> None:
        by_row[row] = entry
        pool.remove(entry)

    for entry in list(pool):
        hits = [
            index
            for index, segment in enumerate(segments)
            if index not in by_row
            and times_close(segment.start, entry.start)
            and text_close(segment.text, entry.text)
        ]
        if len(hits) == 1:
            claim(entry, hits[0])
        elif len(hits) > 1:
            best = max(hits, key=lambda index: text_similarity(segments[index].text, entry.text))
            claim(entry, best)

    for entry in list(pool):
        hits = [
            index
            for index, segment in enumerate(segments)
            if index not in by_row
            and int(segment.index) == int(entry.index)
            and text_close(segment.text, entry.text)
        ]
        if len(hits) == 1:
            claim(entry, hits[0])

    for entry in list(pool):
        key = normalize_tag_text(entry.text)
        if not key:
            continue
        hits = [
            index
            for index, segment in enumerate(segments)
            if index not in by_row and normalize_tag_text(segment.text) == key
        ]
        if len(hits) == 1:
            claim(entry, hits[0])
            continue
        if len(hits) < 2:
            continue
        ordered = sorted(hits, key=lambda index: abs(segments[index].start - entry.start))
        nearest = abs(segments[ordered[0]].start - entry.start)
        second = abs(segments[ordered[1]].start - entry.start)
        if second - nearest >= 1.0:
            claim(entry, ordered[0])

    return TagAssignment(by_row=by_row, unmatched=list(pool))


def _parse_entry(item: object) -> SubtitleTagEntry | None:
    if not isinstance(item, dict):
        return None
    try:
        start = float(item.get("start"))
        end = float(item.get("end"))
        index = int(item.get("index"))
    except (TypeError, ValueError):
        return None
    raw_tags = item.get("tags") or []
    tags: list[str] = []
    if isinstance(raw_tags, list):
        for tag in raw_tags:
            cleaned = str(tag).strip()
            if cleaned:
                tags.append(cleaned)
    entry_id = str(item.get("id") or "").strip() or new_entry_id()
    return SubtitleTagEntry(
        id=entry_id,
        index=index,
        start=start,
        end=end,
        text=str(item.get("text") or ""),
        tags=order_tags(tags),
        note=str(item.get("note") or "").strip(),
    )
