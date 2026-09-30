"""打开视频后的截图同步：先抽帧，抽不出再下载压缩图。"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from core.cloud_sync import mark_screenshot_catalog_cleared, record_screenshot_baseline
from core.screenshot_sync import sync_screenshots
from core.screenshots import Screenshot, ScreenshotDocument, load_screenshots, save_screenshots, screenshot_image_file


HASH = "a" * 64
SHOT = "a1b2c3d4e5f6"
WEBP = b"RIFF" + len(b"WEBPTEST").to_bytes(4, "little") + b"WEBPTEST"


class FakeCloud:
    def __init__(self, shots: list[dict] | None = None) -> None:
        self.username = "tester"
        self.shots = shots
        self.uploads: list[tuple[str, bytes]] = []
        self.downloads = 0
        self.puts = 0
        self.baselines: list[list[str]] = []

    def put_screenshots(self, video_hash, video_stem, shots, baseline_ids):
        del video_hash, video_stem
        self.puts += 1
        self.baselines.append(list(baseline_ids))
        if self.shots is not None:
            return {"shots": self.shots}
        return {"shots": [{**shot, "has_image": False, "image_hash": ""} for shot in shots]}

    def put_screenshot_image(self, video_hash, shot_id, data):
        del video_hash
        self.uploads.append((shot_id, data))
        return {"has_image": True}

    def get_screenshot_image(self, video_hash, shot_id):
        del video_hash, shot_id
        self.downloads += 1
        return WEBP


def _shot(**overrides) -> Screenshot:
    payload = dict(
        id=SHOT,
        title="画面",
        time=3.5,
        frame=12,
        image=f"{SHOT}.png",
        created_at=1_700_000_000_000,
        updated_at=20,
        notes=[],
    )
    payload.update(overrides)
    return Screenshot(**payload)


class ScreenshotSyncTests(unittest.TestCase):
    def _run(self, tmp: Path, shot: Screenshot, cloud: FakeCloud, *, png: bool, extract_result: bool):
        media = tmp / "第1课.mp4"
        media.write_bytes(b"video")
        if png or shot.title:
            save_screenshots(media, ScreenshotDocument(entries=[shot]))
        if png:
            from core.screenshots import screenshot_image_path

            image = screenshot_image_path(media, shot.id)
            image.parent.mkdir(parents=True, exist_ok=True)
            image.write_bytes(b"png")
        calls = {"extract": 0, "compress": 0}

        def extract(media_path, seconds, dest):
            del media_path, seconds
            calls["extract"] += 1
            if not extract_result:
                return False
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"frame")
            return True

        def compress(path):
            calls["compress"] += 1
            self.assertEqual(path.suffix.lower(), ".png")
            return WEBP

        with (
            patch("core.screenshot_sync.video_content_hash", return_value=HASH),
            patch("core.screenshot_sync.extract_video_frame", extract),
            patch("core.screenshot_sync.compress_png_to_webp", compress),
            patch("core.cloud_sync._store_path", return_value=tmp / "baseline.json"),
        ):
            changed = sync_screenshots(cloud, media)
        return changed, calls

    def test_existing_original_is_uploaded_without_extracting_or_downloading(self) -> None:
        folder = Path(self.id().split(".")[-1])
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            cloud = FakeCloud()
            _changed, calls = self._run(Path(raw), _shot(), cloud, png=True, extract_result=True)
            self.assertEqual(calls["extract"], 0)
            self.assertEqual(calls["compress"], 1)
            self.assertEqual(cloud.downloads, 0)
            self.assertEqual(cloud.uploads[0][0], SHOT)
            del folder

    def test_frame_is_extracted_before_any_download(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            cloud = FakeCloud(
                shots=[
                    {
                        "id": SHOT,
                        "title": "画面",
                        "time": 3.5,
                        "frame": 12,
                        "created_at": 1_700_000_000_000,
                        "updated_at": 20,
                        "notes": [],
                        "has_image": True,
                        "image_hash": "abc",
                    }
                ]
            )
            _changed, calls = self._run(Path(raw), _shot(), cloud, png=False, extract_result=True)
            self.assertEqual(calls["extract"], 1)
            self.assertEqual(cloud.downloads, 0)
            self.assertEqual(cloud.uploads, [])
            media = Path(raw) / "第1课.mp4"
            stored = screenshot_image_file(media, SHOT)
            self.assertIsNotNone(stored)
            self.assertEqual(stored.suffix.lower(), ".png")

    def test_download_happens_only_after_extraction_fails(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            cloud = FakeCloud(
                shots=[
                    {
                        "id": SHOT,
                        "title": "画面",
                        "time": 3.5,
                        "frame": 12,
                        "created_at": 1_700_000_000_000,
                        "updated_at": 20,
                        "notes": [],
                        "has_image": True,
                        "image_hash": "abc",
                    }
                ]
            )
            changed, calls = self._run(Path(raw), _shot(), cloud, png=False, extract_result=False)
            self.assertTrue(changed)
            self.assertEqual(calls["extract"], 1)
            self.assertEqual(cloud.downloads, 1)
            self.assertEqual(cloud.uploads, [])
            stored = screenshot_image_file(Path(raw) / "第1课.mp4", SHOT)
            self.assertIsNotNone(stored)
            self.assertEqual(stored.suffix.lower(), ".webp")

    def test_missing_frame_number_downloads_without_extracting(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            cloud = FakeCloud(
                shots=[
                    {
                        "id": SHOT,
                        "title": "画面",
                        "time": 3.5,
                        "frame": None,
                        "created_at": 1_700_000_000_000,
                        "updated_at": 20,
                        "notes": [],
                        "has_image": True,
                        "image_hash": "abc",
                    }
                ]
            )
            _changed, calls = self._run(Path(raw), _shot(frame=None), cloud, png=False, extract_result=True)
            self.assertEqual(calls["extract"], 0)
            self.assertEqual(cloud.downloads, 1)

    def test_renamed_file_downloads_instead_of_deleting(self) -> None:
        import json
        import tempfile

        remote = {
            "id": SHOT,
            "title": "画面",
            "time": 3.5,
            "frame": 12,
            "created_at": 1_700_000_000_000,
            "updated_at": 20,
            "notes": [],
            "has_image": True,
            "image_hash": "abc",
        }
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            media = tmp / "hahaha.mp4"
            media.write_bytes(b"video")
            cloud = FakeCloud(shots=[remote])

            def extract(media_path, seconds, dest):
                del media_path, seconds
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(b"frame")
                return True

            with (
                patch("core.screenshot_sync.video_content_hash", return_value=HASH),
                patch("core.screenshot_sync.extract_video_frame", extract),
                patch("core.cloud_sync._store_path", return_value=tmp / "baseline.json"),
            ):
                (tmp / "baseline.json").write_text(
                    json.dumps({"screenshots": {HASH: [SHOT]}}),
                    encoding="utf-8",
                )
                changed = sync_screenshots(cloud, media)
            self.assertTrue(changed)
            self.assertEqual(cloud.baselines[0], [])
            self.assertEqual([shot.id for shot in load_screenshots(media).entries], [SHOT])
            stored = screenshot_image_file(media, SHOT)
            self.assertIsNotNone(stored)
            self.assertEqual(stored.suffix.lower(), ".png")

    def test_deleted_data_folder_downloads_instead_of_deleting(self) -> None:
        import tempfile

        remote = {
            "id": SHOT,
            "title": "画面",
            "time": 3.5,
            "frame": 12,
            "created_at": 1_700_000_000_000,
            "updated_at": 20,
            "notes": [],
            "has_image": True,
            "image_hash": "abc",
        }
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            media = tmp / "hahaha.mp4"
            media.write_bytes(b"video")
            cloud = FakeCloud(shots=[remote])

            def extract(media_path, seconds, dest):
                del media_path, seconds
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(b"frame")
                return True

            with (
                patch("core.screenshot_sync.video_content_hash", return_value=HASH),
                patch("core.screenshot_sync.extract_video_frame", extract),
                patch("core.cloud_sync._store_path", return_value=tmp / "baseline.json"),
            ):
                record_screenshot_baseline("tester", HASH, [SHOT], media)
                changed = sync_screenshots(cloud, media)
            self.assertTrue(changed)
            self.assertEqual(cloud.baselines[0], [])
            self.assertEqual([shot.id for shot in load_screenshots(media).entries], [SHOT])

    def test_clearing_in_the_player_still_reports_deletion(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            media = tmp / "第1课.mp4"
            media.write_bytes(b"video")
            cloud = FakeCloud(shots=[])
            with (
                patch("core.screenshot_sync.video_content_hash", return_value=HASH),
                patch("core.cloud_sync._store_path", return_value=tmp / "baseline.json"),
            ):
                record_screenshot_baseline("tester", HASH, [SHOT], media)
                mark_screenshot_catalog_cleared("tester", HASH, media)
                sync_screenshots(cloud, media)
            self.assertEqual(cloud.baselines[0], [SHOT])


if __name__ == "__main__":
    unittest.main()
