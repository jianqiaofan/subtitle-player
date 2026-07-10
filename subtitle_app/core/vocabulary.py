"""从字幕语料提取词汇并生成生词表（本地分词，无需大模型）。"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from core.ai_notes import SubtitleCorpusItem, collect_valid_subtitle_corpus

VOCABULARY_FILENAME_SUFFIX = "生词表"

LANGUAGE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("英语", "en"),
    ("日语", "ja"),
    ("中文", "zh"),
)

LANGUAGE_LABELS = {code: label for label, code in LANGUAGE_OPTIONS}

ENGLISH_WORD_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")

ENGLISH_STOPWORDS = frozenset(
    """
    a an the and or but if then else when at by for with about against between into
    through during before after above below to from up down in out on off over under
    again further then once here there all each few more most other some such no nor
    not only own same so than too very can will just don should now of is am are was
    were be been being have has had do does did doing would could ought i me my myself
    we our ours ourselves you your yours yourself yourselves he him his himself she her
    hers herself it its itself they them their theirs themselves what which who whom
    this that these those am is are was were be been being as until while because although
    though since unless until whereas whether while where why how both either neither
    each every all any few many much most other another such no nor not only own same
    get got getting go goes going went gone come comes coming came take takes taking took
    make makes making made say says said tell tells told know knows knew think thinks
    thought see sees saw look looks looked want wants wanted use uses used give gives
    given also just like really well very much still even back already still
    """.split()
)


@dataclass(frozen=True)
class VocabularyOptions:
    language: str = "en"
    min_frequency: int = 1
    max_items: int = 500
    min_english_length: int = 2
    min_chinese_length: int = 2


@dataclass(frozen=True)
class VocabularyEntry:
    word: str
    count: int
    first_time: float
    example: str
    reading: str = ""
    pos: str = ""
    definition: str = ""


def _format_clock(seconds: float) -> str:
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def build_vocabulary_output_path(media_path: Path, language: str, *, csv: bool = False) -> Path:
    label = LANGUAGE_LABELS.get(language, language)
    suffix = f"{media_path.stem}_{VOCABULARY_FILENAME_SUFFIX}_{label}"
    ext = ".csv" if csv else ".md"
    return media_path.parent / f"{suffix}{ext}"


def find_vocabulary_path(media_path: Path, language: str) -> Path | None:
    path = build_vocabulary_output_path(media_path, language)
    if not path.is_file():
        return None
    try:
        if path.stat().st_size < 16:
            return None
    except OSError:
        return None
    return path


def _clean_example(text: str, limit: int = 80) -> str:
    cleaned = re.sub(r"\s+", " ", text.replace("\n", " ")).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "…"


def _extract_english_tokens(text: str, min_length: int) -> list[str]:
    tokens: list[str] = []
    for match in ENGLISH_WORD_RE.finditer(text):
        word = match.group(0)
        key = word.lower()
        if len(key) < min_length:
            continue
        if key in ENGLISH_STOPWORDS:
            continue
        tokens.append(word)
    return tokens


def _extract_japanese_tokens(text: str) -> list[str]:
    try:
        from janome.tokenizer import Tokenizer
    except ImportError as exc:
        raise RuntimeError(
            "日语分词需要安装 janome。\n请运行：pip install janome"
        ) from exc

    allowed_pos = ("名詞", "動詞", "形容詞", "副詞", "連体詞", "感動詞")
    skip_pos_details = ("助詞", "助動詞", "記号", "接続詞", "接頭詞", "接尾詞", "フィラー")

    tokenizer = Tokenizer()
    tokens: list[str] = []
    for token in tokenizer.tokenize(text):
        surface = token.surface.strip()
        if not surface or re.fullmatch(r"[\W\d_]+", surface):
            continue
        pos_main = token.part_of_speech.split(",")[0]
        if pos_main not in allowed_pos:
            continue
        if any(part in token.part_of_speech for part in skip_pos_details):
            continue
        if len(surface) == 1 and re.fullmatch(r"[ぁ-んァ-ン]", surface):
            continue
        tokens.append(surface)
    return tokens


def _extract_chinese_tokens(text: str, min_length: int) -> list[str]:
    try:
        import jieba.posseg as pseg
    except ImportError as exc:
        raise RuntimeError(
            "中文分词需要安装 jieba。\n请运行：pip install jieba"
        ) from exc

    allowed_flags = ("n", "v", "a", "i", "l", "nz", "vn", "an")
    tokens: list[str] = []
    for word, flag in pseg.cut(text):
        word = word.strip()
        if not word:
            continue
        if not re.search(r"[\u4e00-\u9fff]", word):
            continue
        if len(word) < min_length:
            continue
        if not flag.startswith(allowed_flags):
            continue
        tokens.append(word)
    return tokens


def _extract_tokens(text: str, options: VocabularyOptions) -> list[str]:
    if options.language == "en":
        return _extract_english_tokens(text, options.min_english_length)
    if options.language == "ja":
        return _extract_japanese_tokens(text)
    if options.language == "zh":
        return _extract_chinese_tokens(text, options.min_chinese_length)
    raise ValueError(f"不支持的语言：{options.language}")


def extract_vocabulary_from_corpus(
    items: list[SubtitleCorpusItem],
    options: VocabularyOptions,
) -> list[VocabularyEntry]:
    stats: dict[str, dict[str, object]] = {}

    for item in items:
        for segment in item.segments:
            example = _clean_example(segment.text)
            for token in _extract_tokens(segment.text, options):
                if options.language == "en":
                    key = token.lower()
                    display = token
                else:
                    key = token
                    display = token

                if key not in stats:
                    stats[key] = {
                        "word": display,
                        "count": 0,
                        "first_time": segment.start,
                        "example": example,
                    }
                entry = stats[key]
                entry["count"] = int(entry["count"]) + 1
                if int(entry["count"]) == 1:
                    entry["word"] = display

    entries = [
        VocabularyEntry(
            word=str(data["word"]),
            count=int(data["count"]),
            first_time=float(data["first_time"]),
            example=str(data["example"]),
        )
        for data in stats.values()
        if int(data["count"]) >= options.min_frequency
    ]
    entries.sort(key=lambda item: (-item.count, item.first_time, item.word))
    if options.max_items > 0:
        entries = entries[: options.max_items]
    return entries


def _escape_table_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _has_lexicon_columns(language: str) -> bool:
    return language in {"en", "ja"}


def compose_vocabulary_markdown(
    media_path: Path,
    entries: list[VocabularyEntry],
    options: VocabularyOptions,
    *,
    generated_at: datetime | None = None,
) -> str:
    when = generated_at or datetime.now()
    time_str = when.strftime("%Y-%m-%d %H:%M:%S")
    language_label = LANGUAGE_LABELS.get(options.language, options.language)
    total_tokens = sum(entry.count for entry in entries)

    lines = [
        "---",
        f"title: {media_path.stem} 生词表（{language_label}）",
        f"source_media: {media_path.name}",
        f"vocabulary_language: {language_label}",
        f"word_count: {len(entries)}",
        f"token_occurrences: {total_tokens}",
        f"generated_at: {time_str}",
        "---",
        "",
        f"# {media_path.stem} — {language_label}生词表",
        "",
        f"> 生成时间：{time_str}  ",
        f"> 源视频：{media_path.name}  ",
        f"> 词汇数：{len(entries)}（出现次数合计 {total_tokens}）  ",
        f"> 筛选：最少出现 {options.min_frequency} 次"
        + (f"，最多 {options.max_items} 词" if options.max_items > 0 else ""),
        "",
    ]
    if _has_lexicon_columns(options.language):
        phonetic_label = "音标" if options.language == "en" else "注音"
        lines.extend(
            [
                f"| 序号 | 词汇 | {phonetic_label} | 词性 | 释义 | 频次 | 首次出现 | 例句 |",
                "| ---: | --- | --- | --- | --- | ---: | --- | --- |",
            ]
        )
        for index, entry in enumerate(entries, start=1):
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(index),
                        _escape_table_cell(entry.word),
                        _escape_table_cell(entry.reading or "—"),
                        _escape_table_cell(entry.pos or "—"),
                        _escape_table_cell(entry.definition or "—"),
                        str(entry.count),
                        _format_clock(entry.first_time),
                        _escape_table_cell(entry.example),
                    ]
                )
                + " |"
            )
    else:
        lines.extend(
            [
                "| 序号 | 词汇 | 频次 | 首次出现 | 例句 |",
                "| ---: | --- | ---: | --- | --- |",
            ]
        )
        for index, entry in enumerate(entries, start=1):
            lines.append(
                f"| {index} | {_escape_table_cell(entry.word)} | {entry.count} | "
                f"{_format_clock(entry.first_time)} | {_escape_table_cell(entry.example)} |"
            )
    lines.append("")
    return "\n".join(lines)


def write_vocabulary_csv(path: Path, entries: list[VocabularyEntry], language: str = "en") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        if _has_lexicon_columns(language):
            phonetic_label = "音标" if language == "en" else "注音"
            writer.writerow(["序号", "词汇", phonetic_label, "词性", "释义", "频次", "首次出现", "例句"])
            for index, entry in enumerate(entries, start=1):
                writer.writerow(
                    [
                        index,
                        entry.word,
                        entry.reading,
                        entry.pos,
                        entry.definition,
                        entry.count,
                        _format_clock(entry.first_time),
                        entry.example,
                    ]
                )
        else:
            writer.writerow(["序号", "词汇", "频次", "首次出现", "例句"])
            for index, entry in enumerate(entries, start=1):
                writer.writerow(
                    [
                        index,
                        entry.word,
                        entry.count,
                        _format_clock(entry.first_time),
                        entry.example,
                    ]
                )


def generate_vocabulary_list(
    media_path: Path,
    options: VocabularyOptions,
    *,
    ecdict_db_path: str = "",
) -> tuple[Path, Path]:
    from core.vocabulary_lookup import enrich_vocabulary_entries

    items = collect_valid_subtitle_corpus(media_path)
    if not items:
        raise RuntimeError("未找到有效字幕文件，无法生成生词表。")

    entries = extract_vocabulary_from_corpus(items, options)
    if not entries:
        raise RuntimeError("未从字幕中提取到符合条件的词汇。可尝试降低最少出现次数或更换语言。")

    if _has_lexicon_columns(options.language):
        entries = enrich_vocabulary_entries(
            entries,
            options.language,
            ecdict_path=ecdict_db_path,
        )

    markdown_path = build_vocabulary_output_path(media_path, options.language)
    csv_path = build_vocabulary_output_path(media_path, options.language, csv=True)
    markdown_path.write_text(
        compose_vocabulary_markdown(media_path, entries, options),
        encoding="utf-8",
    )
    write_vocabulary_csv(csv_path, entries, options.language)
    return markdown_path, csv_path
