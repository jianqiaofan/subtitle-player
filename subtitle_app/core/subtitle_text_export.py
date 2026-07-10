"""将字幕整理为无时间轴的纯文字 Markdown（按时间间隔分段）。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from core.subtitle import SubtitleSegment

PLAIN_TEXT_FILENAME_SUFFIX = "纯文字"

_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass(frozen=True)
class PlainTextExportOptions:
    gap_seconds: float = 2.0


def _sanitize_filename_part(text: str) -> str:
    cleaned = _INVALID_FILENAME_CHARS.sub("_", text.strip())
    return cleaned or "字幕"


@lru_cache(maxsize=1)
def _opencc_converter():
    try:
        import opencc
    except ImportError as exc:
        raise RuntimeError(
            "繁转简需要 OpenCC 库。\n请运行：pip install opencc-python-reimplemented"
        ) from exc
    return opencc.OpenCC("t2s")


def _to_simplified(text: str) -> str:
    if not text:
        return text
    return _opencc_converter().convert(text)


CJK_CHAR_RE = re.compile(
    r"[\u3000-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
    r"\uac00-\ud7af]"
)
SENTENCE_END_CHARS = frozenset("。！？.!?…")
CLAUSE_END_CHARS = frozenset("，、,;:：；")
PUNCTUATION_END_CHARS = SENTENCE_END_CHARS | CLAUSE_END_CHARS | frozenset(")]}'\"」』】》）")


def _is_cjk_char(char: str) -> bool:
    return bool(CJK_CHAR_RE.match(char))


def _dominant_script(text: str) -> str:
    cjk_count = sum(1 for char in text if _is_cjk_char(char))
    latin_count = sum(1 for char in text if char.isascii() and char.isalpha())
    return "cjk" if cjk_count >= latin_count else "latin"


def _ends_with_punctuation(text: str) -> bool:
    stripped = text.rstrip()
    return bool(stripped) and stripped[-1] in PUNCTUATION_END_CHARS


def _ends_with_sentence_punctuation(text: str) -> bool:
    stripped = text.rstrip()
    return bool(stripped) and stripped[-1] in SENTENCE_END_CHARS


def _normalize_segment_text(text: str) -> str:
    text = _to_simplified(text)
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if not lines:
        return ""
    if len(lines) == 1:
        return lines[0]
    script = _dominant_script("".join(lines))
    if script == "cjk":
        return "，".join(lines)
    return " ".join(lines)


def _join_two_text_parts(left: str, right: str) -> str:
    left = left.rstrip()
    right = right.lstrip()
    if not left:
        return right
    if not right:
        return left
    if right[0] in PUNCTUATION_END_CHARS:
        if _needs_space_between(left, right):
            return left + " " + right
        return left + right
    if _ends_with_punctuation(left):
        if _needs_space_between(left, right):
            return left + " " + right
        return left + right

    script = _dominant_script(left + right)
    if script == "cjk":
        return left + "，" + right
    if _needs_space_between(left, right):
        return left + ", " + right
    return left + ", " + right


def _finalize_paragraph_punctuation(text: str) -> str:
    cleaned = text.strip()
    if not cleaned:
        return ""
    if _ends_with_sentence_punctuation(cleaned):
        return cleaned

    script = _dominant_script(cleaned)
    if script == "cjk":
        if cleaned.endswith(("吗", "呢", "么")) or cleaned.endswith("か"):
            return cleaned + "？"
        return cleaned + "。"
    if cleaned.endswith(("吗", "呢")):
        return cleaned + "?"
    return cleaned + "."


def _needs_space_between(left: str, right: str) -> bool:
    if not left or not right:
        return False
    left_char = left[-1]
    right_char = right[0]
    return (
        left_char.isascii()
        and left_char.isalnum()
        and right_char.isascii()
        and right_char.isalnum()
    )


def _join_segment_texts(texts: list[str]) -> str:
    parts = [_normalize_segment_text(text) for text in texts if text.strip()]
    if not parts:
        return ""
    result = parts[0]
    for text in parts[1:]:
        result = _join_two_text_parts(result, text)
    return _finalize_paragraph_punctuation(result)


def segments_to_paragraphs(
    segments: list[SubtitleSegment],
    *,
    gap_seconds: float,
) -> list[str]:
    """相邻字幕间隔超过 gap_seconds 时开始新段落。"""
    if not segments:
        return []

    gap = max(0.0, gap_seconds)
    paragraphs: list[str] = []
    current_texts: list[str] = [segments[0].text]

    for index in range(1, len(segments)):
        previous = segments[index - 1]
        current = segments[index]
        pause = current.start - previous.end
        if gap > 0 and pause > gap:
            paragraph = _join_segment_texts(current_texts)
            if paragraph:
                paragraphs.append(paragraph)
            current_texts = [current.text]
        else:
            current_texts.append(current.text)

    final = _join_segment_texts(current_texts)
    if final:
        paragraphs.append(final)
    return paragraphs


def _format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _resolve_media_duration_seconds(
    segments: list[SubtitleSegment],
    media_duration_seconds: float | None,
) -> float:
    if media_duration_seconds is not None and media_duration_seconds > 0:
        return media_duration_seconds
    if segments:
        return max(seg.end for seg in segments)
    return 0.0


def _count_body_characters(paragraphs: list[str]) -> int:
    return sum(
        sum(1 for char in paragraph if not char.isspace())
        for paragraph in paragraphs
    )


def build_plain_text_output_path(media_path: Path, subtitle_label: str = "") -> Path:
    label = subtitle_label.strip()
    if label and label not in {"默认", "字幕"}:
        safe_label = _sanitize_filename_part(label)
        suffix = f"{media_path.stem}_{PLAIN_TEXT_FILENAME_SUFFIX}_{safe_label}"
    else:
        suffix = f"{media_path.stem}_{PLAIN_TEXT_FILENAME_SUFFIX}"
    return media_path.parent / f"{suffix}.md"


def compose_plain_text_markdown(
    media_path: Path,
    subtitle_label: str,
    subtitle_filename: str,
    paragraphs: list[str],
    options: PlainTextExportOptions,
    *,
    media_duration_seconds: float = 0.0,
    generated_at: datetime | None = None,
) -> str:
    when = generated_at or datetime.now()
    time_str = when.strftime("%Y-%m-%d %H:%M:%S")
    media_stem = _to_simplified(media_path.stem)
    subtitle_label = _to_simplified(subtitle_label.strip())
    char_count = _count_body_characters(paragraphs)
    duration_text = _format_duration(media_duration_seconds)
    title = f"{media_stem} 纯文字版"
    if subtitle_label:
        title += f"（{subtitle_label}）"

    lines = [
        "---",
        f"title: {title}",
        f"source_media: {media_path.name}",
        f"subtitle_source: {subtitle_filename}",
        f"media_duration_seconds: {media_duration_seconds:.3f}",
        f"media_duration: {duration_text}",
        f"character_count: {char_count}",
        f"paragraph_gap_seconds: {options.gap_seconds}",
        f"paragraph_count: {len(paragraphs)}",
        f"generated_at: {time_str}",
        "---",
        "",
        f"# {media_stem} — 纯文字版",
        "",
        f"> 生成时间：{time_str}  ",
        f"> 源视频：{media_path.name}  ",
        f"> 视频总时长：{duration_text}  ",
        f"> 字幕来源：{subtitle_label}（{subtitle_filename}）  ",
        f"> 分段规则：相邻字幕间隔超过 {options.gap_seconds:g} 秒则另起一段  ",
        f"> 繁简转换：已转为简体中文  ",
        f"> 段落数：{len(paragraphs)}  ",
        f"> 字数总计：{char_count}",
        "",
        "## 正文",
        "",
    ]

    for paragraph in paragraphs:
        lines.append(paragraph)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def export_plain_text_markdown(
    media_path: Path,
    segments: list[SubtitleSegment],
    *,
    subtitle_label: str,
    subtitle_filename: str,
    options: PlainTextExportOptions | None = None,
    media_duration_seconds: float | None = None,
) -> Path:
    opts = options or PlainTextExportOptions()
    if not segments:
        raise RuntimeError("当前字幕为空，无法导出纯文字版。")

    paragraphs = segments_to_paragraphs(segments, gap_seconds=opts.gap_seconds)
    if not paragraphs:
        raise RuntimeError("未能从字幕中提取有效文字。")

    duration_seconds = _resolve_media_duration_seconds(segments, media_duration_seconds)
    output_path = build_plain_text_output_path(media_path, subtitle_label)
    content = compose_plain_text_markdown(
        media_path,
        subtitle_label,
        subtitle_filename,
        paragraphs,
        opts,
        media_duration_seconds=duration_seconds,
    )
    output_path.write_text(content, encoding="utf-8")
    return output_path
