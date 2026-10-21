"""字幕译文的保存格式、显示和取句。"""

from __future__ import annotations

import unittest

from core.subtitle import SubtitleSegment
from core.subtitle_tags import SubtitleTagEntry, assign_tags
from core.subtitle_translation import (
    WAITING_SUBTITLE,
    LanguageCatalog,
    SubtitleLanguage,
    collect_untranslated_rows,
    compose_cue,
    cue_needs_translation,
    detect_source_language,
    language_catalog,
    onscreen_cue_text,
    parse_cue,
    parse_numbered_translations,
    segment_index_at_or_after,
    set_translation,
    source_text,
)


def _segment(index: int, text: str, start: float | None = None) -> SubtitleSegment:
    begin = float(index if start is None else start)
    return SubtitleSegment(index, begin, begin + 1, text)


class SubtitleTranslationTests(unittest.TestCase):
    def test_marker_splits_source_and_several_translations(self) -> None:
        text = "How are you?\n【中译】\n你好吗？\n【日译】\n元気？"
        cue = parse_cue(text)
        self.assertEqual(cue.source, "How are you?")
        self.assertEqual(cue.translations["zh"], "你好吗？")
        self.assertEqual(cue.translations["ja"], "元気？")
        self.assertEqual(compose_cue(cue.source, cue.translations), text)

    def test_set_translation_keeps_the_other_language(self) -> None:
        original = "Hello\n【日译】\nこんにちは"
        updated = set_translation(original, "zh", "你好")
        self.assertEqual(source_text(updated), "Hello")
        cue = parse_cue(updated)
        self.assertEqual(cue.translations["ja"], "こんにちは")
        self.assertEqual(cue.translations["zh"], "你好")
        cleared = set_translation(updated, "zh", "  ")
        self.assertNotIn("【中译】", cleared)
        self.assertIn("【日译】", cleared)

    def test_onscreen_text_uses_one_language_and_waits_for_the_rest(self) -> None:
        text = "Hello\n【中译】\n你好"
        self.assertEqual(onscreen_cue_text(text, "en", ""), "Hello")
        self.assertEqual(onscreen_cue_text(text, "en", "en"), "Hello")
        self.assertEqual(onscreen_cue_text(text, "en", "zh"), "你好")
        self.assertEqual(onscreen_cue_text(text, "en", "ja"), WAITING_SUBTITLE)
        self.assertEqual(onscreen_cue_text("   ", "en", "zh"), "")

    def test_language_choices_skip_the_source_language(self) -> None:
        english_source = language_catalog.choices("en")
        self.assertEqual(english_source[0], ("", "原文"))
        self.assertNotIn("en", [code for code, _label in english_source])
        self.assertEqual(
            [label for _code, label in english_source],
            ["原文", "中文", "日语"],
        )
        chinese_source = [code for code, _label in language_catalog.choices("zh")]
        self.assertEqual(chinese_source, ["", "en", "ja"])
        japanese_source = [label for _code, label in language_catalog.choices("ja")]
        self.assertEqual(japanese_source, ["原文", "中文", "英文"])

    def test_one_registration_adds_a_language(self) -> None:
        class KoreanLanguage(SubtitleLanguage):
            code = "ko"
            name = "韩语"
            marker = "【韩译】"
            prompt_name = "韩语"
            menu_order = 40

            def score(self, text: str) -> int:
                return sum(1 for char in text if 0xAC00 <= ord(char) <= 0xD7A3)

            def prefers(self, scores: dict[str, int]) -> bool:
                return scores.get(self.code, 0) >= 2

        catalog = LanguageCatalog()
        catalog.register(KoreanLanguage())
        chinese = language_catalog.get("zh")
        english = language_catalog.get("en")
        self.assertIsNotNone(chinese)
        self.assertIsNotNone(english)
        assert chinese is not None and english is not None
        catalog.register(chinese)
        catalog.register(english)
        self.assertEqual(
            [label for _code, label in catalog.choices("en")],
            ["原文", "中文", "韩语"],
        )
        self.assertEqual(catalog.code_for_marker("【韩译】"), "ko")
        self.assertEqual(catalog.detect(["안녕하세요", "감사합니다"]), "ko")

    def test_detects_japanese_english_and_chinese(self) -> None:
        self.assertEqual(detect_source_language(["今日はいい天気です", "明日も晴れます"]), "ja")
        self.assertEqual(
            detect_source_language(["How are you doing today", "I am fine thanks"]),
            "en",
        )
        self.assertEqual(detect_source_language(["今天天气不错", "我们出发吧"]), "zh")

    def test_collects_forward_from_the_playhead_and_skips_translated_rows(self) -> None:
        segments = [
            _segment(1, "one"),
            _segment(2, "two\n【中译】\n二"),
            _segment(3, "   "),
            _segment(4, "four"),
            _segment(5, "five"),
        ]
        self.assertEqual(collect_untranslated_rows(segments, 0, "zh", limit=40), [0, 3, 4])
        self.assertEqual(collect_untranslated_rows(segments, 3, "zh", limit=1), [3])
        self.assertFalse(cue_needs_translation(segments[1].text, "zh"))
        self.assertTrue(cue_needs_translation(segments[1].text, "ja"))

    def test_collection_stops_at_forty_and_does_not_look_backward(self) -> None:
        segments = [_segment(index, f"line {index}") for index in range(1, 81)]
        rows = collect_untranslated_rows(segments, 10, "zh", limit=40)
        self.assertEqual(rows[0], 10)
        self.assertEqual(len(rows), 40)
        self.assertNotIn(9, rows)

    def test_playhead_between_cues_uses_the_later_one(self) -> None:
        segments = [
            SubtitleSegment(1, 0.0, 1.0, "a"),
            SubtitleSegment(2, 3.0, 4.0, "b"),
        ]
        self.assertEqual(segment_index_at_or_after(segments, 0.4), 0)
        self.assertEqual(segment_index_at_or_after(segments, 2.0), 1)
        self.assertEqual(segment_index_at_or_after(segments, 5.0), 2)

    def test_numbered_response_must_cover_every_requested_line(self) -> None:
        parsed = parse_numbered_translations("```\n1\t你好\n2\t再见\n```", [1, 2])
        self.assertEqual(parsed, {1: "你好", 2: "再见"})
        self.assertIsNone(parse_numbered_translations("1\t你好", [1, 2]))

    def test_tags_match_the_source_after_a_translation_is_appended(self) -> None:
        segment = SubtitleSegment(3, 12.0, 14.0, "Hello there\n【中译】\n你好")
        entry = SubtitleTagEntry(
            id="abc",
            index=3,
            start=12.0,
            end=14.0,
            text="Hello there",
            tags=["生词"],
        )
        assignment = assign_tags([segment], [entry])
        self.assertIs(assignment.by_row[0], entry)
        self.assertEqual(assignment.unmatched, [])


if __name__ == "__main__":
    unittest.main()
