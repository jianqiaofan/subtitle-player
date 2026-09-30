"""播放记录的本地追加和展示时间。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from core.playback_log import (
    begin_playback_session,
    end_playback_session,
    format_duration_ms,
    format_timestamp_ms,
    load_playback_sessions,
    merge_playback_sessions,
    playback_log_path,
    total_study_ms,
)


class PlaybackLogTests(unittest.TestCase):
    def test_session_is_stored_in_the_data_folder(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            media = Path(folder) / "电脑.mp4"
            media.write_bytes(b"video")
            started = begin_playback_session(media, 1_758_000_000_000)
            self.assertIsNone(end_playback_session(media, started.started_at + 500))
            self.assertEqual(load_playback_sessions(media), [])
            begin_playback_session(media, 1_758_000_000_000)
            finished = end_playback_session(media, 1_758_000_065_000)
            self.assertIsNotNone(finished)
            stored = load_playback_sessions(media)
            self.assertEqual(len(stored), 1)
            self.assertEqual(stored[0].ended_at, 1_758_000_065_000)
            self.assertEqual(playback_log_path(media).parent.name, "电脑.mp4.data")

    def test_remote_sessions_are_appended_without_dropping_local_ones(self) -> None:
        from core.playback_log import PlaybackSession

        local = PlaybackSession("a" * 32, 1_758_000_000_000, 1_758_000_010_000)
        open_session = PlaybackSession("c" * 32, 1_758_000_200_000, None)
        merged = merge_playback_sessions(
            [local, open_session],
            [{"id": "b" * 32, "started_at": 1_758_000_100_000, "ended_at": 1_758_000_130_000}],
        )
        self.assertEqual([item.id for item in merged], ["a" * 32, "b" * 32, "c" * 32])
        self.assertIsNone(merged[-1].ended_at)

    def test_total_and_shanghai_time(self) -> None:
        from core.playback_log import PlaybackSession

        sessions = [PlaybackSession("d" * 32, 1_758_000_000_000, 1_758_000_125_000)]
        self.assertEqual(total_study_ms(sessions), 125_000)
        self.assertEqual(format_duration_ms(125_000), "2分5秒")
        shown = format_timestamp_ms(1_758_000_000_000, "Asia/Shanghai")
        expected = datetime.fromtimestamp(1_758_000_000, tz=ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
        self.assertEqual(shown, expected)
        self.assertEqual(format_timestamp_ms(1_758_000_000_000, "不存在的时区"), expected)


if __name__ == "__main__":
    unittest.main()
