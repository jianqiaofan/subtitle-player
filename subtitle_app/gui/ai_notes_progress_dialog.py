from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QDialog, QLabel, QProgressBar, QVBoxLayout

from gui.styles import DARK_STYLE


class AiNotesProgressDialog(QDialog):
    """AI 笔记生成进度对话框（模拟进度，提升提交反馈体验）。"""

    def __init__(self, media_name: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("AI 笔记生成中")
        self.setMinimumWidth(440)
        self.setModal(True)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setStyleSheet(DARK_STYLE)

        self._current = 0
        self._target = 8
        self._finished = False

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        self.title_label = QLabel(f"正在为「{media_name}」生成 AI 笔记")
        self.title_label.setWordWrap(True)
        layout.addWidget(self.title_label)

        self.status_label = QLabel("任务已提交，正在准备…")
        self.status_label.setObjectName("hintLabel")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("0%")
        layout.addWidget(self.progress)

        self._timer = QTimer(self)
        self._timer.setInterval(80)
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        self._current = 0
        self._target = 8
        self._finished = False
        self.progress.setValue(0)
        self.progress.setFormat("0%")
        self.status_label.setText("任务已提交，正在准备…")
        self._timer.start()

    def set_status(self, message: str) -> None:
        self.status_label.setText(message)
        if "整理" in message:
            self._target = max(self._target, 28)
        elif "调用" in message or "DeepSeek" in message:
            self._target = max(self._target, 48)

    def finish_success(self) -> None:
        self._finished = True
        self._timer.stop()
        self._target = 100
        self._current = 100
        self.progress.setValue(100)
        self.progress.setFormat("100% — 完成")
        self.status_label.setText("笔记生成成功，正在保存…")

    def finish_failure(self, message: str) -> None:
        self._finished = True
        self._timer.stop()
        self.progress.setFormat("已停止")
        self.status_label.setText(message)

    def _tick(self) -> None:
        if self._finished:
            return

        if self._current < self._target:
            step = max(1, (self._target - self._current + 2) // 3)
            self._current = min(self._target, self._current + step)
        elif self._target < 92:
            self._target += 1

        self.progress.setValue(self._current)
        self.progress.setFormat(f"{self._current}%")
