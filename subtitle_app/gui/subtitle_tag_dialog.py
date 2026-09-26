from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.subtitle_tags import PRESET_TAGS, order_tags
from gui.styles import DARK_STYLE

_MAX_TAG_LENGTH = 48


class SubtitleTagDialog(QDialog):
    """选择标签和备注。多选时备注默认不改，勾选后才覆盖。"""

    def __init__(
        self,
        parent=None,
        *,
        selected_tags: list[str],
        note: str,
        custom_tags: list[str],
        row_count: int,
        notes_differ: bool,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("标签")
        self.setMinimumWidth(420)
        self.setStyleSheet(DARK_STYLE)
        self._checks: dict[str, QCheckBox] = {}
        self._note_overridden = False

        layout = QVBoxLayout(self)
        if row_count > 1:
            layout.addWidget(QLabel(f"将把所选 {row_count} 条字幕设为下面这些标签"))
        else:
            layout.addWidget(QLabel("选择标签，确认后显示在这条字幕上"))

        self._checks_host = QWidget()
        self._checks_layout = QVBoxLayout(self._checks_host)
        self._checks_layout.setContentsMargins(0, 0, 0, 0)
        self._checks_layout.setSpacing(4)
        layout.addWidget(self._checks_host)

        known = list(PRESET_TAGS)
        for name in custom_tags:
            if name not in known:
                known.append(name)
        for name in selected_tags:
            if name not in known:
                known.append(name)
        for name in known:
            self._add_checkbox(name, checked=name in selected_tags)

        add_row = QHBoxLayout()
        self.custom_edit = QLineEdit()
        self.custom_edit.setPlaceholderText("自定义标签")
        self.custom_edit.setMaxLength(_MAX_TAG_LENGTH)
        add_button = QPushButton("添加")
        add_button.clicked.connect(self._add_custom_tag)
        self.custom_edit.returnPressed.connect(self._add_custom_tag)
        add_row.addWidget(self.custom_edit, stretch=1)
        add_row.addWidget(add_button)
        layout.addLayout(add_row)

        layout.addWidget(QLabel("备注"))
        self.note_edit = QPlainTextEdit()
        self.note_edit.setMinimumHeight(120)
        self.note_edit.setPlaceholderText("可选。列表里只显示一行，鼠标移上去可看全文")
        if row_count > 1 and notes_differ:
            self.note_edit.setPlaceholderText("多条备注不同")
        else:
            self.note_edit.setPlainText(note)
        layout.addWidget(self.note_edit)

        self._apply_note = row_count == 1
        self.note_toggle = QCheckBox("把备注设为上面的内容")
        self.note_toggle.setChecked(False)
        self.note_toggle.toggled.connect(self._on_note_toggle)
        if row_count > 1:
            layout.addWidget(self.note_toggle)
            self.note_edit.setEnabled(False)
        else:
            self.note_toggle.hide()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def tags(self) -> list[str]:
        return order_tags(
            [name for name, box in self._checks.items() if box.isChecked()]
        )

    def note(self) -> str | None:
        """单条返回备注；多条未勾选覆盖时返回 None，表示保留各自备注。"""
        if not self._apply_note:
            return None
        return self.note_edit.toPlainText().strip()

    def _on_note_toggle(self, checked: bool) -> None:
        self._apply_note = checked
        self.note_edit.setEnabled(checked)

    def _add_checkbox(self, name: str, *, checked: bool) -> None:
        if name in self._checks:
            if checked:
                self._checks[name].setChecked(True)
            return
        box = QCheckBox(name)
        box.setChecked(checked)
        self._checks[name] = box
        self._checks_layout.addWidget(box)

    def _add_custom_tag(self) -> None:
        name = self.custom_edit.text().strip()
        if not name:
            return
        if len(name) > _MAX_TAG_LENGTH:
            QMessageBox.information(self, "标签", f"标签最多 {_MAX_TAG_LENGTH} 个字符。")
            return
        if any(ch in name for ch in "\r\n\t"):
            return
        self._add_checkbox(name, checked=True)
        self.custom_edit.clear()
