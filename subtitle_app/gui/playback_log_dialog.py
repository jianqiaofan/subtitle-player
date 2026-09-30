"""查看当前视频的播放记录。时间按设备时区显示，读不到时区时用上海时间。"""

from __future__ import annotations

from pathlib import Path

from zoneinfo import ZoneInfo

from PyQt6.QtCore import QDateTime, QTimeZone
from PyQt6.QtWidgets import QDialog, QLabel, QListWidget, QPushButton, QVBoxLayout

from core.playback_log import (
    SHANGHAI,
    completed_sessions,
    format_duration_ms,
    format_timestamp_ms,
    load_playback_sessions,
    total_study_ms,
)
from gui.styles import DARK_STYLE


def _format_point(timestamp_ms: int) -> str:
    zone = QTimeZone.systemTimeZone()
    if zone.isValid():
        name = bytes(zone.id()).decode("utf-8", errors="replace").strip()
        try:
            ZoneInfo(name)
        except Exception:
            shown = QDateTime.fromMSecsSinceEpoch(int(timestamp_ms), zone).toString("yyyy-MM-dd HH:mm:ss")
            if shown:
                return shown
        else:
            return format_timestamp_ms(timestamp_ms, name)
    return format_timestamp_ms(timestamp_ms, SHANGHAI)


class PlaybackLogDialog(QDialog):
    def __init__(self, media_path: Path, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("学习记录")
        self.setStyleSheet(DARK_STYLE)
        self.resize(520, 420)
        sessions = completed_sessions(load_playback_sessions(media_path))
        sessions.sort(key=lambda item: item.started_at, reverse=True)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(media_path.name))
        layout.addWidget(QLabel(f"学习总时长：{format_duration_ms(total_study_ms(sessions))}"))
        listing = QListWidget()
        if not sessions:
            listing.addItem("还没有播放记录。")
        for item in sessions:
            if item.ended_at is None:
                continue
            listing.addItem(
                f"{_format_point(item.started_at)}  →  {_format_point(item.ended_at)}"
                f"    {format_duration_ms(item.ended_at - item.started_at)}"
            )
        layout.addWidget(listing, stretch=1)
        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button)
