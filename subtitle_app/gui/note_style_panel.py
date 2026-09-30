"""笔记文本框的样式面板。

这个面板只编辑一份样式，不关心样式属于哪条笔记。
宿主决定当前激活的是哪一条，把面板放在它旁边，并在样式变化时写回去。
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QRect, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from core.screenshots import (
    SCREENSHOT_NOTE_ALIGNS,
    SCREENSHOT_NOTE_BACKGROUND_DEFAULT,
    SCREENSHOT_NOTE_COLOR_DEFAULT,
    SCREENSHOT_NOTE_COLORS,
    normalize_font_ratio,
    normalize_note_align,
    normalize_note_color,
)

_PANEL_STYLE = """
QFrame#noteStylePanel {
    background-color: rgba(16, 16, 16, 214);
    border: 1px solid rgba(255, 255, 255, 0.38);
    border-radius: 8px;
}
QLabel { color: #ffffff; background: transparent; }
QSlider { background: transparent; }
QPushButton {
    background-color: rgba(255, 255, 255, 0.08);
    color: #ffffff;
    border: 1px solid rgba(255, 255, 255, 0.35);
    border-radius: 4px;
    padding: 2px 8px;
}
QPushButton:hover { background-color: rgba(255, 255, 255, 0.16); }
"""


@dataclass
class NoteDisplayStyle:
    background: str = SCREENSHOT_NOTE_BACKGROUND_DEFAULT
    opacity: float = 0.85
    font: float = 0.06
    color: str = SCREENSHOT_NOTE_COLOR_DEFAULT
    align: str = "center"


class _ClickText(QLabel):
    """和普通标签同一套颜色，但可以点击。"""

    clicked = pyqtSignal()

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class NoteStylePanel(QFrame):
    """调节文本框背景、透明度、字号、文字颜色和对齐。"""

    edited = pyqtSignal()
    committed = pyqtSignal()
    dismissed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("noteStylePanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(_PANEL_STYLE)
        self.setFixedWidth(208)
        self._loading = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(6)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        self._title = QLabel("这条笔记")
        header.addWidget(self._title, stretch=1)
        close = _ClickText("X")
        close.clicked.connect(self.dismissed.emit)
        header.addWidget(close, alignment=Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header)

        self._opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self._opacity_slider.setRange(15, 100)
        self._opacity_slider.setValue(85)
        self._opacity_slider.setToolTip("文本框背景的透明度")
        self._opacity_slider.valueChanged.connect(self._emit_edited)
        self._opacity_slider.sliderReleased.connect(self.committed.emit)
        self._size_slider = QSlider(Qt.Orientation.Horizontal)
        self._size_slider.setRange(2, 16)
        self._size_slider.setValue(6)
        self._size_slider.setToolTip("字号是底图高度的百分比")
        self._size_slider.valueChanged.connect(self._emit_edited)
        self._size_slider.sliderReleased.connect(self.committed.emit)

        self._background_buttons = self._swatch_row(self._choose_background)
        self._color_buttons = self._swatch_row(self._choose_color)
        self._align_buttons: list[tuple[QPushButton, str]] = []
        align_host = QWidget()
        align_host.setStyleSheet("background: transparent;")
        align_row = QHBoxLayout(align_host)
        align_row.setContentsMargins(0, 0, 0, 0)
        align_row.setSpacing(4)
        for label, value in SCREENSHOT_NOTE_ALIGNS:
            button = QPushButton(label)
            button.setFixedHeight(26)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, chosen=value: self._choose_align(chosen))
            align_row.addWidget(button)
            self._align_buttons.append((button, value))
        align_row.addStretch(1)

        layout.addWidget(QLabel("透明度"))
        layout.addWidget(self._opacity_slider)
        layout.addWidget(QLabel("字号"))
        layout.addWidget(self._size_slider)
        layout.addWidget(QLabel("背景"))
        layout.addWidget(self._background_buttons[0])
        layout.addWidget(QLabel("文字"))
        layout.addWidget(self._color_buttons[0])
        layout.addWidget(QLabel("对齐"))
        layout.addWidget(align_host)
        self._apply_swatches()
        self._apply_align()
        self.hide()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        event.accept()

    def set_title(self, title: str) -> None:
        self._title.setText(title)

    def set_style(self, style: NoteDisplayStyle) -> None:
        self._loading = True
        self._opacity_slider.setValue(int(round(max(0.15, min(1.0, style.opacity)) * 100)))
        self._size_slider.setValue(int(round(normalize_font_ratio(style.font) * 100)))
        self._mark(
            self._background_buttons[1],
            normalize_note_color(style.background, SCREENSHOT_NOTE_BACKGROUND_DEFAULT),
        )
        self._mark(self._color_buttons[1], normalize_note_color(style.color))
        chosen = normalize_note_align(style.align)
        for button, value in self._align_buttons:
            button.setProperty("selected", value == chosen)
        self._loading = False
        self._apply_swatches()
        self._apply_align()

    def style(self) -> NoteDisplayStyle:
        return NoteDisplayStyle(
            background=self._selected(self._background_buttons[1], SCREENSHOT_NOTE_BACKGROUND_DEFAULT),
            opacity=max(0.15, min(1.0, self._opacity_slider.value() / 100)),
            font=normalize_font_ratio(self._size_slider.value() / 100),
            color=self._selected(self._color_buttons[1], SCREENSHOT_NOTE_COLOR_DEFAULT),
            align=self._selected_align(),
        )

    def place_near(self, anchor: QRect, bounds: QRect) -> None:
        """放在文本框上、下、左或右，并保持在底图里面。"""
        self.adjustSize()
        width = self.width()
        height = max(self.height(), self.sizeHint().height())
        gap = 8
        candidates = [
            QRect(anchor.center().x() - width // 2, anchor.bottom() + gap, width, height),
            QRect(anchor.center().x() - width // 2, anchor.top() - gap - height, width, height),
            QRect(anchor.right() + gap, anchor.center().y() - height // 2, width, height),
            QRect(anchor.left() - gap - width, anchor.center().y() - height // 2, width, height),
        ]

        def score(rect: QRect) -> tuple[int, int, int]:
            outside = _outside(rect, bounds)
            overlap = _overlap_area(rect, anchor)
            return (0 if outside == 0 else 1, overlap, outside)

        chosen = min(candidates, key=score)
        self.setGeometry(_shift_inside(chosen, bounds))

    def _swatch_row(self, choose) -> tuple[QWidget, list[tuple[QPushButton, str]]]:
        buttons: list[tuple[QPushButton, str]] = []
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        for name, value in SCREENSHOT_NOTE_COLORS:
            swatch = QPushButton()
            swatch.setFixedSize(22, 22)
            swatch.setCursor(Qt.CursorShape.PointingHandCursor)
            swatch.setToolTip(name)
            swatch.clicked.connect(lambda _checked=False, chosen=value: choose(chosen))
            row.addWidget(swatch)
            buttons.append((swatch, value))
        row.addStretch(1)
        host = QWidget()
        host.setStyleSheet("background: transparent;")
        host.setLayout(row)
        return host, buttons

    def _choose_background(self, color: str) -> None:
        self._mark(self._background_buttons[1], normalize_note_color(color, SCREENSHOT_NOTE_BACKGROUND_DEFAULT))
        self._apply_swatches()
        self._emit_edited()
        self.committed.emit()

    def _choose_color(self, color: str) -> None:
        self._mark(self._color_buttons[1], normalize_note_color(color))
        self._apply_swatches()
        self._emit_edited()
        self.committed.emit()

    def _choose_align(self, align: str) -> None:
        chosen = normalize_note_align(align)
        for button, value in self._align_buttons:
            self._paint_choice(button, value == chosen)
        self._emit_edited()
        self.committed.emit()

    def _emit_edited(self) -> None:
        if self._loading:
            return
        self.edited.emit()

    def _selected(self, buttons: list[tuple[QPushButton, str]], default: str) -> str:
        for button, value in buttons:
            if button.property("selected"):
                return normalize_note_color(value, default)
        return default

    def _selected_align(self) -> str:
        for button, value in self._align_buttons:
            if button.property("selected"):
                return value
        return "center"

    def _mark(self, buttons: list[tuple[QPushButton, str]], color: str) -> None:
        for button, value in buttons:
            button.setProperty("selected", value.upper() == color.upper())

    def _apply_swatches(self) -> None:
        self._paint_swatches(self._background_buttons[1], SCREENSHOT_NOTE_BACKGROUND_DEFAULT)
        self._paint_swatches(self._color_buttons[1], SCREENSHOT_NOTE_COLOR_DEFAULT)

    def _paint_swatches(self, buttons: list[tuple[QPushButton, str]], default: str) -> None:
        if not any(button.property("selected") for button, _value in buttons):
            self._mark(buttons, default)
        for button, value in buttons:
            selected = bool(button.property("selected"))
            border = "2px solid #ffffff" if selected else "1px solid rgba(255,255,255,0.55)"
            button.setStyleSheet(
                "QPushButton {"
                f"background-color: {value};"
                f"border: {border};"
                "border-radius: 4px;"
                "}"
            )

    def _apply_align(self) -> None:
        if not any(button.property("selected") for button, _value in self._align_buttons):
            for button, value in self._align_buttons:
                button.setProperty("selected", value == "center")
        for button, _value in self._align_buttons:
            self._paint_choice(button, bool(button.property("selected")))

    def _paint_choice(self, button: QPushButton, selected: bool) -> None:
        button.setProperty("selected", selected)
        border = "2px solid #ffffff" if selected else "1px solid rgba(255,255,255,0.35)"
        fill = "rgba(255,255,255,0.22)" if selected else "rgba(255,255,255,0.08)"
        button.setStyleSheet(
            "QPushButton {"
            f"background-color: {fill};"
            "color: #ffffff;"
            f"border: {border};"
            "border-radius: 4px;"
            "padding: 2px 8px;"
            "}"
        )


def _outside(rect: QRect, bounds: QRect) -> int:
    return (
        max(0, bounds.left() - rect.left())
        + max(0, bounds.top() - rect.top())
        + max(0, rect.right() - bounds.right())
        + max(0, rect.bottom() - bounds.bottom())
    )


def _overlap_area(rect: QRect, other: QRect) -> int:
    shared = rect.intersected(other)
    if shared.isEmpty():
        return 0
    return shared.width() * shared.height()


def _shift_inside(rect: QRect, bounds: QRect) -> QRect:
    moved = QRect(rect)
    if moved.width() > bounds.width():
        moved.setWidth(bounds.width())
    if moved.height() > bounds.height():
        moved.setHeight(bounds.height())
    if moved.left() < bounds.left():
        moved.moveLeft(bounds.left())
    if moved.top() < bounds.top():
        moved.moveTop(bounds.top())
    if moved.right() > bounds.right():
        moved.moveRight(bounds.right())
    if moved.bottom() > bounds.bottom():
        moved.moveBottom(bounds.bottom())
    return moved
