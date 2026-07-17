from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.subtitle import SubtitleSegment, format_timestamp
from gui.styles import DARK_STYLE


def _seconds_to_parts(seconds: float) -> tuple[int, int, int, int]:
    millis_total = int(round(seconds * 1000 + 1e-9))
    hours, rem = divmod(millis_total, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return hours, minutes, secs, ms


def _parts_to_seconds(hours: int, minutes: int, seconds: int, millis: int) -> float:
    return hours * 3600 + minutes * 60 + seconds + millis / 1000


class SubtitleEditDialog(QDialog):
    def __init__(self, segment: SubtitleSegment, parent=None) -> None:
        super().__init__(parent)
        self._segment = segment
        self.setWindowTitle("编辑字幕")
        self.setMinimumWidth(480)
        self.setStyleSheet(DARK_STYLE)

        layout = QVBoxLayout(self)
        end_label = format_timestamp(segment.end)
        layout.addWidget(
            QLabel(f"第 {segment.index} 条 · 结束时间 {end_label}")
        )

        hours, minutes, seconds, millis = _seconds_to_parts(segment.start)
        self.hour_edit = self._make_time_edit(f"{hours:02d}", 3, "时")
        self.minute_edit = self._make_time_edit(f"{minutes:02d}", 2, "分")
        self.second_edit = self._make_time_edit(f"{seconds:02d}", 2, "秒")
        self.millis_edit = self._make_time_edit(f"{millis:03d}", 3, "毫秒")
        second_stepper = self._make_second_stepper(self.second_edit)

        time_row = QHBoxLayout()
        time_row.setSpacing(6)
        time_row.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        for label_text, edit in (
            ("时", self.hour_edit),
            ("分", self.minute_edit),
        ):
            time_row.addWidget(QLabel(label_text))
            time_row.addWidget(edit)
        time_row.addWidget(QLabel("秒"))
        time_row.addWidget(self.second_edit)
        time_row.addWidget(second_stepper)
        time_row.addWidget(QLabel("毫秒"))
        time_row.addWidget(self.millis_edit)
        time_row.addStretch()

        time_widget = QWidget()
        time_widget.setLayout(time_row)

        form = QFormLayout()
        form.addRow("起始时间", time_widget)

        self.text_edit = QPlainTextEdit(segment.text)
        self.text_edit.setMinimumHeight(120)
        form.addRow("字幕内容", self.text_edit)

        layout.addLayout(form)

        hint = QLabel("修改起始时间后，结束时间保持不变。")
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _make_time_edit(value: str, max_length: int, tooltip: str) -> QLineEdit:
        edit = QLineEdit(value)
        edit.setFixedWidth(56 if max_length <= 2 else 64)
        edit.setMaxLength(max_length)
        edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        edit.setToolTip(tooltip)
        return edit

    def _make_second_stepper(self, second_edit: QLineEdit) -> QWidget:
        edit_height = max(second_edit.sizeHint().height(), 24)
        half_height = edit_height // 2
        btn_width = 22

        up_btn = QToolButton()
        up_btn.setText("+")
        up_btn.setToolTip("秒 +1")
        up_btn.setFixedSize(btn_width, half_height)
        up_btn.setAutoRaise(False)
        up_btn.clicked.connect(lambda: self._nudge_seconds(1))

        down_btn = QToolButton()
        down_btn.setText("−")
        down_btn.setToolTip("秒 -1")
        down_btn.setFixedSize(btn_width, edit_height - half_height)
        down_btn.setAutoRaise(False)
        down_btn.clicked.connect(lambda: self._nudge_seconds(-1))

        for btn in (up_btn, down_btn):
            btn.setStyleSheet(
                "QToolButton { padding: 0; margin: 0; font-size: 11px; }"
            )

        stepper = QWidget()
        stepper.setFixedSize(btn_width, edit_height)
        column = QVBoxLayout(stepper)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(up_btn)
        column.addWidget(down_btn)
        return stepper

    def _nudge_seconds(self, delta: int) -> None:
        text = self.second_edit.text().strip()
        try:
            value = int(text) if text else 0
        except ValueError:
            value = 0
        value = max(0, min(59, value + delta))
        self.second_edit.setText(f"{value:02d}")

    def _parse_start_seconds(self) -> float | None:
        fields = (
            self.hour_edit.text().strip(),
            self.minute_edit.text().strip(),
            self.second_edit.text().strip(),
            self.millis_edit.text().strip(),
        )
        if not all(fields):
            return None
        try:
            hours, minutes, seconds, millis = (int(value) for value in fields)
        except ValueError:
            return None
        if minutes >= 60 or seconds >= 60 or millis >= 1000:
            return None
        return _parts_to_seconds(hours, minutes, seconds, millis)

    def _on_accept(self) -> None:
        new_start = self._parse_start_seconds()
        if new_start is None:
            QMessageBox.warning(
                self,
                "格式错误",
                "请填写完整的起始时间，时/分/秒/毫秒均须为整数。\n"
                "分、秒范围为 0～59，毫秒范围为 0～999。",
            )
            return
        if new_start < 0:
            QMessageBox.warning(self, "格式错误", "起始时间不能为负数。")
            return
        if new_start >= self._segment.end:
            QMessageBox.warning(
                self,
                "格式错误",
                f"起始时间必须早于结束时间（{format_timestamp(self._segment.end)}）。",
            )
            return
        self.accept()

    def values(self) -> tuple[float, str]:
        start = self._parse_start_seconds()
        assert start is not None
        return start, self.text_edit.toPlainText()

    @staticmethod
    def edit_segment(segment: SubtitleSegment, parent=None) -> tuple[float, str] | None:
        dialog = SubtitleEditDialog(segment, parent)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.values()
