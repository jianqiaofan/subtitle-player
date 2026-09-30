"""在播放画面上全屏查看本视频的截图。控件盖在透明层上。"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QPoint, QRect, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core.screenshots import (
    SCREENSHOT_NOTE_BACKGROUND_DEFAULT,
    NoteFrame,
    Screenshot,
    ScreenshotNote,
    copy_note_frame,
    default_note_slots,
    normalize_font_ratio,
    normalize_note_align,
    normalize_note_color,
    note_frames_match,
    now_ms,
)
from gui.note_style_panel import NoteDisplayStyle, NoteStylePanel

_BUTTON_STYLE = """
QPushButton {
    background-color: rgba(0, 0, 0, 90);
    color: #ffffff;
    border: 1px solid rgba(255, 255, 255, 0.45);
    border-radius: 4px;
    padding: 4px 12px;
}
QPushButton:hover { background-color: rgba(0, 0, 0, 140); }
QPushButton:disabled { color: rgba(255, 255, 255, 0.35); }
"""

_GRIP = 14


def _text_flags(align: str) -> int:
    horizontal = {
        "left": Qt.AlignmentFlag.AlignLeft,
        "right": Qt.AlignmentFlag.AlignRight,
    }.get(align, Qt.AlignmentFlag.AlignHCenter)
    return int(horizontal | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap)


def _hit_edge(pos: QPoint, width: int, height: int) -> str:
    on_left = pos.x() <= _GRIP
    on_right = pos.x() >= width - _GRIP
    on_top = pos.y() <= _GRIP
    on_bottom = pos.y() >= height - _GRIP
    if on_top and on_left:
        return "tl"
    if on_top and on_right:
        return "tr"
    if on_bottom and on_left:
        return "bl"
    if on_bottom and on_right:
        return "br"
    if on_left:
        return "l"
    if on_right:
        return "r"
    if on_top:
        return "t"
    if on_bottom:
        return "b"
    return "move"


def _cursor_for(edge: str) -> Qt.CursorShape:
    if edge in {"l", "r"}:
        return Qt.CursorShape.SizeHorCursor
    if edge in {"t", "b"}:
        return Qt.CursorShape.SizeVerCursor
    if edge in {"tl", "br"}:
        return Qt.CursorShape.SizeFDiagCursor
    if edge in {"tr", "bl"}:
        return Qt.CursorShape.SizeBDiagCursor
    return Qt.CursorShape.SizeAllCursor


def _resized(action: str, origin: QRect, delta: QPoint) -> QRect:
    rect = QRect(origin)
    if action == "move":
        rect.translate(delta)
        return rect
    if action in {"l", "tl", "bl"}:
        rect.setLeft(origin.left() + delta.x())
    if action in {"r", "tr", "br"}:
        rect.setRight(origin.right() + delta.x())
    if action in {"t", "tl", "tr"}:
        rect.setTop(origin.top() + delta.y())
    if action in {"b", "bl", "br"}:
        rect.setBottom(origin.bottom() + delta.y())
    return rect.normalized()


def _clamp_rect(rect: QRect, bounds: QRect, max_width: int | None = None) -> QRect:
    if bounds.width() <= 0 or bounds.height() <= 0:
        return QRect(rect)
    rect = rect.normalized()
    limit = bounds.width()
    if max_width is not None:
        limit = min(limit, max(1, max_width))
    min_w = max(1, min(limit, round(bounds.width() * 0.08)))
    min_h = max(1, min(bounds.height(), round(bounds.height() * 0.06)))
    if rect.width() < min_w:
        rect.setWidth(min_w)
    if rect.height() < min_h:
        rect.setHeight(min_h)
    if rect.width() > limit:
        rect.setWidth(limit)
    if rect.height() > bounds.height():
        rect.setHeight(bounds.height())
    if rect.left() < bounds.left():
        rect.moveLeft(bounds.left())
    if rect.top() < bounds.top():
        rect.moveTop(bounds.top())
    if rect.right() > bounds.right():
        rect.moveRight(bounds.right())
    if rect.bottom() > bounds.bottom():
        rect.moveBottom(bounds.bottom())
    return rect


def _px(value: float, scale: float) -> int:
    return max(1, int(round(value * scale)))


def _paint_note_contents(
    painter: QPainter,
    rect: QRect,
    frame: NoteFrame,
    text: str,
    *,
    image_span: int,
    scale: float = 1.0,
    active: bool = False,
    grip: bool = False,
) -> None:
    """把一条笔记画进 rect。image_span 是底图高度，字号按这个高度的比例算。"""
    fill = QColor(frame.background)
    fill.setAlphaF(max(0.15, min(1.0, float(frame.opacity))))
    if active:
        border = QColor("#E2C6FF")
        pen_width = 2.4 * scale
    else:
        border = QColor("#222222" if fill.lightness() > 160 else "#ffffff")
        pen_width = 1.5 * scale
    border.setAlpha(230)
    painter.setPen(QPen(border, pen_width))
    painter.setBrush(fill)
    painter.drawRoundedRect(
        rect.adjusted(_px(1, scale), _px(1, scale), -_px(2, scale), -_px(2, scale)),
        _px(8, scale),
        _px(8, scale),
    )
    painter.setPen(QColor(frame.color))
    font = QFont(painter.font())
    font.setPixelSize(max(1, round(normalize_font_ratio(frame.font) * max(1, image_span))))
    painter.setFont(font)
    painter.drawText(
        rect.adjusted(_px(12, scale), _px(10, scale), -_px(18, scale), -_px(18, scale)),
        _text_flags(frame.align),
        text,
    )
    if not grip:
        return
    painter.setPen(QPen(border, 1.6 * scale))
    right = rect.right() - _px(6, scale)
    bottom = rect.bottom() - _px(6, scale)
    for offset in (0, 4, 8):
        step = 0 if offset == 0 else _px(offset, scale)
        painter.drawLine(right - step, bottom, right, bottom - step)


def _style_of(frame: NoteFrame) -> NoteDisplayStyle:
    return NoteDisplayStyle(
        background=frame.background,
        opacity=frame.opacity,
        font=frame.font,
        color=frame.color,
        align=frame.align,
    )


def _apply_style(frame: NoteFrame, style: NoteDisplayStyle) -> None:
    frame.background = style.background
    frame.opacity = style.opacity
    frame.font = style.font
    frame.color = style.color
    frame.align = style.align


def _short_note(text: str) -> str:
    compact = " ".join(text.split())
    if len(compact) <= 26:
        return compact or "（无文字）"
    return compact[:26] + "…"


class _NoteBox(QWidget):
    """一条笔记的文本框。拖动空白处移动，拖边缘改变宽高。"""

    edited = pyqtSignal()
    committed = pyqtSignal()
    activated = pyqtSignal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.note_id = ""
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet("background: transparent;")
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setToolTip("拖动移动，拖边缘或右下角调整宽高")
        self._frame = NoteFrame()
        self._text = ""
        self._bounds = QRect()
        self._action = ""
        self._press_global = QPoint()
        self._press_rect = QRect()
        self._active = False
        self._max_width_ratio = 1.0
        self.hide()

    def frame(self) -> NoteFrame:
        return self._frame

    def set_width_limit(self, ratio: float) -> None:
        self._max_width_ratio = max(0.05, min(1.0, float(ratio)))

    def set_active(self, active: bool) -> None:
        self._active = active
        self.update()

    def place(self, bounds: QRect, frame: NoteFrame, text: str) -> None:
        self._frame = frame
        self._text = text
        self.relayout(bounds)

    def relayout(self, bounds: QRect) -> None:
        self._bounds = QRect(bounds)
        if bounds.width() <= 0 or bounds.height() <= 0 or self._action:
            return
        frame = self._frame
        ratio = min(frame.width, self._max_width_ratio)
        rect = QRect(
            bounds.x() + round(frame.x * bounds.width()),
            bounds.y() + round(frame.y * bounds.height()),
            max(1, round(ratio * bounds.width())),
            max(1, round(frame.height * bounds.height())),
        )
        self.setGeometry(_clamp_rect(rect, bounds, self._max_pixels()))
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setFont(self.font())
        _paint_note_contents(
            painter,
            self.rect(),
            self._frame,
            self._text,
            image_span=self._bounds.height() if self._bounds.height() > 0 else self.height(),
            active=self._active,
            grip=True,
        )
        painter.end()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self.activated.emit()
        self._action = _hit_edge(event.position().toPoint(), self.width(), self.height())
        self._press_global = event.globalPosition().toPoint()
        self._press_rect = QRect(self.geometry())
        self.setCursor(_cursor_for(self._action))
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._action and event.buttons() & Qt.MouseButton.LeftButton:
            delta = event.globalPosition().toPoint() - self._press_global
            rect = _clamp_rect(
                _resized(self._action, self._press_rect, delta),
                self._bounds,
                self._max_pixels(),
            )
            self.setGeometry(rect)
            self._write_fractions(rect)
            self.edited.emit()
            event.accept()
            return
        edge = _hit_edge(event.position().toPoint(), self.width(), self.height())
        self.setCursor(_cursor_for(edge))
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        moved = bool(self._action) and self.geometry() != self._press_rect
        self._action = ""
        if moved:
            self.committed.emit()
        event.accept()

    def _max_pixels(self) -> int | None:
        if self._bounds.width() <= 0:
            return None
        return max(1, int(round(self._bounds.width() * self._max_width_ratio)))

    def _write_fractions(self, rect: QRect) -> None:
        bounds = self._bounds
        if bounds.width() <= 0 or bounds.height() <= 0:
            return
        self._frame.x = (rect.x() - bounds.x()) / bounds.width()
        self._frame.y = (rect.y() - bounds.y()) / bounds.height()
        self._frame.width = min(rect.width() / bounds.width(), self._max_width_ratio)
        self._frame.height = rect.height() / bounds.height()


class _ClickText(QLabel):
    """和旁边的文字同一套颜色，但可以点击。"""

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


class _NotePickList(QFrame):
    """从「显示笔记」向右展开的复选列表，决定同时展示哪几条。"""

    selection_changed = pyqtSignal(list)
    dismissed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("notePickList")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            """
            QFrame#notePickList {
                background-color: rgba(16, 16, 16, 214);
                border: 1px solid rgba(255, 255, 255, 0.38);
                border-radius: 8px;
            }
            QLabel { color: #ffffff; background: transparent; }
            QCheckBox { color: #ffffff; background: transparent; spacing: 6px; }
            QScrollArea { background: transparent; border: none; }
            """
        )
        self._checks: list[tuple[QCheckBox, str]] = []
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(6)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(QLabel("同时显示"), stretch=1)
        hide = _ClickText("X隐藏")
        hide.clicked.connect(self.dismissed.emit)
        header.addWidget(hide, alignment=Qt.AlignmentFlag.AlignTop)
        outer.addLayout(header)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setMaximumHeight(240)
        self._body = QWidget()
        self._body.setStyleSheet("background: transparent;")
        self._list = QVBoxLayout(self._body)
        self._list.setContentsMargins(0, 0, 0, 0)
        self._list.setSpacing(4)
        self._scroll.setWidget(self._body)
        outer.addWidget(self._scroll)
        self.setMinimumWidth(220)
        self.hide()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        event.accept()

    def set_notes(self, notes: list[tuple[str, str]]) -> None:
        while self._checks:
            checkbox, _note_id = self._checks.pop()
            self._list.removeWidget(checkbox)
            checkbox.setParent(None)
            checkbox.deleteLater()
        for note_id, text in notes:
            checkbox = QCheckBox(_short_note(text))
            checkbox.setToolTip(text)
            checkbox.toggled.connect(self._emit_selection)
            self._list.addWidget(checkbox)
            self._checks.append((checkbox, note_id))
        rows = max(1, min(len(notes), 6))
        self._scroll.setMinimumHeight(rows * 28)
        self.adjustSize()

    def _emit_selection(self) -> None:
        chosen = [note_id for checkbox, note_id in self._checks if checkbox.isChecked()]
        self.selection_changed.emit(chosen)


class ScreenshotPreview(QWidget):
    closed = pyqtSignal()
    style_changed = pyqtSignal(float, float, str, str, str)
    box_changed = pyqtSignal()
    export_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("screenshotPreview")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("#screenshotPreview { background-color: #000000; }")
        self.hide()

        self._shots: list[Screenshot] = []
        self._paths: list[Path] = []
        self._index = 0
        self._defaults = NoteFrame()
        self._pixmap = QPixmap()
        self._missing = False
        self._boxes: list[_NoteBox] = []
        self._visible_ids: list[str] = []
        self._active_id = ""
        self._dirty_ids: set[str] = set()
        self._notes_open = False

        self._notes_button = self._button("显示笔记")
        self._notes_button.setCheckable(True)
        self._notes_button.clicked.connect(self._toggle_notes)
        self._prev_image = self._button("上一图")
        self._next_image = self._button("下一图")
        self._prev_image.clicked.connect(lambda: self._step_image(-1))
        self._next_image.clicked.connect(lambda: self._step_image(1))
        self._export_button = self._button("普通截图")
        self._export_button.clicked.connect(self.export_requested.emit)
        nav = QWidget(self)
        nav.setStyleSheet("background: transparent;")
        nav_layout = QHBoxLayout(nav)
        nav_layout.setContentsMargins(0, 0, 0, 0)
        nav_layout.setSpacing(8)
        nav_layout.addWidget(self._export_button)
        nav_layout.addWidget(self._notes_button)
        nav_layout.addWidget(self._prev_image)
        nav_layout.addWidget(self._next_image)
        self._nav = nav

        self._close_button = self._button("×")
        self._close_button.setFixedSize(36, 32)
        self._close_button.clicked.connect(self.close_preview)

        self._picker = _NotePickList(self)
        self._picker.selection_changed.connect(self._show_notes)
        self._picker.dismissed.connect(self._hide_picker)
        self._panel = NoteStylePanel(self)
        self._panel.edited.connect(self._on_panel_edited)
        self._panel.committed.connect(self._on_panel_committed)
        self._panel.dismissed.connect(self._dismiss_style_panel)
        self._apply_notes_button_style()

    def show_shots(
        self,
        shots: list[Screenshot],
        paths: list[Path],
        index: int,
        *,
        opacity: float,
        font_size: float,
        color: str,
        align: str,
        background: str,
    ) -> None:
        self._commit_dirty(save_style=False)
        self._shots = list(shots)
        self._paths = list(paths)
        self._index = max(0, min(index, len(self._shots) - 1)) if self._shots else 0
        self._hide_notes(commit=False)
        self._defaults = NoteFrame(
            opacity=max(0.15, min(1.0, float(opacity))),
            font=normalize_font_ratio(font_size),
            color=normalize_note_color(color),
            align=normalize_note_align(align),
            background=normalize_note_color(background, SCREENSHOT_NOTE_BACKGROUND_DEFAULT),
        )
        self._load_current()
        self._sync_transport()
        self.show()
        self.raise_()
        self._restore_saved_layout()
        if not self._notes_open:
            self._position_controls()

    def _restore_saved_layout(self) -> None:
        notes = self._current_notes()
        saved = [note.id for note in notes if note.frame is not None]
        if not saved:
            return
        self._notes_open = True
        self._notes_button.blockSignals(True)
        self._notes_button.setChecked(True)
        self._notes_button.blockSignals(False)
        self._apply_notes_button_style()
        if len(notes) > 1:
            self._picker.set_notes([(note.id, note.text) for note in notes])
            chosen = set(saved)
            for checkbox, note_id in self._picker._checks:
                checkbox.blockSignals(True)
                checkbox.setChecked(note_id in chosen)
                checkbox.blockSignals(False)
            self._picker.hide()
        self._show_notes(saved)

    def close_preview(self) -> None:
        self._commit_dirty(save_style=False)
        self.release_images()
        self.hide()
        self.closed.emit()

    def release_images(self) -> None:
        """放开图片文件，方便删除后立刻从磁盘去掉。"""
        self._pixmap = QPixmap()
        self._missing = False

    def current_index(self) -> int:
        return self._index

    def current_title(self) -> str:
        if not self._shots or self._index >= len(self._shots):
            return ""
        return self._shots[self._index].title

    def render_composite(self) -> QImage | None:
        """底图按原尺寸，叠上当前显示的笔记。没有底图时返回空。"""
        if self._pixmap.isNull():
            return None
        source = self._pixmap.toImage()
        if source.isNull() or source.width() <= 0 or source.height() <= 0:
            return None
        image = QImage(source.size(), QImage.Format.Format_RGB32)
        image.fill(QColor("#000000"))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.drawImage(0, 0, source)
        painter.setFont(self.font())
        width = image.width()
        height = image.height()
        display = self._fitted_pixmap_rect()
        scale = height / display.height() if display is not None and display.height() > 0 else 1.0
        boxes = [box for box in self._boxes if box.isVisible() and box.note_id]
        boxes.sort(key=lambda box: box.note_id == self._active_id)
        limit = QRect(0, 0, width, height)
        for box in boxes:
            frame = box.frame()
            ratio = min(frame.width, box._max_width_ratio)
            rect = QRect(
                round(frame.x * width),
                round(frame.y * height),
                max(1, round(ratio * width)),
                max(1, round(frame.height * height)),
            )
            _paint_note_contents(
                painter,
                _clamp_rect(rect, limit, max(1, round(width * box._max_width_ratio))),
                frame,
                box._text,
                image_span=height,
                scale=scale,
            )
        painter.end()
        return image

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#000000"))
        fitted = self._fitted_pixmap_rect()
        if fitted is not None:
            painter.drawPixmap(fitted, self._pixmap)
        elif self._missing:
            painter.setPen(QColor(255, 255, 255, 180))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "图片文件不在")
        painter.end()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._dismiss_style_panel()
        super().mousePressEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._position_controls()

    def _button(self, text: str) -> QPushButton:
        button = QPushButton(text, self)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setStyleSheet(_BUTTON_STYLE)
        return button

    def _toggle_notes(self) -> None:
        notes = self._current_notes()
        if not notes:
            self._hide_notes(commit=False)
            return
        if len(notes) > 1 and self._notes_open and not self._picker.isVisible():
            self._notes_button.blockSignals(True)
            self._notes_button.setChecked(True)
            self._notes_button.blockSignals(False)
            self._apply_notes_button_style()
            self._picker.show()
            self._position_controls()
            return
        if not self._notes_button.isChecked():
            self._hide_notes()
            self._position_controls()
            return
        self._apply_notes_button_style()
        if len(notes) == 1:
            self._notes_open = True
            self._picker.hide()
            self._show_notes([notes[0].id])
            return
        self._notes_open = True
        self._picker.set_notes([(note.id, note.text) for note in notes])
        self._picker.show()
        self._show_notes([])

    def _hide_picker(self) -> None:
        self._picker.hide()
        self._position_controls()

    def _dismiss_style_panel(self) -> None:
        self._active_id = ""
        for box in self._boxes:
            box.set_active(False)
        self._panel.hide()

    def _hide_notes(self, *, commit: bool = True) -> None:
        if commit:
            self._commit_dirty(save_style=False)
        self._notes_open = False
        self._visible_ids = []
        self._active_id = ""
        self._dirty_ids.clear()
        for box in self._boxes:
            box.note_id = ""
            box.hide()
        self._picker.hide()
        self._panel.hide()
        self._notes_button.blockSignals(True)
        self._notes_button.setChecked(False)
        self._notes_button.blockSignals(False)
        self._apply_notes_button_style()

    def _show_notes(self, note_ids: list[str]) -> None:
        self._commit_dirty(save_style=False)
        known = {note.id for note in self._current_notes()}
        self._visible_ids = [note_id for note_id in note_ids if note_id in known]
        if self._active_id not in self._visible_ids:
            self._active_id = self._visible_ids[0] if self._visible_ids else ""
        self._layout_visible_boxes()
        self._sync_panel()
        self._position_controls()

    def _layout_visible_boxes(self) -> None:
        notes = {note.id: note for note in self._current_notes()}
        visible = [notes[note_id] for note_id in self._visible_ids if note_id in notes]
        self._visible_ids = [note.id for note in visible]
        count = len(visible)
        slots = default_note_slots(count) if count else []
        bounds = self._image_rect()
        fresh: list[str] = []
        for index, note in enumerate(visible):
            box = self._ensure_box(index)
            box.note_id = note.id
            box.set_width_limit(1.0)
            box.set_active(note.id == self._active_id)
            if note.frame is None:
                fresh.append(note.id)
            box.place(bounds, self._frame_for(note, slots[index]), note.text)
            box.show()
        for box in self._boxes[count:]:
            box.note_id = ""
            box.hide()
        for note_id in fresh:
            self._dirty_ids.add(note_id)
        if fresh:
            self._commit_dirty(save_style=False)

    def _frame_for(self, note: ScreenshotNote, slot: tuple[float, float, float, float]) -> NoteFrame:
        if note.frame is None:
            frame = copy_note_frame(self._defaults)
            frame.x, frame.y, frame.width, frame.height = slot
            return frame
        frame = copy_note_frame(note.frame)
        if frame.x + frame.width > 1:
            frame.x = max(0.0, 1 - frame.width)
        if frame.y + frame.height > 1:
            frame.y = max(0.0, 1 - frame.height)
        return frame

    def _ensure_box(self, index: int) -> _NoteBox:
        while len(self._boxes) <= index:
            box = _NoteBox(self)
            box.activated.connect(lambda b=box: self._on_box_activated(b.note_id))
            box.edited.connect(lambda b=box: self._on_box_edited(b.note_id))
            box.committed.connect(lambda b=box: self._on_box_committed(b.note_id))
            self._boxes.append(box)
        return self._boxes[index]

    def _on_box_activated(self, note_id: str) -> None:
        if not note_id or note_id not in self._visible_ids:
            return
        self._active_id = note_id
        for box in self._boxes:
            box.set_active(box.isVisible() and box.note_id == note_id)
        self._sync_panel()
        self._position_controls()

    def _on_box_edited(self, note_id: str) -> None:
        if note_id:
            self._dirty_ids.add(note_id)
        if note_id == self._active_id:
            self._place_panel()

    def _on_box_committed(self, note_id: str) -> None:
        if note_id:
            self._dirty_ids.add(note_id)
        self._commit_note(note_id, save_style=False)
        if note_id == self._active_id:
            self._place_panel()

    def _on_panel_edited(self) -> None:
        box = self._box_for(self._active_id)
        if box is None:
            return
        _apply_style(box.frame(), self._panel.style())
        box.update()
        self._dirty_ids.add(self._active_id)

    def _on_panel_committed(self) -> None:
        self._on_panel_edited()
        self._commit_note(self._active_id, save_style=True)

    def _sync_panel(self) -> None:
        box = self._box_for(self._active_id)
        if box is None:
            self._panel.hide()
            return
        self._panel.set_style(_style_of(box.frame()))
        self._panel.show()
        self._place_panel()

    def _place_panel(self) -> None:
        box = self._box_for(self._active_id)
        if box is None or not self._panel.isVisible():
            return
        self._panel.place_near(box.geometry(), self._image_rect())
        self._raise_chrome()

    def _commit_dirty(self, *, save_style: bool) -> None:
        for note_id in list(self._dirty_ids):
            self._commit_note(note_id, save_style=save_style)

    def _commit_note(self, note_id: str, *, save_style: bool) -> None:
        if note_id not in self._dirty_ids:
            return
        note = self._note_by_id(note_id)
        box = self._box_for(note_id)
        self._dirty_ids.discard(note_id)
        if note is None or box is None or not self._shots:
            return
        frame = copy_note_frame(box.frame())
        if note_frames_match(note.frame, frame):
            if save_style:
                self._publish_style(frame)
            return
        note.frame = frame
        moment = now_ms()
        note.updated_at = moment
        self._shots[self._index].updated_at = moment
        self.box_changed.emit()
        if save_style:
            self._publish_style(frame)

    def _publish_style(self, frame: NoteFrame) -> None:
        self._defaults.opacity = frame.opacity
        self._defaults.font = frame.font
        self._defaults.color = frame.color
        self._defaults.align = frame.align
        self._defaults.background = frame.background
        self.style_changed.emit(
            frame.opacity,
            frame.font,
            frame.color,
            frame.align,
            frame.background,
        )

    def _step_image(self, step: int) -> None:
        if not self._shots:
            return
        nxt = self._index + step
        if nxt < 0 or nxt >= len(self._shots):
            return
        self._commit_dirty(save_style=False)
        self._index = nxt
        self._hide_notes(commit=False)
        self._load_current()
        self._sync_transport()
        self._position_controls()
        self.update()

    def _box_for(self, note_id: str) -> _NoteBox | None:
        if not note_id:
            return None
        for box in self._boxes:
            if box.isVisible() and box.note_id == note_id:
                return box
        return None

    def _note_by_id(self, note_id: str) -> ScreenshotNote | None:
        for note in self._current_notes():
            if note.id == note_id:
                return note
        return None

    def _current_notes(self) -> list[ScreenshotNote]:
        if not self._shots or self._index >= len(self._shots):
            return []
        return list(self._shots[self._index].notes)

    def _load_current(self) -> None:
        self._pixmap = QPixmap()
        self._missing = False
        if not self._shots or self._index >= len(self._paths):
            return
        path = self._paths[self._index]
        if not path.is_file():
            self._missing = True
            return
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            self._missing = True
            return
        self._pixmap = pixmap

    def _sync_transport(self) -> None:
        count = len(self._shots)
        self._prev_image.setEnabled(self._index > 0)
        self._next_image.setEnabled(self._index < count - 1)
        self._export_button.setEnabled(not self._pixmap.isNull())
        enabled = bool(self._current_notes())
        self._notes_button.setEnabled(enabled)
        if not enabled:
            self._hide_notes(commit=False)

    def _apply_notes_button_style(self) -> None:
        selected = self._notes_button.isChecked()
        border = "2px solid #ffffff" if selected else "1px solid rgba(255,255,255,0.45)"
        fill = "background-color: rgba(255,255,255,0.22);" if selected else ""
        self._notes_button.setStyleSheet(
            _BUTTON_STYLE + "QPushButton {" + f"border: {border}; {fill}" + "}"
        )

    def _fitted_pixmap_rect(self) -> QRect | None:
        if self._pixmap.isNull() or self.width() <= 0 or self.height() <= 0:
            return None
        source_w = self._pixmap.width()
        source_h = self._pixmap.height()
        if source_w <= 0 or source_h <= 0:
            return None
        scale = min(self.width() / source_w, self.height() / source_h)
        width = max(1, int(source_w * scale))
        height = max(1, int(source_h * scale))
        return QRect((self.width() - width) // 2, (self.height() - height) // 2, width, height)

    def _image_rect(self) -> QRect:
        fitted = self._fitted_pixmap_rect()
        if fitted is not None:
            return fitted
        return self.rect().adjusted(24, 56, -24, -24)

    def _position_controls(self) -> None:
        self._nav.adjustSize()
        self._nav.move(max(0, (self.width() - self._nav.width()) // 2), 12)
        for box in self._boxes:
            if box.isVisible():
                box.relayout(self._image_rect())
        if self._panel.isVisible():
            self._place_panel()
        self._position_picker()
        self._raise_chrome()

    def _position_picker(self) -> None:
        if not self._picker.isVisible():
            return
        self._picker.adjustSize()
        origin = self._notes_button.mapTo(self, QPoint(0, self._notes_button.height() + 6))
        x = origin.x()
        y = origin.y()
        if x + self._picker.width() > self.width() - 12:
            x = max(12, self.width() - 12 - self._picker.width())
        if y + self._picker.height() > self.height() - 12:
            above = self._notes_button.mapTo(self, QPoint(0, 0)).y() - 6 - self._picker.height()
            y = max(12, above)
        self._picker.move(x, y)

    def _raise_chrome(self) -> None:
        for box in self._boxes:
            if box.isVisible() and box.note_id != self._active_id:
                box.raise_()
        active = self._box_for(self._active_id)
        if active is not None:
            active.raise_()
        if self._panel.isVisible():
            self._panel.raise_()
        if self._picker.isVisible():
            self._picker.raise_()
        self._nav.raise_()
        self._close_button.move(max(0, self.width() - self._close_button.width() - 12), 12)
        self._close_button.raise_()
