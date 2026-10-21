"""同文件夹选集：只列出可播放视频，并按文件名里的数字排序。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from gui.player_window import list_folder_videos, natural_name_key


class NaturalNameKeyTests(unittest.TestCase):
    def test_numbers_sort_by_value(self) -> None:
        names = ["第10集.mp4", "第2集.mp4", "第1集.mkv"]
        self.assertEqual(
            sorted(names, key=natural_name_key),
            ["第1集.mkv", "第2集.mp4", "第10集.mp4"],
        )


class ListFolderVideosTests(unittest.TestCase):
    def test_lists_videos_in_natural_order_and_skips_other_files(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "第10集.mp4").write_bytes(b"")
            (root / "第2集.MKV").write_bytes(b"")
            (root / "第1集.webm").write_bytes(b"")
            (root / "旁白.mp3").write_bytes(b"")
            (root / "字幕.srt").write_text("", encoding="utf-8")
            nested = root / "子目录"
            nested.mkdir()
            (nested / "第3集.mp4").write_bytes(b"")

            names = [item.name for item in list_folder_videos(root)]

        self.assertEqual(names, ["第1集.webm", "第2集.MKV", "第10集.mp4"])

    def test_missing_folder_is_empty(self) -> None:
        self.assertEqual(list_folder_videos(None), [])
        self.assertEqual(list_folder_videos(Path(r"E:\does-not-exist-episode-folder")), [])
