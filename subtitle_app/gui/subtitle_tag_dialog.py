from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core.subtitle_tags import TAG_CATEGORIES, is_preset_tag, order_tags
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
        intro: str | None = None,
        show_note: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("标签")
        self.setMinimumWidth(520)
        self.resize(520, 640)
        self.setStyleSheet(DARK_STYLE)
        self._checks: dict[str, QCheckBox] = {}
        self._custom_count = 0
        selected = set(selected_tags)

        layout = QVBoxLayout(self)
        if intro:
            layout.addWidget(QLabel(intro))
        elif row_count > 1:
            layout.addWidget(QLabel(f"将把所选 {row_count} 条字幕设为下面这些标签"))
        else:
            layout.addWidget(QLabel("选择标签，确认后显示在这条字幕上"))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll = scroll
        host = QWidget()
        host_layout = QVBoxLayout(host)
        host_layout.setContentsMargins(0, 0, 8, 0)
        host_layout.setSpacing(8)

        for title, names in TAG_CATEGORIES:
            host_layout.addWidget(self._section_label(title))
            grid = QGridLayout()
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(12)
            grid.setVerticalSpacing(4)
            for index, name in enumerate(names):
                box = self._make_checkbox(name, checked=name in selected)
                grid.addWidget(box, index // 3, index % 3)
            host_layout.addLayout(grid)

        host_layout.addWidget(self._section_label("自定义"))
        hint = QLabel("自己输入。只出现在当前这部视频里，同文件夹的其它视频不会带上。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #b5b5b5; font-weight: normal;")
        host_layout.addWidget(hint)

        self._custom_grid = QGridLayout()
        self._custom_grid.setContentsMargins(0, 0, 0, 0)
        self._custom_grid.setHorizontalSpacing(12)
        self._custom_grid.setVerticalSpacing(4)
        host_layout.addLayout(self._custom_grid)

        extra: list[str] = []
        for name in list(custom_tags) + list(selected_tags):
            cleaned = name.strip()
            if not cleaned or is_preset_tag(cleaned) or cleaned in extra:
                continue
            extra.append(cleaned)
        for name in extra:
            self._add_custom_checkbox(name, checked=name in selected, reveal=False)

        add_row = QHBoxLayout()
        self.custom_edit = QLineEdit()
        self.custom_edit.setPlaceholderText("输入自定义标签")
        self.custom_edit.setMaxLength(_MAX_TAG_LENGTH)
        add_button = QPushButton("添加")
        add_button.clicked.connect(self._add_custom_tag)
        self.custom_edit.returnPressed.connect(self._add_custom_tag)
        add_row.addWidget(self.custom_edit, stretch=1)
        add_row.addWidget(add_button)
        host_layout.addLayout(add_row)
        host_layout.addStretch(1)

        scroll.setWidget(host)
        layout.addWidget(scroll, stretch=1)

        self._apply_note = show_note and row_count == 1
        self.note_edit = QPlainTextEdit()
        self.note_toggle = QCheckBox("把备注设为上面的内容")
        if show_note:
            layout.addWidget(QLabel("备注"))
            self.note_edit.setMinimumHeight(120)
            self.note_edit.setPlaceholderText("可选。列表里只显示一行，鼠标移上去可看全文")
            if row_count > 1 and notes_differ:
                self.note_edit.setPlaceholderText("多条备注不同")
            else:
                self.note_edit.setPlainText(note)
            layout.addWidget(self.note_edit)
            self.note_toggle.setChecked(False)
            self.note_toggle.toggled.connect(self._on_note_toggle)
            if row_count > 1:
                layout.addWidget(self.note_toggle)
                self.note_edit.setEnabled(False)
            else:
                self.note_toggle.hide()
        else:
            self.note_edit.hide()
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

    def _section_label(self, title: str) -> QLabel:
        label = QLabel(title)
        label.setStyleSheet("color: #e8e8e8; font-weight: bold;")
        return label

    def _make_checkbox(self, name: str, *, checked: bool) -> QCheckBox:
        box = QCheckBox(name)
        box.setChecked(checked)
        self._checks[name] = box
        return box

    def _add_custom_checkbox(self, name: str, *, checked: bool, reveal: bool = True) -> None:
        existing = self._checks.get(name)
        if existing is not None:
            if checked:
                existing.setChecked(True)
            if reveal:
                self._scroll.ensureWidgetVisible(existing)
            return
        box = self._make_checkbox(name, checked=checked)
        row, column = divmod(self._custom_count, 3)
        self._custom_grid.addWidget(box, row, column)
        self._custom_count += 1
        if reveal:
            self._scroll.ensureWidgetVisible(box)

    def _add_custom_tag(self) -> None:
        name = self.custom_edit.text().strip()
        if not name:
            return
        if len(name) > _MAX_TAG_LENGTH:
            QMessageBox.information(self, "标签", f"标签最多 {_MAX_TAG_LENGTH} 个字符。")
            return
        if any(ch in name for ch in "\r\n\t"):
            return
        self._add_custom_checkbox(name, checked=True)
        self.custom_edit.clear()
