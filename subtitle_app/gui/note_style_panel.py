"""笔记文本框的样式面板。

这个面板只编辑一份样式，不关心样式属于哪条笔记。
宿主决定当前激活的是哪一条，并在样式变化时写回去。
面板默认出现在底图中央；可用标题栏拖动，下次仍出现在拖过后的位置。
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QPoint, QRect, QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.screenshots import (
    SCREENSHOT_NOTE_ALIGNS,
    SCREENSHOT_NOTE_ALIGN_DEFAULT,
    SCREENSHOT_NOTE_BACKGROUND_DEFAULT,
    SCREENSHOT_NOTE_COLOR_DEFAULT,
    SCREENSHOT_NOTE_COLORS,
    SCREENSHOT_NOTE_FONT_DEFAULT,
    SCREENSHOT_NOTE_SEASON_PRESETS,
    SCREENSHOT_NOTE_VALIGN_DEFAULT,
    SCREENSHOT_NOTE_VALIGNS,
    normalize_font_ratio,
    normalize_note_align,
    normalize_note_color,
    normalize_note_valign,
    normalize_title_background,
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
QToolButton#noteStyleClose {
    background: transparent;
    border: none;
    padding: 0;
    margin: 0;
}
QToolButton#noteStyleClose:hover {
    background-color: rgba(255, 255, 255, 0.14);
    border-radius: 4px;
}
"""


@dataclass
class NoteDisplayStyle:
    background: str = SCREENSHOT_NOTE_BACKGROUND_DEFAULT
    opacity: float = 0.85
    font: float = SCREENSHOT_NOTE_FONT_DEFAULT
    color: str = SCREENSHOT_NOTE_COLOR_DEFAULT
    align: str = SCREENSHOT_NOTE_ALIGN_DEFAULT
    valign: str = SCREENSHOT_NOTE_VALIGN_DEFAULT
    title_background: str = ""
    title_bold: bool = False
    title_italic: bool = False


class NoteStylePanel(QFrame):
    """调节文本框背景、透明度、字号、文字颜色和对齐。"""

    edited = pyqtSignal()
    committed = pyqtSignal()
    dismissed = pyqtSignal()
    moved = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("noteStylePanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(_PANEL_STYLE)
        self.setFixedWidth(228)
        self._loading = False
        self._drag_offset: QPoint | None = None
        self._header_height = 28
        self._background = SCREENSHOT_NOTE_BACKGROUND_DEFAULT
        self._color = SCREENSHOT_NOTE_COLOR_DEFAULT
        self._title_background = ""
        self._title_bold = False
        self._title_italic = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(6)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(4)
        self._title = QLabel("Note")
        self._title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._title.setToolTip("按住标题栏可拖动面板")
        header.addWidget(self._title, stretch=1)
        close = QToolButton()
        close.setObjectName("noteStyleClose")
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setToolTip("关闭")
        close.setFixedSize(22, 22)
        close.setIcon(QWidget.style(self).standardIcon(QStyle.StandardPixmap.SP_TitleBarCloseButton))
        close.setIconSize(QSize(14, 14))
        close.clicked.connect(self.dismissed.emit)
        self._close_button = close
        header.addWidget(close, alignment=Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header)
        self.setToolTip("按住标题栏可拖动面板")

        self._opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self._opacity_slider.setRange(15, 100)
        self._opacity_slider.setValue(85)
        self._opacity_slider.setToolTip("文本框背景的透明度")
        self._opacity_slider.valueChanged.connect(self._emit_edited)
        self._opacity_slider.sliderReleased.connect(self.committed.emit)
        self._size_slider = QSlider(Qt.Orientation.Horizontal)
        self._size_slider.setRange(2, 16)
        self._size_slider.setValue(int(round(SCREENSHOT_NOTE_FONT_DEFAULT * 100)))
        self._size_slider.setToolTip("字号是底图高度的百分比")
        self._size_slider.valueChanged.connect(self._emit_edited)
        self._size_slider.sliderReleased.connect(self.committed.emit)

        self._background_buttons = self._swatch_row(self._choose_background)
        self._color_buttons = self._swatch_row(self._choose_color)
        self._align_buttons: list[tuple[QPushButton, str]] = []
        self._valign_buttons: list[tuple[QPushButton, str]] = []
        align_host = self._choice_row(
            SCREENSHOT_NOTE_ALIGNS,
            self._align_buttons,
            self._choose_align,
        )
        valign_host = self._choice_row(
            SCREENSHOT_NOTE_VALIGNS,
            self._valign_buttons,
            self._choose_valign,
        )
        preset_host = QWidget()
        preset_host.setStyleSheet("background: transparent;")
        preset_grid = QVBoxLayout(preset_host)
        preset_grid.setContentsMargins(0, 0, 0, 0)
        preset_grid.setSpacing(4)
        row_buttons: list[QPushButton] = []
        for index, (_preset_id, label, background, color, opacity, title_bg) in enumerate(
            SCREENSHOT_NOTE_SEASON_PRESETS
        ):
            button = QPushButton(label)
            button.setFixedHeight(26)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setToolTip(label)
            button.clicked.connect(
                lambda _checked=False,
                bg=background,
                fg=color,
                op=opacity,
                title=title_bg: self._apply_season(bg, fg, op, title)
            )
            row_buttons.append(button)
            if len(row_buttons) == 2 or index == len(SCREENSHOT_NOTE_SEASON_PRESETS) - 1:
                row = QHBoxLayout()
                row.setContentsMargins(0, 0, 0, 0)
                row.setSpacing(4)
                for item in row_buttons:
                    row.addWidget(item, stretch=1)
                preset_grid.addLayout(row)
                row_buttons = []

        layout.addWidget(QLabel("预设"))
        layout.addWidget(preset_host)
        layout.addWidget(QLabel("透明度"))
        layout.addWidget(self._opacity_slider)
        layout.addWidget(QLabel("字号"))
        layout.addWidget(self._size_slider)
        layout.addWidget(QLabel("背景"))
        layout.addWidget(self._background_buttons[0])
        layout.addWidget(QLabel("文字"))
        layout.addWidget(self._color_buttons[0])
        layout.addWidget(QLabel("水平对齐"))
        layout.addWidget(align_host)
        layout.addWidget(QLabel("垂直对齐"))
        layout.addWidget(valign_host)
        self._apply_swatches()
        self._apply_align()
        self._apply_valign()
        self.hide()

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
        self._background = normalize_note_color(style.background, SCREENSHOT_NOTE_BACKGROUND_DEFAULT)
        self._mark(self._color_buttons[1], normalize_note_color(style.color))
        self._color = normalize_note_color(style.color)
        chosen = normalize_note_align(style.align)
        for button, value in self._align_buttons:
            button.setProperty("selected", value == chosen)
        valign = normalize_note_valign(style.valign)
        for button, value in self._valign_buttons:
            button.setProperty("selected", value == valign)
        self._title_background = normalize_title_background(style.title_background)
        self._title_bold = bool(style.title_bold)
        self._title_italic = bool(style.title_italic)
        self._loading = False
        self._apply_swatches()
        self._apply_align()
        self._apply_valign()

    def style(self) -> NoteDisplayStyle:
        return NoteDisplayStyle(
            background=self._background,
            opacity=max(0.15, min(1.0, self._opacity_slider.value() / 100)),
            font=normalize_font_ratio(self._size_slider.value() / 100),
            color=self._color,
            align=self._selected_align(),
            valign=self._selected_valign(),
            title_background=self._title_background,
            title_bold=self._title_bold,
            title_italic=self._title_italic,
        )

    def place_at(self, bounds: QRect, saved: QPoint | None = None) -> None:
        """首次放到画面中央；已有记忆位置则用记忆位置，并夹在 bounds 内。"""
        self.adjustSize()
        width = self.width()
        height = max(self.height(), self.sizeHint().height())
        if saved is not None:
            rect = QRect(saved.x(), saved.y(), width, height)
        else:
            rect = QRect(
                bounds.center().x() - width // 2,
                bounds.center().y() - height // 2,
                width,
                height,
            )
        self.setGeometry(_shift_inside(rect, bounds))

    def clamp_inside(self, bounds: QRect) -> None:
        if not self.isVisible() or not bounds.isValid():
            return
        self.setGeometry(_shift_inside(QRect(self.geometry()), bounds))

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._in_drag_zone(event.position().toPoint()):
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self.setCursor(Qt.CursorShape.SizeAllCursor)
            event.accept()
            return
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._drag_offset is not None and event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = None
            self.unsetCursor()
            self.moved.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _in_drag_zone(self, pos: QPoint) -> bool:
        if pos.y() > self._header_height + 8:
            return False
        close_geo = self._close_button.geometry()
        # 关闭按钮周围不开始拖动
        return not close_geo.adjusted(-4, -4, 4, 4).contains(pos)

    def _choice_row(
        self,
        choices: tuple[tuple[str, str], ...],
        store: list[tuple[QPushButton, str]],
        choose,
    ) -> QWidget:
        host = QWidget()
        host.setStyleSheet("background: transparent;")
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        for label, value in choices:
            button = QPushButton(label)
            button.setFixedHeight(26)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, chosen=value: choose(chosen))
            row.addWidget(button)
            store.append((button, value))
        row.addStretch(1)
        return host

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

    def _apply_season(self, background: str, color: str, opacity: float, title_background: str) -> None:
        self._background = normalize_note_color(background, SCREENSHOT_NOTE_BACKGROUND_DEFAULT)
        self._color = normalize_note_color(color)
        self._mark(self._background_buttons[1], self._background)
        self._mark(self._color_buttons[1], self._color)
        self._opacity_slider.setValue(int(round(max(0.15, min(1.0, opacity)) * 100)))
        self._title_background = normalize_title_background(title_background)
        self._title_bold = True
        self._title_italic = True
        self._apply_swatches()
        self._emit_edited()
        self.committed.emit()

    def _choose_background(self, color: str) -> None:
        self._background = normalize_note_color(color, SCREENSHOT_NOTE_BACKGROUND_DEFAULT)
        self._mark(self._background_buttons[1], self._background)
        self._apply_swatches()
        self._emit_edited()
        self.committed.emit()

    def _choose_color(self, color: str) -> None:
        self._color = normalize_note_color(color)
        self._mark(self._color_buttons[1], self._color)
        self._apply_swatches()
        self._emit_edited()
        self.committed.emit()

    def _choose_align(self, align: str) -> None:
        chosen = normalize_note_align(align)
        for button, value in self._align_buttons:
            self._paint_choice(button, value == chosen)
        self._emit_edited()
        self.committed.emit()

    def _choose_valign(self, valign: str) -> None:
        chosen = normalize_note_valign(valign)
        for button, value in self._valign_buttons:
            self._paint_choice(button, value == chosen)
        self._emit_edited()
        self.committed.emit()

    def _emit_edited(self) -> None:
        if self._loading:
            return
        self.edited.emit()

    def _selected_align(self) -> str:
        for button, value in self._align_buttons:
            if button.property("selected"):
                return value
        return SCREENSHOT_NOTE_ALIGN_DEFAULT

    def _selected_valign(self) -> str:
        for button, value in self._valign_buttons:
            if button.property("selected"):
                return value
        return SCREENSHOT_NOTE_VALIGN_DEFAULT

    def _mark(self, buttons: list[tuple[QPushButton, str]], color: str) -> None:
        for button, value in buttons:
            button.setProperty("selected", value.upper() == color.upper())

    def _apply_swatches(self) -> None:
        self._paint_swatches(self._background_buttons[1], SCREENSHOT_NOTE_BACKGROUND_DEFAULT)
        self._paint_swatches(self._color_buttons[1], SCREENSHOT_NOTE_COLOR_DEFAULT)

    def _paint_swatches(self, buttons: list[tuple[QPushButton, str]], default: str) -> None:
        # 预设色可不在色板上；此时不高亮任何色块，也不强制回默认色。
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
                button.setProperty("selected", value == SCREENSHOT_NOTE_ALIGN_DEFAULT)
        for button, _value in self._align_buttons:
            self._paint_choice(button, bool(button.property("selected")))

    def _apply_valign(self) -> None:
        if not any(button.property("selected") for button, _value in self._valign_buttons):
            for button, value in self._valign_buttons:
                button.setProperty("selected", value == SCREENSHOT_NOTE_VALIGN_DEFAULT)
        for button, _value in self._valign_buttons:
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
