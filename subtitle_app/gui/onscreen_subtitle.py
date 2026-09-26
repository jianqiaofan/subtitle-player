from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import QRect, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QImage,
    QPainter,
    QPainterPath,
    QPalette,
)
from PyQt6.QtMultimedia import QVideoFrame, QVideoSink
from PyQt6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from core.config import AppConfig, save_config
from gui.styles import DARK_STYLE

_POSITION_OPTIONS = (
    ("上方", "top"),
    ("中间", "middle"),
    ("下方", "bottom"),
)

_LIST_SIDE_OPTIONS = (
    ("左侧", "left"),
    ("右侧", "right"),
)

_EDGE_MARGIN = 24
_TEXT_PADDING_X = 16
_TEXT_PADDING_Y = 10
_BOX_RADIUS = 8


@dataclass
class OnScreenSubtitleStyle:
    enabled: bool = True
    font_size: int = 28
    color: str = "#FFFFFF"
    bg_opacity: float = 0.55
    width_percent: int = 80
    position: str = "bottom"
    immersive_list: bool = False
    immersive_list_opacity: float = 0.28
    immersive_list_side: str = "right"
    immersive_list_width_percent: int = 36
    # 仅当前视频有效，不写入配置；打开新文件时由播放器重置为显示。
    subtitle_list_visible: bool = True

    @classmethod
    def from_config(cls, config: AppConfig) -> "OnScreenSubtitleStyle":
        return cls(
            enabled=bool(config.onscreen_subtitle_enabled),
            font_size=int(config.onscreen_subtitle_font_size),
            color=str(config.onscreen_subtitle_color or "#FFFFFF"),
            bg_opacity=float(config.onscreen_subtitle_bg_opacity),
            width_percent=int(config.onscreen_subtitle_width_percent),
            position=str(config.onscreen_subtitle_position or "bottom"),
            immersive_list=bool(config.immersive_subtitle_list),
            immersive_list_opacity=float(config.immersive_subtitle_list_opacity),
            immersive_list_side=str(config.immersive_subtitle_list_side or "right"),
            immersive_list_width_percent=int(config.immersive_subtitle_list_width_percent),
        )

    def apply_to_config(self, config: AppConfig) -> None:
        config.onscreen_subtitle_enabled = bool(self.enabled)
        config.onscreen_subtitle_font_size = max(12, min(72, int(self.font_size)))
        config.onscreen_subtitle_color = self.color.strip() or "#FFFFFF"
        config.onscreen_subtitle_bg_opacity = max(0.0, min(1.0, float(self.bg_opacity)))
        config.onscreen_subtitle_width_percent = max(30, min(100, int(self.width_percent)))
        position = (self.position or "bottom").strip().lower()
        config.onscreen_subtitle_position = (
            position if position in {"top", "middle", "bottom"} else "bottom"
        )
        config.immersive_subtitle_list = bool(self.immersive_list)
        config.immersive_subtitle_list_opacity = max(
            0.0, min(1.0, float(self.immersive_list_opacity))
        )
        side = (self.immersive_list_side or "right").strip().lower()
        config.immersive_subtitle_list_side = side if side in {"left", "right"} else "right"
        config.immersive_subtitle_list_width_percent = max(
            18, min(70, int(self.immersive_list_width_percent))
        )


def immersive_list_pixel_width(total_width: int, width_percent: int) -> int:
    """沉浸列表实际宽度，与播放器里拖动边缘时的算法一致。"""
    host_w = max(1, int(total_width))
    percent = max(18, min(70, int(width_percent)))
    width = max(180, int(host_w * percent / 100.0))
    return min(width, max(180, int(host_w * 0.7)))


def bounds_beside_immersive_list(bounds: QRect, style: OnScreenSubtitleStyle) -> QRect:
    """沉浸列表打开时，字幕水平范围改到列表以外的画面里。高度与垂直位置不变。"""
    if not style.immersive_list:
        return bounds
    list_w = immersive_list_pixel_width(bounds.width(), style.immersive_list_width_percent)
    list_w = min(list_w, max(0, bounds.width() - 8))
    free_w = max(8, bounds.width() - list_w)
    side = (style.immersive_list_side or "right").strip().lower()
    if side == "left":
        return QRect(bounds.x() + list_w, bounds.y(), free_w, bounds.height())
    return QRect(bounds.x(), bounds.y(), free_w, bounds.height())


def paint_onscreen_subtitle(
    painter: QPainter,
    bounds: QRect,
    text: str,
    style: OnScreenSubtitleStyle,
) -> None:
    """Draw solid text on a semi-transparent bar inside bounds."""
    cleaned = (text or "").replace("\r\n", "\n").strip()
    bounds = bounds_beside_immersive_list(bounds, style)
    if not style.enabled or not cleaned or bounds.width() < 8 or bounds.height() < 8:
        return

    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

    font = QFont(painter.font())
    font.setPixelSize(max(12, int(style.font_size)))
    font.setBold(True)
    painter.setFont(font)

    max_content_w = max(
        40,
        int(bounds.width() * max(30, min(100, style.width_percent)) / 100.0)
        - 2 * _TEXT_PADDING_X,
    )
    flags = (
        Qt.AlignmentFlag.AlignHCenter
        | Qt.AlignmentFlag.AlignVCenter
        | Qt.TextFlag.TextWordWrap
    )
    metrics = QFontMetrics(font)
    text_bound = metrics.boundingRect(
        QRect(0, 0, max_content_w, max(1, bounds.height() - 2 * _EDGE_MARGIN)),
        int(flags),
        cleaned,
    )

    box_w = min(bounds.width() - 8, max(text_bound.width() + 2 * _TEXT_PADDING_X, 40))
    box_h = text_bound.height() + 2 * _TEXT_PADDING_Y
    box_x = bounds.x() + (bounds.width() - box_w) // 2

    position = (style.position or "bottom").lower()
    if position == "top":
        box_y = bounds.y() + _EDGE_MARGIN
    elif position == "middle":
        box_y = bounds.y() + max(_EDGE_MARGIN, (bounds.height() - box_h) // 2)
    else:
        box_y = bounds.y() + max(_EDGE_MARGIN, bounds.height() - box_h - _EDGE_MARGIN)

    bg = QColor(0, 0, 0)
    bg.setAlphaF(max(0.0, min(1.0, float(style.bg_opacity))))
    path = QPainterPath()
    path.addRoundedRect(
        float(box_x),
        float(box_y),
        float(box_w),
        float(box_h),
        float(_BOX_RADIUS),
        float(_BOX_RADIUS),
    )
    painter.fillPath(path, bg)

    text_color = QColor(style.color)
    if not text_color.isValid():
        text_color = QColor("#FFFFFF")
    text_color.setAlpha(255)
    painter.setPen(text_color)
    painter.drawText(
        QRect(
            box_x + _TEXT_PADDING_X,
            box_y + _TEXT_PADDING_Y,
            box_w - 2 * _TEXT_PADDING_X,
            box_h - 2 * _TEXT_PADDING_Y,
        ),
        int(flags),
        cleaned,
    )
    painter.restore()


class SubtitleVideoWidget(QWidget):
    """Software-composited video surface with on-screen subtitles.

    Uses QVideoSink + QPainter so subtitles are drawn in the same pass as the
    frame. This avoids QVideoWidget's native HWND covering any Qt overlay.
    """

    _SINGLE_CLICK_MS = 250

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("subtitleVideoWidget")
        self.setMinimumSize(480, 270)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("background-color: #000000; border: none;")
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor("#000000"))
        palette.setColor(QPalette.ColorRole.Base, QColor("#000000"))
        self.setPalette(palette)
        self.setAutoFillBackground(True)

        self._sink = QVideoSink(self)
        self._sink.videoFrameChanged.connect(self._on_video_frame)
        self._frame = QImage()
        self._subtitle_text = ""
        self._style = OnScreenSubtitleStyle()

        self._toggle_callback = None
        self._double_click_callback = None
        self._single_click_timer = QTimer(self)
        self._single_click_timer.setSingleShot(True)
        self._single_click_timer.setInterval(self._SINGLE_CLICK_MS)
        self._single_click_timer.timeout.connect(self._emit_single_click)

    def video_sink(self) -> QVideoSink:
        return self._sink

    def clear_frame(self) -> None:
        self._frame = QImage()
        self.update()

    def set_subtitle_text(self, text: str) -> None:
        cleaned = (text or "").replace("\r\n", "\n").strip()
        if cleaned == self._subtitle_text:
            return
        self._subtitle_text = cleaned
        self.update()

    def set_subtitle_style(self, style: OnScreenSubtitleStyle) -> None:
        self._style = style
        self.update()

    def set_toggle_callback(self, callback) -> None:
        self._toggle_callback = callback

    def set_double_click_callback(self, callback) -> None:
        self._double_click_callback = callback

    def _emit_single_click(self) -> None:
        if self._toggle_callback is not None:
            self._toggle_callback()

    def _on_video_frame(self, frame: QVideoFrame) -> None:
        if not frame.isValid():
            return
        # toImage() maps the frame when needed; keep a copy for later paints.
        image = frame.toImage()
        if image.isNull():
            return
        self._frame = image.copy()
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#000000"))
        if not self._frame.isNull():
            scaled = self._frame.scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = (self.width() - scaled.width()) // 2
            y = (self.height() - scaled.height()) // 2
            painter.drawImage(x, y, scaled)
        paint_onscreen_subtitle(painter, self.rect(), self._subtitle_text, self._style)
        painter.end()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._single_click_timer.stop()
            if self._double_click_callback is not None:
                self._double_click_callback()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._single_click_timer.start()
            event.accept()
            return
        super().mousePressEvent(event)


class _AudioSubtitleLayer(QWidget):
    """Child overlay for audio placeholder (no native video HWND)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self.setStyleSheet("background: transparent;")
        self._text = ""
        self._style = OnScreenSubtitleStyle()

    def set_text(self, text: str) -> None:
        cleaned = (text or "").replace("\r\n", "\n").strip()
        if cleaned == self._text:
            return
        self._text = cleaned
        self.update()

    def set_style(self, style: OnScreenSubtitleStyle) -> None:
        self._style = style
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        paint_onscreen_subtitle(painter, self.rect(), self._text, self._style)
        painter.end()


class OnScreenSubtitleFacade:
    """Unified API used by the player for video + audio subtitle display."""

    def __init__(self, video: SubtitleVideoWidget, audio_layer: _AudioSubtitleLayer) -> None:
        self._video = video
        self._audio_layer = audio_layer

    def set_text(self, text: str) -> None:
        self._video.set_subtitle_text(text)
        self._audio_layer.set_text(text)

    def set_style(self, style: OnScreenSubtitleStyle) -> None:
        self._video.set_subtitle_style(style)
        self._audio_layer.set_style(style)

    def set_immersive_metrics(self, *, active: bool, side: str, width_percent: int) -> None:
        """拖动列表宽度时同步画面字幕的水平居中范围。"""
        side_norm = side if side in {"left", "right"} else "right"
        percent = max(18, min(70, int(width_percent)))
        for target in (self._video, self._audio_layer):
            style = target._style
            style.immersive_list = bool(active)
            style.immersive_list_side = side_norm
            style.immersive_list_width_percent = percent
            target.update()

    def text(self) -> str:
        return self._video._subtitle_text


class MediaViewport(QWidget):
    """Hosts video/audio surface and routes on-screen subtitle updates."""

    def __init__(
        self,
        video_widget: SubtitleVideoWidget,
        audio_placeholder: QWidget,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("mediaViewport")
        self.setMinimumSize(480, 270)
        self.setStyleSheet("background-color: #000000;")

        self.video_widget = video_widget
        self.audio_placeholder = audio_placeholder
        video_widget.setParent(self)
        audio_placeholder.setParent(self)

        self._audio_layer = _AudioSubtitleLayer(self)
        self.overlay = OnScreenSubtitleFacade(video_widget, self._audio_layer)
        self._relayout()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._relayout()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._relayout()

    def set_audio_mode(self, is_audio: bool) -> None:
        self.video_widget.setVisible(not is_audio)
        self.audio_placeholder.setVisible(is_audio)
        self._audio_layer.setVisible(is_audio)
        self._relayout()

    def refresh_stacking(self) -> None:
        self._relayout()

    def _relayout(self) -> None:
        rect = self.rect()
        self.video_widget.setGeometry(rect)
        self.audio_placeholder.setGeometry(rect)
        self._audio_layer.setGeometry(rect)
        if self._audio_layer.isVisible():
            self._audio_layer.raise_()


class OnScreenSubtitleSettingsDialog(QDialog):
    style_changed = pyqtSignal(object)

    def __init__(self, style: OnScreenSubtitleStyle, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("画面字幕")
        self.setMinimumWidth(420)
        self.setWindowOpacity(0.82)
        self._close_confirmed = False
        self.setStyleSheet(
            DARK_STYLE
            + """
QGroupBox#settingsSection {
    color: #1a1a1a;
    border: 1px solid rgba(0, 0, 0, 0.28);
}
QGroupBox#settingsSection::title {
    color: #1a1a1a;
    background-color: #f3f3f3;
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
}
"""
        )
        self._style = OnScreenSubtitleStyle(
            enabled=style.enabled,
            font_size=style.font_size,
            color=style.color,
            bg_opacity=style.bg_opacity,
            width_percent=style.width_percent,
            position=style.position,
            immersive_list=style.immersive_list,
            immersive_list_opacity=style.immersive_list_opacity,
            immersive_list_side=style.immersive_list_side,
            immersive_list_width_percent=style.immersive_list_width_percent,
            subtitle_list_visible=style.subtitle_list_visible,
        )

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("修改后即时预览。打开新文件时会重新显示字幕列表。"))

        list_box = QGroupBox("字幕列表")
        list_box.setObjectName("settingsSection")
        list_form = QFormLayout(list_box)

        self.list_visible_check = QCheckBox("显示字幕列表")
        self.list_visible_check.setChecked(self._style.subtitle_list_visible)
        self.list_visible_check.setToolTip(
            "取消后隐藏字幕列表，把播放器当作普通播放器使用。"
            "此选项只对当前视频有效，打开新文件时会重新显示列表。"
        )
        self.list_visible_check.toggled.connect(self._on_list_visible_toggled)
        list_form.addRow("", self.list_visible_check)

        self.immersive_check = QCheckBox("沉浸列表（字幕列表透明叠在画面上）")
        self.immersive_check.setChecked(self._style.immersive_list)
        self.immersive_check.setToolTip(
            "开启后画面铺满播放区，字幕列表半透明叠在画面上方；"
            "可左右拖动列表边缘调节宽度；点击跳转与右键菜单仍可用。"
        )
        self.immersive_check.toggled.connect(self._on_immersive_toggled)
        list_form.addRow("", self.immersive_check)

        self.list_side_combo = QComboBox()
        for label, value in _LIST_SIDE_OPTIONS:
            self.list_side_combo.addItem(label, value)
        side_idx = self.list_side_combo.findData(self._style.immersive_list_side)
        self.list_side_combo.setCurrentIndex(side_idx if side_idx >= 0 else 1)
        self.list_side_combo.currentIndexChanged.connect(self._emit_preview)
        self._list_side_label = QLabel("列表位置")
        list_form.addRow(self._list_side_label, self.list_side_combo)

        self.list_bg_slider = QSlider(Qt.Orientation.Horizontal)
        self.list_bg_slider.setRange(0, 100)
        self.list_bg_slider.setValue(int(round(self._style.immersive_list_opacity * 100)))
        self.list_bg_value = QLabel(f"{self.list_bg_slider.value()}%")
        self.list_bg_slider.valueChanged.connect(self._on_list_bg_changed)
        list_bg_row = QHBoxLayout()
        list_bg_row.addWidget(self.list_bg_slider, stretch=1)
        list_bg_row.addWidget(self.list_bg_value)
        self._list_bg_row_widget = QWidget()
        self._list_bg_row_widget.setLayout(list_bg_row)
        self._list_bg_label = QLabel("列表背景透明度")
        list_form.addRow(self._list_bg_label, self._list_bg_row_widget)

        self._list_drag_hint = QLabel("提示：沉浸模式下可拖动列表内侧边缘调节宽度")
        self._list_drag_hint.setObjectName("hintLabel")
        self._list_drag_hint.setWordWrap(True)
        list_form.addRow("", self._list_drag_hint)
        layout.addWidget(list_box)

        subtitle_box = QGroupBox("画面字幕")
        subtitle_box.setObjectName("settingsSection")
        subtitle_form = QFormLayout(subtitle_box)

        self.enabled_check = QCheckBox("显示画面字幕")
        self.enabled_check.setChecked(self._style.enabled)
        self.enabled_check.toggled.connect(self._on_subtitle_enabled_toggled)
        subtitle_form.addRow("", self.enabled_check)

        self.font_slider = QSlider(Qt.Orientation.Horizontal)
        self.font_slider.setRange(12, 72)
        self.font_slider.setValue(self._style.font_size)
        self.font_value = QLabel(str(self._style.font_size))
        self.font_slider.valueChanged.connect(self._on_font_changed)
        self._font_row_widget = self._slider_row(self.font_slider, self.font_value)
        self._font_label = QLabel("字体大小")
        subtitle_form.addRow(self._font_label, self._font_row_widget)

        self.color_btn = QPushButton()
        self.color_btn.setFixedHeight(28)
        self.color_btn.clicked.connect(self._pick_color)
        self._update_color_button()
        self._color_label = QLabel("文字颜色")
        subtitle_form.addRow(self._color_label, self.color_btn)

        self.bg_slider = QSlider(Qt.Orientation.Horizontal)
        self.bg_slider.setRange(0, 100)
        self.bg_slider.setValue(int(round(self._style.bg_opacity * 100)))
        self.bg_value = QLabel(f"{self.bg_slider.value()}%")
        self.bg_slider.valueChanged.connect(self._on_bg_changed)
        self._bg_row_widget = self._slider_row(self.bg_slider, self.bg_value)
        self._bg_label = QLabel("底条透明度")
        subtitle_form.addRow(self._bg_label, self._bg_row_widget)

        self.width_slider = QSlider(Qt.Orientation.Horizontal)
        self.width_slider.setRange(30, 100)
        self.width_slider.setValue(self._style.width_percent)
        self.width_value = QLabel(f"{self._style.width_percent}%")
        self.width_slider.valueChanged.connect(self._on_width_changed)
        self._width_row_widget = self._slider_row(self.width_slider, self.width_value)
        self._width_label = QLabel("字幕宽度")
        subtitle_form.addRow(self._width_label, self._width_row_widget)

        self.position_combo = QComboBox()
        for label, value in _POSITION_OPTIONS:
            self.position_combo.addItem(label, value)
        idx = self.position_combo.findData(self._style.position)
        self.position_combo.setCurrentIndex(idx if idx >= 0 else 2)
        self.position_combo.currentIndexChanged.connect(self._emit_preview)
        self._position_label = QLabel("字幕位置")
        subtitle_form.addRow(self._position_label, self.position_combo)
        layout.addWidget(subtitle_box)

        self._sync_list_related_controls()
        self._sync_subtitle_related_controls()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._initial_style = self.current_style()

    def current_style(self) -> OnScreenSubtitleStyle:
        return OnScreenSubtitleStyle(
            enabled=self.enabled_check.isChecked(),
            font_size=self.font_slider.value(),
            color=self._style.color,
            bg_opacity=self.bg_slider.value() / 100.0,
            width_percent=self.width_slider.value(),
            position=str(self.position_combo.currentData() or "bottom"),
            immersive_list=self.immersive_check.isChecked(),
            immersive_list_opacity=self.list_bg_slider.value() / 100.0,
            immersive_list_side=str(self.list_side_combo.currentData() or "right"),
            immersive_list_width_percent=int(self._style.immersive_list_width_percent),
            subtitle_list_visible=self.list_visible_check.isChecked(),
        )

    def _sync_list_related_controls(self) -> None:
        list_on = self.list_visible_check.isChecked()
        immersive_on = list_on and self.immersive_check.isChecked()
        self.immersive_check.setEnabled(list_on)
        for widget in (
            self._list_side_label,
            self.list_side_combo,
            self._list_bg_label,
            self._list_bg_row_widget,
            self._list_drag_hint,
        ):
            widget.setEnabled(immersive_on)

    def _sync_subtitle_related_controls(self) -> None:
        enabled = self.enabled_check.isChecked()
        for widget in (
            self._font_label,
            self._font_row_widget,
            self._color_label,
            self.color_btn,
            self._bg_label,
            self._bg_row_widget,
            self._width_label,
            self._width_row_widget,
            self._position_label,
            self.position_combo,
        ):
            widget.setEnabled(enabled)

    def _on_list_visible_toggled(self) -> None:
        self._sync_list_related_controls()
        self._emit_preview()

    def _on_subtitle_enabled_toggled(self) -> None:
        self._sync_subtitle_related_controls()
        self._emit_preview()

    @staticmethod
    def _slider_row(slider: QSlider, value_label: QLabel) -> QWidget:
        row = QHBoxLayout()
        row.addWidget(slider, stretch=1)
        row.addWidget(value_label)
        widget = QWidget()
        widget.setLayout(row)
        return widget

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if getattr(self, "_geometry_locked", False):
            return
        self._geometry_locked = True
        self.adjustSize()
        host = self.parentWidget()
        if host is not None:
            host = host.window()
        if host is not None and host.isVisible():
            frame = host.frameGeometry()
            size = self.size()
            x = frame.x() + max(0, (frame.width() - size.width()) // 2)
            y = frame.y() + max(0, (frame.height() - size.height()) // 2)
            self.move(x, y)
        self.setFixedSize(self.size())

    def _is_dirty(self) -> bool:
        return self.current_style() != self._initial_style

    def accept(self) -> None:  # noqa: N802
        self._close_confirmed = True
        super().accept()

    def reject(self) -> None:  # noqa: N802
        self._close_confirmed = True
        super().reject()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._close_confirmed or not self._is_dirty():
            super().closeEvent(event)
            return
        choice = QMessageBox(self)
        choice.setWindowTitle("画面字幕")
        choice.setIcon(QMessageBox.Icon.Question)
        choice.setText("画面字幕设置已修改。关闭窗口前，要确认这些修改，还是放弃？")
        confirm_btn = choice.addButton("确认修改", QMessageBox.ButtonRole.AcceptRole)
        discard_btn = choice.addButton("放弃修改", QMessageBox.ButtonRole.DestructiveRole)
        choice.setDefaultButton(confirm_btn)
        choice.exec()
        clicked = choice.clickedButton()
        if clicked is confirm_btn:
            self._close_confirmed = True
            self.done(QDialog.DialogCode.Accepted)
            event.accept()
            return
        if clicked is discard_btn:
            self._close_confirmed = True
            event.accept()
            return
        event.ignore()

    def _on_immersive_toggled(self) -> None:
        self._sync_list_related_controls()
        self._emit_preview()

    def _on_font_changed(self, value: int) -> None:
        self.font_value.setText(str(value))
        self._emit_preview()

    def _on_list_bg_changed(self, value: int) -> None:
        self.list_bg_value.setText(f"{value}%")
        self._emit_preview()

    def _on_bg_changed(self, value: int) -> None:
        self.bg_value.setText(f"{value}%")
        self._emit_preview()

    def _on_width_changed(self, value: int) -> None:
        self.width_value.setText(f"{value}%")
        self._emit_preview()

    def _update_color_button(self) -> None:
        color = QColor(self._style.color)
        if not color.isValid():
            color = QColor("#FFFFFF")
            self._style.color = "#FFFFFF"
        self.color_btn.setText(self._style.color)
        self.color_btn.setStyleSheet(
            f"background-color: {color.name()}; color: {'#000' if color.lightness() > 160 else '#fff'};"
            "border: 1px solid #555555; border-radius: 4px;"
        )

    def _pick_color(self) -> None:
        initial = QColor(self._style.color)
        if not initial.isValid():
            initial = QColor("#FFFFFF")
        chosen = QColorDialog.getColor(initial, self, "选择字幕颜色")
        if not chosen.isValid():
            return
        self._style.color = chosen.name()
        self._update_color_button()
        self._emit_preview()

    def _emit_preview(self) -> None:
        self.style_changed.emit(self.current_style())

    @classmethod
    def edit_style(
        cls,
        style: OnScreenSubtitleStyle,
        parent: QWidget | None = None,
        on_preview=None,
    ) -> OnScreenSubtitleStyle | None:
        dialog = cls(style, parent)
        if on_preview is not None:
            dialog.style_changed.connect(on_preview)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.current_style()


def apply_onscreen_style_to_config(config: AppConfig, style: OnScreenSubtitleStyle) -> None:
    style.apply_to_config(config)
    save_config(config)
