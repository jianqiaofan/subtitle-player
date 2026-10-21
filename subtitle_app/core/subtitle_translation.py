"""把译文写在同一条字幕里，用单独一行的标记分开。

原文在标记上面。每种译文各占一个标记，例如：

    How are you?
    【中译】
    你好吗？
    【日译】
    元気？

标记只表示下面那段是哪种语言，不表示从哪种语言译来。

新增一种语言时，写一个 SubtitleLanguage 子类，填好名称、标记和识别规则，
再调用 language_catalog.register。注册位置不影响结果：
fallback 为真的语种只在其它语种都不认领时使用，menu_order 决定选择框里的先后。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from core.subtitle import SubtitleSegment


BATCH_SIZE = 40
WAITING_SUBTITLE = "请稍等，字幕正在翻译中"

DISPLAY_SOURCE = "source"
DISPLAY_TARGET = "target"
DISPLAY_BOTH = "both"


ORIGINAL_LANGUAGE = ""
ORIGINAL_LANGUAGE_LABEL = "原文"


class SubtitleLanguage:
    """一种字幕语言。子类补上识别规则后，注册进 language_catalog。"""

    code = ""
    name = ""
    marker = ""
    prompt_name = ""
    menu_order = 100
    fallback = False

    def score(self, text: str) -> int:
        return 0

    def prefers(self, scores: dict[str, int]) -> bool:
        """在已经统计过的分数里，这段文字是否应判成本语种。"""
        return False


class JapaneseLanguage(SubtitleLanguage):
    code = "ja"
    name = "日语"
    marker = "【日译】"
    prompt_name = "日语"
    menu_order = 30

    def score(self, text: str) -> int:
        total = 0
        for char in text:
            code = ord(char)
            if 0x3040 <= code <= 0x30FF or 0x31F0 <= code <= 0x31FF:
                total += 1
        return total

    def prefers(self, scores: dict[str, int]) -> bool:
        return scores.get(self.code, 0) >= 2


class EnglishLanguage(SubtitleLanguage):
    code = "en"
    name = "英文"
    marker = "【英译】"
    prompt_name = "英文"
    menu_order = 20

    def score(self, text: str) -> int:
        return sum(1 for char in text if ("A" <= char <= "Z") or ("a" <= char <= "z"))

    def prefers(self, scores: dict[str, int]) -> bool:
        english = scores.get(self.code, 0)
        chinese = scores.get("zh", 0)
        return english >= 10 and english > chinese


class ChineseLanguage(SubtitleLanguage):
    code = "zh"
    name = "中文"
    marker = "【中译】"
    prompt_name = "简体中文"
    menu_order = 10
    fallback = True

    def score(self, text: str) -> int:
        return sum(1 for char in text if 0x4E00 <= ord(char) <= 0x9FFF)

    def prefers(self, scores: dict[str, int]) -> bool:
        return True


class LanguageCatalog:
    """语种注册表。画面语言选项、标记和原文识别都从这里取。"""

    def __init__(self) -> None:
        self._by_code: dict[str, SubtitleLanguage] = {}
        self._order: list[str] = []

    def register(self, language: SubtitleLanguage) -> None:
        if not language.code or language.code in self._by_code:
            raise ValueError(f"语种无法注册：{language.code}")
        self._by_code[language.code] = language
        self._order.append(language.code)

    def get(self, code: str | None) -> SubtitleLanguage | None:
        return self._by_code.get(str(code or "").strip().lower())

    def languages(self) -> tuple[SubtitleLanguage, ...]:
        return tuple(self._by_code[code] for code in self._order)

    def code_for_marker(self, line: str) -> str | None:
        stripped = line.strip()
        for language in self.languages():
            if language.marker == stripped:
                return language.code
        return None

    def choices(self, source_lang: str) -> tuple[tuple[str, str], ...]:
        """画面语言选项。第一项是原文，并且不再列出原文自己的语种。"""
        source = self.get(source_lang)
        source_code = source.code if source is not None else ""
        others = sorted(
            (
                language
                for language in self.languages()
                if language.code != source_code
            ),
            key=lambda language: (language.menu_order, language.code),
        )
        return (
            (ORIGINAL_LANGUAGE, ORIGINAL_LANGUAGE_LABEL),
            *[(language.code, language.name) for language in others],
        )

    def detect(self, texts: list[str]) -> str:
        scores = {language.code: 0 for language in self.languages()}
        checked = 0
        for text in texts:
            sample = source_text(text)
            if not sample.strip():
                continue
            checked += 1
            for language in self.languages():
                scores[language.code] += language.score(sample)
            if checked >= 40:
                break
        ranked = [lang for lang in self.languages() if not lang.fallback]
        ranked.extend(lang for lang in self.languages() if lang.fallback)
        for language in ranked:
            if language.prefers(scores):
                return language.code
        if ranked:
            return ranked[-1].code
        return "zh"


language_catalog = LanguageCatalog()
language_catalog.register(JapaneseLanguage())
language_catalog.register(EnglishLanguage())
language_catalog.register(ChineseLanguage())

LANGUAGES: dict[str, SubtitleLanguage] = language_catalog._by_code
TRANSLATION_LANGUAGES: tuple[SubtitleLanguage, ...] = language_catalog.languages()
_NUMBERED_LINE = re.compile(r"^(\d+)(?:\t|[.、:：]|\s+)\s*(.+)$")


@dataclass
class CueText:
    source: str
    translations: dict[str, str] = field(default_factory=dict)


def normalize_display_mode(value: str | None) -> str:
    mode = str(value or "").strip().lower()
    if mode in {DISPLAY_SOURCE, DISPLAY_TARGET, DISPLAY_BOTH}:
        return mode
    return DISPLAY_SOURCE


def language_code(value: str | None) -> str | None:
    code = str(value or "").strip().lower()
    if code in LANGUAGES:
        return code
    return None


def normalize_language(value: str | None, fallback: str = "en") -> str:
    return language_code(value) or (fallback if fallback in LANGUAGES else "en")


def clean_source_overrides(raw: object) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    cleaned: dict[str, str] = {}
    for key, value in raw.items():
        path = str(key or "").strip()
        code = language_code(str(value or ""))
        if path and code:
            cleaned[path] = code
        if len(cleaned) >= 200:
            break
    return cleaned


def parse_cue(text: str) -> CueText:
    """切开一条字幕。标记必须单独成行，且整行就是【中译】【英译】或【日译】。"""
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    source_lines: list[str] = []
    translations: dict[str, str] = {}
    current: str | None = None
    bucket: list[str] = []

    def flush() -> None:
        nonlocal bucket
        if current is None:
            return
        translations[current] = "\n".join(bucket).strip()
        bucket = []

    for line in normalized.split("\n"):
        code = language_catalog.code_for_marker(line)
        if code is not None:
            flush()
            current = code
            bucket = []
            continue
        if current is None:
            source_lines.append(line)
        else:
            bucket.append(line)
    flush()
    return CueText("\n".join(source_lines).strip(), translations)


def source_text(text: str) -> str:
    return parse_cue(text).source


def compose_cue(source: str, translations: dict[str, str]) -> str:
    chunks: list[str] = []
    body = (source or "").strip()
    if body:
        chunks.append(body)
    for code, translation in translations.items():
        lang = LANGUAGES.get(code)
        translated = (translation or "").strip()
        if lang is None or not translated:
            continue
        chunks.append(lang.marker)
        chunks.append(translated)
    return "\n".join(chunks)


def set_translation(text: str, target_lang: str, translation: str) -> str:
    """写入一种译文，其它语种保持原样。译文为空则去掉这种标记。"""
    code = language_code(target_lang)
    if code is None:
        return text or ""
    cue = parse_cue(text)
    body = (translation or "").strip()
    if body:
        cue.translations[code] = body
    else:
        cue.translations.pop(code, None)
    return compose_cue(cue.source, cue.translations)


def has_translation(text: str, target_lang: str) -> bool:
    code = language_code(target_lang)
    if code is None:
        return False
    return bool((parse_cue(text).translations.get(code) or "").strip())


def cue_needs_translation(text: str, target_lang: str) -> bool:
    if not source_text(text).strip():
        return False
    return not has_translation(text, target_lang)


def onscreen_language(selected: str | None, source_lang: str) -> str:
    """没单独选过画面语言时，画面字幕用原文语种。"""
    return language_code(selected) or normalize_language(source_lang)


def onscreen_cue_text(text: str, source_lang: str, selected_lang: str | None) -> str:
    """画面字幕只显示一种语言。译文还没写好时，显示等待提示。"""
    cue = parse_cue(text)
    source = cue.source
    selected = onscreen_language(selected_lang, source_lang)
    if selected == normalize_language(source_lang):
        return source
    translation = (cue.translations.get(selected) or "").strip()
    if translation:
        return translation
    if source.strip():
        return WAITING_SUBTITLE
    return ""


def detect_source_language(texts: list[str]) -> str:
    """让每种语言自己判断。新增语种不用改这里。"""
    return language_catalog.detect(texts)


def segment_index_at_or_after(segments: list[SubtitleSegment], seconds: float) -> int:
    """当前时间所在的字幕；落在两条中间时，取后面那条。已经超过最后一条则返回条数。"""
    for index, segment in enumerate(segments):
        if seconds <= segment.end:
            return index
    return len(segments)


def collect_untranslated_rows(
    segments: list[SubtitleSegment],
    start_index: int,
    target_lang: str,
    limit: int = BATCH_SIZE,
) -> list[int]:
    """从 start_index 向后收集还没有目标译文的句子，跳过已翻译和空行。"""
    rows: list[int] = []
    if limit <= 0:
        return rows
    begin = max(0, start_index)
    for index in range(begin, len(segments)):
        text = segments[index].text or ""
        if not cue_needs_translation(text, target_lang):
            continue
        rows.append(index)
        if len(rows) >= limit:
            break
    return rows


def _single_line(text: str) -> str:
    return " ".join((text or "").replace("\r\n", "\n").replace("\n", " ").split())


def build_translation_messages(batch_text: str, target_lang: str) -> list[dict[str, str]]:
    lang = LANGUAGES[normalize_language(target_lang)]
    system = (
        f"你是字幕翻译。请把每条字幕译成{lang.prompt_name}。\n"
        "规则：\n"
        "1. 每条单独翻译，不要合并、拆开、跳过或调换顺序。\n"
        "2. 只输出译文，不要解释，不要加上【中译】【英译】【日译】这类标记。\n"
        "3. 每一行的格式为：序号、制表符、译文。序号必须与输入一致。\n"
        "4. 一条译文只占一行，译文里面不要换行。\n"
        "5. 用自然、简短的字幕用语，不要增删信息。"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": batch_text},
    ]


def parse_numbered_translations(text: str, expected: list[int]) -> dict[int, str] | None:
    """序号对不齐时返回 None，调用方不要把这组译文写进字幕。"""
    stripped = (text or "").strip()
    fenced = re.match(r"^```[^\n]*\n(.*)\n```\s*$", stripped, re.DOTALL)
    if fenced:
        stripped = fenced.group(1).strip()
    found: dict[int, str] = {}
    for raw_line in stripped.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("```"):
            continue
        match = _NUMBERED_LINE.match(line)
        if match is None:
            continue
        number = int(match.group(1))
        body = _clean_translation_body(match.group(2))
        if number in expected and body:
            found[number] = body
    if set(found) != set(expected):
        return None
    return found


def _clean_translation_body(text: str) -> str:
    lines = []
    for line in (text or "").replace("\r\n", "\n").split("\n"):
        if language_catalog.code_for_marker(line) is not None:
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def translate_cue_batch(
    config: object,
    items: list[tuple[int, str]],
    target_lang: str,
) -> list[tuple[int, str, str]]:
    """翻译一组句子。返回 (行号, 送出时的原文, 译文)。

    整组序号对不上时，拆成两半各请求一次。仍对不上的半组放弃，不写入。
    """
    return _translate_group(config, items, target_lang, allow_split=True)


def _translate_group(
    config: object,
    items: list[tuple[int, str]],
    target_lang: str,
    *,
    allow_split: bool,
) -> list[tuple[int, str, str]]:
    pending = [(row, source) for row, source in items if _single_line(source)]
    if not pending:
        return []
    from core.ai_notes import call_deepseek_chat

    slot_to_item: dict[int, tuple[int, str]] = {}
    lines: list[str] = []
    for slot, (row, source) in enumerate(pending, start=1):
        slot_to_item[slot] = (row, source)
        lines.append(f"{slot}\t{_single_line(source)}")
    content = call_deepseek_chat(
        config,
        build_translation_messages("\n".join(lines), target_lang),
    )
    parsed = parse_numbered_translations(content, list(slot_to_item))
    if parsed is None:
        if allow_split and len(pending) > 1:
            mid = len(pending) // 2
            left = _translate_group(config, pending[:mid], target_lang, allow_split=False)
            right = _translate_group(config, pending[mid:], target_lang, allow_split=False)
            return left + right
        return []
    translated: list[tuple[int, str, str]] = []
    for slot, translation in parsed.items():
        row, source = slot_to_item[slot]
        translated.append((row, source, translation))
    return translated
