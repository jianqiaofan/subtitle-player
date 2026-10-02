"""嵌入播放画面的截图查看层：浮层按钮与笔记排版。"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import (
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QRect,
    QSequentialAnimationGroup,
    Qt,
    pyqtSignal,
)
from PyQt6.QtGui import QCloseEvent, QColor, QImage, QKeySequence, QResizeEvent, QShortcut, QShowEvent
from PyQt6.QtWidgets import (
    QGraphicsOpacityEffect,
    QLabel,
    QMenu,
    QPushButton,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.screenshots import Screenshot
from gui.image_viewer import ImageViewer
from gui.note_overlay import NoteOverlay

_WINDOW_STYLE = """
QWidget#screenshotViewerWindow {
    background-color: #000000;
    color: #f2f2f5;
}
"""

# 亮色底（白板）：深底 + 亮黄字/描边，避免和白板融在一起
_NAV_ON_LIGHT = """
QPushButton#viewerNavButton {
    background-color: rgba(18, 18, 22, 210);
    color: #FFE14A;
    border: 2px solid #FFE14A;
    border-radius: 8px;
    padding: 14px 16px;
    font-size: 15px;
    font-weight: 600;
    min-width: 72px;
}
QPushButton#viewerNavButton:hover { background-color: rgba(40, 30, 10, 230); }
QPushButton#viewerNavButton:disabled {
    color: rgba(255, 225, 74, 0.35);
    border-color: rgba(255, 225, 74, 0.35);
}
QLabel#viewerNavCounter {
    background-color: rgba(18, 18, 22, 210);
    color: #FFE14A;
    border: 2px solid #FFE14A;
    border-radius: 8px;
    padding: 8px 10px;
    font-size: 13px;
    font-weight: 600;
}
"""

# 暗色底（黑板）：浅底 + 深紫字 + 亮黄描边，避免和黑板融在一起
_NAV_ON_DARK = """
QPushButton#viewerNavButton {
    background-color: rgba(245, 245, 248, 220);
    color: #3A2458;
    border: 2px solid #FFE14A;
    border-radius: 8px;
    padding: 14px 16px;
    font-size: 15px;
    font-weight: 600;
    min-width: 72px;
}
QPushButton#viewerNavButton:hover { background-color: rgba(255, 255, 255, 240); }
QPushButton#viewerNavButton:disabled {
    color: rgba(58, 36, 88, 0.35);
    border-color: rgba(255, 225, 74, 0.45);
}
QLabel#viewerNavCounter {
    background-color: rgba(245, 245, 248, 220);
    color: #3A2458;
    border: 2px solid #FFE14A;
    border-radius: 8px;
    padding: 8px 10px;
    font-size: 13px;
    font-weight: 600;
}
"""

_CLOSE_ON_LIGHT = """
QToolButton#viewerCloseButton {
    background-color: rgba(18, 18, 22, 210);
    border: 2px solid #FFE14A;
    border-radius: 6px;
    padding: 4px;
}
QToolButton#viewerCloseButton:hover { background-color: rgba(40, 30, 10, 230); }
"""

_CLOSE_ON_DARK = """
QToolButton#viewerCloseButton {
    background-color: rgba(245, 245, 248, 220);
    border: 2px solid #FFE14A;
    border-radius: 6px;
    padding: 4px;
}
QToolButton#viewerCloseButton:hover { background-color: rgba(255, 255, 255, 240); }
"""

_MODE_TOAST_STYLE = """
QLabel#viewerModeToast {
    background-color: rgba(20, 16, 28, 220);
    color: #F5EDFF;
    border: 1px solid #E2C6FF;
    border-radius: 10px;
    padding: 14px 18px;
    font-size: 15px;
}
"""

_MODE_TOAST_TEXT = (
    "已退出播放模式，进入看图模式。\n"
    "点击右上角关闭图标可退出看图模式，返回播放模式。"
)


class ScreenshotViewerWindow(QWidget):
    """嵌入播放器画面区的截图查看层。打开/关闭不改变播放状态。"""

    closed = pyqtSignal()
    style_changed = pyqtSignal(float, float, str, str, str)
    box_changed = pyqtSignal()
    notes_changed = pyqtSignal()
    export_requested = pyqtSignal()
    edit_requested = pyqtSignal(str)
    jump_requested = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("screenshotViewerWindow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(_WINDOW_STYLE)
        self.hide()
        self._shots: list[Screenshot] = []
        self._paths: list[Path] = []
        self._index = 0
        self._extra_tags: list[str] = []
        self._chrome_on_light = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._canvas = QWidget()
        self._canvas.setStyleSheet("background-color: #000000;")
        canvas_layout = QVBoxLayout(self._canvas)
        canvas_layout.setContentsMargins(0, 0, 0, 0)
        self._viewer = ImageViewer(self._canvas)
        self._viewer.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._viewer.customContextMenuRequested.connect(self._show_canvas_menu)
        canvas_layout.addWidget(self._viewer)
        self._overlay = NoteOverlay(self._canvas)
        self._overlay.box_changed.connect(self.box_changed.emit)
        self._overlay.notes_changed.connect(self._on_notes_changed)
        self._overlay.style_changed.connect(self.style_changed.emit)
        self._overlay.chrome_raised.connect(self._raise_chrome)
        self._overlay.watch_background(self._viewer)
        self._viewer.image_rect_changed.connect(self._on_image_rect_changed)
        root.addWidget(self._canvas, stretch=1)

        self._nav = QWidget(self._canvas)
        self._nav.setStyleSheet("background: transparent;")
        nav_layout = QVBoxLayout(self._nav)
        nav_layout.setContentsMargins(0, 0, 0, 0)
        nav_layout.setSpacing(10)
        self._prev_btn = QPushButton("上一图", self._nav)
        self._next_btn = QPushButton("下一图", self._nav)
        self._counter_label = QLabel("0/0", self._nav)
        self._counter_label.setObjectName("viewerNavCounter")
        self._counter_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        for button in (self._prev_btn, self._next_btn):
            button.setObjectName("viewerNavButton")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._prev_btn.clicked.connect(lambda: self._step_image(-1))
        self._next_btn.clicked.connect(lambda: self._step_image(1))
        nav_layout.addWidget(self._prev_btn)
        nav_layout.addWidget(self._counter_label)
        nav_layout.addWidget(self._next_btn)

        self._close_btn = QToolButton(self._canvas)
        self._close_btn.setObjectName("viewerCloseButton")
        self._close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_btn.setToolTip("关闭")
        self._close_btn.setFixedSize(36, 32)
        self._close_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_TitleBarCloseButton))
        self._close_btn.clicked.connect(self.close)
        self._apply_chrome_theme(on_light=False)

        self._mode_toast = QLabel(_MODE_TOAST_TEXT, self._canvas)
        self._mode_toast.setObjectName("viewerModeToast")
        self._mode_toast.setStyleSheet(_MODE_TOAST_STYLE)
        self._mode_toast.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._mode_toast.setWordWrap(True)
        self._mode_toast.setFixedWidth(420)
        self._mode_toast.hide()
        self._toast_effect = QGraphicsOpacityEffect(self._mode_toast)
        self._mode_toast.setGraphicsEffect(self._toast_effect)
        self._toast_anim: QSequentialAnimationGroup | None = None

        QShortcut(QKeySequence(Qt.Key.Key_Left), self, activated=lambda: self._step_image(-1))
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, activated=lambda: self._step_image(1))
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, activated=self.close)

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
        extra_tags: list[str] | None = None,
    ) -> None:
        self._overlay.commit_pending()
        self._shots = list(shots)
        self._paths = list(paths)
        self._index = max(0, min(index, len(self._shots) - 1)) if self._shots else 0
        self._extra_tags = list(extra_tags or [])
        self._overlay.set_defaults(
            opacity=opacity,
            font_size=font_size,
            color=color,
            align=align,
            background=background,
        )
        entering = not self.isVisible()
        self._load_current()
        self.show()
        self.raise_()
        self._position_chrome()
        if entering:
            self._show_mode_toast()

    def is_open(self) -> bool:
        return self.isVisible()

    def current_index(self) -> int:
        return self._index

    def current_shot_id(self) -> str:
        if not self._shots or self._index >= len(self._shots):
            return ""
        return self._shots[self._index].id

    def current_title(self) -> str:
        if not self._shots or self._index >= len(self._shots):
            return ""
        return self._shots[self._index].title

    def release_images(self) -> None:
        self._viewer.clear()

    def render_composite(self):
        return self._overlay.render_composite(self._viewer.source_pixmap())

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self._stop_mode_toast()
        self._overlay.commit_pending()
        self.release_images()
        self.hide()
        self.closed.emit()
        event.accept()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        self._sync_overlay_bounds()
        self._position_chrome()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._position_chrome()

    def _load_current(self) -> None:
        path = self._paths[self._index] if self._paths and self._index < len(self._paths) else None
        self._viewer.set_path(path)
        shot = self._shots[self._index] if self._shots and self._index < len(self._shots) else None
        title = (shot.title if shot is not None else "").strip() or "（无标题）"
        count = len(self._shots)
        counter = f"{self._index + 1}/{count}" if count else "0/0"
        self._counter_label.setText(counter)
        self.setToolTip(f"查看截图 · {counter} · {title}")
        self._prev_btn.setEnabled(self._index > 0)
        self._next_btn.setEnabled(self._index < count - 1)
        self._sync_overlay_bounds()
        if shot is not None:
            self._overlay.set_notes(shot.notes, touch_shot=shot)
        else:
            self._overlay.clear()
        self._position_chrome()

    def _on_notes_changed(self) -> None:
        self.notes_changed.emit()

    def _on_image_rect_changed(self) -> None:
        self._sync_overlay_bounds()
        self._refresh_chrome_contrast()

    def _sync_overlay_bounds(self) -> None:
        origin = self._viewer.mapTo(self._canvas, self._viewer.rect().topLeft())
        local = self._viewer.image_rect()
        bounds = local.translated(origin)
        self._overlay.set_image_bounds(bounds)

    def _position_chrome(self) -> None:
        margin = 12
        self._nav.adjustSize()
        nav_w = max(self._nav.sizeHint().width(), self._nav.width())
        nav_h = max(self._nav.sizeHint().height(), self._nav.height())
        self._nav.setFixedSize(nav_w, nav_h)
        x = max(margin, self._canvas.width() - nav_w - margin)
        y = max(margin, (self._canvas.height() - nav_h) // 2)
        self._nav.move(x, y)
        self._close_btn.move(
            max(margin, self._canvas.width() - self._close_btn.width() - margin),
            margin,
        )
        self._position_mode_toast()
        self._refresh_chrome_contrast()
        self._raise_chrome()

    def _raise_chrome(self) -> None:
        self._nav.raise_()
        self._close_btn.raise_()
        if self._mode_toast.isVisible():
            self._mode_toast.raise_()

    def _position_mode_toast(self) -> None:
        if not hasattr(self, "_mode_toast"):
            return
        self._mode_toast.adjustSize()
        width = min(420, max(240, self._canvas.width() - 48))
        self._mode_toast.setFixedWidth(width)
        self._mode_toast.adjustSize()
        x = max(12, (self._canvas.width() - self._mode_toast.width()) // 2)
        y = max(12, int(self._canvas.height() * 0.12))
        self._mode_toast.move(x, y)

    def _show_mode_toast(self) -> None:
        self._stop_mode_toast()
        self._position_mode_toast()
        self._toast_effect.setOpacity(0.0)
        self._mode_toast.show()
        self._mode_toast.raise_()

        fade_in = QPropertyAnimation(self._toast_effect, b"opacity", self)
        fade_in.setDuration(700)
        fade_in.setStartValue(0.0)
        fade_in.setEndValue(1.0)
        fade_in.setEasingCurve(QEasingCurve.Type.OutCubic)

        hold = QPropertyAnimation(self._toast_effect, b"opacity", self)
        hold.setDuration(1300)
        hold.setStartValue(1.0)
        hold.setEndValue(1.0)

        fade_out = QPropertyAnimation(self._toast_effect, b"opacity", self)
        fade_out.setDuration(1000)
        fade_out.setStartValue(1.0)
        fade_out.setEndValue(0.0)
        fade_out.setEasingCurve(QEasingCurve.Type.InCubic)

        group = QSequentialAnimationGroup(self)
        group.addAnimation(fade_in)
        group.addAnimation(hold)
        group.addAnimation(fade_out)
        group.finished.connect(self._hide_mode_toast)
        self._toast_anim = group
        group.start()

    def _hide_mode_toast(self) -> None:
        self._mode_toast.hide()
        self._toast_effect.setOpacity(0.0)

    def _stop_mode_toast(self) -> None:
        if self._toast_anim is not None:
            self._toast_anim.stop()
            self._toast_anim.deleteLater()
            self._toast_anim = None
        self._hide_mode_toast()

    def _refresh_chrome_contrast(self) -> None:
        """按按钮背后画面明暗切换配色：白板用深色钮，黑板用浅色钮。"""
        luminance = self._sample_luminance_behind(self._nav)
        self._apply_chrome_theme(on_light=luminance >= 0.55)

    def _apply_chrome_theme(self, *, on_light: bool) -> None:
        self._chrome_on_light = on_light
        if on_light:
            self._prev_btn.setStyleSheet(_NAV_ON_LIGHT)
            self._next_btn.setStyleSheet(_NAV_ON_LIGHT)
            self._counter_label.setStyleSheet(_NAV_ON_LIGHT)
            self._close_btn.setStyleSheet(_CLOSE_ON_LIGHT)
        else:
            self._prev_btn.setStyleSheet(_NAV_ON_DARK)
            self._next_btn.setStyleSheet(_NAV_ON_DARK)
            self._counter_label.setStyleSheet(_NAV_ON_DARK)
            self._close_btn.setStyleSheet(_CLOSE_ON_DARK)

    def _sample_luminance_behind(self, widget: QWidget) -> float:
        """采样控件背后底图的平均相对亮度，0 暗 1 亮。落在黑边上时按暗处理。"""
        pixmap = self._viewer.source_pixmap()
        display = self._viewer.image_rect()
        if pixmap.isNull() or display.width() <= 0 or display.height() <= 0:
            return 0.0
        top_left = widget.mapTo(self._viewer, QPoint(0, 0))
        sample = QRect(top_left, widget.size()).intersected(display)
        if sample.isEmpty():
            return 0.0
        image = pixmap.toImage().convertToFormat(QImage.Format.Format_RGB32)
        if image.isNull():
            return 0.0
        src_w = image.width()
        src_h = image.height()
        if src_w <= 0 or src_h <= 0:
            return 0.0
        total = 0.0
        count = 0
        steps_x = max(1, min(8, sample.width()))
        steps_y = max(1, min(8, sample.height()))
        for iy in range(steps_y):
            for ix in range(steps_x):
                vx = sample.x() + int((ix + 0.5) * sample.width() / steps_x)
                vy = sample.y() + int((iy + 0.5) * sample.height() / steps_y)
                sx = int((vx - display.x()) * src_w / display.width())
                sy = int((vy - display.y()) * src_h / display.height())
                if sx < 0 or sy < 0 or sx >= src_w or sy >= src_h:
                    continue
                color = QColor(image.pixel(sx, sy))
                total += (0.2126 * color.red() + 0.7152 * color.green() + 0.0722 * color.blue()) / 255.0
                count += 1
        if count <= 0:
            return 0.0
        return total / count

    def _show_canvas_menu(self, pos: QPoint) -> None:
        menu = QMenu(self)
        edit_action = menu.addAction("编辑截图")
        export_action = menu.addAction("截屏保存")
        edit_action.setEnabled(bool(self.current_shot_id()))
        export_action.setEnabled(self._viewer.has_image())
        chosen = menu.exec(self._viewer.mapToGlobal(pos))
        if chosen == edit_action:
            self._request_edit()
        elif chosen == export_action:
            self.export_requested.emit()

    def _step_image(self, step: int) -> None:
        if not self._shots:
            return
        nxt = self._index + step
        if nxt < 0 or nxt >= len(self._shots):
            return
        self._overlay.commit_pending()
        self._index = nxt
        self._load_current()

    def _request_edit(self) -> None:
        shot_id = self.current_shot_id()
        if shot_id:
            self.edit_requested.emit(shot_id)

    def _request_jump(self) -> None:
        shot_id = self.current_shot_id()
        if shot_id:
            self.jump_requested.emit(shot_id)
