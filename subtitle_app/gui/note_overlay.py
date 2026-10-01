"""叠在底图上的笔记框与样式面板宿主。"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QObject, QPoint, QRect, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap, QTextCursor
from PyQt6.QtWidgets import QMenu, QPlainTextEdit, QWidget

from core.screenshots import (
    SCREENSHOT_NOTE_BACKGROUND_DEFAULT,
    NoteFrame,
    ScreenshotNote,
    copy_note_frame,
    default_note_slots,
    normalize_font_ratio,
    normalize_note_align,
    normalize_note_color,
    note_frames_match,
    note_ordinal_labels,
    now_ms,
)
from gui.note_style_panel import NoteDisplayStyle, NoteStylePanel
from gui.screenshot_dialog import _confirm_delete_note_dialog

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


def paint_note_contents(
    painter: QPainter,
    rect: QRect,
    frame: NoteFrame,
    text: str,
    *,
    image_span: int,
    scale: float = 1.0,
    active: bool = False,
    grip: bool = False,
    caption: str = "",
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
    body_size = max(1, round(normalize_font_ratio(frame.font) * max(1, image_span)))
    text_rect = rect.adjusted(_px(12, scale), _px(10, scale), -_px(18, scale), -_px(18, scale))
    if caption.strip():
        caption_font = QFont(painter.font())
        caption_font.setBold(True)
        caption_font.setPixelSize(max(1, round(body_size * 0.9)))
        painter.setFont(caption_font)
        caption_h = painter.fontMetrics().height() + _px(4, scale)
        painter.drawText(
            QRect(text_rect.x(), text_rect.y(), text_rect.width(), caption_h),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            caption.strip(),
        )
        text_rect = text_rect.adjusted(0, caption_h, 0, 0)
    body_font = QFont(painter.font())
    body_font.setBold(False)
    body_font.setPixelSize(body_size)
    painter.setFont(body_font)
    painter.drawText(
        text_rect,
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


def style_of(frame: NoteFrame) -> NoteDisplayStyle:
    return NoteDisplayStyle(
        background=frame.background,
        opacity=frame.opacity,
        font=frame.font,
        color=frame.color,
        align=frame.align,
    )


def apply_style(frame: NoteFrame, style: NoteDisplayStyle) -> None:
    frame.background = style.background
    frame.opacity = style.opacity
    frame.font = style.font
    frame.color = style.color
    frame.align = style.align


def apply_reused_frame_style(target: NoteFrame, source: NoteFrame) -> None:
    """复用另一条笔记的样式与尺寸，保留当前这条的位置。"""
    target.width = source.width
    target.height = source.height
    target.background = source.background
    target.opacity = source.opacity
    target.font = source.font
    target.color = source.color
    target.align = source.align


class NoteBox(QWidget):
    """一条笔记的文本框。拖动空白处移动，拖边缘改变宽高。"""

    edited = pyqtSignal()
    committed = pyqtSignal()
    activated = pyqtSignal()
    context_menu_requested = pyqtSignal(object)  # QPoint global
    text_committed = pyqtSignal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.note_id = ""
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet("background: transparent;")
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setToolTip("拖动移动，拖边缘或右下角调整宽高；右键更多操作")
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.DefaultContextMenu)
        self._frame = NoteFrame()
        self._text = ""
        self._caption = ""
        self._bounds = QRect()
        self._action = ""
        self._press_global = QPoint()
        self._press_rect = QRect()
        self._active = False
        self._max_width_ratio = 1.0
        self._editing = False
        self._editor = QPlainTextEdit(self)
        self._editor.setFrameShape(QPlainTextEdit.Shape.NoFrame)
        self._editor.setTabChangesFocus(True)
        self._editor.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._editor.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._editor.hide()
        self._editor.installEventFilter(self)
        self.hide()

    def frame(self) -> NoteFrame:
        return self._frame

    def note_text(self) -> str:
        if self._editing:
            return self._editor.toPlainText()
        return self._text

    def caption(self) -> str:
        return self._caption

    def is_editing(self) -> bool:
        return self._editing

    def max_width_ratio(self) -> float:
        return self._max_width_ratio

    def set_width_limit(self, ratio: float) -> None:
        self._max_width_ratio = max(0.05, min(1.0, float(ratio)))

    def set_active(self, active: bool) -> None:
        self._active = active
        self.update()

    def place(self, bounds: QRect, frame: NoteFrame, text: str, *, caption: str = "") -> None:
        if self._editing:
            self.finish_edit(commit=True)
        self._frame = frame
        self._text = text
        self._caption = caption
        self.relayout(bounds)

    def begin_edit(self) -> None:
        if self._editing:
            self._editor.setFocus(Qt.FocusReason.OtherFocusReason)
            return
        self._editing = True
        self._editor.setPlainText(self._text)
        self._style_editor()
        self._layout_editor()
        self._editor.show()
        self._editor.raise_()
        self._editor.setFocus(Qt.FocusReason.OtherFocusReason)
        cursor = self._editor.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self._editor.setTextCursor(cursor)
        self.update()

    def finish_edit(self, *, commit: bool) -> None:
        if not self._editing:
            return
        text = self._editor.toPlainText()
        self._editor.hide()
        self._editing = False
        if commit and text != self._text:
            self._text = text
            self.text_committed.emit()
        elif commit:
            self._text = text
        self.update()

    def refresh_editor(self) -> None:
        if not self._editing:
            return
        self._style_editor()
        self._layout_editor()

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
        if self._editing:
            self._layout_editor()
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setFont(self.font())
        paint_note_contents(
            painter,
            self.rect(),
            self._frame,
            "" if self._editing else self._text,
            image_span=self._bounds.height() if self._bounds.height() > 0 else self.height(),
            active=self._active or self._editing,
            grip=not self._editing,
            caption=self._caption,
        )
        painter.end()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._editing:
            self._layout_editor()

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        # 右键只出菜单，不通过 activated 打开样式面板
        self.context_menu_requested.emit(event.globalPos())
        event.accept()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.RightButton:
            event.accept()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._editing:
            event.ignore()
            return
        self.activated.emit()
        self._action = _hit_edge(event.position().toPoint(), self.width(), self.height())
        self._press_global = event.globalPosition().toPoint()
        self._press_rect = QRect(self.geometry())
        self.setCursor(_cursor_for(self._action))
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._editing:
            return
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
        if self._editing:
            return
        moved = bool(self._action) and self.geometry() != self._press_rect
        self._action = ""
        if moved:
            self.committed.emit()
        event.accept()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self._editor and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            mods = event.modifiers()
            if key == Qt.Key.Key_Escape:
                self.finish_edit(commit=False)
                return True
            if key in {Qt.Key.Key_Return, Qt.Key.Key_Enter} and mods & Qt.KeyboardModifier.ControlModifier:
                self.finish_edit(commit=True)
                return True
        if watched is self._editor and event.type() == QEvent.Type.FocusOut:
            # 延迟提交，避免点菜单时立刻丢掉编辑态
            from PyQt6.QtCore import QTimer

            QTimer.singleShot(0, self._commit_if_still_unfocused)
        return super().eventFilter(watched, event)

    def _commit_if_still_unfocused(self) -> None:
        if not self._editing:
            return
        focus = self.window().focusWidget() if self.window() is not None else None
        if focus is self._editor or (focus is not None and self.isAncestorOf(focus)):
            return
        current = focus
        while current is not None:
            if current.objectName() == "noteStylePanel":
                return
            current = current.parentWidget()
        self.finish_edit(commit=True)

    def _caption_band(self) -> int:
        if not self._caption.strip():
            return _px(10, 1.0)
        span = self._bounds.height() if self._bounds.height() > 0 else self.height()
        body_size = max(1, round(normalize_font_ratio(self._frame.font) * max(1, span)))
        return _px(10, 1.0) + max(1, round(body_size * 0.9)) + _px(4, 1.0)

    def _layout_editor(self) -> None:
        top = self._caption_band()
        self._editor.setGeometry(
            _px(12, 1.0),
            top,
            max(1, self.width() - _px(30, 1.0)),
            max(1, self.height() - top - _px(18, 1.0)),
        )

    def _style_editor(self) -> None:
        span = self._bounds.height() if self._bounds.height() > 0 else max(1, self.height())
        size = max(1, round(normalize_font_ratio(self._frame.font) * span))
        color = self._frame.color
        self._editor.setStyleSheet(
            "QPlainTextEdit {"
            "background: transparent;"
            f"color: {color};"
            "border: none;"
            f"font-size: {size}px;"
            "padding: 0;"
            "}"
        )

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


class NoteOverlay(QObject):
    """在宿主上管理笔记框和样式面板。宿主负责提供底图矩形。"""

    box_changed = pyqtSignal()
    notes_changed = pyqtSignal()
    style_changed = pyqtSignal(float, float, str, str, str)
    chrome_raised = pyqtSignal()

    def __init__(self, host: QWidget) -> None:
        super().__init__(host)
        self._host = host
        self._defaults = NoteFrame()
        self._notes: list[ScreenshotNote] = []
        self._shot_touch = None
        self._boxes: list[NoteBox] = []
        self._active_id = ""
        self._dirty_ids: set[str] = set()
        self._bounds = QRect()
        self._panel_pos: QPoint | None = None
        self._panel = NoteStylePanel(host)
        self._panel.edited.connect(self._on_panel_edited)
        self._panel.committed.connect(self._on_panel_committed)
        self._panel.dismissed.connect(self._dismiss_style_panel)
        self._panel.moved.connect(self._remember_panel_pos)
        self._panel.hide()
        host.installEventFilter(self)

    def watch_background(self, widget: QWidget) -> None:
        """在底图等空白区域点击时取消激活并关闭样式面板。"""
        widget.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() != QEvent.Type.MouseButtonPress:
            return False
        if not hasattr(event, "button") or event.button() != Qt.MouseButton.LeftButton:
            return False
        editing = any(box.is_editing() for box in self._boxes)
        if not self._active_id and not self._panel.isVisible() and not editing:
            return False
        target = watched if isinstance(watched, QWidget) else None
        if target is None:
            return False
        if self._is_style_chrome(target) or self._is_note_box(target):
            return False
        self._finish_all_edits(commit=True)
        self._dismiss_style_panel()
        return False

    def set_defaults(
        self,
        *,
        opacity: float,
        font_size: float,
        color: str,
        align: str,
        background: str,
    ) -> None:
        self._defaults = NoteFrame(
            opacity=max(0.15, min(1.0, float(opacity))),
            font=normalize_font_ratio(font_size),
            color=normalize_note_color(color),
            align=normalize_note_align(align),
            background=normalize_note_color(background, SCREENSHOT_NOTE_BACKGROUND_DEFAULT),
        )

    def set_notes(self, notes: list[ScreenshotNote], *, touch_shot) -> None:
        """touch_shot 是当前 Screenshot，写入 box 时会更新它的 updated_at。"""
        self._finish_all_edits(commit=True)
        self._commit_dirty(save_style=False)
        self._notes = list(notes)
        self._shot_touch = touch_shot
        self._active_id = ""
        self._dirty_ids.clear()
        self._panel.hide()
        self._layout_boxes()

    def clear(self) -> None:
        self._finish_all_edits(commit=False)
        self._commit_dirty(save_style=False)
        self._notes = []
        self._shot_touch = None
        self._active_id = ""
        self._dirty_ids.clear()
        for box in self._boxes:
            box.note_id = ""
            box.hide()
        self._panel.hide()

    def commit_pending(self) -> None:
        self._finish_all_edits(commit=True)
        self._commit_dirty(save_style=False)

    def set_image_bounds(self, bounds: QRect) -> None:
        self._bounds = QRect(bounds)
        for box in self._boxes:
            if box.isVisible():
                box.relayout(self._bounds)
        if self._panel.isVisible():
            self._panel.clamp_inside(self._placement_bounds())

    def activate_note(self, note_id: str) -> None:
        if not note_id:
            return
        if note_id not in {note.id for note in self._notes}:
            return
        self._on_box_activated(note_id, show_panel=True)

    def render_composite(self, pixmap: QPixmap) -> QImage | None:
        if pixmap.isNull():
            return None
        source = pixmap.toImage()
        if source.isNull() or source.width() <= 0 or source.height() <= 0:
            return None
        image = QImage(source.size(), QImage.Format.Format_RGB32)
        image.fill(QColor("#000000"))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.drawImage(0, 0, source)
        painter.setFont(self._host.font())
        width = image.width()
        height = image.height()
        display_h = self._bounds.height() if self._bounds.height() > 0 else height
        scale = height / display_h if display_h > 0 else 1.0
        boxes = [box for box in self._boxes if box.isVisible() and box.note_id]
        boxes.sort(key=lambda box: box.note_id == self._active_id)
        limit = QRect(0, 0, width, height)
        for box in boxes:
            frame = box.frame()
            ratio = min(frame.width, box.max_width_ratio())
            rect = QRect(
                round(frame.x * width),
                round(frame.y * height),
                max(1, round(ratio * width)),
                max(1, round(frame.height * height)),
            )
            paint_note_contents(
                painter,
                _clamp_rect(rect, limit, max(1, round(width * box.max_width_ratio()))),
                frame,
                box.note_text(),
                image_span=height,
                scale=scale,
                caption=box.caption(),
            )
        painter.end()
        return image

    def _layout_boxes(self) -> None:
        count = len(self._notes)
        slots = default_note_slots(count) if count else []
        labels = note_ordinal_labels(self._notes)
        bounds = self._bounds if self._bounds.width() > 0 else self._host.rect().adjusted(24, 24, -24, -24)
        fresh: list[str] = []
        for index, note in enumerate(self._notes):
            box = self._ensure_box(index)
            box.note_id = note.id
            box.set_width_limit(1.0)
            box.set_active(note.id == self._active_id)
            if note.frame is None:
                fresh.append(note.id)
            box.place(
                bounds,
                self._frame_for(note, slots[index]),
                note.text,
                caption=labels.get(note.id, ""),
            )
            box.show()
            box.raise_()
        for box in self._boxes[count:]:
            box.note_id = ""
            box.hide()
        for note_id in fresh:
            self._dirty_ids.add(note_id)
        if fresh:
            self._commit_dirty(save_style=False)
        if self._active_id:
            self._sync_panel()

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

    def _ensure_box(self, index: int) -> NoteBox:
        while len(self._boxes) <= index:
            box = NoteBox(self._host)
            box.activated.connect(lambda b=box: self._on_box_activated(b.note_id))
            box.edited.connect(lambda b=box: self._on_box_edited(b.note_id))
            box.committed.connect(lambda b=box: self._on_box_committed(b.note_id))
            box.context_menu_requested.connect(
                lambda pos, b=box: self._show_note_menu(b.note_id, pos)
            )
            box.text_committed.connect(lambda b=box: self._on_text_committed(b.note_id))
            self._boxes.append(box)
        return self._boxes[index]

    def _on_box_activated(self, note_id: str, *, show_panel: bool = True) -> None:
        if not note_id:
            return
        for box in self._boxes:
            if box.note_id != note_id and box.is_editing():
                box.finish_edit(commit=True)
        self._active_id = note_id
        for box in self._boxes:
            box.set_active(box.isVisible() and box.note_id == note_id)
        if show_panel:
            self._sync_panel()
        else:
            if self._panel.isVisible():
                self._panel_pos = self._panel.pos()
            self._panel.hide()
        self._raise_chrome()

    def _show_note_menu(self, note_id: str, global_pos) -> None:
        if not note_id:
            return
        self._on_box_activated(note_id, show_panel=False)
        labels = note_ordinal_labels(self._notes)
        own_label = labels.get(note_id, "")
        menu = QMenu(self._host)
        edit_action = menu.addAction("编辑笔记")
        reuse_actions: list[tuple[object, str]] = []
        ordered = sorted(self._notes, key=lambda note: (int(note.created_at), note.id))
        for note in ordered:
            if note.id == note_id:
                continue
            label = labels.get(note.id)
            if not label:
                continue
            action = menu.addAction(f"复用{label}样式")
            reuse_actions.append((action, note.id))
        menu.addSeparator()
        delete_label = f"删除{own_label}笔记" if own_label else "删除笔记"
        delete_action = menu.addAction(delete_label)
        chosen = menu.exec(global_pos)
        if chosen is None:
            return
        if chosen == edit_action:
            box = self._box_for(note_id)
            if box is not None:
                box.begin_edit()
            return
        for action, source_id in reuse_actions:
            if chosen == action:
                self._reuse_style(note_id, source_id)
                return
        if chosen == delete_action:
            self._delete_note(note_id)

    def _reuse_style(self, target_id: str, source_id: str) -> None:
        target_box = self._box_for(target_id)
        source_box = self._box_for(source_id)
        target_note = self._note_by_id(target_id)
        if target_box is None or source_box is None or target_note is None:
            return
        apply_reused_frame_style(target_box.frame(), source_box.frame())
        # 尺寸变大时避免超出底图
        if target_box.frame().x + target_box.frame().width > 1:
            target_box.frame().x = max(0.0, 1 - target_box.frame().width)
        if target_box.frame().y + target_box.frame().height > 1:
            target_box.frame().y = max(0.0, 1 - target_box.frame().height)
        target_box.relayout(self._bounds if self._bounds.isValid() else self._host.rect())
        self._dirty_ids.add(target_id)
        self._commit_note(target_id, save_style=False)
        if target_id == self._active_id:
            self._panel.set_style(style_of(target_box.frame()))
            self._panel.update()
        self._raise_chrome()

    def _delete_note(self, note_id: str) -> None:
        if not _confirm_delete_note_dialog(self._host):
            return
        self._finish_all_edits(commit=True)
        self._commit_dirty(save_style=False)
        self._notes = [note for note in self._notes if note.id != note_id]
        if self._shot_touch is not None:
            self._shot_touch.notes = list(self._notes)
            self._shot_touch.updated_at = now_ms()
        self._active_id = ""
        self._panel.hide()
        self._layout_boxes()
        self.notes_changed.emit()

    def _on_text_committed(self, note_id: str) -> None:
        note = self._note_by_id(note_id)
        box = self._box_for(note_id)
        if note is None or box is None:
            return
        text = box.note_text()
        if text == note.text:
            return
        note.text = text
        moment = now_ms()
        note.updated_at = moment
        if self._shot_touch is not None:
            self._shot_touch.updated_at = moment
        self.notes_changed.emit()

    def _finish_all_edits(self, *, commit: bool) -> None:
        for box in self._boxes:
            if box.is_editing():
                box.finish_edit(commit=commit)

    def _on_box_edited(self, note_id: str) -> None:
        if note_id:
            self._dirty_ids.add(note_id)

    def _on_box_committed(self, note_id: str) -> None:
        if note_id:
            self._dirty_ids.add(note_id)
        self._commit_note(note_id, save_style=False)

    def _on_panel_edited(self) -> None:
        box = self._box_for(self._active_id)
        if box is None:
            return
        apply_style(box.frame(), self._panel.style())
        box.update()
        box.refresh_editor()
        self._dirty_ids.add(self._active_id)

    def _on_panel_committed(self) -> None:
        self._on_panel_edited()
        self._commit_note(self._active_id, save_style=True)

    def _remember_panel_pos(self) -> None:
        if not self._panel.isVisible():
            return
        self._panel.clamp_inside(self._placement_bounds())
        self._panel_pos = self._panel.pos()

    def _dismiss_style_panel(self) -> None:
        self._finish_all_edits(commit=True)
        if self._panel.isVisible():
            self._panel_pos = self._panel.pos()
        self._active_id = ""
        for box in self._boxes:
            box.set_active(False)
        self._panel.hide()

    def _sync_panel(self) -> None:
        box = self._box_for(self._active_id)
        if box is None:
            self._panel.hide()
            return
        was_visible = self._panel.isVisible()
        self._panel.set_style(style_of(box.frame()))
        self._panel.set_title(box.caption() or "这条笔记")
        self._panel.show()
        if was_visible:
            self._panel.clamp_inside(self._placement_bounds())
        else:
            self._place_panel()
        self._raise_chrome()

    def _place_panel(self) -> None:
        if not self._panel.isVisible():
            return
        self._panel.place_at(self._placement_bounds(), self._panel_pos)

    def _placement_bounds(self) -> QRect:
        if self._bounds.isValid() and self._bounds.width() > 0 and self._bounds.height() > 0:
            return self._bounds
        return self._host.rect()

    def _is_note_box(self, widget: QWidget) -> bool:
        current: QWidget | None = widget
        while current is not None:
            if isinstance(current, NoteBox):
                return True
            current = current.parentWidget()
        return False

    def _is_style_chrome(self, widget: QWidget) -> bool:
        current: QWidget | None = widget
        while current is not None:
            if current is self._panel:
                return True
            current = current.parentWidget()
        return False

    def _raise_chrome(self) -> None:
        for box in self._boxes:
            if box.isVisible() and box.note_id != self._active_id:
                box.raise_()
        active = self._box_for(self._active_id)
        if active is not None:
            active.raise_()
        if self._panel.isVisible():
            self._panel.raise_()
        self.chrome_raised.emit()

    def _commit_dirty(self, *, save_style: bool) -> None:
        for note_id in list(self._dirty_ids):
            self._commit_note(note_id, save_style=save_style)

    def _commit_note(self, note_id: str, *, save_style: bool) -> None:
        if note_id not in self._dirty_ids:
            return
        note = self._note_by_id(note_id)
        box = self._box_for(note_id)
        self._dirty_ids.discard(note_id)
        if note is None or box is None:
            return
        frame = copy_note_frame(box.frame())
        if note_frames_match(note.frame, frame):
            if save_style:
                self._publish_style(frame)
            return
        note.frame = frame
        moment = now_ms()
        note.updated_at = moment
        if self._shot_touch is not None:
            self._shot_touch.updated_at = moment
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

    def _box_for(self, note_id: str) -> NoteBox | None:
        if not note_id:
            return None
        for box in self._boxes:
            if box.isVisible() and box.note_id == note_id:
                return box
        return None

    def _note_by_id(self, note_id: str) -> ScreenshotNote | None:
        for note in self._notes:
            if note.id == note_id:
                return note
        return None
