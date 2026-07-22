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
        layout.addWidget(QLabel(f"第 {segment.index} 条"))

        (
            self.hour_edit,
            self.minute_edit,
            self.second_edit,
            self.millis_edit,
            start_widget,
        ) = self._make_time_row(segment.start)

        (
            self.end_hour_edit,
            self.end_minute_edit,
            self.end_second_edit,
            self.end_millis_edit,
            end_widget,
        ) = self._make_time_row(segment.end)

        form = QFormLayout()
        form.addRow("起始时间", start_widget)
        form.addRow("终止时间", end_widget)

        self.text_edit = QPlainTextEdit(segment.text)
        self.text_edit.setMinimumHeight(120)
        form.addRow("字幕内容", self.text_edit)

        layout.addLayout(form)

        hint = QLabel("若起始时间不早于终止时间，终止时间将自动调整为起始时间 +1 秒。")
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        for edit in (
            self.hour_edit,
            self.minute_edit,
            self.second_edit,
            self.millis_edit,
        ):
            edit.textChanged.connect(self._ensure_end_after_start)

    def _make_time_row(
        self, seconds: float
    ) -> tuple[QLineEdit, QLineEdit, QLineEdit, QLineEdit, QWidget]:
        hours, minutes, secs, millis = _seconds_to_parts(seconds)
        hour_edit = self._make_time_edit(f"{hours:02d}", 3, "时")
        minute_edit = self._make_time_edit(f"{minutes:02d}", 2, "分")
        second_edit = self._make_time_edit(f"{secs:02d}", 2, "秒")
        millis_edit = self._make_time_edit(f"{millis:03d}", 3, "毫秒")
        second_stepper = self._make_second_stepper(second_edit)

        time_row = QHBoxLayout()
        time_row.setSpacing(6)
        time_row.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        for label_text, edit in (
            ("时", hour_edit),
            ("分", minute_edit),
        ):
            time_row.addWidget(QLabel(label_text))
            time_row.addWidget(edit)
        time_row.addWidget(QLabel("秒"))
        time_row.addWidget(second_edit)
        time_row.addWidget(second_stepper)
        time_row.addWidget(QLabel("毫秒"))
        time_row.addWidget(millis_edit)
        time_row.addStretch()

        time_widget = QWidget()
        time_widget.setLayout(time_row)
        return hour_edit, minute_edit, second_edit, millis_edit, time_widget

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
        up_btn.clicked.connect(lambda: self._nudge_seconds(second_edit, 1))

        down_btn = QToolButton()
        down_btn.setText("−")
        down_btn.setToolTip("秒 -1")
        down_btn.setFixedSize(btn_width, edit_height - half_height)
        down_btn.setAutoRaise(False)
        down_btn.clicked.connect(lambda: self._nudge_seconds(second_edit, -1))

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

    def _nudge_seconds(self, second_edit: QLineEdit, delta: int) -> None:
        text = second_edit.text().strip()
        try:
            value = int(text) if text else 0
        except ValueError:
            value = 0
        value = max(0, min(59, value + delta))
        second_edit.setText(f"{value:02d}")

    @staticmethod
    def _parse_time_fields(
        hour_edit: QLineEdit,
        minute_edit: QLineEdit,
        second_edit: QLineEdit,
        millis_edit: QLineEdit,
    ) -> float | None:
        fields = (
            hour_edit.text().strip(),
            minute_edit.text().strip(),
            second_edit.text().strip(),
            millis_edit.text().strip(),
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

    def _parse_start_seconds(self) -> float | None:
        return self._parse_time_fields(
            self.hour_edit,
            self.minute_edit,
            self.second_edit,
            self.millis_edit,
        )

    def _parse_end_seconds(self) -> float | None:
        return self._parse_time_fields(
            self.end_hour_edit,
            self.end_minute_edit,
            self.end_second_edit,
            self.end_millis_edit,
        )

    def _set_end_seconds(self, seconds: float) -> None:
        hours, minutes, secs, millis = _seconds_to_parts(seconds)
        self.end_hour_edit.setText(f"{hours:02d}")
        self.end_minute_edit.setText(f"{minutes:02d}")
        self.end_second_edit.setText(f"{secs:02d}")
        self.end_millis_edit.setText(f"{millis:03d}")

    def _ensure_end_after_start(self) -> None:
        start = self._parse_start_seconds()
        end = self._parse_end_seconds()
        if start is None or end is None:
            return
        if start >= end:
            self._set_end_seconds(start + 1.0)

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

        self._ensure_end_after_start()

        new_end = self._parse_end_seconds()
        if new_end is None:
            QMessageBox.warning(
                self,
                "格式错误",
                "请填写完整的终止时间，时/分/秒/毫秒均须为整数。\n"
                "分、秒范围为 0～59，毫秒范围为 0～999。",
            )
            return
        if new_end < 0:
            QMessageBox.warning(self, "格式错误", "终止时间不能为负数。")
            return
        if new_start >= new_end:
            QMessageBox.warning(
                self,
                "格式错误",
                f"起始时间必须早于终止时间（{format_timestamp(new_end)}）。",
            )
            return
        self.accept()

    def values(self) -> tuple[float, float, str]:
        start = self._parse_start_seconds()
        end = self._parse_end_seconds()
        assert start is not None and end is not None
        return start, end, self.text_edit.toPlainText()

    @staticmethod
    def edit_segment(
        segment: SubtitleSegment, parent=None
    ) -> tuple[float, float, str] | None:
        dialog = SubtitleEditDialog(segment, parent)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.values()
