from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class SubtitleSegment:
    index: int
    start: float
    end: float
    text: str


def format_timestamp(seconds: float, vtt: bool = False) -> str:
    millis = int(round(seconds * 1000))
    hours, rem = divmod(millis, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    if vtt:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}.{ms:03d}"
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def segments_to_srt(segments: list[SubtitleSegment]) -> str:
    lines: list[str] = []
    for seg in segments:
        lines.append(str(seg.index))
        lines.append(f"{format_timestamp(seg.start)} --> {format_timestamp(seg.end)}")
        lines.append(seg.text.strip())
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def segments_to_vtt(segments: list[SubtitleSegment]) -> str:
    lines = ["WEBVTT", ""]
    for seg in segments:
        lines.append(f"{format_timestamp(seg.start, vtt=True)} --> {format_timestamp(seg.end, vtt=True)}")
        lines.append(seg.text.strip())
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def segments_to_txt(segments: list[SubtitleSegment]) -> str:
    return "\n".join(seg.text.strip() for seg in segments if seg.text.strip()) + "\n"


def write_subtitle_file(segments: list[SubtitleSegment], output_path: Path, fmt: str) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "srt":
        content = segments_to_srt(segments)
    elif fmt == "vtt":
        content = segments_to_vtt(segments)
    else:
        content = segments_to_txt(segments)
    output_path.write_text(content, encoding="utf-8")
    return output_path


def parse_timestamp(value: str) -> float:
    value = value.replace(",", ".").strip()
    parts = value.split(":")
    if len(parts) != 3:
        return 0.0
    hours = int(parts[0])
    minutes = int(parts[1])
    sec_parts = parts[2].split(".")
    seconds = int(sec_parts[0])
    millis = int(sec_parts[1]) if len(sec_parts) > 1 else 0
    return hours * 3600 + minutes * 60 + seconds + millis / 1000


def load_subtitle_file(path: Path) -> list[SubtitleSegment]:
    name = path.name.lower()
    if name.endswith(".srt") or name.endswith(".srt.partial"):
        return _load_srt(path)
    suffix = path.suffix.lower()
    if suffix == ".vtt":
        return _load_vtt(path)
    raise ValueError(f"不支持的字幕格式: {path.suffix}")


def _load_srt(path: Path) -> list[SubtitleSegment]:
    content = path.read_text(encoding="utf-8", errors="replace").strip()
    blocks = [b.strip() for b in content.split("\n\n") if b.strip()]
    segments: list[SubtitleSegment] = []
    for block in blocks:
        lines = block.splitlines()
        if len(lines) < 3:
            continue
        try:
            index = int(lines[0].strip())
        except ValueError:
            continue
        timing = lines[1]
        if "-->" not in timing:
            continue
        start_str, end_str = [p.strip() for p in timing.split("-->")]
        text = "\n".join(lines[2:]).strip()
        segments.append(
            SubtitleSegment(index, parse_timestamp(start_str), parse_timestamp(end_str), text)
        )
    return segments


def _load_vtt(path: Path) -> list[SubtitleSegment]:
    content = path.read_text(encoding="utf-8", errors="replace").strip()
    lines = content.splitlines()
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.strip().upper() == "WEBVTT" or line.strip().startswith("NOTE"):
            continue
        if not line.strip():
            if current:
                blocks.append(current)
                current = []
            continue
        current.append(line)
    if current:
        blocks.append(current)

    segments: list[SubtitleSegment] = []
    for index, block in enumerate(blocks, start=1):
        if len(block) < 2 or "-->" not in block[0]:
            continue
        start_str, end_str = [p.strip() for p in block[0].split("-->")]
        text = "\n".join(block[1:]).strip()
        segments.append(
            SubtitleSegment(index, parse_timestamp(start_str), parse_timestamp(end_str), text)
        )
    return segments


def find_segment_index_at_time(segments: list[SubtitleSegment], seconds: float) -> int:
    for index, seg in enumerate(segments):
        if seg.start <= seconds <= seg.end:
            return index
    for index, seg in enumerate(segments):
        if seg.start > seconds:
            return max(0, index - 1)
    return len(segments) - 1 if segments else -1
