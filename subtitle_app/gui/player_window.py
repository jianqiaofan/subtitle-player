from __future__ import annotations

import sys
import time
from pathlib import Path

from PyQt6.QtCore import QEvent, QRectF, QSizeF, Qt, QTimer, QUrl
from PyQt6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QDesktopServices,
    QDragEnterEvent,
    QDropEvent,
    QGuiApplication,
    QIcon,
    QPen,
)
from PyQt6.QtMultimedia import QAudioDevice, QAudioOutput, QMediaDevices, QMediaPlayer
from PyQt6.QtMultimediaWidgets import QGraphicsVideoItem
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSlider,
    QSplitter,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

_RECOVERABLE_PLAYBACK_MARKERS = (
    "demuxing failed",
    "failed to seek",
    "permission denied",
)
_MAX_PLAYBACK_RECOVERY_ATTEMPTS = 2
_ERROR_DIALOG_COOLDOWN_SEC = 4.0
_SUBTITLE_REPEAT_GAP_MS = 500

from core.ai_notes import (
    build_notes_output_path,
    collect_valid_subtitle_corpus,
    corpus_to_text,
    find_notes_path,
)
from core.ai_notes_worker import AiNotesWorker
from core.subtitle_text_export import export_plain_text_markdown
from core.vocabulary_worker import VocabularyWorker
from core.config import CONFIG_PATH, INFERENCE_DEVICE_OPTIONS, LIVE_SYNC_FILENAME_LABEL, MEDIA_EXTENSIONS, is_deepseek_configured, load_config, save_config
from core.transcriber import clear_model_cache, is_cuda_available
from core.live_worker import LiveTranscribeWorker
from core.subtitle import SubtitleSegment, find_segment_index_at_time, write_subtitle_file
from core.subtitle_loader import find_subtitles_for_media, load_subtitles
from core.subtitle_resolve import SubtitleAction, SubtitleChoice, auto_load_choice
from core.sync_subtitle import sync_subtitle_paths
from gui.ai_notes_corpus_dialog import AiNotesCorpusDialog
from gui.ai_notes_progress_dialog import AiNotesProgressDialog
from gui.llm_settings_dialog import LlmSettingsDialog
from gui.main_window import TranscribeWindow
from gui.styles import DARK_STYLE, PLAYER_LIST_STYLE
from gui.subtitle_edit_dialog import SubtitleEditDialog
from gui.subtitle_text_dialog import SubtitleTextDialog
from gui.vocabulary_dialog import VocabularyDialog

AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma", ".opus"}


class VideoDisplayHost(QGraphicsView):
    """用场景渲染视频，并在同一场景叠亮度遮罩（可避开原生视频层盖住普通控件的问题）。"""

    _SINGLE_CLICK_MS = 250

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._toggle_callback = None
        self._double_click_callback = None
        self._brightness = 100
        self._single_click_timer = QTimer(self)
        self._single_click_timer.setSingleShot(True)
        self._single_click_timer.setInterval(self._SINGLE_CLICK_MS)
        self._single_click_timer.timeout.connect(self._emit_single_click)

        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setBackgroundBrush(QBrush(QColor("#000000")))
        self.setStyleSheet("background: #000; border: none;")
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)

        self.video_item = QGraphicsVideoItem()
        self._scene.addItem(self.video_item)
        self.video_item.nativeSizeChanged.connect(self._sync_video_geometry)

        self._brightness_item = QGraphicsRectItem()
        self._brightness_item.setPen(QPen(Qt.PenStyle.NoPen))
        self._brightness_item.setZValue(10)
        self._brightness_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._brightness_item.hide()
        self._scene.addItem(self._brightness_item)

    def set_toggle_callback(self, callback) -> None:
        self._toggle_callback = callback

    def set_double_click_callback(self, callback) -> None:
        self._double_click_callback = callback

    def set_brightness(self, value: int) -> None:
        self._brightness = max(0, min(200, int(value)))
        self._update_brightness_overlay()

    def _update_brightness_overlay(self) -> None:
        value = self._brightness
        if value == 100:
            self._brightness_item.hide()
            return
        if value < 100:
            alpha = int(round((100 - value) / 100 * 255))
            color = QColor(0, 0, 0, alpha)
        else:
            alpha = int(round((value - 100) / 100 * 140))
            color = QColor(255, 255, 255, alpha)
        self._brightness_item.setBrush(QBrush(color))
        self._brightness_item.show()
        self._brightness_item.setZValue(10)

    def _sync_video_geometry(self, *_args) -> None:
        view_size = self.viewport().size()
        if view_size.width() <= 0 or view_size.height() <= 0:
            return

        native = self.video_item.nativeSize()
        if native.width() > 0 and native.height() > 0:
            video_aspect = native.width() / native.height()
            view_aspect = view_size.width() / max(1, view_size.height())
            if view_aspect > video_aspect:
                height = float(view_size.height())
                width = height * video_aspect
            else:
                width = float(view_size.width())
                height = width / video_aspect
            self.video_item.setSize(QSizeF(width, height))
            self.video_item.setPos(
                (view_size.width() - width) / 2.0,
                (view_size.height() - height) / 2.0,
            )
        else:
            self.video_item.setSize(QSizeF(view_size))
            self.video_item.setPos(0, 0)

        self._brightness_item.setRect(
            QRectF(0, 0, view_size.width(), view_size.height())
        )
        self._brightness_item.setPos(0, 0)
        self._scene.setSceneRect(0, 0, view_size.width(), view_size.height())

    def _emit_single_click(self) -> None:
        if self._toggle_callback is not None:
            self._toggle_callback()

    def _schedule_single_click(self) -> None:
        self._single_click_timer.start()

    def _cancel_single_click(self) -> None:
        self._single_click_timer.stop()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._sync_video_geometry()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._sync_video_geometry()

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._cancel_single_click()
            if self._double_click_callback is not None:
                self._double_click_callback()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._schedule_single_click()
            event.accept()
            return
        super().mousePressEvent(event)

class PlayerWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self._media_path: Path | None = None
        self._segments: list[SubtitleSegment] = []
        self._current_subtitle_row = -1
        self._seeking = False
        self._subtitle_auto_follow = True
        self._subtitle_menu_open = False
        self._transcribe_window: TranscribeWindow | None = None
        self._live_worker: LiveTranscribeWorker | None = None
        self._ai_notes_worker: AiNotesWorker | None = None
        self._ai_notes_progress: AiNotesProgressDialog | None = None
        self._pending_notes_output = ""
        self._vocabulary_worker: VocabularyWorker | None = None
        self._live_mode = False
        self._transcribed_until = 0.0
        self._config = load_config()
        self._media_area_click_timer = QTimer(self)
        self._media_area_click_timer.setSingleShot(True)
        self._media_area_click_timer.setInterval(VideoDisplayHost._SINGLE_CLICK_MS)
        self._media_area_click_timer.timeout.connect(self._on_deferred_media_area_click)
        self._study_countdown_remaining = 0
        self._study_countdown_timer = QTimer(self)
        self._study_countdown_timer.setInterval(1000)
        self._study_countdown_timer.timeout.connect(self._on_study_countdown_tick)
        self._pending_seek_ms: int | None = None
        self._pending_play_after_seek = False
        self._awaiting_reload_seek = False
        self._recovering_playback = False
        self._playback_recovery_attempts = 0
        self._last_good_position_ms = 0
        self._error_dialog_suppressed_until = 0.0
        self._saved_playback_rate = 1.0
        # Remember the user's output-device choice across Windows re-enumeration
        # (Bluetooth headsets often fire audioOutputsChanged and briefly vanish).
        self._preferred_audio_device_id: bytes | None = None
        self._preferred_audio_device_name: str = ""
        self._audio_device_refresh_timer = QTimer(self)
        self._audio_device_refresh_timer.setSingleShot(True)
        self._audio_device_refresh_timer.setInterval(200)
        self._audio_device_refresh_timer.timeout.connect(self._refresh_audio_devices)
        self._repeat_start_ms: int | None = None
        self._repeat_end_ms: int | None = None
        self._repeat_gap_timer = QTimer(self)
        self._repeat_gap_timer.setSingleShot(True)
        self._repeat_gap_timer.setInterval(_SUBTITLE_REPEAT_GAP_MS)
        self._repeat_gap_timer.timeout.connect(self._on_subtitle_repeat_gap_elapsed)

        self.setWindowTitle("字幕播放器")
        self.setMinimumSize(1000, 640)
        self.resize(1180, 720)
        self.setAcceptDrops(True)
        self.setStyleSheet(DARK_STYLE + PLAYER_LIST_STYLE)

        self._player = QMediaPlayer()
        self._media_devices = QMediaDevices()
        self._media_devices.audioOutputsChanged.connect(self._schedule_audio_device_refresh)
        self._audio_output = QAudioOutput()
        self._audio_output.setVolume(1.0)
        self._player.setAudioOutput(self._audio_output)
        self._sync_audio_output_device()
        self._player.positionChanged.connect(self._on_position_changed)
        self._player.durationChanged.connect(self._on_duration_changed)
        self._player.playbackStateChanged.connect(self._on_playback_state_changed)
        self._player.mediaStatusChanged.connect(self._on_media_status_changed)
        self._player.errorOccurred.connect(self._on_player_error)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        root.addLayout(self._build_toolbar())
        root.addWidget(self._build_main_splitter(), stretch=1)
        root.addLayout(self._build_controls())

    def _create_toolbar_menu_button(
        self,
        text: str,
        icon: QIcon,
        tooltip: str,
    ) -> QToolButton:
        button = QToolButton()
        button.setObjectName("toolbarMenuButton")
        button.setText(text)
        button.setIcon(icon)
        button.setToolTip(tooltip)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        button.setAutoRaise(False)
        return button

    def _build_toolbar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        style = self.style()

        open_btn = QPushButton("打开媒体")
        open_btn.clicked.connect(self._open_media)
        bar.addWidget(open_btn)

        bar.addWidget(QLabel("字幕"))
        self.subtitle_combo = QComboBox()
        self.subtitle_combo.setMinimumWidth(180)
        self.subtitle_combo.currentIndexChanged.connect(self._on_subtitle_selected)
        bar.addWidget(self.subtitle_combo, stretch=1)

        self.media_label = QLabel("未加载媒体文件")
        self.media_label.setObjectName("hintLabel")
        bar.addWidget(self.media_label, stretch=1)

        bar.addStretch(1)

        tools_menu = QMenu(self)
        action_transcribe = tools_menu.addAction("音视频转字幕")
        action_transcribe.triggered.connect(self._open_transcribe_tool)
        action_live = tools_menu.addAction("边播边转")
        action_live.triggered.connect(self._start_live_transcribe_manual)
        tools_menu.addSeparator()
        self._action_ai_notes = tools_menu.addAction("AI笔记")
        self._action_ai_notes.triggered.connect(self._generate_ai_notes)
        self._action_view_notes = tools_menu.addAction("查看笔记")
        self._action_view_notes.setEnabled(False)
        self._action_view_notes.triggered.connect(self._view_ai_notes)
        tools_menu.addSeparator()
        self._action_vocabulary = tools_menu.addAction("生词表")
        self._action_vocabulary.triggered.connect(self._generate_vocabulary_list)
        action_plain_text = tools_menu.addAction("纯文字")
        action_plain_text.triggered.connect(self._export_plain_text)

        tools_btn = self._create_toolbar_menu_button(
            "工具",
            style.standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView),
            "转写、笔记与字幕工具",
        )
        tools_btn.setMenu(tools_menu)
        bar.addWidget(tools_btn)

        settings_menu = QMenu(self)
        action_llm_settings = settings_menu.addAction("大模型配置")
        action_llm_settings.triggered.connect(self._open_llm_settings)
        settings_menu.addSeparator()

        inference_widget = QWidget()
        inference_layout = QHBoxLayout(inference_widget)
        inference_layout.setContentsMargins(12, 6, 12, 6)
        inference_layout.addWidget(QLabel("推理设备"))
        self.inference_combo = QComboBox()
        self.inference_combo.setMinimumWidth(180)
        for label, value in INFERENCE_DEVICE_OPTIONS:
            self.inference_combo.addItem(label, value)
        idx = self.inference_combo.findData(self._config.inference_device)
        if idx >= 0:
            self.inference_combo.setCurrentIndex(idx)
        self.inference_combo.setToolTip(
            "Whisper 推理设备。GPU 需安装 CUDA 版 pywhispercpp。"
        )
        self.inference_combo.currentIndexChanged.connect(self._on_inference_device_changed)
        inference_layout.addWidget(self.inference_combo, stretch=1)
        inference_action = QWidgetAction(self)
        inference_action.setDefaultWidget(inference_widget)
        settings_menu.addAction(inference_action)

        output_widget = QWidget()
        output_layout = QHBoxLayout(output_widget)
        output_layout.setContentsMargins(12, 6, 12, 6)
        output_layout.addWidget(QLabel("输出设备"))
        self.audio_device_combo = QComboBox()
        self.audio_device_combo.setMinimumWidth(220)
        self.audio_device_combo.currentIndexChanged.connect(self._on_audio_device_changed)
        output_layout.addWidget(self.audio_device_combo, stretch=1)
        output_action = QWidgetAction(self)
        output_action.setDefaultWidget(output_widget)
        settings_menu.addAction(output_action)
        self._refresh_audio_devices()

        settings_btn = self._create_toolbar_menu_button(
            "设置",
            style.standardIcon(QStyle.StandardPixmap.SP_FileDialogInfoView),
            "应用与模型设置",
        )
        settings_btn.setMenu(settings_menu)
        bar.addWidget(settings_btn)
        return bar

    def _build_main_splitter(self) -> QSplitter:
        splitter = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)

        self._video_host = VideoDisplayHost()
        self._video_host.setMinimumSize(480, 270)
        self._video_host.set_toggle_callback(self.toggle_playback_from_video)
        self._video_host.set_double_click_callback(self.toggle_maximize_from_video)
        self._player.setVideoOutput(self._video_host.video_item)
        left_layout.addWidget(self._video_host, stretch=1)

        self._audio_placeholder = QLabel("音频播放中")
        self._audio_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._audio_placeholder.setMinimumHeight(280)
        self._audio_placeholder.setStyleSheet(
            "background-color: #2b2b2b; border: 1px solid rgba(255,255,255,0.2);"
            "font-size: 22px; color: #b980ff;"
        )
        self._audio_placeholder.hide()
        left_layout.addWidget(self._audio_placeholder)

        self._subtitle_panel = QWidget()
        right_layout = QVBoxLayout(self._subtitle_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        header.addWidget(QLabel("字幕列表（点击跳转）"))
        self.live_status_label = QLabel("")
        self.live_status_label.setObjectName("hintLabel")
        header.addStretch(1)
        header.addWidget(self.live_status_label)
        right_layout.addLayout(header)

        self.subtitle_list = QListWidget()
        self.subtitle_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.subtitle_list.customContextMenuRequested.connect(self._on_subtitle_context_menu)
        self.subtitle_list.itemClicked.connect(self._on_subtitle_clicked)
        right_layout.addWidget(self.subtitle_list)
        self.subtitle_list.installEventFilter(self)

        splitter.addWidget(left)
        splitter.addWidget(self._subtitle_panel)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        self._audio_placeholder.installEventFilter(self)
        return splitter

    def _build_controls(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        self.play_btn = QPushButton("播放")
        self.play_btn.clicked.connect(self._toggle_play)
        bar.addWidget(self.play_btn)

        self.position_slider = QSlider(Qt.Orientation.Horizontal)
        self.position_slider.setRange(0, 0)
        self.position_slider.sliderPressed.connect(self._on_slider_pressed)
        self.position_slider.sliderReleased.connect(self._on_slider_released)
        self.position_slider.valueChanged.connect(self._on_slider_moved)
        bar.addWidget(self.position_slider, stretch=1)

        self.time_label = QLabel("00:00 / 00:00")
        bar.addWidget(self.time_label)

        bar.addWidget(QLabel("倍速"))
        self.speed_combo = QComboBox()
        self.speed_combo.setMinimumWidth(72)
        for step in range(2, 9):
            rate = step * 0.25
            label = f"{rate:g}x" if rate != int(rate) else f"{int(rate)}x"
            self.speed_combo.addItem(label, rate)
        self.speed_combo.setCurrentIndex(2)  # 1.0x
        self.speed_combo.currentIndexChanged.connect(self._on_speed_changed)
        bar.addWidget(self.speed_combo)

        self.study_countdown_btn = QPushButton("倒计时")
        self.study_countdown_btn.setMinimumWidth(72)
        self.study_countdown_btn.setToolTip(
            "点击设置学习倒计时。仅在本窗口激活且位于屏幕最前时读秒。"
        )
        self.study_countdown_btn.clicked.connect(self._on_study_countdown_clicked)
        bar.addWidget(self.study_countdown_btn)

        self.mute_btn = QPushButton("静音")
        self.mute_btn.setCheckable(True)
        self.mute_btn.setFixedWidth(52)
        self.mute_btn.toggled.connect(self._on_mute_toggled)
        bar.addWidget(self.mute_btn)

        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(100)
        self.volume_slider.setFixedWidth(100)
        self.volume_slider.setToolTip("音量")
        self.volume_slider.valueChanged.connect(self._on_volume_changed)
        bar.addWidget(self.volume_slider)

        self.volume_label = QLabel("100%")
        self.volume_label.setMinimumWidth(36)
        bar.addWidget(self.volume_label)

        bar.addWidget(QLabel("亮度"))
        self.brightness_slider = QSlider(Qt.Orientation.Horizontal)
        self.brightness_slider.setRange(0, 200)
        self.brightness_slider.setValue(100)
        self.brightness_slider.setFixedWidth(100)
        self.brightness_slider.setToolTip("亮度（100% 为原始画面）")
        self.brightness_slider.valueChanged.connect(self._on_brightness_changed)
        bar.addWidget(self.brightness_slider)

        self.brightness_label = QLabel("100%")
        self.brightness_label.setMinimumWidth(36)
        bar.addWidget(self.brightness_label)
        return bar

    def _study_countdown_is_foreground(self) -> bool:
        if sys.platform != "win32":
            return self.isActiveWindow()
        try:
            import ctypes

            foreground = ctypes.windll.user32.GetForegroundWindow()
            return bool(foreground) and int(self.winId()) == foreground
        except (AttributeError, OSError, ValueError):
            return self.isActiveWindow()

    def _study_countdown_should_tick(self) -> bool:
        if not self.isVisible() or self.isMinimized() or self.isHidden():
            return False
        if not self.isActiveWindow():
            return False
        return self._study_countdown_is_foreground()

    @staticmethod
    def _format_study_countdown(seconds: int) -> str:
        minutes, secs = divmod(max(0, seconds), 60)
        return f"{minutes}:{secs:02d}"

    def _update_study_countdown_button(self) -> None:
        if self._study_countdown_remaining > 0:
            self.study_countdown_btn.setText(
                self._format_study_countdown(self._study_countdown_remaining)
            )
        else:
            self.study_countdown_btn.setText("倒计时")

    def _reset_study_countdown(self) -> None:
        self._study_countdown_timer.stop()
        self._study_countdown_remaining = 0
        self._update_study_countdown_button()

    def _on_study_countdown_clicked(self) -> None:
        default_minutes = 10
        if self._study_countdown_remaining > 0:
            default_minutes = max(1, (self._study_countdown_remaining + 59) // 60)
        minutes, ok = QInputDialog.getInt(
            self,
            "学习倒计时",
            "倒计时（分钟）：",
            value=default_minutes,
            min=1,
            max=600,
        )
        if not ok:
            return
        self._study_countdown_remaining = minutes * 60
        self._update_study_countdown_button()
        if not self._study_countdown_timer.isActive():
            self._study_countdown_timer.start()

    def _on_study_countdown_tick(self) -> None:
        if self._study_countdown_remaining <= 0:
            self._reset_study_countdown()
            return
        if not self._study_countdown_should_tick():
            return
        self._study_countdown_remaining -= 1
        if self._study_countdown_remaining <= 0:
            self._reset_study_countdown()
            return
        self._update_study_countdown_button()

    def _open_media(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "打开音视频文件",
            str(self._config.resolved_last_media_dir()),
            "媒体文件 (*.mp4 *.mkv *.avi *.mov *.mp3 *.wav *.flac *.m4a *.webm);;所有文件 (*.*)",
        )
        if path:
            self.load_media(Path(path))

    def _persist_last_media_dir(self, media_path: Path | None = None) -> None:
        target = media_path or self._media_path
        if target is None:
            return
        folder = str(target.parent.resolve())
        if self._config.last_media_dir == folder:
            return
        self._config.last_media_dir = folder
        save_config(self._config)

    def load_media(self, media_path: Path, *, choice: SubtitleChoice | None = None) -> None:
        media_path = media_path.resolve()
        if not media_path.is_file():
            return

        self._stop_live_transcribe()
        self._media_path = media_path
        self._persist_last_media_dir(media_path)
        self.media_label.setText(media_path.name)
        self._pending_seek_ms = None
        self._pending_play_after_seek = False
        self._awaiting_reload_seek = False
        self._recovering_playback = False
        self._playback_recovery_attempts = 0
        self._last_good_position_ms = 0
        self._stop_subtitle_repeat()
        self._recreate_audio_output()
        self._player.setSource(QUrl.fromLocalFile(str(media_path)))

        is_audio = media_path.suffix.lower() in AUDIO_EXTENSIONS
        self._video_host.setVisible(not is_audio)
        self._audio_placeholder.setVisible(is_audio)
        if not is_audio:
            self._player.setVideoOutput(self._video_host.video_item)

        self.subtitle_list.clear()
        self._segments.clear()
        self._current_subtitle_row = -1
        self._transcribed_until = 0.0
        self._update_live_status("")

        if choice is not None:
            self._apply_subtitle_choice(choice)
            return

        auto = auto_load_choice(media_path)
        if auto is not None:
            self._apply_subtitle_choice(auto)
            return

        self._live_mode = False
        self._refresh_subtitle_options()
        self._update_notes_buttons()

    def _update_notes_buttons(self) -> None:
        if not self._media_path:
            self._action_view_notes.setEnabled(False)
            return
        self._action_view_notes.setEnabled(find_notes_path(self._media_path) is not None)

    def _view_ai_notes(self) -> None:
        if not self._media_path:
            QMessageBox.information(self, "提示", "请先打开媒体文件。")
            return
        notes_path = find_notes_path(self._media_path)
        if notes_path is None:
            QMessageBox.information(
                self,
                "提示",
                f"未找到笔记文件。\n可先点击「AI笔记」生成：\n{self._media_path.stem}_AI笔记.md",
            )
            self._update_notes_buttons()
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(notes_path.resolve())))

    def _apply_subtitle_choice(self, choice: SubtitleChoice) -> None:
        if choice.action == SubtitleAction.USE_EXISTING and choice.subtitle_path:
            self._live_mode = False
            self._refresh_subtitle_options()
            self._select_subtitle_path(choice.subtitle_path)
            self._update_notes_buttons()
            return

        if choice.action == SubtitleAction.BATCH_TRANSCRIBE:
            self._live_mode = False
            self._refresh_subtitle_options()
            self._open_transcribe_tool()
            if self.subtitle_combo.count() == 0:
                QMessageBox.information(
                    self,
                    "提示",
                    "已打开全量转写工具。完成后将自动刷新字幕列表。",
                )
            self._update_notes_buttons()
            return

        self._live_mode = False
        self._refresh_subtitle_options()
        if self.subtitle_combo.count() > 0:
            self.subtitle_combo.setCurrentIndex(0)
            self._load_subtitle_at_index(0)
        self._update_notes_buttons()

    def _select_subtitle_path(self, path: Path) -> None:
        self._refresh_subtitle_options()
        for index in range(self.subtitle_combo.count()):
            if self.subtitle_combo.itemData(index) == str(path):
                self.subtitle_combo.setCurrentIndex(index)
                self._load_subtitle_at_index(index)
                return
        try:
            self._segments = load_subtitles(path)
        except Exception as exc:
            QMessageBox.warning(self, "字幕加载失败", str(exc))
            self._segments = []
        self._populate_subtitle_list()

    def _refresh_subtitle_options(self) -> None:
        if self._live_mode:
            return
        self.subtitle_combo.blockSignals(True)
        self.subtitle_combo.clear()
        if self._media_path:
            for path, label in find_subtitles_for_media(self._media_path):
                self.subtitle_combo.addItem(f"{label} ({path.name})", str(path))
        self.subtitle_combo.blockSignals(False)

    def _set_live_subtitle_combo(self) -> None:
        self.subtitle_combo.blockSignals(True)
        self.subtitle_combo.clear()
        self.subtitle_combo.addItem(f"边播边转（{LIVE_SYNC_FILENAME_LABEL}）", "__live__")
        self.subtitle_combo.blockSignals(False)

    def _begin_live_transcribe(
        self,
        resume_segments: list[SubtitleSegment] | None = None,
    ) -> None:
        if not self._media_path:
            return
        if self._live_worker and self._live_worker.isRunning():
            return

        self._live_mode = True
        self._current_subtitle_row = -1
        self._set_live_subtitle_combo()

        if resume_segments:
            self._segments = list(resume_segments)
            self._populate_subtitle_list()
            self._transcribed_until = max(seg.end for seg in resume_segments)
            self._update_live_status("检测到未完成的同步字幕，正在续转…")
        else:
            self._segments.clear()
            self.subtitle_list.clear()
            self._transcribed_until = 0.0
            self._update_live_status("准备中…")

        self._config.language = "mixed"
        self._config.inference_device = self.inference_combo.currentData() or "auto"
        self._live_worker = LiveTranscribeWorker(
            self._media_path,
            get_playhead=lambda: self._player.position() / 1000.0,
            get_priority=lambda: None,
            config=self._config,
            is_playing=lambda: self._player.playbackState()
            == QMediaPlayer.PlaybackState.PlayingState,
            resume_segments=resume_segments,
        )
        self._live_worker.status_changed.connect(self._update_live_status)
        self._live_worker.segments_ready.connect(self._on_live_segments_ready)
        self._live_worker.buffer_updated.connect(self._on_live_buffer_updated)
        self._live_worker.finished_ok.connect(self._on_live_finished)
        self._live_worker.failed.connect(self._on_live_failed)
        self._live_worker.start()

        if self._player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            self._play_media()

    def _start_live_transcribe_manual(self) -> None:
        if not self._media_path:
            QMessageBox.information(self, "提示", "请先打开媒体文件。")
            return
        if not self._confirm_live_transcribe():
            return
        choice = SubtitleChoiceDialog.ask(self._media_path, self)
        if choice is None:
            return
        if choice.action == SubtitleAction.LIVE_TRANSCRIBE:
            self._stop_live_transcribe()
            self._begin_live_transcribe()
            self._update_notes_buttons()
            return
        if choice.action == SubtitleAction.RESUME_LIVE_TRANSCRIBE:
            resume_segments: list[SubtitleSegment] = []
            if choice.subtitle_path:
                try:
                    resume_segments = load_subtitles(choice.subtitle_path)
                except Exception as exc:
                    QMessageBox.warning(self, "字幕加载失败", str(exc))
            self._begin_live_transcribe(resume_segments=resume_segments)
            self._update_notes_buttons()
            return
        self._apply_subtitle_choice(choice)

    def _confirm_live_transcribe(self) -> bool:
        box = QMessageBox(self)
        box.setWindowTitle("确认边播边转")
        box.setText(
            "确定要开始边播边转吗？\n\n"
            "该功能会在播放时实时转写字幕，并占用 CPU/GPU 资源。"
        )
        box.setIcon(QMessageBox.Icon.Question)
        box.setStandardButtons(
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel
        )
        ok_btn = box.button(QMessageBox.StandardButton.Ok)
        cancel_btn = box.button(QMessageBox.StandardButton.Cancel)
        if ok_btn:
            ok_btn.setText("确定")
        if cancel_btn:
            cancel_btn.setText("取消")
        return box.exec() == QMessageBox.StandardButton.Ok

    def _stop_live_transcribe(self) -> None:
        if self._live_worker and self._live_worker.isRunning():
            self._live_worker.cancel()
            self._live_worker.wait(3000)
        self._live_worker = None
        self._live_mode = False

    def _notify_live_seek(self, seconds: float) -> None:
        if self._live_worker and self._live_worker.isRunning():
            self._live_worker.set_priority_sec(seconds)

    def _update_live_status(self, message: str) -> None:
        if not message:
            self.live_status_label.setText("")
            return
        if self._live_mode and self._transcribed_until > 0:
            playhead = self._player.position() / 1000.0
            buffer_sec = max(0.0, self._transcribed_until - playhead)
            self.live_status_label.setText(f"{message} | 缓冲 +{buffer_sec:.0f}s")
        else:
            self.live_status_label.setText(message)

    def _on_live_segments_ready(self, new_segments: list) -> None:
        self._append_live_segments(new_segments)
        playhead = self._player.position() / 1000.0
        self._sync_subtitle_highlight(playhead)

    def _on_live_buffer_updated(self, transcribed_until: float) -> None:
        self._transcribed_until = transcribed_until

    def _on_live_finished(self, output_path: str) -> None:
        self._live_mode = False
        self._live_worker = None
        self._update_live_status("边播边转完成")
        self._refresh_subtitle_options()
        self._select_subtitle_path(Path(output_path))
        QMessageBox.information(
            self,
            "完成",
            f"字幕已保存：\n{Path(output_path).name}",
        )

    def _on_live_failed(self, message: str) -> None:
        self._live_mode = False
        self._live_worker = None
        self._update_live_status("")
        self._refresh_subtitle_options()
        QMessageBox.warning(self, "边播边转失败", message)

    def _append_live_segments(self, new_segments: list[SubtitleSegment]) -> None:
        for seg in new_segments:
            self._segments.append(seg)
        self._segments.sort(key=lambda item: (item.start, item.end))
        for index, seg in enumerate(self._segments, start=1):
            seg.index = index
        for seg in new_segments:
            row = self._find_subtitle_insert_row(seg.start)
            self.subtitle_list.insertItem(row, QListWidgetItem(self._format_subtitle_item(seg)))
        self._refresh_subtitle_list_texts()

    def _refresh_subtitle_list_texts(self) -> None:
        if self.subtitle_list.count() != len(self._segments):
            self._populate_subtitle_list()
            return
        for row, seg in enumerate(self._segments):
            item = self.subtitle_list.item(row)
            if item is not None:
                item.setText(self._format_subtitle_item(seg))

    def _find_subtitle_insert_row(self, start: float) -> int:
        for row in range(self.subtitle_list.count()):
            item_start = self.subtitle_list.item(row).data(Qt.ItemDataRole.UserRole)
            if item_start is not None and float(item_start) > start:
                return row
        return self.subtitle_list.count()

    def _on_subtitle_selected(self, index: int) -> None:
        if index < 0 or self._live_mode:
            return
        self._load_subtitle_at_index(index)

    def _load_subtitle_at_index(self, index: int) -> None:
        if index < 0:
            return
        path_value = self.subtitle_combo.itemData(index)
        if not path_value:
            return
        try:
            self._segments = load_subtitles(Path(path_value))
        except Exception as exc:
            QMessageBox.warning(self, "字幕加载失败", str(exc))
            self._segments = []
        self._populate_subtitle_list()
        if self._segments and self._player.duration() > 0:
            self._sync_subtitle_highlight(self._player.position() / 1000.0)

    def _populate_subtitle_list(self) -> None:
        self.subtitle_list.clear()
        for seg in self._segments:
            self.subtitle_list.addItem(QListWidgetItem(self._format_subtitle_item(seg)))
        self._current_subtitle_row = -1

    @staticmethod
    def _format_subtitle_item(seg: SubtitleSegment) -> str:
        start = PlayerWindow._format_clock(seg.start)
        end = PlayerWindow._format_clock(seg.end)
        text = seg.text.replace("\n", " / ")
        return f"{seg.index}. [{start} → {end}] {text}"

    @staticmethod
    def _format_clock(seconds: float) -> str:
        total = int(seconds)
        hours, rem = divmod(total, 3600)
        minutes, secs = divmod(rem, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"

    def _on_subtitle_clicked(self, item: QListWidgetItem) -> None:
        row = self.subtitle_list.row(item)
        if row < 0 or row >= len(self._segments):
            return
        self._stop_subtitle_repeat()
        self._seek_to(self._segments[row].start, play=True)

    def _stop_subtitle_repeat(self) -> None:
        self._repeat_gap_timer.stop()
        self._repeat_start_ms = None
        self._repeat_end_ms = None

    def _start_subtitle_repeat(self, row: int) -> None:
        if self._media_path is None or row < 0 or row >= len(self._segments):
            return
        seg = self._segments[row]
        start_ms = self._clamp_position_ms(int(round(float(seg.start) * 1000)))
        end_ms = self._clamp_position_ms(int(round(float(seg.end) * 1000)))
        if end_ms <= start_ms:
            end_ms = self._clamp_position_ms(start_ms + 500)
        self._repeat_gap_timer.stop()
        self._repeat_start_ms = start_ms
        self._repeat_end_ms = end_ms
        self.subtitle_list.setCurrentRow(row)
        self._seek_to(start_ms / 1000.0, play=True)

    def _on_subtitle_repeat_gap_elapsed(self) -> None:
        if self._repeat_start_ms is None or self._repeat_end_ms is None:
            return
        self._seek_to(self._repeat_start_ms / 1000.0, play=True)

    def _maybe_handle_subtitle_repeat(self, position_ms: int) -> None:
        if self._repeat_end_ms is None or self._repeat_start_ms is None:
            return
        if self._seeking or self._repeat_gap_timer.isActive():
            return
        if self._player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            return
        if position_ms < self._repeat_end_ms:
            return
        self._player.pause()
        self._seeking = True
        self._player.setPosition(self._repeat_end_ms)
        self._seeking = False
        self.position_slider.blockSignals(True)
        self.position_slider.setValue(self._repeat_end_ms)
        self.position_slider.blockSignals(False)
        self._repeat_gap_timer.start()

    def _pause_subtitle_auto_follow(self) -> None:
        self._subtitle_auto_follow = False

    def _resume_subtitle_auto_follow(self) -> None:
        self._subtitle_auto_follow = True
        self._current_subtitle_row = -1
        if self._player.duration() > 0:
            self._sync_subtitle_highlight(self._player.position() / 1000.0, force=True)

    def _maybe_resume_subtitle_auto_follow(self) -> None:
        if self._subtitle_menu_open or self.subtitle_list.underMouse():
            return
        self._resume_subtitle_auto_follow()

    def _on_subtitle_context_menu(self, pos) -> None:
        self._pause_subtitle_auto_follow()
        self._subtitle_menu_open = True
        try:
            item = self.subtitle_list.itemAt(pos)
            if item is None:
                return
            row = self.subtitle_list.row(item)
            if row < 0 or row >= len(self._segments):
                return

            menu = QMenu(self)
            edit_action = menu.addAction("编辑")
            edit_action.setEnabled(self._subtitle_editing_allowed())
            copy_action = menu.addAction("复制")
            repeat_action = menu.addAction("重复播放")
            chosen = menu.exec(self.subtitle_list.mapToGlobal(pos))
            if chosen == copy_action:
                self._copy_subtitle_text(row)
            elif chosen == edit_action:
                self._edit_subtitle_text(row)
            elif chosen == repeat_action:
                self._start_subtitle_repeat(row)
        finally:
            self._subtitle_menu_open = False
            QTimer.singleShot(0, self._maybe_resume_subtitle_auto_follow)

    def _subtitle_editing_allowed(self) -> bool:
        if self._live_worker and self._live_worker.isRunning():
            return False
        if self._live_mode:
            return False
        index = self.subtitle_combo.currentIndex()
        if index >= 0 and self.subtitle_combo.itemData(index) == "__live__":
            return False
        save_path = self._current_subtitle_save_path()
        if save_path is not None and save_path.name.lower().endswith(".partial"):
            return False
        return True

    def _copy_subtitle_text(self, row: int) -> None:
        if row < 0 or row >= len(self._segments):
            return
        QGuiApplication.clipboard().setText(self._segments[row].text)

    def _edit_subtitle_text(self, row: int) -> None:
        if row < 0 or row >= len(self._segments):
            return
        if not self._subtitle_editing_allowed():
            QMessageBox.information(
                self,
                "提示",
                "边播边转尚未完成，暂不可编辑字幕。请等待转写结束后再修改。",
            )
            return
        seg = self._segments[row]
        result = SubtitleEditDialog.edit_segment(seg, self)
        if result is None:
            return
        new_start, new_end, new_text = result
        if new_start == seg.start and new_end == seg.end and new_text == seg.text:
            return
        self._segments[row] = SubtitleSegment(seg.index, new_start, new_end, new_text)
        item = self.subtitle_list.item(row)
        if item is not None:
            item.setText(self._format_subtitle_item(self._segments[row]))
        self._persist_subtitle_edits()

    def _current_subtitle_save_path(self) -> Path | None:
        if not self._media_path:
            return None
        if self._live_mode:
            _output_path, partial_path = sync_subtitle_paths(self._media_path, self._config)
            return partial_path
        index = self.subtitle_combo.currentIndex()
        if index < 0:
            return None
        path_value = self.subtitle_combo.itemData(index)
        if not path_value or path_value == "__live__":
            return None
        return Path(str(path_value))

    def _subtitle_format_for_path(self, path: Path) -> str:
        name = path.name.lower()
        if name.endswith(".srt.partial"):
            return "srt"
        if name.endswith(".vtt.partial"):
            return "vtt"
        suffix = path.suffix.lower().lstrip(".")
        if suffix in {"srt", "vtt", "txt"}:
            return suffix
        return self._config.output_format or "srt"

    def _persist_subtitle_edits(self) -> None:
        path = self._current_subtitle_save_path()
        if path is None:
            QMessageBox.information(self, "提示", "当前没有可保存的字幕文件。")
            return
        fmt = self._subtitle_format_for_path(path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            write_subtitle_file(self._segments, path, fmt)
        except OSError as exc:
            QMessageBox.warning(self, "保存失败", str(exc))

    def toggle_playback_from_video(self) -> None:
        if self._media_path is None:
            return
        self._toggle_play()

    def toggle_maximize_from_video(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def _on_deferred_media_area_click(self) -> None:
        if self._media_path is not None:
            self._toggle_play()

    def _toggle_play(self) -> None:
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._stop_subtitle_repeat()
            self._player.pause()
        else:
            self._stop_subtitle_repeat()
            self._play_media()

    def _on_playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        self.play_btn.setText("暂停" if state == QMediaPlayer.PlaybackState.PlayingState else "播放")

    def _on_duration_changed(self, duration_ms: int) -> None:
        self.position_slider.setRange(0, max(0, duration_ms))
        self._update_time_label(self._player.position(), duration_ms)

    def _on_position_changed(self, position_ms: int) -> None:
        if not self._seeking:
            self.position_slider.blockSignals(True)
            self.position_slider.setValue(position_ms)
            self.position_slider.blockSignals(False)
            if position_ms >= 0:
                self._last_good_position_ms = position_ms
                if self._playback_recovery_attempts and not self._recovering_playback:
                    self._playback_recovery_attempts = 0
            self._maybe_handle_subtitle_repeat(position_ms)
        self._update_time_label(position_ms, self._player.duration())
        self._sync_subtitle_highlight(position_ms / 1000.0)

    def _update_time_label(self, position_ms: int, duration_ms: int) -> None:
        self.time_label.setText(
            f"{self._format_clock(position_ms / 1000)} / {self._format_clock(duration_ms / 1000)}"
        )

    def _sync_subtitle_highlight(self, seconds: float, *, force: bool = False) -> None:
        if not self._segments:
            return
        if not self._subtitle_auto_follow and not force:
            return
        row = find_segment_index_at_time(self._segments, seconds)
        if row < 0 or row == self._current_subtitle_row:
            return
        self._current_subtitle_row = row
        self.subtitle_list.blockSignals(True)
        self.subtitle_list.setCurrentRow(row)
        self.subtitle_list.scrollToItem(
            self.subtitle_list.item(row),
            QListWidget.ScrollHint.PositionAtCenter,
        )
        self.subtitle_list.blockSignals(False)

    def eventFilter(self, obj, event) -> bool:
        if obj in (self._audio_placeholder,):
            if (
                event.type() == QEvent.Type.MouseButtonDblClick
                and event.button() == Qt.MouseButton.LeftButton
            ):
                self._media_area_click_timer.stop()
                self.toggle_maximize_from_video()
                return True
            if (
                event.type() == QEvent.Type.MouseButtonPress
                and event.button() == Qt.MouseButton.LeftButton
                and self._media_path is not None
            ):
                self._media_area_click_timer.start()
                return True
        subtitle_list = getattr(self, "subtitle_list", None)
        if subtitle_list is not None and obj is subtitle_list:
            event_type = event.type()
            if event_type == QEvent.Type.Enter:
                self._pause_subtitle_auto_follow()
            elif event_type == QEvent.Type.Leave:
                QTimer.singleShot(0, self._maybe_resume_subtitle_auto_follow)
            elif event_type in (
                QEvent.Type.Wheel,
                QEvent.Type.MouseButtonPress,
                QEvent.Type.Scroll,
            ):
                self._pause_subtitle_auto_follow()
        return super().eventFilter(obj, event)

    def _on_slider_pressed(self) -> None:
        self._seeking = True
        self._stop_subtitle_repeat()

    def _on_slider_released(self) -> None:
        self._seeking = False
        was_playing = (
            self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        )
        self._stop_subtitle_repeat()
        self._seek_to(self.position_slider.value() / 1000.0, play=was_playing)

    def _on_slider_moved(self, value: int) -> None:
        if self._seeking:
            self._update_time_label(value, self._player.duration())

    def _on_speed_changed(self, index: int) -> None:
        if index < 0:
            return
        rate = self.speed_combo.itemData(index)
        if rate is not None:
            self._saved_playback_rate = float(rate)
            self._player.setPlaybackRate(self._saved_playback_rate)

    def _clamp_position_ms(self, position_ms: int) -> int:
        position_ms = max(0, int(position_ms))
        duration = self._player.duration()
        if duration > 0:
            position_ms = min(position_ms, max(0, duration - 1))
        return position_ms

    def _seek_to(self, seconds: float, *, play: bool | None = None) -> None:
        if self._media_path is None:
            return

        position_ms = self._clamp_position_ms(int(round(float(seconds) * 1000)))
        was_playing = (
            self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        )
        should_play = was_playing if play is None else play
        self._pending_seek_ms = position_ms
        self._pending_play_after_seek = should_play

        status = self._player.mediaStatus()
        needs_reload = (
            self._recovering_playback
            or self._player.error() != QMediaPlayer.Error.NoError
            or status
            in (
                QMediaPlayer.MediaStatus.NoMedia,
                QMediaPlayer.MediaStatus.InvalidMedia,
            )
        )
        if needs_reload:
            self._reload_media_at_position(position_ms, should_play)
            return

        self._seeking = True
        if was_playing:
            self._player.pause()
        self._player.setPosition(position_ms)
        self._seeking = False
        self.position_slider.blockSignals(True)
        self.position_slider.setValue(position_ms)
        self.position_slider.blockSignals(False)
        self._update_time_label(position_ms, self._player.duration())
        self._notify_live_seek(position_ms / 1000.0)
        if should_play:
            QTimer.singleShot(0, self._play_media)

    def _recreate_audio_output(self) -> None:
        volume = self._audio_output.volume()
        muted = self._audio_output.isMuted()
        self._audio_output = QAudioOutput()
        self._audio_output.setVolume(volume)
        self._audio_output.setMuted(muted)
        self._player.setAudioOutput(self._audio_output)
        self._sync_audio_output_device()

    def _reload_media_at_position(self, position_ms: int, should_play: bool) -> None:
        if self._media_path is None:
            return

        self._recovering_playback = True
        self._awaiting_reload_seek = True
        self._pending_seek_ms = self._clamp_position_ms(position_ms)
        self._pending_play_after_seek = should_play
        self._saved_playback_rate = (
            float(self.speed_combo.currentData() or 1.0)
            if hasattr(self, "speed_combo")
            else self._player.playbackRate() or 1.0
        )

        is_audio = self._media_path.suffix.lower() in AUDIO_EXTENSIONS
        self._recreate_audio_output()
        self._player.stop()
        self._player.setSource(QUrl())
        self._player.setSource(QUrl.fromLocalFile(str(self._media_path)))
        if not is_audio:
            self._player.setVideoOutput(self._video_host.video_item)

    def _on_media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        if not self._awaiting_reload_seek:
            return
        if status not in (
            QMediaPlayer.MediaStatus.LoadedMedia,
            QMediaPlayer.MediaStatus.BufferedMedia,
        ):
            if status == QMediaPlayer.MediaStatus.InvalidMedia:
                self._awaiting_reload_seek = False
                self._recovering_playback = False
            return

        self._awaiting_reload_seek = False
        position_ms = self._clamp_position_ms(
            self._pending_seek_ms
            if self._pending_seek_ms is not None
            else self._last_good_position_ms
        )
        should_play = self._pending_play_after_seek
        self._player.setPlaybackRate(self._saved_playback_rate)
        self._seeking = True
        self._player.setPosition(position_ms)
        self._seeking = False
        self.position_slider.blockSignals(True)
        self.position_slider.setValue(position_ms)
        self.position_slider.blockSignals(False)
        self._update_time_label(position_ms, self._player.duration())
        self._notify_live_seek(position_ms / 1000.0)
        self._recovering_playback = False
        if should_play:
            QTimer.singleShot(0, self._play_media)

    @staticmethod
    def _is_recoverable_playback_error(detail: str) -> bool:
        text = detail.lower()
        return any(marker in text for marker in _RECOVERABLE_PLAYBACK_MARKERS)

    def _try_recover_playback(self, detail: str) -> bool:
        if self._media_path is None:
            return False
        if not self._is_recoverable_playback_error(detail):
            return False
        if self._recovering_playback or self._awaiting_reload_seek:
            return True
        if self._playback_recovery_attempts >= _MAX_PLAYBACK_RECOVERY_ATTEMPTS:
            return False

        self._playback_recovery_attempts += 1
        target_ms = (
            self._pending_seek_ms
            if self._pending_seek_ms is not None
            else self._last_good_position_ms
        )
        should_play = self._pending_play_after_seek
        if (
            self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
            or should_play
        ):
            should_play = True
        self._reload_media_at_position(target_ms, should_play)
        return True

    @staticmethod
    def _normalize_device_id(device_id: object) -> bytes | None:
        if device_id is None:
            return None
        if isinstance(device_id, (bytes, bytearray, memoryview)):
            return bytes(device_id)
        try:
            return bytes(device_id)
        except TypeError:
            return None

    def _schedule_audio_device_refresh(self) -> None:
        # Windows may emit audioOutputsChanged in bursts (esp. Bluetooth).
        self._audio_device_refresh_timer.start()

    def _remember_audio_device(self, device: QAudioDevice | None) -> None:
        if device is None or device.isNull():
            return
        self._preferred_audio_device_id = self._normalize_device_id(device.id())
        self._preferred_audio_device_name = device.description().strip()

    def _find_preferred_audio_device(
        self, devices: list[QAudioDevice]
    ) -> QAudioDevice | None:
        preferred_id = self._preferred_audio_device_id
        if preferred_id is not None:
            for device in devices:
                if self._normalize_device_id(device.id()) == preferred_id:
                    return device
        preferred_name = self._preferred_audio_device_name.strip()
        if preferred_name:
            for device in devices:
                if device.description().strip() == preferred_name:
                    return device
        return None

    def _refresh_audio_devices(self) -> None:
        if not hasattr(self, "audio_device_combo"):
            return

        self.audio_device_combo.blockSignals(True)
        self.audio_device_combo.clear()
        devices = list(self._media_devices.audioOutputs())
        default_device = self._media_devices.defaultAudioOutput()
        preferred = self._find_preferred_audio_device(devices)
        selected_index = 0
        for index, device in enumerate(devices):
            label = device.description()
            if device.isDefault():
                label = f"{label}（系统默认）"
            # Store as bytes so QVariant round-trips reliably in PyQt6.
            self.audio_device_combo.addItem(label, self._normalize_device_id(device.id()))
            if preferred is not None and self._normalize_device_id(device.id()) == self._normalize_device_id(
                preferred.id()
            ):
                selected_index = index
            elif preferred is None and self._normalize_device_id(device.id()) == self._normalize_device_id(
                default_device.id()
            ):
                selected_index = index
        self.audio_device_combo.setCurrentIndex(selected_index if devices else -1)
        self.audio_device_combo.blockSignals(False)
        # If the preferred headset briefly disappeared, keep the preference so
        # the next refresh can restore it instead of locking onto the default.
        self._sync_audio_output_device()

    def _selected_audio_device(self) -> QAudioDevice | None:
        devices = list(self._media_devices.audioOutputs())
        preferred = self._find_preferred_audio_device(devices)
        if preferred is not None:
            return preferred
        if hasattr(self, "audio_device_combo"):
            device_id = self._normalize_device_id(self.audio_device_combo.currentData())
            if device_id is not None:
                for device in devices:
                    if self._normalize_device_id(device.id()) == device_id:
                        return device
        return self._media_devices.defaultAudioOutput()

    def _sync_audio_output_device(self) -> None:
        device = self._selected_audio_device()
        if device is None or device.isNull():
            return
        self._audio_output.setDevice(device)

    def _on_audio_device_changed(self, _index: int) -> None:
        if not hasattr(self, "audio_device_combo"):
            return
        device_id = self._normalize_device_id(self.audio_device_combo.currentData())
        if device_id is None:
            return
        for device in self._media_devices.audioOutputs():
            if self._normalize_device_id(device.id()) == device_id:
                self._remember_audio_device(device)
                break
        self._sync_audio_output_device()

    def _play_media(self) -> None:
        self._sync_audio_output_device()
        self._player.play()

    def _on_volume_changed(self, value: int) -> None:
        self._audio_output.setVolume(value / 100.0)
        self.volume_label.setText(f"{value}%")
        if value > 0 and self.mute_btn.isChecked():
            self.mute_btn.blockSignals(True)
            self.mute_btn.setChecked(False)
            self.mute_btn.setText("静音")
            self.mute_btn.blockSignals(False)
            self._audio_output.setMuted(False)

    def _on_brightness_changed(self, value: int) -> None:
        self.brightness_label.setText(f"{value}%")
        self._video_host.set_brightness(value)

    def _on_mute_toggled(self, muted: bool) -> None:
        self._audio_output.setMuted(muted)
        self.mute_btn.setText("取消静音" if muted else "静音")

    def _open_transcribe_tool(self) -> None:
        initial_files = [self._media_path] if self._media_path else []
        if self._transcribe_window is None:
            self._transcribe_window = TranscribeWindow(initial_files=initial_files, parent=self)
            self._transcribe_window.transcription_finished.connect(self._on_transcription_finished)
        else:
            self._transcribe_window.show()
            self._transcribe_window.raise_()
            self._transcribe_window.activateWindow()
            if initial_files:
                self._transcribe_window._add_files_to_table(initial_files)
            return
        self._transcribe_window.show()

    def _on_transcription_finished(self) -> None:
        if self._media_path:
            self._refresh_subtitle_options()
            if self.subtitle_combo.count() > 0:
                self.subtitle_combo.setCurrentIndex(0)
                self._load_subtitle_at_index(0)

    def _on_inference_device_changed(self, _index: int) -> None:
        value = self.inference_combo.currentData()
        if not value or value == self._config.inference_device:
            return
        self._config.inference_device = value
        save_config(self._config)
        clear_model_cache()
        if value == "gpu" and not is_cuda_available():
            QMessageBox.information(
                self,
                "GPU 不可用",
                "当前未安装 CUDA 版 pywhispercpp。\n"
                "请先运行 subtitle_app/安装CUDA推理.bat，否则将自动使用 CPU。",
            )

    def _open_llm_settings(self) -> None:
        self._config = load_config()
        updated = LlmSettingsDialog.open_settings(self._config, self)
        if updated is not None:
            self._config = updated

    def _ensure_llm_configured(self) -> bool:
        self._config = load_config()
        if is_deepseek_configured(self._config):
            return True
        answer = QMessageBox.question(
            self,
            "未配置大模型",
            "AI 笔记需要配置 DeepSeek API Key。\n是否现在打开「大模型配置」？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return False
        updated = LlmSettingsDialog.open_settings(self._config, self)
        if updated is None:
            return False
        self._config = updated
        return is_deepseek_configured(self._config)

    def _generate_ai_notes(self) -> None:
        if not self._media_path:
            QMessageBox.information(self, "提示", "请先打开媒体文件。")
            return
        if self._ai_notes_worker and self._ai_notes_worker.isRunning():
            QMessageBox.information(self, "提示", "AI 笔记正在生成中，请稍候。")
            return
        if not self._ensure_llm_configured():
            return

        notes_path = build_notes_output_path(self._media_path)
        overwrite_hint = (
            f"\n\n注意：将覆盖已有笔记文件「{notes_path.name}」。"
            if notes_path.is_file()
            else ""
        )
        answer = QMessageBox.question(
            self,
            "确认生成 AI 笔记",
            (
                f"即将为「{self._media_path.name}」生成 AI 笔记。\n"
                f"• 将调用 DeepSeek API（可能产生费用）\n"
                f"• 保存至：{notes_path.name}"
                f"{overwrite_hint}\n\n"
                "下一步可在弹出窗口中选择字幕类型并确认 Prompt。\n\n"
                "是否继续？"
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        corpus_items = collect_valid_subtitle_corpus(self._media_path)
        if not corpus_items:
            QMessageBox.warning(self, "无法生成", "未找到有效字幕文件，无法生成笔记。")
            return

        corpus_text = corpus_to_text(corpus_items)
        edit_result = AiNotesCorpusDialog.edit_prompt(
            self._media_path.name,
            corpus_text,
            self._config.ai_notes_subtitle_type,
            subcategories=dict(self._config.ai_notes_subcategory),
            user_contexts=dict(self._config.ai_notes_user_context),
            parent=self,
        )
        if edit_result is None:
            return

        self._config.ai_notes_subtitle_type = edit_result.subtitle_type
        self._config.ai_notes_subcategory = dict(edit_result.subcategory_by_type)
        self._config.ai_notes_user_context = dict(edit_result.user_context_by_type)
        self._config.ai_notes_template = edit_result.subtitle_type
        save_config(self._config)

        self._action_ai_notes.setEnabled(False)
        self._update_live_status("AI 笔记：准备中…")
        self._show_ai_notes_progress()

        self._ai_notes_worker = AiNotesWorker(
            self._media_path,
            self._config,
            messages=edit_result.messages,
            template_id=edit_result.subtitle_type,
            subcategory=edit_result.subcategory_by_type.get(edit_result.subtitle_type, ""),
        )
        self._ai_notes_worker.status_changed.connect(self._on_ai_notes_status)
        self._ai_notes_worker.finished_ok.connect(self._on_ai_notes_finished)
        self._ai_notes_worker.failed.connect(self._on_ai_notes_failed)
        self._ai_notes_worker.start()

    def _current_subtitle_source(self) -> tuple[str, str] | None:
        if self.subtitle_combo.count() <= 0:
            return None
        index = self.subtitle_combo.currentIndex()
        if index < 0:
            return None
        label = self.subtitle_combo.currentText()
        path_value = self.subtitle_combo.itemData(index)
        if path_value == "__live__":
            filename = f"{self._media_path.stem}.srt.partial" if self._media_path else "live.srt.partial"
            return label, filename
        if path_value:
            return label, Path(str(path_value)).name
        return label, label

    def _export_plain_text(self) -> None:
        if not self._media_path:
            QMessageBox.information(self, "提示", "请先打开媒体文件。")
            return
        if not self._segments:
            QMessageBox.information(self, "提示", "当前没有可导出的字幕。请先加载或生成字幕。")
            return

        source = self._current_subtitle_source()
        subtitle_label = source[0] if source else "字幕"
        subtitle_filename = source[1] if source else "subtitle.srt"

        options = SubtitleTextDialog.get_options(subtitle_label, self)
        if options is None:
            return

        try:
            media_duration = self._player.duration() / 1000.0
            if media_duration <= 0:
                media_duration = None
            output_path = export_plain_text_markdown(
                self._media_path,
                self._segments,
                subtitle_label=subtitle_label,
                subtitle_filename=subtitle_filename,
                options=options,
                media_duration_seconds=media_duration,
            )
        except Exception as exc:
            QMessageBox.warning(self, "纯文字导出失败", str(exc))
            return

        QMessageBox.information(
            self,
            "纯文字版已生成",
            f"Markdown：\n{output_path}",
        )
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(output_path.resolve())))

    def _generate_vocabulary_list(self) -> None:
        if not self._media_path:
            QMessageBox.information(self, "提示", "请先打开媒体文件。")
            return
        if self._vocabulary_worker and self._vocabulary_worker.isRunning():
            QMessageBox.information(self, "提示", "生词表正在生成中，请稍候。")
            return

        options = VocabularyDialog.get_options(self)
        if options is None:
            return

        self._action_vocabulary.setEnabled(False)
        self._update_live_status("生词表：分析中…")

        self._vocabulary_worker = VocabularyWorker(self._media_path, options, self._config)
        self._vocabulary_worker.status_changed.connect(self._on_vocabulary_status)
        self._vocabulary_worker.finished_ok.connect(self._on_vocabulary_finished)
        self._vocabulary_worker.failed.connect(self._on_vocabulary_failed)
        self._vocabulary_worker.start()

    def _on_vocabulary_status(self, message: str) -> None:
        self._update_live_status(f"生词表：{message}")

    def _on_vocabulary_finished(self, markdown_path: str, csv_path: str) -> None:
        self._action_vocabulary.setEnabled(True)
        self._vocabulary_worker = None
        self._update_live_status("")
        QMessageBox.information(
            self,
            "生词表已生成",
            f"Markdown：\n{markdown_path}\n\nCSV：\n{csv_path}",
        )
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(markdown_path).resolve())))

    def _on_vocabulary_failed(self, message: str) -> None:
        self._action_vocabulary.setEnabled(True)
        self._vocabulary_worker = None
        self._update_live_status("")
        QMessageBox.warning(self, "生词表生成失败", message)

    def _stop_vocabulary_worker(self) -> None:
        if self._vocabulary_worker and self._vocabulary_worker.isRunning():
            self._vocabulary_worker.cancel()
            self._vocabulary_worker.wait(3000)
        self._vocabulary_worker = None
        if hasattr(self, "_action_vocabulary"):
            self._action_vocabulary.setEnabled(True)

    def _show_ai_notes_progress(self) -> None:
        self._close_ai_notes_progress()
        if not self._media_path:
            return
        self._ai_notes_progress = AiNotesProgressDialog(self._media_path.name, self)
        self._ai_notes_progress.start()
        self._ai_notes_progress.show()

    def _close_ai_notes_progress(self) -> None:
        if self._ai_notes_progress is not None:
            self._ai_notes_progress.close()
            self._ai_notes_progress.deleteLater()
            self._ai_notes_progress = None

    def _on_ai_notes_status(self, message: str) -> None:
        self._update_live_status(f"AI 笔记：{message}")
        if self._ai_notes_progress is not None:
            self._ai_notes_progress.set_status(message)

    def _on_ai_notes_finished(self, output_path: str) -> None:
        self._action_ai_notes.setEnabled(True)
        self._ai_notes_worker = None
        self._update_live_status("")
        self._update_notes_buttons()
        if self._ai_notes_progress is not None:
            self._ai_notes_progress.finish_success()
            QTimer.singleShot(700, self._close_ai_notes_progress_and_notify_success)
            self._pending_notes_output = output_path
        else:
            QMessageBox.information(
                self,
                "AI 笔记已生成",
                f"笔记已保存至：\n{output_path}",
            )

    def _close_ai_notes_progress_and_notify_success(self) -> None:
        output_path = self._pending_notes_output
        self._pending_notes_output = ""
        self._close_ai_notes_progress()
        if output_path:
            QMessageBox.information(
                self,
                "AI 笔记已生成",
                f"笔记已保存至：\n{output_path}",
            )

    def _on_ai_notes_failed(self, message: str) -> None:
        self._action_ai_notes.setEnabled(True)
        self._ai_notes_worker = None
        self._update_live_status("")
        hint = ""
        if "API Key" in message:
            hint = f"\n\n请编辑配置文件填写密钥：\n{CONFIG_PATH}"
        full_message = f"{message}{hint}"
        if self._ai_notes_progress is not None:
            self._ai_notes_progress.finish_failure(message)
            self._pending_notes_output = full_message
            QTimer.singleShot(500, self._close_ai_notes_progress_and_notify_failure)
        else:
            QMessageBox.warning(self, "AI 笔记生成失败", full_message)

    def _close_ai_notes_progress_and_notify_failure(self) -> None:
        message = self._pending_notes_output
        self._pending_notes_output = ""
        self._close_ai_notes_progress()
        if message:
            QMessageBox.warning(self, "AI 笔记生成失败", message)

    def _stop_ai_notes_worker(self) -> None:
        if self._ai_notes_worker and self._ai_notes_worker.isRunning():
            self._ai_notes_worker.cancel()
            self._ai_notes_worker.wait(3000)
        self._ai_notes_worker = None
        self._action_ai_notes.setEnabled(True)
        self._close_ai_notes_progress()

    def _on_player_error(self, error: QMediaPlayer.Error, message: str = "") -> None:
        if error == QMediaPlayer.Error.NoError:
            return
        detail = message or self._player.errorString() or "未知错误"
        if self._try_recover_playback(detail):
            return
        now = time.monotonic()
        if now < self._error_dialog_suppressed_until:
            return
        self._error_dialog_suppressed_until = now + _ERROR_DIALOG_COOLDOWN_SEC
        QMessageBox.warning(
            self,
            "媒体播放失败",
            f"无法播放该文件：\n{detail}\n\n"
            "可尝试：重新打开该媒体文件后再点击字幕跳转；"
            "若正在边播边转，请稍等转写释放文件后再试。\n"
            "若为视频黑屏，请确认已安装 Windows「HEVC 视频扩展」或「媒体功能包」。",
        )

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        for url in event.mimeData().urls():
            if not url.isLocalFile():
                continue
            path = Path(url.toLocalFile())
            if path.suffix.lower() in MEDIA_EXTENSIONS:
                self.load_media(path)
                break
        event.acceptProposedAction()

    def closeEvent(self, event) -> None:
        self._study_countdown_timer.stop()
        self._stop_subtitle_repeat()
        self._persist_last_media_dir()
        self._stop_live_transcribe()
        self._stop_ai_notes_worker()
        self._stop_vocabulary_worker()
        self._player.stop()
        if self._transcribe_window is not None:
            self._transcribe_window.close()
        super().closeEvent(event)
