"""一部视频的截图。图片和说明都在配套文件夹的 screenshot 目录里。

说明只写 screenshots.json。每张截图一张原尺寸图片，文件名是截图编号。
没有截图时，这个目录一并去掉。
"""

from __future__ import annotations

import json
import re
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from core.media_bundle import bundle_dir
from core.subtitle import SubtitleSegment
from core.subtitle_tags import PRESET_TAGS, order_tags

SCREENSHOT_DIR_NAME = "screenshot"
SCREENSHOT_FILE = "screenshots.json"
DOCUMENT_VERSION = 1
_EXPORT_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

# 预览里笔记文字的可选颜色。黑是默认，用来看白板和浅色课件。
SCREENSHOT_NOTE_COLORS: tuple[tuple[str, str], ...] = (
    ("黑", "#1A1A1A"),
    ("白", "#FFFFFF"),
    ("黄", "#FFD54A"),
    ("红", "#E53935"),
    ("蓝", "#1565C0"),
    ("绿", "#2E7D32"),
    ("紫", "#7A4EB5"),
)
SCREENSHOT_NOTE_COLOR_DEFAULT = "#1A1A1A"
SCREENSHOT_NOTE_ALIGNS: tuple[tuple[str, str], ...] = (
    ("左", "left"),
    ("中", "center"),
    ("右", "right"),
)
SCREENSHOT_NOTE_ALIGN_DEFAULT = "left"
SCREENSHOT_NOTE_VALIGNS: tuple[tuple[str, str], ...] = (
    ("上", "top"),
    ("中", "middle"),
    ("下", "bottom"),
)
SCREENSHOT_NOTE_VALIGN_DEFAULT = "top"
SCREENSHOT_NOTE_FONT_DEFAULT = 0.045
SCREENSHOT_NOTE_BACKGROUND_DEFAULT = "#FFFFFF"
# 样式面板四季预设：只改颜色/透明度/标题条，不改位置、字号、对齐。
SCREENSHOT_NOTE_SEASON_PRESETS: tuple[tuple[str, str, str, str, float, str], ...] = (
    ("spring", "春日清新", "#E8F5E9", "#1B5E20", 0.90, "#A5D6A7"),
    ("summer", "夏日火热", "#FFE0B2", "#BF360C", 0.92, "#FFB74D"),
    ("autumn", "秋天朴素", "#EFEBE9", "#3E2723", 0.90, "#BCAAA4"),
    ("winter", "冬日静谧", "#E3F2FD", "#0D47A1", 0.92, "#90CAF9"),
)
_HEX_COLOR = re.compile(r"^#[0-9A-F]{6}$", re.IGNORECASE)


def default_note_slots(count: int) -> list[tuple[float, float, float, float]]:
    """同时展示 count 条笔记时，每条文本框的默认位置。

    返回值是相对画面的 x、y、宽、高。一条时居中，宽度为一半。
    多条时横向摊开，每条宽度不超过画面的 1/count。
    """
    count = max(1, int(count))
    height = 0.30
    y = (1.0 - height) / 2
    max_width = 1.0 / count
    if count == 1:
        width = min(0.5, max_width)
        return [((1.0 - width) / 2, y, width, height)]
    gap_budget = min(0.04, max_width * 0.15)
    width = min(max_width, (1.0 - gap_budget * (count + 1)) / count)
    width = max(0.05, min(width, max_width))
    leftover = max(0.0, 1.0 - count * width)
    gap = leftover / (count + 1)
    return [(gap + index * (width + gap), y, width, height) for index in range(count)]


@dataclass
class NoteFrame:
    """一条笔记在底图上的文本框。

    x、y、宽、高、字号都是相对这张底图的比例，不是像素。
    字号 font 是底图高度的比例。这些值和笔记一起写在 screenshots.json 里。
    """

    x: float = 0.18
    y: float = 0.56
    width: float = 0.64
    height: float = 0.30
    background: str = SCREENSHOT_NOTE_BACKGROUND_DEFAULT
    opacity: float = 0.85
    font: float = SCREENSHOT_NOTE_FONT_DEFAULT
    color: str = SCREENSHOT_NOTE_COLOR_DEFAULT
    align: str = SCREENSHOT_NOTE_ALIGN_DEFAULT
    valign: str = SCREENSHOT_NOTE_VALIGN_DEFAULT
    title_background: str = ""
    title_bold: bool = False
    title_italic: bool = False


@dataclass
class ScreenshotNote:
    id: str
    text: str
    created_at: int
    updated_at: int
    frame: NoteFrame | None = None


@dataclass
class Screenshot:
    id: str
    title: str
    time: float
    frame: int | None
    image: str
    created_at: int
    updated_at: int
    notes: list[ScreenshotNote] = field(default_factory=list)


@dataclass
class ScreenshotDocument:
    entries: list[Screenshot] = field(default_factory=list)


@dataclass(frozen=True)
class TimelineRow:
    kind: str
    segment_index: int = -1
    screenshot_id: str = ""


def new_screenshot_id() -> str:
    return uuid.uuid4().hex[:12]


def now_ms() -> int:
    return int(datetime.now().timestamp() * 1000)


def format_local_time(timestamp_ms: int) -> str:
    moment = datetime.fromtimestamp(int(timestamp_ms) / 1000).astimezone()
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def time_ms(seconds: float) -> int:
    return int(round(float(seconds) * 1000))


def screenshot_dir(media_path: Path) -> Path:
    return bundle_dir(media_path) / SCREENSHOT_DIR_NAME


def screenshot_document_path(media_path: Path) -> Path:
    return screenshot_dir(media_path) / SCREENSHOT_FILE


def _known_title_tags(extra_tags: list[str] | None = None) -> set[str]:
    known = set(PRESET_TAGS)
    for tag in extra_tags or []:
        cleaned = str(tag).strip()
        if cleaned:
            known.add(cleaned)
    return known


def tags_in_title(title: str, extra_tags: list[str] | None = None) -> list[str]:
    """标题里按短横线拆开后、属于已知标签的那些段，按出现顺序去重。"""
    known = _known_title_tags(extra_tags)
    found: list[str] = []
    seen: set[str] = set()
    for part in str(title or "").split("-"):
        name = part.strip()
        if not name or name not in known or name in seen:
            continue
        seen.add(name)
        found.append(name)
    return found


def title_has_known_tag(title: str, extra_tags: list[str] | None = None) -> bool:
    """标题被短横线分开的某一段是已知标签时，算已经给主题打过标签。"""
    return bool(tags_in_title(title, extra_tags))


def suggested_screenshot_title(video_name: str, tags: list[str]) -> str:
    """新建截图时的建议标题：文件名，当前字幕有标签时再接上「-标签」。"""
    stem = " ".join(str(video_name or "").split()) or "视频"
    return append_screenshot_title_tags(stem, tags)


def append_screenshot_title_tags(title: str, tags: list[str]) -> str:
    """把选中的标签接到标题后面，多个标签之间用短横线隔开。"""
    names = order_tags([str(tag) for tag in tags])
    if not names:
        return title.strip()
    suffix = "-".join(names)
    base = title.strip()
    if not base:
        return suffix
    if base.endswith("-"):
        return f"{base}{suffix}"
    return f"{base}-{suffix}"


def screenshot_export_filename(title: str) -> str:
    """预览合成图的默认文件名：zm-截图标题.jpg（zm = 字幕，方便筛选）。"""
    shot = _export_name_part(title, "截图")
    stem = f"zm-{shot}".rstrip(" .")
    if len(stem) > 180:
        stem = stem[:180].rstrip(" .")
    return f"{stem or 'zm-截图'}.jpg"


@dataclass(frozen=True)
class ManagedScreenshotFile:
    """某部视频在配套文件夹里登记过的截图图片。"""

    path: Path
    relative_path: str
    title: str
    created_at: float
    modified_at: float
    media_path: Path
    shot_id: str
    note_count: int = 0


def managed_screenshot_path_key(relative_path: str) -> str:
    """按路径分页时，同一目录下的截图归为一页。"""
    parent = Path(str(relative_path or "")).parent.as_posix()
    return parent if parent not in {"", "."} else "."


def _image_save_times(path: Path) -> tuple[float, float]:
    """图片文件的创建/保存时间；保存时间优先用 mtime。"""
    st = path.stat()
    modified = float(st.st_mtime)
    created = getattr(st, "st_birthtime", None)
    if created is None:
        created = st.st_ctime
    created = float(created)
    if created <= 0:
        created = modified
    if modified <= 0:
        modified = created
    return created, modified


def _coerce_timestamp(value: object, fallback: float) -> float:
    try:
        stamp = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback
    if stamp <= 0:
        return fallback
    try:
        datetime.fromtimestamp(stamp)
    except (OverflowError, OSError, ValueError):
        return fallback
    return stamp


def scan_screenshots_under(root: Path) -> list[ManagedScreenshotFile]:
    """扫描文件夹及其子文件夹：有对应视频，且 screenshots.json 里登记过的截图。

    按广度优先：先当前文件夹里的视频截图，再第二层子文件夹，再第三层……
    「截屏保存」导出的普通 JPEG 不在此范围。
    """
    from core.config import MEDIA_EXTENSIONS
    from core.media_bundle import is_bundle_dir

    root = Path(root)
    if not root.is_dir():
        return []
    try:
        root_resolved = root.resolve()
    except OSError:
        return []

    found: list[ManagedScreenshotFile] = []
    seen_media: set[Path] = set()
    queue: deque[Path] = deque([root])

    while queue:
        folder = queue.popleft()
        try:
            children = list(folder.iterdir())
        except OSError:
            continue
        children.sort(key=lambda item: item.name.lower())
        subdirs: list[Path] = []
        for item in children:
            if item.is_dir():
                # 配套 .data 不算用户目录层级，不往里继续找视频
                if is_bundle_dir(item):
                    continue
                subdirs.append(item)
                continue
            if not item.is_file() or item.suffix.lower() not in MEDIA_EXTENSIONS:
                continue
            try:
                media_resolved = item.resolve()
            except OSError:
                continue
            if media_resolved in seen_media:
                continue
            seen_media.add(media_resolved)
            found.extend(_managed_shots_for_media(item, root_resolved))
        queue.extend(subdirs)

    return found


def _managed_shots_for_media(
    media: Path,
    root_resolved: Path,
) -> list[ManagedScreenshotFile]:
    entries: list[ManagedScreenshotFile] = []
    document = load_screenshots(media)
    for shot in document.entries:
        image = screenshot_image_file(media, shot.id)
        if image is None:
            continue
        try:
            relative = image.resolve().relative_to(root_resolved)
        except ValueError:
            continue
        try:
            file_created, file_modified = _image_save_times(image)
        except OSError:
            file_created = file_modified = 0.0
        title = str(shot.title or "").strip() or "截图"
        entries.append(
            ManagedScreenshotFile(
                path=image,
                relative_path=relative.as_posix(),
                title=title,
                created_at=_coerce_timestamp(shot.created_at, file_created),
                modified_at=_coerce_timestamp(shot.updated_at, file_modified),
                media_path=media,
                shot_id=shot.id,
                note_count=len(list(shot.notes or [])),
            )
        )
    return entries


def paginate_screenshots_by_path(
    entries: list[ManagedScreenshotFile],
    page_index: int,
) -> tuple[list[ManagedScreenshotFile], int, int]:
    """按路径分页：同一目录下的截图在同一页。"""
    if not entries:
        return [], 0, 1
    groups: list[list[ManagedScreenshotFile]] = []
    current_key = None
    bucket: list[ManagedScreenshotFile] = []
    for item in entries:
        key = managed_screenshot_path_key(item.relative_path)
        if current_key is None:
            current_key = key
            bucket = [item]
            continue
        if key == current_key:
            bucket.append(item)
            continue
        groups.append(bucket)
        current_key = key
        bucket = [item]
    if bucket:
        groups.append(bucket)
    pages = max(1, len(groups))
    index = max(0, min(int(page_index), pages - 1))
    return list(groups[index]), index, pages


def _export_name_part(text: str, fallback: str) -> str:
    cleaned = _EXPORT_FILENAME_CHARS.sub("_", " ".join(str(text or "").split()))
    return cleaned.strip(" .") or fallback


def note_ordinal_labels(notes: list[ScreenshotNote]) -> dict[str, str]:
    """每条笔记框顶部标题：单条为 Note，多条按创建时间编号 Note 1、Note 2…。"""
    if not notes:
        return {}
    if len(notes) == 1:
        return {notes[0].id: "Note"}
    ordered = sorted(notes, key=lambda note: (int(note.created_at), note.id))
    return {note.id: f"Note {index}" for index, note in enumerate(ordered, start=1)}


def screenshot_image_path(media_path: Path, shot_id: str) -> Path:
    return screenshot_dir(media_path) / f"{_safe_id(shot_id)}.png"


def screenshot_image_file(media_path: Path, shot_id: str) -> Path | None:
    """本机原图优先，没有原图时用下载来的压缩图。"""
    folder = screenshot_dir(media_path)
    safe = _safe_id(shot_id)
    if not safe:
        return None
    for name in (f"{safe}.png", f"{safe}.webp"):
        path = folder / name
        if path.is_file() and path.stat().st_size > 0:
            return path
    return None


def delete_screenshot_images(media_path: Path, shot_id: str) -> None:
    folder = screenshot_dir(media_path)
    safe = _safe_id(shot_id)
    if not safe or not folder.is_dir():
        return
    for name in (f"{safe}.png", f"{safe}.webp"):
        path = folder / name
        if path.is_file():
            path.unlink()


def sorted_screenshots(entries: list[Screenshot]) -> list[Screenshot]:
    return sorted(entries, key=lambda shot: (time_ms(shot.time), shot.created_at, shot.id))


def nearest_screenshot_index(entries: list[Screenshot], seconds: float) -> int:
    ordered = sorted_screenshots(entries)
    if not ordered:
        return -1
    target = time_ms(seconds)
    best = 0
    best_gap = abs(time_ms(ordered[0].time) - target)
    for index, shot in enumerate(ordered[1:], start=1):
        gap = abs(time_ms(shot.time) - target)
        if gap < best_gap:
            best = index
            best_gap = gap
    return best


def screenshot_content_matches(shot: Screenshot, title: str, notes: list[ScreenshotNote]) -> bool:
    if shot.title != title:
        return False
    if len(shot.notes) != len(notes):
        return False
    for old, new in zip(shot.notes, notes):
        if old.id != new.id or old.text != new.text or not note_frames_match(old.frame, new.frame):
            return False
    return True


def normalize_note_color(color: str, default: str = SCREENSHOT_NOTE_COLOR_DEFAULT) -> str:
    chosen = str(color or "").strip().upper()
    if chosen and not chosen.startswith("#"):
        chosen = f"#{chosen}"
    if _HEX_COLOR.match(chosen):
        return chosen
    fallback = str(default or "").strip().upper()
    if fallback and not fallback.startswith("#"):
        fallback = f"#{fallback}"
    if _HEX_COLOR.match(fallback):
        return fallback
    return SCREENSHOT_NOTE_COLOR_DEFAULT


def normalize_title_background(value: object) -> str:
    text = str(value if value is not None else "").strip()
    if not text:
        return ""
    chosen = text.upper() if text.startswith("#") else f"#{text.upper()}"
    if _HEX_COLOR.match(chosen):
        return chosen
    return ""


def normalize_note_align(align: str) -> str:
    chosen = str(align or "").strip().lower()
    allowed = {value for _label, value in SCREENSHOT_NOTE_ALIGNS}
    if chosen in allowed:
        return chosen
    return SCREENSHOT_NOTE_ALIGN_DEFAULT


def normalize_note_valign(valign: str) -> str:
    chosen = str(valign or "").strip().lower()
    if chosen == "center":
        chosen = "middle"
    allowed = {value for _label, value in SCREENSHOT_NOTE_VALIGNS}
    if chosen in allowed:
        return chosen
    return SCREENSHOT_NOTE_VALIGN_DEFAULT


def copy_note_frame(frame: NoteFrame) -> NoteFrame:
    return NoteFrame(
        x=frame.x,
        y=frame.y,
        width=frame.width,
        height=frame.height,
        background=normalize_note_color(frame.background, SCREENSHOT_NOTE_BACKGROUND_DEFAULT),
        opacity=max(0.15, min(1.0, float(frame.opacity))),
        font=normalize_font_ratio(frame.font),
        color=normalize_note_color(frame.color),
        align=normalize_note_align(frame.align),
        valign=normalize_note_valign(frame.valign),
        title_background=normalize_title_background(frame.title_background),
        title_bold=bool(frame.title_bold),
        title_italic=bool(frame.title_italic),
    )


def note_frames_match(left: NoteFrame | None, right: NoteFrame | None) -> bool:
    if left is None and right is None:
        return True
    if left is None or right is None:
        return False
    return (
        round(left.x, 4) == round(right.x, 4)
        and round(left.y, 4) == round(right.y, 4)
        and round(left.width, 4) == round(right.width, 4)
        and round(left.height, 4) == round(right.height, 4)
        and left.background.upper() == right.background.upper()
        and round(float(left.opacity), 3) == round(float(right.opacity), 3)
        and round(float(left.font), 4) == round(float(right.font), 4)
        and left.color.upper() == right.color.upper()
        and left.align == right.align
        and left.valign == right.valign
        and left.title_background.upper() == right.title_background.upper()
        and bool(left.title_bold) == bool(right.title_bold)
        and bool(left.title_italic) == bool(right.title_italic)
    )


def interleave_screenshots(
    segments: list[SubtitleSegment],
    shots: list[Screenshot],
) -> list[TimelineRow]:
    """按时间把截图插进字幕。时间与某句起点相同的截图，排在这句后面。"""
    rows: list[tuple[int, int, int, str, TimelineRow]] = []
    for index, segment in enumerate(segments):
        rows.append(
            (
                time_ms(segment.start),
                0,
                index,
                "",
                TimelineRow("subtitle", segment_index=index),
            )
        )
    for shot in shots:
        rows.append(
            (
                time_ms(shot.time),
                1,
                shot.created_at,
                shot.id,
                TimelineRow("screenshot", screenshot_id=shot.id),
            )
        )
    rows.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
    return [item[4] for item in rows]


def load_screenshots(media_path: Path) -> ScreenshotDocument:
    path = screenshot_document_path(media_path)
    if not path.is_file():
        return ScreenshotDocument()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ScreenshotDocument()
    raw_entries = payload.get("screenshots") if isinstance(payload, dict) else None
    if not isinstance(raw_entries, list):
        return ScreenshotDocument()
    entries: list[Screenshot] = []
    seen: set[str] = set()
    for item in raw_entries:
        parsed = _parse_screenshot(item)
        if parsed is None or parsed.id in seen:
            continue
        seen.add(parsed.id)
        entries.append(parsed)
    return ScreenshotDocument(entries=sorted_screenshots(entries))


def save_screenshots(media_path: Path, document: ScreenshotDocument) -> None:
    entries = sorted_screenshots(list(document.entries))
    document.entries = entries
    if not entries:
        _clear_screenshot_dir(media_path)
        return
    folder = screenshot_dir(media_path)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / SCREENSHOT_FILE
    payload = {
        "version": DOCUMENT_VERSION,
        "screenshots": [_screenshot_payload(item) for item in entries],
    }
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def remove_screenshot(media_path: Path, document: ScreenshotDocument, shot_id: str) -> None:
    removed: Screenshot | None = None
    kept: list[Screenshot] = []
    for item in document.entries:
        if item.id == shot_id and removed is None:
            removed = item
            continue
        kept.append(item)
    if removed is None:
        return
    document.entries = kept
    if not kept:
        _clear_screenshot_dir(media_path)
        return
    save_screenshots(media_path, document)
    delete_screenshot_images(media_path, removed.id)


def _clear_screenshot_dir(media_path: Path) -> None:
    folder = screenshot_dir(media_path)
    if not folder.is_dir():
        return
    for child in folder.iterdir():
        if child.is_file():
            child.unlink()
    folder.rmdir()


def _screenshot_payload(item: Screenshot) -> dict:
    return {
        "id": item.id,
        "title": item.title,
        "time": item.time,
        "frame": item.frame,
        "image": item.image,
        "created_at": int(item.created_at),
        "updated_at": int(item.updated_at),
        "notes": [_note_payload(note) for note in item.notes],
    }


def _note_payload(note: ScreenshotNote) -> dict:
    payload: dict = {
        "id": note.id,
        "text": note.text,
        "created_at": int(note.created_at),
        "updated_at": int(note.updated_at),
    }
    if note.frame is not None:
        frame = note.frame
        payload["box"] = {
            "x": round(float(frame.x), 4),
            "y": round(float(frame.y), 4),
            "width": round(float(frame.width), 4),
            "height": round(float(frame.height), 4),
            "background": normalize_note_color(frame.background, SCREENSHOT_NOTE_BACKGROUND_DEFAULT),
            "opacity": round(max(0.15, min(1.0, float(frame.opacity))), 3),
            "font": round(normalize_font_ratio(frame.font), 4),
            "color": normalize_note_color(frame.color),
            "align": normalize_note_align(frame.align),
            "valign": normalize_note_valign(frame.valign),
            "title_background": normalize_title_background(frame.title_background),
            "title_bold": bool(frame.title_bold),
            "title_italic": bool(frame.title_italic),
        }
    return payload


def _parse_screenshot(item: object) -> Screenshot | None:
    if not isinstance(item, dict):
        return None
    shot_id = _safe_id(str(item.get("id") or ""))
    if not shot_id:
        return None
    try:
        created_at = int(item.get("created_at"))
        updated_at = int(item.get("updated_at"))
        moment = float(item.get("time"))
    except (TypeError, ValueError):
        return None
    notes: list[ScreenshotNote] = []
    seen: set[str] = set()
    raw_notes = item.get("notes")
    if isinstance(raw_notes, list):
        for raw in raw_notes:
            note = _parse_note(raw)
            if note is None or note.id in seen:
                continue
            seen.add(note.id)
            notes.append(note)
    frame = item.get("frame")
    parsed_frame: int | None
    if frame is None or frame == "":
        parsed_frame = None
    else:
        try:
            parsed_frame = int(frame)
        except (TypeError, ValueError):
            parsed_frame = None
        if parsed_frame is not None and parsed_frame < 0:
            parsed_frame = None
    return Screenshot(
        id=shot_id,
        title=str(item.get("title") or ""),
        time=moment,
        frame=parsed_frame,
        image=f"{shot_id}.png",
        created_at=created_at,
        updated_at=updated_at,
        notes=notes,
    )


def _parse_note(item: object) -> ScreenshotNote | None:
    if not isinstance(item, dict):
        return None
    note_id = _safe_id(str(item.get("id") or ""))
    text = str(item.get("text") or "").strip()
    if not note_id or not text:
        return None
    try:
        created_at = int(item.get("created_at"))
        updated_at = int(item.get("updated_at"))
    except (TypeError, ValueError):
        return None
    return ScreenshotNote(
        id=note_id,
        text=text,
        created_at=created_at,
        updated_at=updated_at,
        frame=_parse_frame(item.get("box")),
    )


def _parse_frame(item: object) -> NoteFrame | None:
    if not isinstance(item, dict):
        return None
    frame = NoteFrame(
        x=_unit(item.get("x"), 0.18),
        y=_unit(item.get("y"), 0.56),
        width=_unit(item.get("width"), 0.64, lower=0.08),
        height=_unit(item.get("height"), 0.30, lower=0.06),
        background=normalize_note_color(
            str(item.get("background") or ""),
            SCREENSHOT_NOTE_BACKGROUND_DEFAULT,
        ),
        opacity=_opacity(item.get("opacity")),
        font=_font_ratio(item),
        color=normalize_note_color(str(item.get("color") or "")),
        align=normalize_note_align(str(item.get("align") or "")),
        valign=normalize_note_valign(str(item.get("valign") or "")),
        title_background=normalize_title_background(item.get("title_background")),
        title_bold=bool(item.get("title_bold")),
        title_italic=bool(item.get("title_italic")),
    )
    if frame.x + frame.width > 1:
        frame.x = max(0.0, 1 - frame.width)
    if frame.y + frame.height > 1:
        frame.y = max(0.0, 1 - frame.height)
    return frame


def _unit(value: object, default: float, *, lower: float = 0.0, upper: float = 1.0) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(lower, min(upper, number))


def _opacity(value: object) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.85
    return max(0.15, min(1.0, number))


def normalize_font_ratio(value: object) -> float:
    """字号是底图高度的比例。旧数据里大于 1 的数字是像素，按 480 高的画面换算。"""
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return SCREENSHOT_NOTE_FONT_DEFAULT
    if number > 1:
        number = number / 480
    return max(0.02, min(0.16, number))


def _font_ratio(item: dict) -> float:
    if "font" in item:
        return normalize_font_ratio(item.get("font"))
    if "font_size" in item:
        return normalize_font_ratio(item.get("font_size"))
    return SCREENSHOT_NOTE_FONT_DEFAULT


def _safe_id(value: str) -> str:
    cleaned = value.strip().lower()
    if len(cleaned) != 12 or any(ch not in "0123456789abcdef" for ch in cleaned):
        return ""
    return cleaned
