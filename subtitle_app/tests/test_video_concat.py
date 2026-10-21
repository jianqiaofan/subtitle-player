"""视频拼接的默认文件名、排序和 ffmpeg 拼接。"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from core.audio import find_ffmpeg, get_media_duration
from core.video_concat import concat_videos, default_output_path


class DefaultOutputTests(unittest.TestCase):
    def test_name_uses_first_file_directory_and_suffix(self) -> None:
        first = Path(r"E:\videos\第1课.mp4")
        output = default_output_path(first)
        self.assertEqual(output.parent, first.parent)
        self.assertEqual(output.name, "第1课拼接.mp4")

    def test_missing_suffix_becomes_mp4(self) -> None:
        output = default_output_path(Path(r"D:\clips\裸名"))
        self.assertEqual(output.name, "裸名拼接.mp4")


class SortDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication

        cls._app = QApplication.instance() or QApplication([])

    def test_move_changes_join_order(self) -> None:
        from gui.video_concat_dialog import VideoConcatDialog

        dialog = VideoConcatDialog([Path(r"E:\a\第二段.mp4"), Path(r"E:\a\第一段.mp4")])
        dialog._list.setCurrentRow(0)
        dialog._move(1)
        ordered = dialog.ordered_paths()
        self.assertEqual([item.name for item in ordered], ["第一段.mp4", "第二段.mp4"])
        self.assertEqual(dialog._list.item(0).text(), "1. 第一段.mp4")
        dialog.close()


class FfmpegConcatTests(unittest.TestCase):
    def test_same_layout_and_different_size(self) -> None:
        try:
            find_ffmpeg()
        except RuntimeError:
            self.skipTest("未找到 ffmpeg")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first = root / "第一段.mp4"
            second = root / "第二段.mp4"
            wide = root / "宽屏.mp4"
            _make_clip(first, "320x240")
            _make_clip(second, "320x240")
            _make_clip(wide, "160x120")
            same = root / "一样拼接.mp4"
            concat_videos([first, second], same)
            self.assertTrue(same.is_file())
            self.assertGreater(get_media_duration(same), 1.5)
            mixed = root / "尺寸拼接.mp4"
            tall = root / "竖屏.mp4"
            _make_clip(tall, "240x320")
            # 小画面放前面，确认画布取的是最大宽和最大高，而不是第一段的尺寸。
            concat_videos([wide, first, tall], mixed)
            self.assertTrue(mixed.is_file())
            duration = get_media_duration(mixed)
            self.assertGreater(duration, 2.5)
            self.assertLess(duration, 3.8)
            self.assertEqual(_frame_size(mixed), (320, 320))
            self.assertFalse((root / "尺寸拼接.part.mp4").exists())

    def test_short_video_long_audio_keeps_both_clips(self) -> None:
        try:
            find_ffmpeg()
        except RuntimeError:
            self.skipTest("未找到 ffmpeg")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first = root / "甲.mp4"
            second = root / "乙.mp4"
            _make_uneven_clip(first)
            _make_uneven_clip(second)
            output = root / "甲拼接.mp4"
            concat_videos([first, second], output)
            # 每段是 1 秒画面 + 3 秒声音，两段接起来应接近 6 秒，不能停在第 1 段画面结束。
            self.assertGreater(get_media_duration(output), 5.0)


def _frame_size(path: Path) -> tuple[int, int]:
    import json
    import subprocess

    from core.audio import find_ffprobe

    cmd = [
        find_ffprobe(),
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "json",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    stream = json.loads(result.stdout)["streams"][0]
    return int(stream["width"]), int(stream["height"])


def _make_clip(path: Path, size: str) -> None:
    import subprocess
    import sys

    cmd = [
        find_ffmpeg(),
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size={size}:rate=25:duration=1",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:sample_rate=48000:duration=1",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
        str(path),
    ]
    kwargs: dict = {"capture_output": True, "text": True, "encoding": "utf-8", "errors": "replace"}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    result = subprocess.run(cmd, **kwargs)
    if result.returncode != 0 or not path.is_file():
        raise RuntimeError(result.stderr[-500:])


def _make_uneven_clip(path: Path) -> None:
    """1 秒画面、3 秒声音，模拟 B 站成片里画面先结束的情况。"""
    import subprocess
    import sys

    cmd = [
        find_ffmpeg(),
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=320x240:rate=25:duration=1",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:sample_rate=48000:duration=3",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        str(path),
    ]
    kwargs: dict = {"capture_output": True, "text": True, "encoding": "utf-8", "errors": "replace"}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    result = subprocess.run(cmd, **kwargs)
    if result.returncode != 0 or not path.is_file():
        raise RuntimeError(result.stderr[-500:])
