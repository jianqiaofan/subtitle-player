"""字幕标签筛选条：标签自动换行，控制按钮在宽度不够时单独占最下一行。"""

from __future__ import annotations

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtWidgets import QPushButton, QSizePolicy, QWidget

_GAP = 4


class TagFilterBar(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._tag_buttons: list[QPushButton] = []
        self._controls: list[QPushButton] = []
        self._laying_out = False
        policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def set_controls(self, buttons: list[QPushButton]) -> None:
        self._controls = list(buttons)
        for button in self._controls:
            button.setParent(self)
            button.show()

    def set_tag_buttons(self, buttons: list[QPushButton]) -> None:
        for old in self._tag_buttons:
            old.hide()
            old.setParent(None)
            old.deleteLater()
        self._tag_buttons = list(buttons)
        for button in self._tag_buttons:
            button.setParent(self)
            button.show()
        self.updateGeometry()
        self._relayout()

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._arrange(max(1, width), apply=False)

    def sizeHint(self) -> QSize:
        width = max(self.width(), 280)
        return QSize(width, self.heightForWidth(width))

    def minimumSizeHint(self) -> QSize:
        return QSize(120, 28)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._relayout()

    def _relayout(self) -> None:
        if self._laying_out:
            return
        self._laying_out = True
        try:
            height = self._arrange(max(1, self.width()), apply=True)
            if self.minimumHeight() != height:
                self.setMinimumHeight(height)
                self.updateGeometry()
        finally:
            self._laying_out = False

    def _measure(self, button: QPushButton, text: str) -> tuple[int, int]:
        previous = button.text()
        button.setText(text)
        hint = button.sizeHint()
        button.setText(previous)
        return hint.width(), max(hint.height(), 24)

    def _elide(self, button: QPushButton, text: str, max_width: int) -> str:
        full_width, _height = self._measure(button, text)
        if full_width <= max_width:
            return text
        metrics = button.fontMetrics()
        padding = max(16, full_width - metrics.horizontalAdvance(text))
        avail = max(24, max_width - padding)
        elided = metrics.elidedText(text, Qt.TextElideMode.ElideRight, avail)
        return elided or text[:1]

    def _arrange(self, width: int, *, apply: bool) -> int:
        x = 0
        y = 0
        row_h = 0

        def break_line() -> None:
            nonlocal x, y, row_h
            if row_h <= 0:
                return
            y += row_h + _GAP
            x = 0
            row_h = 0

        def place(button: QPushButton, button_width: int, button_height: int) -> None:
            nonlocal x, row_h
            button_width = max(1, min(button_width, width))
            if apply:
                button.setGeometry(x, y, button_width, button_height)
                button.show()
            x += button_width + _GAP
            row_h = max(row_h, button_height)

        for button in self._tag_buttons:
            full = str(button.property("fullText") or button.text())
            custom = bool(button.property("customTag"))
            full_w, height = self._measure(button, full)
            own_line = custom and full_w > width
            if own_line:
                break_line()
                shown = self._elide(button, full, width)
                shown_w, height = self._measure(button, shown)
                if apply:
                    button.setText(shown)
                    button.setToolTip(full)
                place(button, shown_w, height)
                break_line()
                continue
            if x > 0 and x + full_w > width:
                break_line()
            if apply:
                button.setText(full)
                button.setToolTip(full)
            place(button, full_w, height)

        if self._controls:
            sizes = [self._measure(button, button.text()) for button in self._controls]
            total = sum(item[0] for item in sizes) + _GAP * max(0, len(sizes) - 1)
            if x > 0 and x + total > width:
                break_line()
            for button, (button_width, button_height) in zip(self._controls, sizes):
                place(button, button_width, button_height)

        if row_h <= 0:
            return 0
        return y + row_h
