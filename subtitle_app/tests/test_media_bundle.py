"""配套文件夹、时间线和字幕冲突选择。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.media_bundle import (
    apply_subtitle_choice,
    bundle_dir,
    settle_media_sidecars,
)
from core.subtitle import SubtitleSegment
from core.subtitle_loader import find_valid_subtitles
from core.subtitle_tags import SubtitleTagDocument, SubtitleTagEntry
from core.timeline import (
    assign_tags_by_time,
    build_timeline_segments,
    default_timeline_step,
    fold_timeline_into_subtitle,
)


def _srt(text: str = "1\n00:00:01,000 --> 00:00:02,000\n你好\n") -> str:
    return text


class BundleTests(unittest.TestCase):
    def test_legacy_subtitle_and_tag_move_into_data_folder(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            media = root / "电脑.mp4"
            media.write_bytes(b"video")
            subtitle = root / "电脑.srt"
            subtitle.write_text(_srt(), encoding="utf-8")
            tag = root / "电脑.srt.tags.json"
            tag.write_text("{}", encoding="utf-8")
            conflicts = settle_media_sidecars(media)
            self.assertEqual(conflicts, [])
            self.assertFalse(subtitle.exists())
            self.assertTrue((bundle_dir(media) / "电脑.srt").is_file())
            self.assertTrue((bundle_dir(media) / "电脑.srt.tags.json").is_file())
            found = find_valid_subtitles(media)
            self.assertEqual([path.name for path, _label in found], ["电脑.srt"])
            self.assertEqual(found[0][0].parent.name, "电脑.mp4.data")

    def test_same_name_in_both_places_is_a_conflict_until_chosen(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            media = root / "电脑.mp4"
            media.write_bytes(b"video")
            legacy = root / "电脑_中文.srt"
            bundled = bundle_dir(media) / "电脑_中文.srt"
            bundled.parent.mkdir()
            legacy.write_text("legacy", encoding="utf-8")
            bundled.write_text("bundled", encoding="utf-8")
            conflicts = settle_media_sidecars(media)
            self.assertEqual(len(conflicts), 1)
            self.assertTrue(legacy.is_file())
            self.assertTrue(bundled.is_file())
            kept = apply_subtitle_choice(media, conflicts[0], "legacy")
            self.assertEqual(kept.read_text(encoding="utf-8"), "legacy")
            self.assertFalse(legacy.exists())
            self.assertEqual(kept.parent.name, "电脑.mp4.data")

    def test_longer_video_name_keeps_its_subtitle(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            short = root / "第1课.mp4"
            longer = root / "第1课_复习.mp4"
            short.write_bytes(b"a")
            longer.write_bytes(b"b")
            (root / "第1课_复习.srt").write_text(_srt(), encoding="utf-8")
            self.assertEqual(settle_media_sidecars(short), [])
            self.assertTrue((root / "第1课_复习.srt").is_file())
            self.assertEqual(settle_media_sidecars(longer), [])
            self.assertTrue((bundle_dir(longer) / "第1课_复习.srt").is_file())


class TimelineTests(unittest.TestCase):
    def test_default_step_follows_duration(self) -> None:
        self.assertEqual(default_timeline_step(60), 10)
        self.assertEqual(default_timeline_step(61), 30)
        self.assertEqual(default_timeline_step(600), 30)
        self.assertEqual(default_timeline_step(601), 60)

    def test_timeline_covers_duration_without_a_file(self) -> None:
        segments = build_timeline_segments(25, 10)
        self.assertEqual([segment.start for segment in segments], [0, 10, 20])
        self.assertEqual(segments[-1].end, 25)

    def test_timeline_tags_follow_time_when_step_changes(self) -> None:
        wide = build_timeline_segments(60, 30)
        entry = SubtitleTagEntry(
            id="t1",
            index=1,
            start=0,
            end=30,
            text="00:00 → 00:30",
            tags=["重点"],
        )
        by_row, unmatched = assign_tags_by_time(wide, [entry])
        self.assertEqual(list(by_row), [0])
        self.assertEqual(unmatched, [])
        narrow = build_timeline_segments(60, 10)
        by_row, unmatched = assign_tags_by_time(narrow, [entry])
        self.assertEqual(list(by_row), [0])
        self.assertEqual(unmatched, [])

    def test_timeline_tags_attach_to_subtitle_by_start_time(self) -> None:
        segments = [
            SubtitleSegment(1, 0.0, 5.0, "第一句"),
            SubtitleSegment(2, 5.0, 12.0, "第二句"),
        ]
        timeline = SubtitleTagDocument(
            subtitle_file="电脑_时间线.srt",
            entries=[
                SubtitleTagEntry(
                    id="t1",
                    index=1,
                    start=6.0,
                    end=10.0,
                    text="00:00 → 00:10",
                    tags=["重点"],
                    tag_ops=[{"name": "重点", "present": True, "at": "2026-09-30T00:00:00Z"}],
                )
            ],
        )
        document = SubtitleTagDocument(subtitle_file="电脑.srt")
        changed = fold_timeline_into_subtitle(segments, document, timeline)
        self.assertTrue(changed)
        self.assertEqual(document.entries[0].text, "第二句")
        self.assertEqual(document.entries[0].tags, ["重点"])
        self.assertEqual(document.entries[0].start, 5.0)
        self.assertFalse(timeline.entries[0].has_content())


if __name__ == "__main__":
    unittest.main()
