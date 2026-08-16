from __future__ import annotations

from PyQt6.QtCore import QEvent, Qt, QTimer
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.config import AppConfig, load_config
from core.external_translate import get_translator, is_translator_running, send_hotkey
from core.subtitle import SubtitleSegment, format_timestamp
from gui.styles import DARK_STYLE


_MAX_HOURS = 999
# 分/秒模 60，毫秒模 1000；时不回绕。
_WRAP_MODULI = {1: 60, 2: 60, 3: 1000}


def _millis_to_parts(millis_total: int) -> tuple[int, int, int, int]:
    millis_total = max(0, millis_total)
    hours, rem = divmod(millis_total, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return hours, minutes, secs, ms


def _seconds_to_parts(seconds: float) -> tuple[int, int, int, int]:
    millis_total = int(round(seconds * 1000 + 1e-9))
    return _millis_to_parts(millis_total)


def _parts_to_seconds(hours: int, minutes: int, seconds: int, millis: int) -> float:
    return hours * 3600 + minutes * 60 + seconds + millis / 1000


class SubtitleEditDialog(QDialog):
    def __init__(
        self,
        segment: SubtitleSegment,
        parent=None,
        config: AppConfig | None = None,
    ) -> None:
        super().__init__(parent)
        self._segment = segment
        cfg = config or load_config()
        self._translator = get_translator(cfg.translate_app)
        self._hotkey = (cfg.translate_hotkey or "").strip() or self._translator.default_hotkey
        self._focused_time_edit: QLineEdit | None = None
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

        self._start_edits = (
            self.hour_edit,
            self.minute_edit,
            self.second_edit,
            self.millis_edit,
        )
        self._end_edits = (
            self.end_hour_edit,
            self.end_minute_edit,
            self.end_second_edit,
            self.end_millis_edit,
        )
        self._time_edits = self._start_edits + self._end_edits
        # (字段下标, 步进)
        self._field_nudge = {
            self.hour_edit: (0, 1),
            self.minute_edit: (1, 1),
            self.second_edit: (2, 1),
            self.millis_edit: (3, 100),
            self.end_hour_edit: (0, 1),
            self.end_minute_edit: (1, 1),
            self.end_second_edit: (2, 1),
            self.end_millis_edit: (3, 100),
        }

        form = QFormLayout()
        form.addRow(self._make_focus_nudge_row())
        form.addRow("起始时间", start_widget)
        form.addRow("终止时间", end_widget)

        self.text_edit = QPlainTextEdit(segment.text)
        self.text_edit.setMinimumHeight(120)
        self.text_edit.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.text_edit.customContextMenuRequested.connect(self._show_text_context_menu)
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

        for edit in self._time_edits:
            edit.installEventFilter(self)
        self.text_edit.installEventFilter(self)
        buttons.installEventFilter(self)
        for child in buttons.findChildren(QWidget):
            child.installEventFilter(self)

        for edit in (
            self.hour_edit,
            self.minute_edit,
            self.second_edit,
            self.millis_edit,
        ):
            edit.textChanged.connect(self._ensure_end_after_start)

        self.second_edit.setFocus(Qt.FocusReason.OtherFocusReason)
        self.second_edit.selectAll()
        self._focused_time_edit = self.second_edit

    def _make_focus_nudge_row(self) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        increase_btn = QPushButton("增加")
        increase_btn.setToolTip("对当前焦点时间框 +1（毫秒为 +100）")
        increase_btn.clicked.connect(lambda: self._nudge_focused_time_field(1))

        decrease_btn = QPushButton("减少")
        decrease_btn.setToolTip("对当前焦点时间框 -1（毫秒为 -100）")
        decrease_btn.clicked.connect(lambda: self._nudge_focused_time_field(-1))

        nudge_hint = QLabel("如需增加或减少，请先将鼠标移入到对应框中")
        nudge_hint.setObjectName("hintLabel")
        nudge_hint.setWordWrap(True)

        self._nudge_buttons = {increase_btn, decrease_btn}
        for btn in (increase_btn, decrease_btn):
            btn.installEventFilter(self)
            layout.addWidget(btn)
        layout.addWidget(nudge_hint)
        layout.addStretch()
        return row

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Type.FocusIn:
            if obj in self._time_edits:
                self._focused_time_edit = obj
            elif obj not in getattr(self, "_nudge_buttons", ()):
                self._focused_time_edit = None
        return super().eventFilter(obj, event)

    def _show_text_context_menu(self, pos) -> None:
        menu = self.text_edit.createStandardContextMenu()
        translate_action = QAction("翻译", menu)
        translate_action.setToolTip(f"调用{self._translator.name}快捷键 {self._hotkey}")
        selected = self.text_edit.textCursor().selectedText().replace("\u2029", "\n")
        translate_action.setEnabled(bool(selected.strip()))
        translate_action.triggered.connect(self._translate_selection)
        first = menu.actions()[0] if menu.actions() else None
        if first is not None:
            menu.insertAction(first, translate_action)
            menu.insertSeparator(first)
        else:
            menu.addAction(translate_action)
        menu.exec(self.text_edit.mapToGlobal(pos))

    def _ensure_translator_running(self) -> bool:
        if is_translator_running(self._translator):
            return True
        QMessageBox.information(
            self,
            self._translator.not_running_title,
            self._translator.not_running_message,
        )
        return False

    def _translate_selection(self) -> None:
        if not self.text_edit.textCursor().hasSelection():
            return
        if not self._ensure_translator_running():
            return
        self.text_edit.setFocus(Qt.FocusReason.OtherFocusReason)
        QTimer.singleShot(80, self._send_translate_hotkey)

    def _send_translate_hotkey(self) -> None:
        if not self.text_edit.textCursor().hasSelection():
            return
        if not self._ensure_translator_running():
            return
        self.text_edit.setFocus(Qt.FocusReason.OtherFocusReason)
        if send_hotkey(self._hotkey):
            return
        QMessageBox.warning(
            self,
            "翻译失败",
            f"无法发送{self._translator.name}快捷键 {self._hotkey}。",
        )

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

    def _row_edits_for(self, edit: QLineEdit) -> tuple[QLineEdit, QLineEdit, QLineEdit, QLineEdit]:
        if edit in self._start_edits:
            return self._start_edits
        return self._end_edits

    @staticmethod
    def _read_time_parts(
        hour_edit: QLineEdit,
        minute_edit: QLineEdit,
        second_edit: QLineEdit,
        millis_edit: QLineEdit,
    ) -> tuple[int, int, int, int]:
        def parse(field: QLineEdit) -> int:
            text = field.text().strip()
            try:
                return int(text) if text else 0
            except ValueError:
                return 0

        return parse(hour_edit), parse(minute_edit), parse(second_edit), parse(millis_edit)

    def _write_time_parts(
        self,
        hour_edit: QLineEdit,
        minute_edit: QLineEdit,
        second_edit: QLineEdit,
        millis_edit: QLineEdit,
        hours: int,
        minutes: int,
        secs: int,
        millis: int,
    ) -> None:
        edits = (hour_edit, minute_edit, second_edit, millis_edit)
        for field in edits:
            field.blockSignals(True)
        hour_edit.setText(f"{hours:02d}")
        minute_edit.setText(f"{minutes:02d}")
        second_edit.setText(f"{secs:02d}")
        millis_edit.setText(f"{millis:03d}")
        for field in edits:
            field.blockSignals(False)
        if hour_edit is self.hour_edit:
            self._ensure_end_after_start()

    def _nudge_time_row_field(self, edit: QLineEdit, direction: int) -> None:
        hour_edit, minute_edit, second_edit, millis_edit = self._row_edits_for(edit)
        values = list(
            self._read_time_parts(hour_edit, minute_edit, second_edit, millis_edit)
        )
        idx, step = self._field_nudge[edit]
        values[idx] += direction * step
        for i in range(idx, 0, -1):
            carry, values[i] = divmod(values[i], _WRAP_MODULI[i])
            values[i - 1] += carry
        if not 0 <= values[0] <= _MAX_HOURS:
            return
        self._write_time_parts(
            hour_edit,
            minute_edit,
            second_edit,
            millis_edit,
            *values,
        )

    def _nudge_seconds(self, second_edit: QLineEdit, delta: int) -> None:
        self._nudge_time_row_field(second_edit, delta)

    def _nudge_focused_time_field(self, direction: int) -> None:
        edit = self._focused_time_edit
        if edit is None or edit not in self._time_edits:
            return

        self._nudge_time_row_field(edit, direction)
        edit.setFocus(Qt.FocusReason.OtherFocusReason)
        edit.selectAll()

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
        self._write_time_parts(
            self.end_hour_edit,
            self.end_minute_edit,
            self.end_second_edit,
            self.end_millis_edit,
            hours,
            minutes,
            secs,
            millis,
        )

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
        segment: SubtitleSegment,
        parent=None,
        config: AppConfig | None = None,
    ) -> tuple[float, float, str] | None:
        dialog = SubtitleEditDialog(segment, parent, config=config)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.values()
