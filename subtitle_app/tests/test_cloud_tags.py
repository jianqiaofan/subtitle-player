"""云端标签差异、合并，以及字幕是否需要确认替换。"""

from __future__ import annotations

import unittest
from pathlib import Path

from core.cloud_sync import SubtitleFile, classify_subtitles, content_hash, subtitle_path_after_download
from core.video_hash import language_suffix, video_content_hash
from core.cloud_tags import build_upload_document, merge_server_document
from core.subtitle_tags import SubtitleTagDocument, SubtitleTagEntry


def _entry(entry_id: str, tags: list[str], ops: list[dict], note: str = "", note_at: str = "") -> SubtitleTagEntry:
    return SubtitleTagEntry(
        id=entry_id,
        index=0,
        start=1.0,
        end=2.0,
        text="你好",
        tags=list(tags),
        note=note,
        tag_ops=ops,
        note_at=note_at,
    )


class CloudTagTests(unittest.TestCase):
    def test_unchanged_tags_are_not_uploaded(self) -> None:
        entry = _entry("a", ["重点"], [{"name": "重点", "present": True, "at": "2026-01-01T00:00:00.000Z"}])
        local = SubtitleTagDocument(subtitle_file="第1课.srt", entries=[entry])
        baseline = SubtitleTagDocument(subtitle_file="第1课.srt", entries=[entry])
        self.assertIsNone(build_upload_document(local, baseline))

    def test_removed_tag_is_uploaded_as_absent(self) -> None:
        baseline = SubtitleTagDocument(
            subtitle_file="第1课.srt",
            entries=[_entry("a", ["重点"], [{"name": "重点", "present": True, "at": "2026-01-01T00:00:00.000Z"}])],
        )
        local = SubtitleTagDocument(
            subtitle_file="第1课.srt",
            entries=[_entry("a", [], [{"name": "重点", "present": False, "at": "2026-01-02T00:00:00.000Z"}])],
        )
        payload = build_upload_document(local, baseline)
        self.assertIsNotNone(payload)
        ops = payload["entries"][0]["tag_ops"]
        self.assertEqual(ops, [{"name": "重点", "present": False, "at": "2026-01-02T00:00:00.000Z"}])

    def test_newer_delete_wins_over_older_tag(self) -> None:
        local = SubtitleTagDocument(
            subtitle_file="第1课.srt",
            entries=[_entry("a", ["重点"], [{"name": "重点", "present": True, "at": "2026-01-01T00:00:00.000Z"}])],
        )
        server = {
            "version": 2,
            "subtitle_file": "第1课.srt",
            "entries": [
                {
                    "id": "a",
                    "index": 0,
                    "start": 1.0,
                    "end": 2.0,
                    "text": "你好",
                    "tag_ops": [{"name": "重点", "present": False, "at": "2026-01-03T00:00:00.000Z"}],
                }
            ],
        }
        merged = merge_server_document(local, server)
        self.assertEqual(merged.entries[0].tags, [])

    def test_older_cloud_delete_does_not_remove_a_newer_local_tag(self) -> None:
        local = SubtitleTagDocument(
            subtitle_file="第1课.srt",
            entries=[_entry("a", ["重点"], [{"name": "重点", "present": True, "at": "2026-01-05T00:00:00.000Z"}])],
        )
        server = {
            "version": 2,
            "entries": [
                {
                    "id": "a",
                    "index": 0,
                    "start": 1.0,
                    "end": 2.0,
                    "text": "你好",
                    "tag_ops": [{"name": "重点", "present": False, "at": "2026-01-01T00:00:00.000Z"}],
                }
            ],
        }
        merged = merge_server_document(local, server)
        self.assertEqual(merged.entries[0].tags, ["重点"])


class VideoHashTests(unittest.TestCase):
    def test_unchanged_file_reuses_saved_hash(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            media = Path(folder) / "1.mp4"
            media.write_bytes(b"same-bytes")
            first = video_content_hash(media)
            second = video_content_hash(media)
            self.assertEqual(first, second)
            bundle = Path(folder) / "1.mp4.data" / "1.mp4.videohash.json"
            self.assertTrue(bundle.is_file())
            self.assertFalse((Path(folder) / "1.mp4.videohash.json").is_file())
            media.write_bytes(b"edited-bytes")
            self.assertNotEqual(video_content_hash(media), first)

    def test_subtitle_uses_hash_file_beside_it(self) -> None:
        import tempfile

        from core.video_hash import cached_video_for_subtitle

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            media = root / "第1课.mp4"
            media.write_bytes(b"video-bytes")
            digest = video_content_hash(media)
            subtitle = root / "第1课_中文.srt"
            subtitle.write_text("1\n00:00:01,000 --> 00:00:02,000\n你好\n", encoding="utf-8")
            found, reason = cached_video_for_subtitle(subtitle)
            self.assertEqual(reason, "")
            self.assertIsNotNone(found)
            self.assertEqual(found.video_hash, digest)
            self.assertEqual(found.video_stem, "第1课")

    def test_missing_or_invalid_hash_file_explains_why(self) -> None:
        import tempfile

        from core.video_hash import cached_video_for_subtitle

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            subtitle = root / "第1课.srt"
            subtitle.write_text("字幕", encoding="utf-8")
            _found, missing = cached_video_for_subtitle(subtitle)
            self.assertIn("没有视频哈希文件", missing)
            cache = root / "第1课.mp4.videohash.json"
            cache.write_text('{"hash": "不是哈希"}', encoding="utf-8")
            _found, invalid = cached_video_for_subtitle(subtitle)
            self.assertIn("没有合法的视频哈希", invalid)
            other = root / "别的课.srt"
            other.write_text("字幕", encoding="utf-8")
            cache.write_text('{"hash": "%s"}' % ("a" * 64), encoding="utf-8")
            _found, mismatch = cached_video_for_subtitle(other)
            self.assertIn("对不上", mismatch)
            (root / "第1课.mkv.videohash.json").write_text('{"hash": "%s"}' % ("b" * 64), encoding="utf-8")
            _found, ambiguous = cached_video_for_subtitle(subtitle)
            self.assertIn("多个不同的视频哈希", ambiguous)

    def test_longer_video_name_claims_its_own_subtitle(self) -> None:
        import json
        import tempfile

        from core.video_hash import cached_video_for_subtitle

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            short = root / "第1课.mp4.videohash.json"
            long = root / "第1课_复习.mp4.videohash.json"
            short.write_text(json.dumps({"hash": "a" * 64}), encoding="utf-8")
            long.write_text(json.dumps({"hash": "b" * 64}), encoding="utf-8")
            subtitle = root / "第1课_复习.srt"
            subtitle.write_text("字幕", encoding="utf-8")
            found, reason = cached_video_for_subtitle(subtitle)
            self.assertEqual(reason, "")
            self.assertEqual(found.video_hash, "b" * 64)
            self.assertEqual(found.video_stem, "第1课_复习")

    def test_language_suffix_survives_a_rename(self) -> None:
        self.assertEqual(language_suffix("第1课_中文.srt", "第1课"), "_中文.srt")
        self.assertEqual(language_suffix("复习_中文.srt", "复习"), "_中文.srt")


class SubtitleClassifyTests(unittest.TestCase):
    def test_same_hash_is_not_a_conflict(self) -> None:
        text = "1\n00:00:01,000 --> 00:00:02,000\n你好\n"
        item = SubtitleFile(
            path=Path("第1课.srt"),
            video_hash="a" * 64,
            video_stem="第1课",
            subtitle_name="第1课.srt",
            content=text,
        )
        remote = {("a" * 64, ".srt"): {"content_hash": content_hash(text), "updated_at": "2026-01-01T00:00:00Z"}}
        plan = classify_subtitles([item], remote)
        self.assertEqual(plan[0].state, "same")

    def test_different_hash_needs_confirmation(self) -> None:
        item = SubtitleFile(
            path=Path("第1课.srt"),
            video_hash="a" * 64,
            video_stem="第1课",
            subtitle_name="第1课.srt",
            content="本地",
        )
        remote = {("a" * 64, ".srt"): {"content_hash": content_hash("云端"), "updated_at": "2026-01-02T08:00:00Z"}}
        plan = classify_subtitles([item], remote)
        self.assertEqual(plan[0].state, "changed")
        self.assertEqual(plan[0].remote_updated_at, "2026-01-02T08:00:00Z")

    def test_missing_remote_is_new(self) -> None:
        item = SubtitleFile(
            path=Path("第1课_中文.srt"),
            video_hash="a" * 64,
            video_stem="第1课",
            subtitle_name="第1课_中文.srt",
            content="中文",
        )
        plan = classify_subtitles([item], {})
        self.assertEqual(plan[0].state, "new")


class OpenMediaTagTests(unittest.TestCase):
    def test_missing_local_tag_is_offered_even_when_baseline_matches(self) -> None:
        import tempfile
        from unittest.mock import patch

        from core.cloud_sync import inspect_open_media

        document = {
            "version": 2,
            "subtitle_file": "第1课_中文.srt",
            "entries": [
                {
                    "id": "a",
                    "index": 1,
                    "start": 1.0,
                    "end": 2.0,
                    "text": "你好",
                    "tags": ["重点"],
                    "tag_ops": [{"name": "重点", "present": True, "at": "2026-09-29T00:00:00Z"}],
                    "note": "",
                }
            ],
        }

        class FakeClient:
            username = "unit-user"

            def get_sync(self, video_hash: str) -> dict:
                return {
                    "subtitles": [],
                    "tags": [
                        {
                            "subtitle_suffix": "_中文.srt",
                            "content_hash": "c" * 64,
                            "updated_at": "2026-09-29T00:00:00Z",
                            "document": document,
                        }
                    ],
                }

            def list_shares(self, video_hash: str) -> list:
                return []

        with tempfile.TemporaryDirectory() as folder:
            media = Path(folder) / "第1课.mp4"
            media.write_bytes(b"video")
            with patch("core.cloud_sync.tag_baseline_hash", return_value="c" * 64):
                update = inspect_open_media(FakeClient(), media)
            self.assertEqual(len(update.tags), 1)
            self.assertEqual(update.tags[0]["subtitle_name"], "第1课_中文.srt")

    def test_existing_local_tag_is_not_offered_when_cloud_is_unchanged(self) -> None:
        import json
        import tempfile
        from unittest.mock import patch

        from core.cloud_sync import inspect_open_media

        document = {
            "version": 2,
            "subtitle_file": "第1课_中文.srt",
            "entries": [
                {
                    "id": "a",
                    "index": 1,
                    "start": 1.0,
                    "end": 2.0,
                    "text": "你好",
                    "tags": ["重点"],
                    "tag_ops": [{"name": "重点", "present": True, "at": "2026-09-29T00:00:00Z"}],
                    "note": "",
                }
            ],
        }

        class FakeClient:
            username = "unit-user"

            def get_sync(self, video_hash: str) -> dict:
                return {
                    "subtitles": [],
                    "tags": [
                        {
                            "subtitle_suffix": "_中文.srt",
                            "content_hash": "c" * 64,
                            "updated_at": "2026-09-29T00:00:00Z",
                            "document": document,
                        }
                    ],
                }

            def list_shares(self, video_hash: str) -> list:
                return []

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            media = root / "第1课.mp4"
            media.write_bytes(b"video")
            tag_path = root / "第1课_中文.srt.tags.json"
            tag_path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
            with patch("core.cloud_sync.tag_baseline_hash", return_value="c" * 64):
                update = inspect_open_media(FakeClient(), media)
            self.assertEqual(update.tags, [])


class SubtitleSelectionAfterDownloadTests(unittest.TestCase):
    def test_timeline_placeholder_is_replaced_by_downloaded_subtitle(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            timeline = root / "hahaha.mp4.data" / "hahaha_时间线.srt"
            downloaded = root / "hahaha.mp4.data" / "hahaha_中文.srt"
            downloaded.parent.mkdir()
            downloaded.write_text("1\n00:00:00,000 --> 00:00:01,000\n你好\n", encoding="utf-8")
            chosen = subtitle_path_after_download(timeline, timeline, [downloaded])
            self.assertEqual(chosen, downloaded)

    def test_existing_subtitle_stays_selected(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            current = root / "hahaha_英文.srt"
            downloaded = root / "hahaha_中文.srt"
            current.write_text("en", encoding="utf-8")
            downloaded.write_text("zh", encoding="utf-8")
            timeline = root / "hahaha_时间线.srt"
            chosen = subtitle_path_after_download(current, timeline, [downloaded])
            self.assertEqual(chosen, current)


if __name__ == "__main__":
    unittest.main()
