"""没有字幕时，用播放时间线代替字幕列表。时间线本身不写成文件。"""

from __future__ import annotations

from core.subtitle import SubtitleSegment
from core.subtitle_tags import SubtitleTagDocument, SubtitleTagEntry, entry_from_segment, retire_tag_entry

TIMELINE_STEP_CHOICES: tuple[tuple[int, str], ...] = (
    (5, "5秒"),
    (10, "10秒"),
    (15, "15秒"),
    (30, "30秒"),
    (60, "1分钟"),
    (120, "2分钟"),
    (300, "5分钟"),
)


def default_timeline_step(duration_seconds: float) -> int:
    if duration_seconds <= 60:
        return 10
    if duration_seconds <= 10 * 60:
        return 30
    return 60


def format_clock(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def build_timeline_segments(duration_seconds: float, step_seconds: int) -> list[SubtitleSegment]:
    if duration_seconds <= 0 or step_seconds <= 0:
        return []
    segments: list[SubtitleSegment] = []
    start = 0.0
    index = 1
    while start < duration_seconds - 0.001 and index <= 100000:
        end = min(duration_seconds, start + step_seconds)
        if end <= start:
            break
        segments.append(
            SubtitleSegment(
                index=index,
                start=start,
                end=end,
                text=f"{format_clock(start)} → {format_clock(end)}",
            )
        )
        start = end
        index += 1
    return segments


def row_for_time(segments: list[SubtitleSegment], moment: float) -> int | None:
    if not segments:
        return None
    for index, segment in enumerate(segments):
        if segment.start <= moment < segment.end:
            return index
    last = segments[-1]
    if last.start <= moment <= last.end + 0.001:
        return len(segments) - 1
    prior = [index for index, segment in enumerate(segments) if segment.start <= moment]
    if prior:
        return prior[-1]
    return 0


def assign_tags_by_time(
    segments: list[SubtitleSegment],
    entries: list[SubtitleTagEntry],
) -> tuple[dict[int, SubtitleTagEntry], list[SubtitleTagEntry]]:
    by_row: dict[int, SubtitleTagEntry] = {}
    unmatched: list[SubtitleTagEntry] = []
    for entry in entries:
        if not entry.has_content():
            continue
        row = row_for_time(segments, entry.start)
        if row is None or row in by_row:
            unmatched.append(entry)
            continue
        by_row[row] = entry
    return by_row, unmatched


def fold_timeline_into_subtitle(
    segments: list[SubtitleSegment],
    subtitle_document: SubtitleTagDocument,
    timeline_document: SubtitleTagDocument,
) -> bool:
    """把时间线上的标签按所在时间挂到字幕行，并在时间线文档里记成已取消。"""
    changed = False
    existing_ids = {entry.id for entry in subtitle_document.entries}
    for entry in timeline_document.entries:
        if not entry.has_content():
            continue
        row = row_for_time(segments, entry.start)
        if row is None:
            continue
        if entry.id not in existing_ids:
            segment = segments[row]
            copied = entry_from_segment(segment, list(entry.tags), entry.note, entry.id)
            copied.tag_ops = [dict(op) for op in entry.tag_ops]
            copied.note_at = entry.note_at
            subtitle_document.entries.append(copied)
            existing_ids.add(entry.id)
        retire_tag_entry(entry)
        changed = True
    return changed
