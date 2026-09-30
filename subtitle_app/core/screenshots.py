"""一部视频的截图。图片和说明都在配套文件夹的 screenshot 目录里。

说明只写 screenshots.json。每张截图一张原尺寸图片，文件名是截图编号。
没有截图时，这个目录一并去掉。
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from core.media_bundle import bundle_dir
from core.subtitle import SubtitleSegment

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
SCREENSHOT_NOTE_ALIGN_DEFAULT = "center"
SCREENSHOT_NOTE_BACKGROUND_DEFAULT = "#FFFFFF"


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
    font: float = 0.06
    color: str = SCREENSHOT_NOTE_COLOR_DEFAULT
    align: str = SCREENSHOT_NOTE_ALIGN_DEFAULT


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


def screenshot_export_filename(video_name: str, title: str) -> str:
    """预览合成图的默认文件名：视频名-截图标题.jpg。"""
    video = _export_name_part(video_name, "视频")
    shot = _export_name_part(title, "截图")
    stem = f"{video}-{shot}".rstrip(" .")
    if len(stem) > 180:
        stem = stem[:180].rstrip(" .")
    return f"{stem or '截图'}.jpg"


def _export_name_part(text: str, fallback: str) -> str:
    cleaned = _EXPORT_FILENAME_CHARS.sub("_", " ".join(str(text or "").split()))
    return cleaned.strip(" .") or fallback


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
    allowed = {value.upper() for _name, value in SCREENSHOT_NOTE_COLORS}
    if chosen in allowed:
        return chosen
    return default


def normalize_note_align(align: str) -> str:
    chosen = str(align or "").strip().lower()
    allowed = {value for _label, value in SCREENSHOT_NOTE_ALIGNS}
    if chosen in allowed:
        return chosen
    return SCREENSHOT_NOTE_ALIGN_DEFAULT


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
        return 0.06
    if number > 1:
        number = number / 480
    return max(0.02, min(0.16, number))


def _font_ratio(item: dict) -> float:
    if "font" in item:
        return normalize_font_ratio(item.get("font"))
    return normalize_font_ratio(item.get("font_size"))


def _safe_id(value: str) -> str:
    cleaned = value.strip().lower()
    if len(cleaned) != 12 or any(ch not in "0123456789abcdef" for ch in cleaned):
        return ""
    return cleaned
