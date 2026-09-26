from __future__ import annotations

from PyQt6.QtCore import QEvent, QPointF, QRect, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QTextLayout
from PyQt6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from core.subtitle_tags import primary_tag

PAYLOAD_ROLE = int(Qt.ItemDataRole.UserRole) + 8

TAG_COLORS: dict[str, tuple[str, str]] = {
    "重点": ("#6b5420", "#ffd78a"),
    "难点": ("#6b3030", "#ffb0a8"),
    "易错": ("#6b4520", "#ffc48a"),
    "跟读": ("#1e3d55", "#b9e0ff"),
    "已掌握": ("#1e3d32", "#9ddeb8"),
}
CUSTOM_TAG_COLOR = ("#3a3a3a", "#dddddd")
NOTE_COLOR = QColor("#ffd56a")
MASTERED_TEXT = QColor("#9a9a9a")
BODY_COLOR = QColor("#eeeeee")

_CHIP_HEIGHT = 18
_CHIP_GAP = 4
_CHIP_HPAD = 6
_BAR_WIDTH = 3
_BAR_GAP = 6


def tag_colors(name: str) -> tuple[QColor, QColor]:
    bg, fg = TAG_COLORS.get(name, CUSTOM_TAG_COLOR)
    return QColor(bg), QColor(fg)


def _chip_font(base: QFont) -> QFont:
    font = QFont(base)
    font.setPixelSize(11)
    return font


def _note_font(base: QFont) -> QFont:
    font = QFont(base)
    font.setPixelSize(max(11, base.pixelSize() - 1 if base.pixelSize() > 0 else 12))
    return font


def _content_margins(compact: bool) -> tuple[int, int, int, int]:
    if compact:
        return 6, 2, 6, 2
    return 6, 8, 6, 8


class SubtitleListDelegate(QStyledItemDelegate):
    """绘制序号/时间、彩色标签、正文和备注。正文不含标签文字。"""

    noteActivated = pyqtSignal(object)

    def paint(self, painter: QPainter, option, index) -> None:
        payload = index.data(PAYLOAD_ROLE)
        if not isinstance(payload, dict):
            super().paint(painter, option, index)
            return

        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        widget = opt.widget
        style = widget.style() if widget is not None else None
        painter.save()
        if style is not None:
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)
        self._paint_payload(painter, option.rect, opt.font, payload)
        painter.restore()

    def editorEvent(self, event, model, option, index) -> bool:
        if (
            event.type() == QEvent.Type.MouseButtonRelease
            and event.button() == Qt.MouseButton.LeftButton
        ):
            payload = index.data(PAYLOAD_ROLE)
            rect = self.note_rect(option.rect, option.font, payload)
            if rect is not None and rect.contains(event.position().toPoint()):
                self.noteActivated.emit(index)
                return True
        return super().editorEvent(event, model, option, index)

    def note_rect(self, item_rect: QRect, font: QFont, payload: object) -> QRect | None:
        if not isinstance(payload, dict):
            return None
        note = str(payload.get("note") or "")
        if not note:
            return None
        compact = bool(payload.get("compact"))
        tags = list(payload.get("tags") or [])
        body = str(payload.get("body") or "")
        left, top, right, _bottom = _content_margins(compact)
        content = item_rect.adjusted(left, top, -right, 0)
        text_left = content.left()
        if tags:
            text_left += _BAR_WIDTH + _BAR_GAP
        text_width = max(20, content.right() - text_left + 1)
        y = content.top()
        if compact:
            if tags:
                y += _CHIP_HEIGHT + 2
            y += self._wrap_height(font, body, text_width)
        else:
            y += max(QFontMetrics(font).height(), _CHIP_HEIGHT if tags else 0)
            y += 2
            y += self._wrap_height(font, body, text_width)
        y += 2
        height = QFontMetrics(_note_font(font)).height()
        return QRect(text_left, y, text_width, height)

    def sizeHint(self, option, index) -> QSize:
        payload = index.data(PAYLOAD_ROLE)
        if not isinstance(payload, dict):
            return super().sizeHint(option, index)
        width = option.rect.width()
        widget = option.widget
        if widget is not None and widget.viewport() is not None:
            width = widget.viewport().width()
        width = max(width, 120)
        height = self._content_height(width, option.font, payload)
        return QSize(width, height)

    def _paint_payload(self, painter: QPainter, rect: QRect, font: QFont, payload: dict) -> None:
        painter.setFont(font)
        compact = bool(payload.get("compact"))
        tags = list(payload.get("tags") or [])
        note = str(payload.get("note") or "")
        body = str(payload.get("body") or "")
        left, top, right, bottom = _content_margins(compact)
        content = rect.adjusted(left, top, -right, -bottom)
        bar = primary_tag(tags)
        text_left = content.left()
        if bar:
            _bg, fg = tag_colors(bar)
            painter.fillRect(
                content.left(),
                content.top(),
                _BAR_WIDTH,
                max(0, content.height()),
                fg,
            )
            text_left = content.left() + _BAR_WIDTH + _BAR_GAP

        y = content.top()
        text_width = max(20, content.right() - text_left + 1)
        text_color = MASTERED_TEXT if tags and all(tag == "已掌握" for tag in tags) else BODY_COLOR

        if compact:
            if tags:
                y = self._draw_chips(painter, text_left, y, text_width, font, tags) + 2
            y = self._draw_wrapped(
                painter, text_left, y, text_width, font, body, text_color
            )
        else:
            meta = str(payload.get("meta") or "")
            y = self._draw_meta_line(painter, text_left, y, text_width, font, meta, tags, text_color)
            y += 2
            y = self._draw_wrapped(
                painter, text_left, y, text_width, font, body, text_color
            )
        if note:
            y += 2
            self._draw_note(painter, text_left, y, text_width, font, note)

    def _content_height(self, width: int, font: QFont, payload: dict) -> int:
        compact = bool(payload.get("compact"))
        tags = list(payload.get("tags") or [])
        note = str(payload.get("note") or "")
        body = str(payload.get("body") or "")
        left, top, right, bottom = _content_margins(compact)
        text_width = max(20, width - left - right)
        if tags:
            text_width = max(20, text_width - _BAR_WIDTH - _BAR_GAP)
        height = top
        if compact:
            if tags:
                height += _CHIP_HEIGHT + 2
            height += self._wrap_height(font, body, text_width)
        else:
            height += max(QFontMetrics(font).height(), _CHIP_HEIGHT if tags else 0)
            height += 2
            height += self._wrap_height(font, body, text_width)
        if note:
            height += 2 + QFontMetrics(_note_font(font)).height()
        height += bottom
        return max(height, 24)

    def _draw_meta_line(
        self,
        painter: QPainter,
        x: int,
        y: int,
        width: int,
        font: QFont,
        meta: str,
        tags: list[str],
        color: QColor,
    ) -> int:
        metrics = QFontMetrics(font)
        line_height = max(metrics.height(), _CHIP_HEIGHT if tags else metrics.height())
        painter.setPen(color)
        painter.setFont(font)
        meta_width = metrics.horizontalAdvance(meta) if meta else 0
        painter.drawText(QRect(x, y, min(meta_width, width), line_height), Qt.AlignmentFlag.AlignVCenter, meta)
        chip_x = x + meta_width + (8 if meta and tags else 0)
        if tags and chip_x < x + width:
            self._draw_chips(painter, chip_x, y + max(0, (line_height - _CHIP_HEIGHT) // 2), x + width - chip_x, font, tags)
        return y + line_height

    def _draw_chips(
        self,
        painter: QPainter,
        x: int,
        y: int,
        width: int,
        font: QFont,
        tags: list[str],
    ) -> int:
        chip_font = _chip_font(font)
        metrics = QFontMetrics(chip_font)
        cursor = x
        bottom = y + _CHIP_HEIGHT
        painter.setFont(chip_font)
        for tag in tags:
            chip_width = metrics.horizontalAdvance(tag) + _CHIP_HPAD * 2
            if cursor > x and cursor + chip_width > x + width:
                break
            bg, fg = tag_colors(tag)
            rect = QRect(cursor, y, chip_width, _CHIP_HEIGHT)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(bg)
            painter.drawRoundedRect(rect, 3, 3)
            painter.setPen(fg)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, tag)
            cursor += chip_width + _CHIP_GAP
            bottom = rect.bottom()
        return bottom

    def _draw_wrapped(
        self,
        painter: QPainter,
        x: int,
        y: int,
        width: int,
        font: QFont,
        text: str,
        color: QColor,
    ) -> int:
        if not text:
            return y
        painter.setFont(font)
        painter.setPen(color)
        layout = QTextLayout(text, font)
        layout.beginLayout()
        height = 0
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(width)
            line.setPosition(QPointF(0, height))
            height += line.height()
        layout.endLayout()
        layout.draw(painter, QPointF(x, y))
        return y + int(height)

    def _draw_note(self, painter: QPainter, x: int, y: int, width: int, font: QFont, note: str) -> None:
        note_font = _note_font(font)
        painter.setFont(note_font)
        painter.setPen(NOTE_COLOR)
        elided = QFontMetrics(note_font).elidedText(note.replace("\n", " "), Qt.TextElideMode.ElideRight, width)
        painter.drawText(QRect(x, y, width, QFontMetrics(note_font).height()), Qt.AlignmentFlag.AlignVCenter, elided)

    def _wrap_height(self, font: QFont, text: str, width: int) -> int:
        if not text:
            return 0
        layout = QTextLayout(text, font)
        layout.beginLayout()
        height = 0.0
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(max(20, width))
            height += line.height()
        layout.endLayout()
        return int(height) + 1
