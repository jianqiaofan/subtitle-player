"""可复用的看图控件：适配窗口、滚轮缩放、拖移、缺图提示。"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QPoint, QRect, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPixmap, QWheelEvent
from PyQt6.QtWidgets import QWidget


class ImageViewer(QWidget):
    """只负责显示一张图，不知道截图、标题或笔记。"""

    image_rect_changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("imageViewer")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("#imageViewer { background-color: #000000; }")
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._pixmap = QPixmap()
        self._missing = False
        self._zoom = 1.0
        self._offset = QPoint(0, 0)
        self._panning = False
        self._pan_origin = QPoint()
        self._offset_origin = QPoint()
        self._last_rect = QRect()

    def set_path(self, path: Path | None) -> None:
        self._pixmap = QPixmap()
        self._missing = False
        self.reset_view()
        if path is None:
            self.update()
            self._emit_rect_if_changed()
            return
        if not path.is_file():
            self._missing = True
            self.update()
            self._emit_rect_if_changed()
            return
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            self._missing = True
        else:
            self._pixmap = pixmap
        self.update()
        self._emit_rect_if_changed()

    def set_pixmap(self, pixmap: QPixmap) -> None:
        self._pixmap = QPixmap(pixmap)
        self._missing = pixmap.isNull()
        self.reset_view()
        self.update()
        self._emit_rect_if_changed()

    def clear(self) -> None:
        self._pixmap = QPixmap()
        self._missing = False
        self.reset_view()
        self.update()
        self._emit_rect_if_changed()

    def has_image(self) -> bool:
        return not self._pixmap.isNull()

    def source_pixmap(self) -> QPixmap:
        return self._pixmap

    def reset_view(self) -> None:
        self._zoom = 1.0
        self._offset = QPoint(0, 0)

    def image_rect(self) -> QRect:
        """当前底图在本控件里的矩形，没有图时返回可用内框。"""
        fitted = self._fitted_base_rect()
        if fitted is None:
            return self.rect().adjusted(24, 24, -24, -24)
        if abs(self._zoom - 1.0) < 1e-6 and self._offset == QPoint(0, 0):
            return fitted
        width = max(1, int(round(fitted.width() * self._zoom)))
        height = max(1, int(round(fitted.height() * self._zoom)))
        x = fitted.x() + (fitted.width() - width) // 2 + self._offset.x()
        y = fitted.y() + (fitted.height() - height) // 2 + self._offset.y()
        return QRect(x, y, width, height)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#000000"))
        rect = self.image_rect()
        if not self._pixmap.isNull() and rect.width() > 0 and rect.height() > 0:
            painter.drawPixmap(rect, self._pixmap)
        elif self._missing:
            painter.setPen(QColor(255, 255, 255, 180))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "图片文件不在")
        painter.end()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._emit_rect_if_changed()

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        if self._pixmap.isNull():
            return
        delta = event.angleDelta().y()
        if delta == 0:
            return
        factor = 1.12 if delta > 0 else 1 / 1.12
        old_zoom = self._zoom
        self._zoom = max(1.0, min(8.0, self._zoom * factor))
        if abs(self._zoom - old_zoom) < 1e-6:
            return
        if self._zoom <= 1.0:
            self._offset = QPoint(0, 0)
        self.update()
        self._emit_rect_if_changed()
        event.accept()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._zoom > 1.0 and not self._pixmap.isNull():
            self._panning = True
            self._pan_origin = event.globalPosition().toPoint()
            self._offset_origin = QPoint(self._offset)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._panning and event.buttons() & Qt.MouseButton.LeftButton:
            delta = event.globalPosition().toPoint() - self._pan_origin
            self._offset = self._offset_origin + delta
            self._clamp_offset()
            self.update()
            self._emit_rect_if_changed()
            event.accept()
            return
        if self._zoom > 1.0 and not self._pixmap.isNull():
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.unsetCursor()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._panning and event.button() == Qt.MouseButton.LeftButton:
            self._panning = False
            if self._zoom > 1.0:
                self.setCursor(Qt.CursorShape.OpenHandCursor)
            else:
                self.unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.reset_view()
            self.update()
            self._emit_rect_if_changed()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def _fitted_base_rect(self) -> QRect | None:
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

    def _clamp_offset(self) -> None:
        base = self._fitted_base_rect()
        if base is None or self._zoom <= 1.0:
            self._offset = QPoint(0, 0)
            return
        width = max(1, int(round(base.width() * self._zoom)))
        height = max(1, int(round(base.height() * self._zoom)))
        max_x = max(0, (width - base.width()) // 2 + 24)
        max_y = max(0, (height - base.height()) // 2 + 24)
        self._offset.setX(max(-max_x, min(max_x, self._offset.x())))
        self._offset.setY(max(-max_y, min(max_y, self._offset.y())))

    def _emit_rect_if_changed(self) -> None:
        rect = QRect(self.image_rect())
        if rect == self._last_rect:
            return
        self._last_rect = rect
        self.image_rect_changed.emit()
