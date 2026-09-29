from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from PyQt6.QtCore import QEvent, QSize, Qt, QTimer, QUrl
from PyQt6.QtGui import (
    QAction,
    QColor,
    QDesktopServices,
    QDragEnterEvent,
    QDropEvent,
    QGuiApplication,
    QIcon,
    QPainter,
    QPixmap,
)
from PyQt6.QtMultimedia import (
    QAudioDevice,
    QAudioOutput,
    QMediaDevices,
    QMediaPlayer,
    QPlaybackOptions,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSlider,
    QSplitter,
    QStackedWidget,
    QStyle,
    QTextEdit,
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
# 短于这个进度不记成“上次播放位置”，再次打开时停在第一帧。
_PLAYBACK_RESUME_MIN_MS = 2000
# 离结尾太近视为已经看完，下次从第一帧开始。
_PLAYBACK_END_MARGIN_MS = 3000
_PLAYBACK_POSITION_LIMIT = 200
_RESTART_LINK_VISIBLE_MS = 5000
_OPEN_FRAME_WAIT_MS = 350
_OPEN_FRAME_NUDGE_MS = 2000

from core.ai_notes import (
    build_notes_output_path,
    collect_valid_subtitle_corpus,
    corpus_to_text,
    find_notes_path,
)
from core.ai_notes_worker import AiNotesWorker
from core.subtitle_text_export import export_plain_text_markdown
from core.vocabulary_worker import VocabularyWorker
from core.app_paths import app_dir, is_frozen
from core.config import CONFIG_PATH, INFERENCE_DEVICE_OPTIONS, MEDIA_EXTENSIONS, is_deepseek_configured, load_config, save_config
from core.console_window import (
    add_console_visibility_listener,
    console_button_label,
    focus_window_by_title,
    reveal_console_on_error,
    spawn_hidden_console_process,
    toggle_console,
)
from core.external_translate import (
    get_translator,
    is_translator_running,
    normalize_hotkey_text,
    send_hotkey,
)
from core.console_window import (
    add_console_visibility_listener,
    console_button_label,
    focus_window_by_title,
    reveal_console_on_error,
    spawn_hidden_console_process,
    toggle_console,
)
from core.transcriber import clear_model_cache, is_cuda_available
from core.subtitle import SubtitleSegment, find_segment_index_at_time, write_subtitle_file
from core.cloud_sync import (
    DEFAULT_CLOUD_SERVER,
    apply_shared_download,
    apply_subtitle_download,
    apply_tag_download,
    classify_subtitles,
    collect_folder_subtitles,
    collect_folder_tags,
    collect_media_subtitles,
    collect_media_tags,
    fetch_remote_index,
    inspect_open_media,
    load_account_client,
    upload_subtitles,
    upload_tags,
)
from core.subtitle_tags import (
    SubtitleTagDocument,
    SubtitleTagEntry,
    assign_tags,
    entry_from_segment,
    custom_tags_for_media,
    load_tag_document,
    order_tags,
    PRESET_TAGS,
    retire_tag_entry,
    save_tag_document,
    set_entry_note,
    set_entry_tags,
    snapshot_entry,
    stamp_new_entry,
    sync_tag_file_into_folder,
    tag_path_for_subtitle,
)
from core.subtitle_loader import find_valid_subtitles, load_subtitles
from core.video_hash import video_content_hash
from gui.cloud_account_dialog import CloudAccountDialog
from gui.cloud_sync_ui import (
    CloudTask,
    LeftSubmenu,
    choose_shared_subtitle,
    confirm_share_upload,
    confirm_subtitle_download,
    confirm_subtitle_replace,
    confirm_tag_download,
)
from gui.batch_tag_sync_dialog import BatchTagSyncDialog
from gui.extract_tags_dialog import ExtractTagsDialog
from gui.ai_notes_corpus_dialog import AiNotesCorpusDialog
from gui.ai_notes_progress_dialog import AiNotesProgressDialog
from gui.llm_settings_dialog import LlmSettingsDialog
from gui.main_window import WINDOW_TITLE as TRANSCRIBE_WINDOW_TITLE
from gui.onscreen_subtitle import (
    MediaViewport,
    OnScreenSubtitleSettingsDialog,
    OnScreenSubtitleStyle,
    SubtitleVideoWidget,
    apply_onscreen_style_to_config,
    immersive_list_pixel_width,
)
from gui.styles import DARK_STYLE, PLAYER_LIST_STYLE, build_immersive_subtitle_panel_style
from gui.tag_filter_bar import TagFilterBar
from gui.subtitle_edit_dialog import SubtitleEditDialog
from gui.subtitle_list_delegate import PAYLOAD_ROLE, SubtitleListDelegate
from gui.subtitle_note_popup import NotePreviewPopup, SubtitleNoteDialog
from gui.subtitle_tag_dialog import SubtitleTagDialog
from gui.subtitle_text_dialog import SubtitleTextDialog
from gui.translate_hotkey import HotkeyLineEdit, TranslateHotkeyHelpDialog
from gui.vocabulary_dialog import VocabularyDialog, VocabularyProgressDialog

AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma", ".opus"}


class PlayerWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self._media_path: Path | None = None
        self._segments: list[SubtitleSegment] = []
        self._current_subtitle_row = -1
        self._seeking = False
        self._subtitle_auto_follow = True
        self._subtitle_menu_open = False
        self._transcribe_proc: subprocess.Popen | None = None
        self._video_download_proc: subprocess.Popen | None = None
        self._ai_notes_worker: AiNotesWorker | None = None
        self._ai_notes_progress: AiNotesProgressDialog | None = None
        self._pending_notes_output = ""
        self._vocabulary_worker: VocabularyWorker | None = None
        self._vocabulary_progress: VocabularyProgressDialog | None = None
        self._pending_vocabulary_paths: tuple[str, str] | None = None
        self._pending_vocabulary_error = ""
        self._config = load_config()
        self._media_area_click_timer = QTimer(self)
        self._media_area_click_timer.setSingleShot(True)
        self._media_area_click_timer.setInterval(SubtitleVideoWidget._SINGLE_CLICK_MS)
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
        self._open_pause_pending = False
        self._open_pause_position_ms = 0
        self._open_pause_applied_ms = 0
        self._holding_open_pause = False
        self._open_frame_nudge = False
        self._open_preview_should_pause = False
        self._open_nudge_audio_overridden = False
        self._open_nudge_restore_muted = False
        self._play_after_open_pause = False
        self._open_pause_ui_done = True
        self._open_frame_seen = False
        self._open_nudge_timer = QTimer(self)
        self._open_nudge_timer.setSingleShot(True)
        self._open_nudge_timer.setInterval(_OPEN_FRAME_NUDGE_MS)
        self._open_nudge_timer.timeout.connect(self._on_open_nudge_timeout)
        self._restart_link_timer = QTimer(self)
        self._restart_link_timer.setSingleShot(True)
        self._restart_link_timer.setInterval(_RESTART_LINK_VISIBLE_MS)
        self._restart_link_timer.timeout.connect(self._hide_restart_from_start_link)
        self._playback_save_timer = QTimer(self)
        self._playback_save_timer.setSingleShot(True)
        self._playback_save_timer.setInterval(3000)
        self._playback_save_timer.timeout.connect(self._remember_playback_position)
        self._transcribe_poll_timer = QTimer(self)
        self._transcribe_poll_timer.setInterval(1500)
        self._transcribe_poll_timer.timeout.connect(self._poll_transcribe_tool)
        self._immersive_list_active = False
        self._subtitle_list_visible = True
        self._splitter_sizes_before_list_hidden: list[int] | None = None
        self._splitter_sizes_before_immersive: list[int] | None = None
        self._immersive_list_opacity = float(self._config.immersive_subtitle_list_opacity)
        self._immersive_list_side = str(self._config.immersive_subtitle_list_side or "right")
        self._immersive_list_width_percent = int(
            self._config.immersive_subtitle_list_width_percent or 36
        )
        self._immersive_resizing = False
        self._immersive_resize_start_x = 0
        self._immersive_resize_start_width = 0
        self._tag_document = SubtitleTagDocument()
        self._tag_path: Path | None = None
        self._tag_by_row: dict[int, SubtitleTagEntry] = {}
        self._tag_unmatched: list[SubtitleTagEntry] = []
        self._tag_filter_mode = "all"
        self._selected_tag_filters: set[str] = set()
        self._tag_filter_buttons: dict[str, QPushButton] = {}
        self._subtitle_list_layout_width = -1

        self.setWindowTitle("字幕播放器")
        self.setMinimumSize(1000, 640)
        self.resize(1180, 720)
        self.setAcceptDrops(True)
        self.setStyleSheet(DARK_STYLE + PLAYER_LIST_STYLE)

        self._player = QMediaPlayer()
        playback_options = QPlaybackOptions()
        playback_options.setPlaybackIntent(QPlaybackOptions.PlaybackIntent.Playback)
        self._player.setPlaybackOptions(playback_options)
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

        open_btn = QPushButton("打开文件")
        open_btn.setToolTip("打开媒体文件，或从最近使用的文件中选择")
        self._open_file_menu = QMenu(self)
        self._open_file_menu.aboutToShow.connect(self._rebuild_open_file_menu)
        open_btn.setMenu(self._open_file_menu)
        bar.addWidget(open_btn)

        bar.addWidget(QLabel("字幕"))
        self.subtitle_combo = QComboBox()
        self.subtitle_combo.setMinimumWidth(180)
        self.subtitle_combo.currentIndexChanged.connect(self._on_subtitle_selected)
        bar.addWidget(self.subtitle_combo, stretch=1)

        self.media_label = QLabel("未加载媒体文件")
        self.media_label.setObjectName("hintLabel")
        bar.addWidget(self.media_label)

        self._open_dir_btn = QPushButton("打开目录")
        self._open_dir_btn.setToolTip("打开当前媒体文件所在的文件夹")
        self._open_dir_btn.setEnabled(False)
        self._open_dir_btn.clicked.connect(self._open_media_directory)
        bar.addWidget(self._open_dir_btn)

        bar.addStretch(1)

        onscreen_btn = QPushButton("画面字幕")
        onscreen_btn.setToolTip("调整画面叠加字幕的显示、字号、颜色、底条透明度、宽度与位置")
        onscreen_btn.clicked.connect(self._open_onscreen_subtitle_settings)
        bar.addWidget(onscreen_btn)

        tools_menu = QMenu(self)
        action_transcribe = tools_menu.addAction("音视频转字幕")
        action_transcribe.triggered.connect(self._open_transcribe_tool)
        action_download = tools_menu.addAction("视频下载")
        action_download.triggered.connect(self._open_video_download_tool)
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
        upload_subtitle_menu = LeftSubmenu("上传字幕文件 ◀", tools_menu)
        upload_subtitle_menu.addAction("上传当前字幕").triggered.connect(self._upload_current_subtitles)
        upload_subtitle_menu.addAction("批量上传字幕").triggered.connect(self._upload_folder_subtitles)
        tools_menu.addMenu(upload_subtitle_menu)
        local_tag_menu = LeftSubmenu("本地标签同步 ◀", tools_menu)
        local_tag_menu.addAction("同步标签文件").triggered.connect(self._sync_tag_files)
        local_tag_menu.addAction("批量同步标签").triggered.connect(self._batch_sync_tag_files)
        local_tag_menu.addAction("提取全部标签").triggered.connect(self._extract_all_tags)
        tools_menu.addMenu(local_tag_menu)
        upload_tag_menu = LeftSubmenu("上传标签文件 ◀", tools_menu)
        upload_tag_menu.addAction("上传当前标签").triggered.connect(self._upload_current_tags)
        upload_tag_menu.addAction("批量上传标签").triggered.connect(self._upload_folder_tags)
        tools_menu.addMenu(upload_tag_menu)
        tools_menu.addSeparator()
        self._action_console = tools_menu.addAction(console_button_label())
        self._action_console.setToolTip("显示或隐藏后台命令窗口")
        self._action_console.triggered.connect(toggle_console)
        add_console_visibility_listener(self._on_console_visibility_changed)

        tools_btn = self._create_toolbar_menu_button(
            "工具",
            style.standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView),
            "转写、下载、笔记与字幕工具",
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
        inference_layout.addWidget(self._settings_field_label("推理设备"))
        self.inference_combo = QComboBox()
        self.inference_combo.setMinimumWidth(180)
        if is_frozen():
            self.inference_combo.addItem("CPU", "cpu")
            self.inference_combo.setEnabled(False)
            self.inference_combo.setToolTip("当前安装包使用 CPU 转字幕。")
        else:
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
        output_layout.addWidget(self._settings_field_label("输出设备"))
        self.audio_device_combo = QComboBox()
        self.audio_device_combo.setMinimumWidth(220)
        self.audio_device_combo.currentIndexChanged.connect(self._on_audio_device_changed)
        output_layout.addWidget(self.audio_device_combo, stretch=1)
        output_action = QWidgetAction(self)
        output_action.setDefaultWidget(output_widget)
        settings_menu.addAction(output_action)
        self._refresh_audio_devices()

        translator = get_translator(self._config.translate_app)
        hotkey_widget = QWidget()
        hotkey_layout = QHBoxLayout(hotkey_widget)
        hotkey_layout.setContentsMargins(12, 6, 12, 6)
        hotkey_layout.addWidget(self._settings_field_label("翻译热键"))
        self.translate_hotkey_edit = HotkeyLineEdit()
        self.translate_hotkey_edit.setMinimumWidth(140)
        self.translate_hotkey_edit.setText(self._config.translate_hotkey or translator.default_hotkey)
        self.translate_hotkey_edit.setPlaceholderText(translator.default_hotkey)
        self.translate_hotkey_edit.setToolTip(
            "需与翻译软件中的「快捷键发起翻译」一致。点击后按下组合键，或直接输入，例如 Ctrl+Alt+C。"
        )
        self.translate_hotkey_edit.hotkeyEdited.connect(self._on_translate_hotkey_changed)
        hotkey_layout.addWidget(self.translate_hotkey_edit, stretch=1)
        help_btn = QPushButton("说明")
        help_btn.setAutoDefault(False)
        help_btn.setDefault(False)
        help_btn.setToolTip("查看翻译快捷键的设置方法")
        help_btn.clicked.connect(self._open_translate_hotkey_help)
        hotkey_layout.addWidget(help_btn)
        hotkey_action = QWidgetAction(self)
        hotkey_action.setDefaultWidget(hotkey_widget)
        settings_menu.addAction(hotkey_action)

        settings_menu.addSeparator()
        action_cloud_account = settings_menu.addAction("用户设置")
        action_cloud_account.triggered.connect(self._open_cloud_account_settings)
        self._settings_menu = settings_menu

        settings_btn = self._create_toolbar_menu_button(
            "设置",
            style.standardIcon(QStyle.StandardPixmap.SP_FileDialogInfoView),
            "应用与模型设置",
        )
        settings_btn.setMenu(settings_menu)
        bar.addWidget(settings_btn)
        return bar

    @staticmethod
    def _settings_field_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("settingsFieldLabel")
        label.setStyleSheet("color: #ffffff;")
        return label

    def _on_console_visibility_changed(self, visible: bool) -> None:
        def apply() -> None:
            if not hasattr(self, "_action_console"):
                return
            self._action_console.setText("隐藏后台" if visible else "查看后台")

        QTimer.singleShot(0, apply)

    def _error_box(self, title: str, text: str) -> None:
        reveal_console_on_error(f"{title}: {text}")
        QMessageBox.warning(self, title, text)

    def _build_main_splitter(self) -> QSplitter:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self._content_splitter = splitter

        self._media_host = QWidget()
        self._media_host.setObjectName("mediaHost")
        media_host_layout = QVBoxLayout(self._media_host)
        media_host_layout.setContentsMargins(0, 0, 0, 0)
        media_host_layout.setSpacing(0)

        video_widget = SubtitleVideoWidget()
        video_widget.set_toggle_callback(self.toggle_playback_from_video)
        video_widget.set_double_click_callback(self.toggle_maximize_from_video)
        self._player.setVideoSink(video_widget.video_sink())
        video_widget.video_sink().videoFrameChanged.connect(self._on_preview_video_frame)

        audio_placeholder = QLabel("音频播放中")
        audio_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        audio_placeholder.setMinimumHeight(280)
        audio_placeholder.setStyleSheet(
            "background-color: #2b2b2b; border: 1px solid rgba(255,255,255,0.2);"
            "font-size: 22px; color: #b980ff;"
        )
        audio_placeholder.hide()

        self._media_viewport = MediaViewport(video_widget, audio_placeholder)
        self._video_widget = video_widget
        self._audio_placeholder = audio_placeholder
        self._onscreen_overlay = self._media_viewport.overlay
        self._onscreen_overlay.set_style(OnScreenSubtitleStyle.from_config(self._config))
        media_host_layout.addWidget(self._media_viewport, stretch=1)

        self._subtitle_panel = QWidget()
        self._subtitle_panel.setObjectName("subtitlePanel")
        right_layout = QVBoxLayout(self._subtitle_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        header.setSpacing(6)
        self._subtitle_panel_title = QLabel("字幕列表（点击跳转）")
        header.addWidget(self._subtitle_panel_title)
        self._subtitle_density_btn = QPushButton()
        self._subtitle_density_btn.setObjectName("subtitleDensityButton")
        self._subtitle_density_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._subtitle_density_btn.clicked.connect(self._toggle_subtitle_list_density)
        header.addWidget(self._subtitle_density_btn)
        self.live_status_label = QLabel("")
        self.live_status_label.setObjectName("hintLabel")
        header.addStretch(1)
        header.addWidget(self.live_status_label)
        right_layout.addLayout(header)

        self._tag_bar = TagFilterBar()
        self._tag_all_btn = QPushButton("全部")
        self._tag_all_btn.setObjectName("subtitleTagButton")
        self._tag_all_btn.setCheckable(True)
        self._tag_all_btn.setChecked(True)
        self._tag_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._tag_all_btn.clicked.connect(lambda _checked=False: self._set_tag_filter("all"))
        self._tag_prev_btn = QPushButton("上一条")
        self._tag_next_btn = QPushButton("下一条")
        for button in (self._tag_prev_btn, self._tag_next_btn):
            button.setObjectName("subtitleTagButton")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._tag_prev_btn.clicked.connect(lambda _checked=False: self._jump_tagged_row(-1))
        self._tag_next_btn.clicked.connect(lambda _checked=False: self._jump_tagged_row(1))
        self._tag_bar.set_controls([self._tag_all_btn, self._tag_prev_btn, self._tag_next_btn])
        self._tag_bar.hide()
        right_layout.addWidget(self._tag_bar)

        self._tag_unmatched_banner = QPushButton()
        self._tag_unmatched_banner.setObjectName("subtitleTagBanner")
        self._tag_unmatched_banner.setCursor(Qt.CursorShape.PointingHandCursor)
        self._tag_unmatched_banner.clicked.connect(
            lambda _checked=False: self._set_tag_filter("unmatched")
        )
        self._tag_unmatched_banner.hide()
        right_layout.addWidget(self._tag_unmatched_banner)

        self.subtitle_list = QListWidget()
        self.subtitle_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.subtitle_list.setWordWrap(True)
        self.subtitle_list.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.subtitle_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.subtitle_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.subtitle_list.customContextMenuRequested.connect(self._on_subtitle_context_menu)
        self.subtitle_list.itemClicked.connect(self._on_subtitle_clicked)
        self._subtitle_delegate = SubtitleListDelegate(self.subtitle_list)
        self._subtitle_delegate.noteActivated.connect(
            lambda index: self._open_note_from_list(self.subtitle_list, index)
        )
        self.subtitle_list.setItemDelegate(self._subtitle_delegate)
        self.subtitle_list.installEventFilter(self)
        self.subtitle_list.viewport().installEventFilter(self)
        self.subtitle_list.viewport().setMouseTracking(True)

        self._unmatched_list = QListWidget()
        self._unmatched_list.setObjectName("unmatchedTagList")
        self._unmatched_list.setWordWrap(True)
        self._unmatched_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._unmatched_delegate = SubtitleListDelegate(self._unmatched_list)
        self._unmatched_delegate.noteActivated.connect(
            lambda index: self._open_note_from_list(self._unmatched_list, index)
        )
        self._unmatched_list.setItemDelegate(self._unmatched_delegate)
        self._unmatched_list.viewport().installEventFilter(self)
        self._unmatched_list.viewport().setMouseTracking(True)
        self._note_preview = NotePreviewPopup()
        self._note_preview.open_requested.connect(self._open_hovered_note_editor)
        self._note_preview.installEventFilter(self)
        self._note_hover_entry_id = ""
        self._ignore_note_row_click = False
        self._note_preview_hide_timer = QTimer(self)
        self._note_preview_hide_timer.setSingleShot(True)
        self._note_preview_hide_timer.setInterval(250)
        self._note_preview_hide_timer.timeout.connect(self._hide_note_preview_if_idle)
        self._unmatched_list.itemClicked.connect(self._on_unmatched_tag_clicked)
        self._unmatched_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._unmatched_list.customContextMenuRequested.connect(self._on_unmatched_context_menu)

        self._subtitle_stack = QStackedWidget()
        self._subtitle_stack.addWidget(self.subtitle_list)
        self._subtitle_stack.addWidget(self._unmatched_list)
        right_layout.addWidget(self._subtitle_stack, stretch=1)
        self._apply_subtitle_list_density(persist=False)

        # Staging editor so external “快捷键发起翻译” can see a text selection.
        self._translate_staging = QTextEdit(self)
        self._translate_staging.setWindowFlags(
            Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
        )
        self._translate_staging.resize(320, 180)
        self._translate_staging.hide()

        self._immersive_resize_handle = QWidget(self._media_host)
        self._immersive_resize_handle.setObjectName("immersiveResizeHandle")
        self._immersive_resize_handle.setCursor(Qt.CursorShape.SizeHorCursor)
        self._immersive_resize_handle.setFixedWidth(6)
        self._immersive_resize_handle.setToolTip("拖动调节字幕列表宽度")
        self._immersive_resize_handle.hide()
        self._immersive_resize_handle.installEventFilter(self)

        splitter.addWidget(self._media_host)
        splitter.addWidget(self._subtitle_panel)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        self._audio_placeholder.installEventFilter(self)
        self._media_host.installEventFilter(self)

        # Restore immersive list mode from config after widgets exist.
        QTimer.singleShot(
            0,
            lambda: self._apply_immersive_from_style(
                OnScreenSubtitleStyle.from_config(self._config)
            ),
        )
        return splitter

    def _apply_immersive_from_style(self, style: OnScreenSubtitleStyle) -> None:
        self._set_immersive_subtitle_list(
            style.immersive_list,
            style.immersive_list_opacity,
            side=style.immersive_list_side,
            width_percent=style.immersive_list_width_percent,
        )
        self._set_subtitle_list_visible(style.subtitle_list_visible)

    def _set_subtitle_list_visible(self, visible: bool) -> None:
        """隐藏或恢复字幕列表。不写入配置，打开新文件时会重新显示。"""
        if not hasattr(self, "_subtitle_panel") or not hasattr(self, "_content_splitter"):
            return
        visible = bool(visible)
        was_visible = bool(getattr(self, "_subtitle_list_visible", True))
        self._subtitle_list_visible = visible
        if not visible:
            if was_visible and not self._immersive_list_active:
                sizes = self._content_splitter.sizes()
                if len(sizes) >= 2 and sizes[1] > 0:
                    self._splitter_sizes_before_list_hidden = list(sizes)
            self._subtitle_panel.hide()
            self._immersive_resize_handle.hide()
            if not self._immersive_list_active:
                total = max(self._content_splitter.width(), 1)
                self._content_splitter.setSizes([total, 0])
            self._sync_onscreen_immersive_metrics()
            return

        if self._immersive_list_active:
            self._layout_immersive_list_panel()
            return
        self._subtitle_panel.show()
        restored = self._splitter_sizes_before_list_hidden
        if restored and len(restored) >= 2 and sum(restored) > 0:
            self._content_splitter.setSizes(restored)
        elif not was_visible:
            total = max(self._content_splitter.width(), 800)
            self._content_splitter.setSizes([int(total * 0.62), int(total * 0.38)])
        self._sync_onscreen_immersive_metrics()

    def _set_immersive_subtitle_list(
        self,
        enabled: bool,
        opacity: float | None = None,
        *,
        side: str | None = None,
        width_percent: int | None = None,
    ) -> None:
        """沉浸列表：画面铺满，字幕列表半透明叠在左侧或右侧。"""
        if not hasattr(self, "_content_splitter") or not hasattr(self, "_subtitle_panel"):
            return
        enabled = bool(enabled)
        if opacity is None:
            opacity = float(getattr(self, "_immersive_list_opacity", 0.28))
        opacity = max(0.0, min(1.0, float(opacity)))
        self._immersive_list_opacity = opacity

        if side is not None:
            side_norm = str(side).strip().lower()
            self._immersive_list_side = side_norm if side_norm in {"left", "right"} else "right"
        if width_percent is not None:
            try:
                self._immersive_list_width_percent = max(18, min(70, int(width_percent)))
            except (TypeError, ValueError):
                self._immersive_list_width_percent = 36

        if self._immersive_list_active == enabled:
            if enabled and self._subtitle_list_visible:
                self._apply_immersive_list_chrome(True, opacity)
                self._layout_immersive_list_panel()
            elif enabled:
                self._apply_immersive_list_chrome(True, opacity)
                self._subtitle_panel.hide()
                self._immersive_resize_handle.hide()
                self._sync_onscreen_immersive_metrics()
            return

        if enabled:
            sizes = self._content_splitter.sizes()
            if len(sizes) >= 2 and sizes[1] > 0:
                self._splitter_sizes_before_immersive = list(sizes)
            self._subtitle_panel.setParent(self._media_host)
            self._apply_immersive_list_chrome(True, opacity)
            self._immersive_list_active = True
            if self._subtitle_list_visible:
                self._subtitle_panel.show()
                self._layout_immersive_list_panel()
                self._subtitle_panel.raise_()
                self._immersive_resize_handle.raise_()
            else:
                self._subtitle_panel.hide()
                self._immersive_resize_handle.hide()
                self._sync_onscreen_immersive_metrics()
        else:
            self._immersive_resizing = False
            if QWidget.mouseGrabber() is self._immersive_resize_handle:
                self._immersive_resize_handle.releaseMouse()
            self._immersive_resize_handle.hide()
            self._apply_immersive_list_chrome(False)
            self._content_splitter.addWidget(self._subtitle_panel)
            self._immersive_list_active = False
            if self._subtitle_list_visible:
                restored = self._splitter_sizes_before_immersive
                if restored and len(restored) >= 2 and sum(restored) > 0:
                    self._content_splitter.setSizes(restored)
                else:
                    total = max(self._content_splitter.width(), 800)
                    self._content_splitter.setSizes([int(total * 0.62), int(total * 0.38)])
                self._subtitle_panel.show()
            else:
                self._subtitle_panel.hide()
                total = max(self._content_splitter.width(), 1)
                self._content_splitter.setSizes([total, 0])
            self._sync_onscreen_immersive_metrics()

        self._media_viewport.refresh_stacking()

    def _apply_immersive_list_chrome(
        self,
        immersive: bool,
        opacity: float | None = None,
    ) -> None:
        panel = self._subtitle_panel
        panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        if immersive:
            if opacity is None:
                opacity = float(getattr(self, "_immersive_list_opacity", 0.28))
            side = getattr(self, "_immersive_list_side", "right")
            compact = self._is_subtitle_list_compact()
            panel.setStyleSheet(
                build_immersive_subtitle_panel_style(opacity, side, compact=compact)
            )
            self.subtitle_list.setObjectName("immersiveSubtitleList")
            self._subtitle_panel_title.setText("字幕列表")
            self.subtitle_list.setWordWrap(True)
            self.subtitle_list.setTextElideMode(Qt.TextElideMode.ElideNone)
            # Force stylesheet re-polish after objectName change.
            self.subtitle_list.style().unpolish(self.subtitle_list)
            self.subtitle_list.style().polish(self.subtitle_list)
            self.subtitle_list.update()
        else:
            panel.setStyleSheet("")
            self.subtitle_list.setObjectName(
                "subtitleListCompact" if self._is_subtitle_list_compact() else ""
            )
            self.subtitle_list.setStyleSheet("")
            self._subtitle_panel_title.setText("字幕列表（点击跳转）")
            self.subtitle_list.style().unpolish(self.subtitle_list)
            self.subtitle_list.style().polish(self.subtitle_list)
            self.subtitle_list.update()
        self._sync_subtitle_density_button()

    def _layout_immersive_list_panel(self) -> None:
        if not self._immersive_list_active:
            return
        if not self._subtitle_list_visible:
            self._subtitle_panel.hide()
            self._immersive_resize_handle.hide()
            self._sync_onscreen_immersive_metrics()
            return
        host = self._media_host
        host_w = max(1, host.width())
        host_h = host.height()
        width = immersive_list_pixel_width(host_w, self._immersive_list_width_percent)
        side = getattr(self, "_immersive_list_side", "right")
        if side == "left":
            x = 0
        else:
            x = max(0, host_w - width)
        self._subtitle_panel.setGeometry(x, 0, width, host_h)
        self._subtitle_panel.raise_()
        self._subtitle_panel.show()

        handle = self._immersive_resize_handle
        handle_w = handle.width() or 6
        if side == "left":
            handle_x = max(0, width - handle_w)
        else:
            handle_x = max(0, x)
        handle.setGeometry(handle_x, 0, handle_w, host_h)
        handle.setStyleSheet(
            "background-color: rgba(185, 128, 255, 0.45); border: none;"
        )
        handle.show()
        handle.raise_()
        self._refresh_subtitle_list_item_layout()
        self._sync_onscreen_immersive_metrics()

    def _refresh_subtitle_list_item_layout(self) -> None:
        """Recompute wrapped item heights after width changes."""
        if not hasattr(self, "subtitle_list"):
            return
        self.subtitle_list.setWordWrap(True)
        self.subtitle_list.doItemsLayout()
        self.subtitle_list.viewport().update()

    def _sync_onscreen_immersive_metrics(self) -> None:
        overlay = getattr(self, "_onscreen_overlay", None)
        if overlay is None:
            return
        overlay.set_immersive_metrics(
            active=bool(self._immersive_list_active and self._subtitle_list_visible),
            side=str(self._immersive_list_side or "right"),
            width_percent=int(self._immersive_list_width_percent),
        )

    def _persist_immersive_list_geometry(self) -> None:
        self._config.immersive_subtitle_list_side = self._immersive_list_side
        self._config.immersive_subtitle_list_width_percent = int(
            self._immersive_list_width_percent
        )
        self._config.immersive_subtitle_list_opacity = float(self._immersive_list_opacity)
        save_config(self._config)

    def _style_media_icon(self, pixmap: QStyle.StandardPixmap) -> QIcon:
        icon = self.style().standardIcon(pixmap)
        src = icon.pixmap(QSize(18, 18))
        if src.isNull():
            return icon
        tinted = QPixmap(src.size())
        tinted.setDevicePixelRatio(src.devicePixelRatio())
        tinted.fill(Qt.GlobalColor.transparent)
        painter = QPainter(tinted)
        painter.drawPixmap(0, 0, src)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(tinted.rect(), QColor("#b980ff"))
        painter.end()
        result = QIcon()
        result.addPixmap(tinted)
        return result

    def _configure_icon_button(self, button: QPushButton, tooltip: str) -> None:
        button.setObjectName("iconButton")
        button.setText("")
        button.setToolTip(tooltip)
        button.setFixedSize(34, 32)
        button.setIconSize(QSize(18, 18))

    def _set_play_button_state(self, playing: bool) -> None:
        if playing:
            self.play_btn.setIcon(self._style_media_icon(QStyle.StandardPixmap.SP_MediaPause))
            self.play_btn.setToolTip("暂停")
        else:
            self.play_btn.setIcon(self._style_media_icon(QStyle.StandardPixmap.SP_MediaPlay))
            self.play_btn.setToolTip("播放")

    def _set_mute_button_state(self, muted: bool) -> None:
        if muted:
            self.mute_btn.setIcon(self._style_media_icon(QStyle.StandardPixmap.SP_MediaVolumeMuted))
            self.mute_btn.setToolTip("取消静音")
        else:
            self.mute_btn.setIcon(self._style_media_icon(QStyle.StandardPixmap.SP_MediaVolume))
            self.mute_btn.setToolTip("静音")

    def _build_controls(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        self.play_btn = QPushButton()
        self._configure_icon_button(self.play_btn, "播放")
        self._set_play_button_state(False)
        self.play_btn.clicked.connect(self._toggle_play)
        bar.addWidget(self.play_btn)

        self.position_slider = QSlider(Qt.Orientation.Horizontal)
        self.position_slider.setRange(0, 0)
        self.position_slider.sliderPressed.connect(self._on_slider_pressed)
        self.position_slider.sliderReleased.connect(self._on_slider_released)
        self.position_slider.valueChanged.connect(self._on_slider_moved)
        bar.addWidget(self.position_slider, stretch=1)

        self.restart_from_start_btn = QPushButton("从头开始播放")
        self.restart_from_start_btn.setObjectName("linkButton")
        self.restart_from_start_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.restart_from_start_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.restart_from_start_btn.setFlat(True)
        self.restart_from_start_btn.setToolTip("从开头重新播放")
        link_font = self.restart_from_start_btn.font()
        link_font.setUnderline(True)
        self.restart_from_start_btn.setFont(link_font)
        self.restart_from_start_btn.clicked.connect(self._on_restart_from_start_clicked)
        self.restart_from_start_btn.hide()
        bar.addWidget(self.restart_from_start_btn)

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

        self.mute_btn = QPushButton()
        self._configure_icon_button(self.mute_btn, "静音")
        self.mute_btn.setCheckable(True)
        self._set_mute_button_state(False)
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

    def _recent_media_paths(self) -> list[Path]:
        paths: list[Path] = []
        for item in self._config.recent_media_files:
            path = Path(item)
            if path.is_file():
                paths.append(path)
        return paths

    def _recent_media_label(self, path: Path, paths: list[Path]) -> str:
        same_name = [item for item in paths if item.name == path.name]
        if len(same_name) > 1:
            return f"{path.name}  ({path.parent.name})"
        return path.name

    def _rebuild_open_file_menu(self) -> None:
        menu = self._open_file_menu
        menu.clear()
        open_action = menu.addAction("打开媒体")
        open_action.triggered.connect(self._open_media)
        recent = self._recent_media_paths()
        if not recent:
            return
        menu.addSeparator()
        for path in recent[:6]:
            action = menu.addAction(self._recent_media_label(path, recent))
            action.setToolTip(str(path))
            action.triggered.connect(lambda _checked=False, target=path: self._open_recent_media(target))
        older = recent[6:15]
        if not older:
            return
        menu.addSeparator()
        more_menu = menu.addMenu("更多")
        for path in older:
            action = more_menu.addAction(self._recent_media_label(path, recent))
            action.setToolTip(str(path))
            action.triggered.connect(lambda _checked=False, target=path: self._open_recent_media(target))

    def _open_recent_media(self, path: Path) -> None:
        if not path.is_file():
            QMessageBox.information(self, "打开文件", f"找不到文件：\n{path}")
            return
        self.load_media(path)

    def _persist_last_media_dir(self, media_path: Path | None = None) -> None:
        target = media_path or self._media_path
        if target is None:
            return
        target = target.resolve()
        folder = str(target.parent)
        recent = [str(target)]
        for item in self._config.recent_media_files:
            if item != recent[0]:
                recent.append(item)
        recent = recent[:15]
        changed = self._config.last_media_dir != folder or self._config.recent_media_files != recent
        self._config.last_media_dir = folder
        self._config.recent_media_files = recent
        if changed:
            save_config(self._config)

    def load_media(self, media_path: Path) -> None:
        media_path = media_path.resolve()
        if not media_path.is_file():
            return

        self._remember_playback_position()
        self._cancel_open_preview()
        resume_ms = self._saved_playback_position_ms(media_path)

        self._media_path = media_path
        self._set_subtitle_list_visible(True)
        self._persist_last_media_dir(media_path)
        self.media_label.setText(media_path.name)
        self._open_dir_btn.setEnabled(True)
        self._pending_seek_ms = resume_ms
        self._pending_play_after_seek = False
        self._awaiting_reload_seek = False
        self._recovering_playback = False
        self._playback_recovery_attempts = 0
        self._last_good_position_ms = resume_ms
        self._open_pause_position_ms = resume_ms
        self._open_pause_applied_ms = 0
        self._open_pause_pending = True
        self._holding_open_pause = False
        self._stop_subtitle_repeat()
        self._recreate_audio_output()
        is_audio = media_path.suffix.lower() in AUDIO_EXTENSIONS
        self._media_viewport.set_audio_mode(is_audio)
        self._video_widget.clear_frame()
        if not is_audio:
            self._player.setVideoSink(self._video_widget.video_sink())

        self.subtitle_list.clear()
        self._segments.clear()
        self._clear_subtitle_tags()
        self._current_subtitle_row = -1
        self._update_onscreen_subtitle(0.0)
        self._update_live_status("")

        self._player.setSource(QUrl.fromLocalFile(str(media_path)))
        if not is_audio:
            QTimer.singleShot(0, self._media_viewport.refresh_stacking)

        self._refresh_subtitle_options()
        if self.subtitle_combo.count() > 0:
            self.subtitle_combo.setCurrentIndex(0)
            self._load_subtitle_at_index(0)
        self._update_notes_buttons()
        self._schedule_cloud_check()

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
            self._error_box("字幕加载失败", str(exc))
            self._segments = []
        self._reset_tag_filter()
        self._tag_path = tag_path_for_subtitle(path)
        self._tag_document = load_tag_document(self._tag_path, path.name)
        self._populate_subtitle_list()

    def _refresh_subtitle_options(self) -> None:
        self.subtitle_combo.blockSignals(True)
        self.subtitle_combo.clear()
        if self._media_path:
            for path, label in find_valid_subtitles(self._media_path):
                self.subtitle_combo.addItem(f"{label} ({path.name})", str(path))
        self.subtitle_combo.blockSignals(False)

    def _refresh_subtitle_list_texts(self) -> None:
        if self.subtitle_list.count() != len(self._segments):
            self._populate_subtitle_list()
            return
        for row, seg in enumerate(self._segments):
            item = self.subtitle_list.item(row)
            if item is not None:
                self._apply_subtitle_item_payload(item, seg, row)

    def _update_live_status(self, message: str) -> None:
        self.live_status_label.setText(message or "")

    def _on_subtitle_selected(self, index: int) -> None:
        if index < 0:
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
            self._error_box("字幕加载失败", str(exc))
            self._segments = []
        self._load_tag_document_for_current_subtitle()
        self._populate_subtitle_list()
        preview_seconds = self._player.position() / 1000.0
        if self._holding_open_pause:
            preview_seconds = self._open_pause_applied_ms / 1000.0
        if self._segments and (self._player.duration() > 0 or self._holding_open_pause):
            self._sync_subtitle_highlight(preview_seconds, force=True)
        self._update_onscreen_subtitle(preview_seconds)

    def _populate_subtitle_list(self) -> None:
        self._bind_subtitle_tags()
        self.subtitle_list.clear()
        for row, seg in enumerate(self._segments):
            item = QListWidgetItem()
            self._apply_subtitle_item_payload(item, seg, row)
            self.subtitle_list.addItem(item)
        self._current_subtitle_row = -1
        self._refresh_tag_filter_bar()
        self._apply_tag_row_filter()
        self._fill_unmatched_list()
        self._refresh_subtitle_list_item_layout()
        self._update_onscreen_subtitle(self._player.position() / 1000.0)

    def _is_subtitle_list_compact(self) -> bool:
        density = str(
            getattr(self._config, "subtitle_list_density", "normal") or "normal"
        ).strip().lower()
        return density == "compact"

    def _sync_subtitle_density_button(self) -> None:
        btn = getattr(self, "_subtitle_density_btn", None)
        if btn is None:
            return
        if self._is_subtitle_list_compact():
            btn.setText("紧凑")
            btn.setToolTip("当前为紧凑模式（无序号/时间，条目间距较大）。点击切换为普通模式。")
        else:
            btn.setText("普通")
            btn.setToolTip("当前为普通模式（含序号与时间）。点击切换为紧凑模式。")

    def _toggle_subtitle_list_density(self) -> None:
        self._config.subtitle_list_density = (
            "normal" if self._is_subtitle_list_compact() else "compact"
        )
        save_config(self._config)
        self._apply_subtitle_list_density(persist=False)

    def _apply_subtitle_list_density(self, *, persist: bool = True) -> None:
        if persist:
            save_config(self._config)
        compact = self._is_subtitle_list_compact()
        self._sync_subtitle_density_button()
        # Refresh chrome (immersive/normal objectName + padding) and item texts.
        if getattr(self, "_immersive_list_active", False):
            self._apply_immersive_list_chrome(True)
        else:
            self.subtitle_list.setObjectName("subtitleListCompact" if compact else "")
            self.subtitle_list.style().unpolish(self.subtitle_list)
            self.subtitle_list.style().polish(self.subtitle_list)
        self._refresh_subtitle_list_texts()
        self._refresh_tag_filter_bar()
        self._apply_tag_row_filter()
        self._fill_unmatched_list()
        self._refresh_subtitle_list_item_layout()

    def _clear_subtitle_tags(self) -> None:
        self._tag_document = SubtitleTagDocument()
        self._tag_path = None
        self._tag_by_row = {}
        self._tag_unmatched = []
        self._reset_tag_filter()
        self._refresh_tag_filter_bar()
        self._fill_unmatched_list()

    def _taggable_subtitle_path(self) -> Path | None:
        path = self._current_subtitle_save_path()
        if path is None or not self._subtitle_editing_allowed():
            return None
        if path.suffix.lower() not in {".srt", ".vtt"}:
            return None
        return path

    def _load_tag_document_for_current_subtitle(self) -> None:
        self._reset_tag_filter()
        path = self._taggable_subtitle_path()
        if path is None:
            self._tag_document = SubtitleTagDocument()
            self._tag_path = None
            self._tag_by_row = {}
            self._tag_unmatched = []
            return
        self._tag_path = tag_path_for_subtitle(path)
        self._tag_document = load_tag_document(self._tag_path, path.name)

    def _bind_subtitle_tags(self) -> None:
        assignment = assign_tags(self._segments, self._tag_document.entries)
        self._tag_by_row = assignment.by_row
        self._tag_unmatched = assignment.unmatched

    def _save_subtitle_tags(self) -> None:
        path = self._taggable_subtitle_path()
        if path is None:
            return
        self._tag_path = tag_path_for_subtitle(path)
        self._tag_document.subtitle_file = path.name
        try:
            save_tag_document(self._tag_path, self._tag_document)
        except OSError as exc:
            self._error_box("标签保存失败", str(exc))

    def _subtitle_item_payload(self, seg: SubtitleSegment, row: int) -> dict:
        entry = self._tag_by_row.get(row)
        tags = list(entry.tags) if entry is not None else []
        note = entry.note if entry is not None else ""
        compact = self._is_subtitle_list_compact()
        body = (seg.text or "").replace("\r\n", "\n").strip()
        start = self._format_clock(seg.start)
        end = self._format_clock(seg.end)
        return {
            "compact": compact,
            "meta": f"{seg.index}. [{start} → {end}]",
            "body": body,
            "tags": tags,
            "note": note,
            "unmatched": False,
        }

    def _apply_subtitle_item_payload(self, item: QListWidgetItem, seg: SubtitleSegment, row: int) -> None:
        payload = self._subtitle_item_payload(seg, row)
        item.setData(PAYLOAD_ROLE, payload)
        item.setData(Qt.ItemDataRole.UserRole, float(seg.start))
        item.setText(payload["body"])
        item.setToolTip("")

    def _rows_have_tags(self, rows: list[int]) -> bool:
        return any(row in self._tag_by_row for row in rows)

    def _custom_tags_for_current_media(self) -> list[str]:
        return custom_tags_for_media(
            self._media_path,
            self._taggable_subtitle_path(),
            self._tag_document.entries,
        )

    def _edit_subtitle_tags(self, rows: list[int]) -> None:
        rows = [row for row in rows if 0 <= row < len(self._segments)]
        if not rows or not self._subtitle_editing_allowed():
            return
        entries = [self._tag_by_row.get(row) for row in rows]
        tag_sets = [list(entry.tags) if entry is not None else [] for entry in entries]
        shared = [tag for tag in tag_sets[0] if all(tag in tags for tags in tag_sets)]
        notes = [entry.note if entry is not None else "" for entry in entries]
        notes_differ = len(set(notes)) > 1
        dialog = SubtitleTagDialog(
            self,
            selected_tags=shared,
            note="" if notes_differ else notes[0],
            custom_tags=self._custom_tags_for_current_media(),
            row_count=len(rows),
            notes_differ=notes_differ,
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        new_tags = dialog.tags()
        new_note = dialog.note()
        for row in rows:
            segment = self._segments[row]
            entry = self._tag_by_row.get(row)
            note = new_note if new_note is not None else (entry.note if entry is not None else "")
            if not new_tags and not note.strip():
                if entry is not None:
                    retire_tag_entry(entry)
                continue
            if entry is None:
                entry = entry_from_segment(segment, new_tags, note)
                stamp_new_entry(entry)
                self._tag_document.entries.append(entry)
            else:
                snapshot_entry(entry, segment)
                set_entry_tags(entry, new_tags)
                if new_note is not None:
                    set_entry_note(entry, note)
        self._save_subtitle_tags()
        self._bind_subtitle_tags()
        self._refresh_subtitle_list_texts()
        self._refresh_tag_filter_bar()
        self._apply_tag_row_filter()
        self._fill_unmatched_list()
        self._refresh_subtitle_list_item_layout()

    def _clear_tags_on_rows(self, rows: list[int]) -> None:
        changed = False
        for row in rows:
            entry = self._tag_by_row.get(row)
            if entry is None:
                continue
            self._drop_tag_entry(entry)
            changed = True
        if not changed:
            return
        self._save_subtitle_tags()
        self._bind_subtitle_tags()
        self._refresh_subtitle_list_texts()
        self._refresh_tag_filter_bar()
        self._apply_tag_row_filter()
        self._fill_unmatched_list()
        self._refresh_subtitle_list_item_layout()

    def _drop_tag_entry(self, entry: SubtitleTagEntry | None) -> None:
        if entry is None:
            return
        retire_tag_entry(entry)

    def _matched_tag_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in self._tag_by_row.values():
            for tag in entry.tags:
                counts[tag] = counts.get(tag, 0) + 1
        return counts

    def _reset_tag_filter(self) -> None:
        self._tag_filter_mode = "all"
        self._selected_tag_filters = set()

    def _refresh_tag_filter_bar(self) -> None:
        if not hasattr(self, "_tag_bar"):
            return
        counts = self._matched_tag_counts()
        has_tags = bool(counts) or bool(self._tag_unmatched)
        self._tag_bar.setVisible(has_tags)
        if not has_tags:
            self._reset_tag_filter()
            self._tag_filter_buttons = {}
            self._tag_bar.set_tag_buttons([])
            self._tag_unmatched_banner.hide()
            self._subtitle_stack.setCurrentWidget(self.subtitle_list)
            self._show_all_subtitle_rows()
            return
        self._selected_tag_filters &= set(counts)
        if self._tag_filter_mode == "tags" and not self._selected_tag_filters:
            self._tag_filter_mode = "all"
        if self._tag_filter_mode == "unmatched" and not self._tag_unmatched:
            self._reset_tag_filter()
        buttons: list[QPushButton] = []
        self._tag_filter_buttons = {}
        preset = [tag for tag in PRESET_TAGS if tag in counts]
        custom = [tag for tag in counts if tag not in PRESET_TAGS]
        ordered = list(preset)
        if self._tag_unmatched:
            ordered.append("unmatched")
        for tag in ordered + custom:
            if tag == "unmatched":
                button = self._make_tag_filter_button(
                    "unmatched",
                    f"未挂上 {len(self._tag_unmatched)}",
                    custom=False,
                )
                button.setChecked(self._tag_filter_mode == "unmatched")
                buttons.append(button)
                self._tag_filter_buttons["unmatched"] = button
                continue
            label = f"{tag} {counts[tag]}"
            button = self._make_tag_filter_button(tag, label, custom=tag not in PRESET_TAGS)
            button.setChecked(self._tag_filter_mode == "tags" and tag in self._selected_tag_filters)
            buttons.append(button)
            self._tag_filter_buttons[tag] = button
        self._sync_tag_filter_button_checks()
        self._tag_bar.set_tag_buttons(buttons)
        self._update_unmatched_banner()

    def _make_tag_filter_button(self, key: str, label: str, *, custom: bool) -> QPushButton:
        button = QPushButton(label)
        button.setObjectName("subtitleTagButton")
        button.setCheckable(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setProperty("fullText", label)
        button.setProperty("customTag", custom)
        button.setToolTip(label)
        button.clicked.connect(lambda _checked=False, value=key: self._toggle_tag_filter(value))
        return button

    def _sync_tag_filter_button_checks(self) -> None:
        if hasattr(self, "_tag_all_btn"):
            self._tag_all_btn.setChecked(self._tag_filter_mode == "all")
        for key, button in self._tag_filter_buttons.items():
            if key == "unmatched":
                button.setChecked(self._tag_filter_mode == "unmatched")
            else:
                button.setChecked(self._tag_filter_mode == "tags" and key in self._selected_tag_filters)

    def _update_unmatched_banner(self) -> None:
        show = self._tag_filter_mode == "all" and bool(self._tag_unmatched)
        self._tag_unmatched_banner.setVisible(show)
        if show:
            self._tag_unmatched_banner.setText(f"{len(self._tag_unmatched)} 条标签未挂上")

    def _toggle_tag_filter(self, value: str) -> None:
        if value == "unmatched":
            if self._tag_filter_mode == "unmatched":
                self._reset_tag_filter()
            else:
                self._tag_filter_mode = "unmatched"
                self._selected_tag_filters = set()
        else:
            if self._tag_filter_mode != "tags":
                self._selected_tag_filters = set()
                self._tag_filter_mode = "tags"
            if value in self._selected_tag_filters:
                self._selected_tag_filters.remove(value)
            else:
                self._selected_tag_filters.add(value)
            if not self._selected_tag_filters:
                self._tag_filter_mode = "all"
        self._sync_tag_filter_button_checks()
        self._apply_tag_row_filter()
        self._fill_unmatched_list()
        self._update_unmatched_banner()

    def _set_tag_filter(self, value: str) -> None:
        if value == "unmatched":
            self._tag_filter_mode = "unmatched"
            self._selected_tag_filters = set()
        else:
            self._reset_tag_filter()
        self._sync_tag_filter_button_checks()
        self._apply_tag_row_filter()
        self._fill_unmatched_list()
        self._update_unmatched_banner()

    def _show_all_subtitle_rows(self) -> None:
        if not hasattr(self, "subtitle_list"):
            return
        for row in range(self.subtitle_list.count()):
            self.subtitle_list.setRowHidden(row, False)

    def _apply_tag_row_filter(self) -> None:
        if not hasattr(self, "subtitle_list"):
            return
        unmatched_mode = self._tag_filter_mode == "unmatched"
        self._subtitle_stack.setCurrentWidget(
            self._unmatched_list if unmatched_mode else self.subtitle_list
        )
        if self._tag_filter_mode in {"all", "unmatched"}:
            self._show_all_subtitle_rows()
            return
        for row in range(self.subtitle_list.count()):
            entry = self._tag_by_row.get(row)
            visible = entry is not None and any(
                tag in entry.tags for tag in self._selected_tag_filters
            )
            self.subtitle_list.setRowHidden(row, not visible)

    def _fill_unmatched_list(self) -> None:
        if not hasattr(self, "_unmatched_list"):
            return
        self._unmatched_list.clear()
        for entry in self._tag_unmatched:
            item = QListWidgetItem()
            start = self._format_clock(entry.start)
            end = self._format_clock(entry.end)
            payload = {
                "compact": False,
                "meta": f"未挂上  [{start} → {end}]",
                "body": (entry.text or "").replace("\r\n", "\n").strip(),
                "tags": list(entry.tags),
                "note": entry.note,
                "unmatched": True,
            }
            item.setData(PAYLOAD_ROLE, payload)
            item.setData(Qt.ItemDataRole.UserRole, entry.id)
            item.setText(payload["body"])
            item.setToolTip("")
            self._unmatched_list.addItem(item)

    def _jump_tagged_row(self, step: int) -> None:
        if self._tag_filter_mode == "unmatched":
            count = self._unmatched_list.count()
            if count <= 0:
                return
            current = self._unmatched_list.currentRow()
            nxt = 0 if current < 0 else current + step
            nxt = max(0, min(count - 1, nxt))
            self._unmatched_list.setCurrentRow(nxt)
            item = self._unmatched_list.item(nxt)
            if item is not None:
                self._on_unmatched_tag_clicked(item)
            return
        rows = [
            row
            for row in range(len(self._segments))
            if not self.subtitle_list.isRowHidden(row) and row in self._tag_by_row
        ]
        if self._tag_filter_mode == "all":
            rows = [row for row in range(len(self._segments)) if row in self._tag_by_row]
        elif self._tag_filter_mode == "tags":
            rows = sorted(
                row
                for row, entry in self._tag_by_row.items()
                if any(tag in entry.tags for tag in self._selected_tag_filters)
            )
        if not rows:
            return
        anchor = self._current_subtitle_row
        if step > 0:
            nxt = next((row for row in rows if row > anchor), rows[0])
        else:
            prev = [row for row in rows if row < anchor]
            nxt = prev[-1] if prev else rows[-1]
        self._stop_subtitle_repeat()
        self.subtitle_list.clearSelection()
        self.subtitle_list.setCurrentRow(nxt)
        item = self.subtitle_list.item(nxt)
        if item is not None:
            item.setSelected(True)
            self.subtitle_list.scrollToItem(item, QListWidget.ScrollHint.PositionAtCenter)
        self._seek_to(self._segments[nxt].start, play=True)

    def _on_unmatched_tag_clicked(self, item: QListWidgetItem) -> None:
        if self._ignore_note_row_click:
            self._ignore_note_row_click = False
            return
        entry_id = item.data(Qt.ItemDataRole.UserRole)
        entry = next((tag for tag in self._tag_unmatched if tag.id == entry_id), None)
        if entry is None:
            return
        self._stop_subtitle_repeat()
        self._seek_to(entry.start, play=True)

    def _on_unmatched_context_menu(self, pos) -> None:
        item = self._unmatched_list.itemAt(pos)
        if item is None:
            return
        entry_id = item.data(Qt.ItemDataRole.UserRole)
        entry = next((tag for tag in self._tag_unmatched if tag.id == entry_id), None)
        if entry is None:
            return
        menu = QMenu(self)
        attach_action = menu.addAction("挂到当前句")
        remove_action = menu.addAction("删除这条标签")
        chosen = menu.exec(self._unmatched_list.mapToGlobal(pos))
        if chosen == attach_action:
            self._attach_unmatched_to_current_row(entry)
        elif chosen == remove_action:
            self._drop_tag_entry(entry)
            self._save_subtitle_tags()
            self._bind_subtitle_tags()
            self._refresh_subtitle_list_texts()
            self._refresh_tag_filter_bar()
            self._apply_tag_row_filter()
            self._fill_unmatched_list()

    def _attach_unmatched_to_current_row(self, entry: SubtitleTagEntry) -> None:
        row = self._current_subtitle_row
        if row < 0 or row >= len(self._segments):
            row = find_segment_index_at_time(self._segments, self._player.position() / 1000.0)
        if row < 0 or row >= len(self._segments):
            QMessageBox.information(self, "标签", "请先播放到要挂上的那句字幕。")
            return
        segment = self._segments[row]
        existing = self._tag_by_row.get(row)
        if existing is not None and existing.id != entry.id:
            merged = list(existing.tags)
            for tag in entry.tags:
                if tag not in merged:
                    merged.append(tag)
            set_entry_tags(existing, merged)
            if not existing.note.strip() and entry.note.strip():
                set_entry_note(existing, entry.note, entry.note_at or None)
            snapshot_entry(existing, segment)
            self._drop_tag_entry(entry)
        else:
            snapshot_entry(entry, segment)
        self._save_subtitle_tags()
        self._reset_tag_filter()
        self._bind_subtitle_tags()
        self._refresh_subtitle_list_texts()
        self._refresh_tag_filter_bar()
        self._apply_tag_row_filter()
        self._fill_unmatched_list()
        self._refresh_subtitle_list_item_layout()

    def _open_note_from_list(self, widget: QListWidget, index) -> None:
        self._ignore_note_row_click = True
        self._note_preview.hide()
        entry = self._tag_entry_for_list_index(widget, index.row())
        if entry is None:
            return
        self._show_note_editor(entry)

    def _open_hovered_note_editor(self) -> None:
        entry_id = self._note_hover_entry_id
        self._note_preview.hide()
        entry = next((item for item in self._tag_document.entries if item.id == entry_id), None)
        if entry is None:
            return
        self._show_note_editor(entry)

    def _show_note_editor(self, entry: SubtitleTagEntry) -> None:
        dialog = getattr(self, "_note_editor", None)
        if dialog is None:
            dialog = SubtitleNoteDialog(self)
            dialog.saved.connect(self._save_subtitle_note)
            self._note_editor = dialog
        dialog.edit_note(entry.id, entry.note)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _save_subtitle_note(self, entry_id: str, note: str) -> None:
        entry = next((item for item in self._tag_document.entries if item.id == entry_id), None)
        if entry is None:
            return
        set_entry_note(entry, note)
        self._save_subtitle_tags()
        self._bind_subtitle_tags()
        self._refresh_subtitle_list_texts()
        self._refresh_tag_filter_bar()
        self._apply_tag_row_filter()
        self._fill_unmatched_list()
        self._refresh_subtitle_list_item_layout()

    def _tag_entry_for_list_index(self, widget: QListWidget, row: int) -> SubtitleTagEntry | None:
        if widget is self.subtitle_list:
            return self._tag_by_row.get(row)
        item = widget.item(row)
        if item is None:
            return None
        entry_id = item.data(Qt.ItemDataRole.UserRole)
        return next((tag for tag in self._tag_unmatched if tag.id == entry_id), None)

    def _hide_note_preview_if_idle(self) -> None:
        if self._note_preview.underMouse():
            return
        self._note_preview.hide()
        self._note_hover_entry_id = ""

    def _hover_subtitle_note(self, widget: QListWidget, pos) -> None:
        row = widget.indexAt(pos).row() if widget.indexAt(pos).isValid() else -1
        delegate = widget.itemDelegate()
        entry = self._tag_entry_for_list_index(widget, row) if row >= 0 else None
        rect = None
        if entry is not None and isinstance(delegate, SubtitleListDelegate):
            item_rect = widget.visualRect(widget.model().index(row, 0))
            payload = widget.item(row).data(PAYLOAD_ROLE) if widget.item(row) is not None else None
            rect = delegate.note_rect(item_rect, widget.font(), payload)
        if entry is None or rect is None or not rect.contains(pos) or not entry.note.strip():
            if not self._note_preview.underMouse():
                self._note_preview.hide()
                self._note_hover_entry_id = ""
            return
        self._note_hover_entry_id = entry.id
        anchor = widget.viewport().mapToGlobal(rect.bottomLeft())
        self._note_preview.show_note(entry.note, anchor)

    def _format_subtitle_item(self, seg: SubtitleSegment) -> str:
        body = (seg.text or "").replace("\r\n", "\n").strip()
        if self._is_subtitle_list_compact():
            return body
        start = self._format_clock(seg.start)
        end = self._format_clock(seg.end)
        # Keep newlines and rely on QListWidget word-wrap for long lines.
        return f"{seg.index}. [{start} → {end}]\n{body}"

    @staticmethod
    def _format_clock(seconds: float) -> str:
        total = int(seconds)
        hours, rem = divmod(total, 3600)
        minutes, secs = divmod(rem, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"

    def _on_subtitle_clicked(self, item: QListWidgetItem) -> None:
        if self._ignore_note_row_click:
            self._ignore_note_row_click = False
            return
        modifiers = QGuiApplication.keyboardModifiers()
        if modifiers & (
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
        ):
            # Multi-select: do not seek.
            return
        row = self.subtitle_list.row(item)
        if row < 0 or row >= len(self._segments):
            return
        self._stop_subtitle_repeat()
        self._seek_to(self._segments[row].start)

    def _selected_subtitle_rows(self) -> list[int]:
        rows = sorted(
            {
                self.subtitle_list.row(item)
                for item in self.subtitle_list.selectedItems()
                if item is not None
            }
        )
        return [row for row in rows if 0 <= row < len(self._segments)]

    def _plain_text_for_subtitle_rows(self, rows: list[int]) -> str:
        texts: list[str] = []
        for row in rows:
            if 0 <= row < len(self._segments):
                texts.append(
                    (self._segments[row].text or "").replace("\r\n", "\n").strip()
                )
        return "\n".join(texts)

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

            selected_rows = self._selected_subtitle_rows()
            # Right-click on an unselected row: select only that row (standard UX).
            if row not in selected_rows:
                self.subtitle_list.clearSelection()
                item.setSelected(True)
                self.subtitle_list.setCurrentItem(item)
                selected_rows = [row]

            multi = len(selected_rows) > 1
            editing_allowed = self._subtitle_editing_allowed()
            menu = QMenu(self)
            edit_action = None
            tag_action = None
            clear_tag_action = None
            if editing_allowed:
                edit_action = menu.addAction("编辑")
                edit_action.setEnabled(not multi)
                tag_action = menu.addAction("标签")
                clear_tag_action = menu.addAction("清除标签")
                clear_tag_action.setEnabled(self._rows_have_tags(selected_rows))
            copy_action = menu.addAction("复制")
            translate_action = menu.addAction("翻译")
            repeat_action = menu.addAction("重复播放")
            repeat_action.setEnabled(not multi)
            chosen = menu.exec(self.subtitle_list.mapToGlobal(pos))
            if chosen == copy_action:
                self._copy_subtitle_rows(selected_rows)
            elif chosen == translate_action:
                self._translate_subtitle_rows(selected_rows)
            elif edit_action is not None and chosen == edit_action and not multi:
                self._edit_subtitle_text(row)
            elif tag_action is not None and chosen == tag_action:
                self._edit_subtitle_tags(selected_rows)
            elif clear_tag_action is not None and chosen == clear_tag_action:
                self._clear_tags_on_rows(selected_rows)
            elif chosen == repeat_action and not multi:
                self._start_subtitle_repeat(row)
        finally:
            self._subtitle_menu_open = False
            QTimer.singleShot(0, self._maybe_resume_subtitle_auto_follow)

    def _subtitle_editing_allowed(self) -> bool:
        save_path = self._current_subtitle_save_path()
        if save_path is None:
            return False
        return save_path.suffix.lower() in {".srt", ".vtt"}

    def _copy_subtitle_rows(self, rows: list[int]) -> None:
        text = self._plain_text_for_subtitle_rows(rows)
        if not text:
            return
        QGuiApplication.clipboard().setText(text)

    def _copy_subtitle_text(self, row: int) -> None:
        self._copy_subtitle_rows([row])

    def _ensure_list_translator_running(self) -> bool:
        translator = get_translator(self._config.translate_app)
        if is_translator_running(translator):
            return True
        QMessageBox.information(
            self,
            translator.not_running_title,
            translator.not_running_message,
        )
        return False

    def _translate_subtitle_rows(self, rows: list[int]) -> None:
        text = self._plain_text_for_subtitle_rows(rows)
        if not text.strip():
            return
        if not self._ensure_list_translator_running():
            return

        # Put plain subtitle body on clipboard (multi-line joined by \n).
        QGuiApplication.clipboard().setText(text)

        # Stage a focused selection for “快捷键发起翻译”, same idea as the editor.
        staging = self._translate_staging
        staging.setPlainText(text)
        staging.selectAll()
        origin = self.mapToGlobal(self.rect().center())
        staging.move(origin.x() - 160, origin.y() - 90)
        staging.show()
        staging.raise_()
        staging.activateWindow()
        staging.setFocus(Qt.FocusReason.OtherFocusReason)
        cursor = staging.textCursor()
        cursor.select(cursor.SelectionType.Document)
        staging.setTextCursor(cursor)

        QTimer.singleShot(100, self._send_list_translate_hotkey)

    def _send_list_translate_hotkey(self) -> None:
        staging = getattr(self, "_translate_staging", None)
        try:
            if staging is None:
                return
            if not self._ensure_list_translator_running():
                return
            staging.activateWindow()
            staging.setFocus(Qt.FocusReason.OtherFocusReason)
            cursor = staging.textCursor()
            cursor.select(cursor.SelectionType.Document)
            staging.setTextCursor(cursor)
            hotkey = (self._config.translate_hotkey or "").strip() or get_translator(
                self._config.translate_app
            ).default_hotkey
            if not send_hotkey(hotkey):
                QMessageBox.warning(
                    self,
                    "翻译失败",
                    f"无法发送翻译快捷键 {hotkey}。",
                )
        finally:
            if staging is not None:
                staging.hide()

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
        self.subtitle_list.clearSelection()
        self.subtitle_list.setCurrentRow(row)
        item = self.subtitle_list.item(row)
        if item is not None:
            item.setSelected(True)
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

    def _edit_subtitle_text(self, row: int) -> None:
        if row < 0 or row >= len(self._segments):
            return
        if not self._subtitle_editing_allowed():
            QMessageBox.information(
                self,
                "提示",
                "当前没有可编辑的字幕文件。",
            )
            return
        seg = self._segments[row]
        result = SubtitleEditDialog.edit_segment(seg, self, config=self._config)
        if result is None:
            return
        new_start, new_end, new_text = result
        if new_start == seg.start and new_end == seg.end and new_text == seg.text:
            return
        self._segments[row] = SubtitleSegment(seg.index, new_start, new_end, new_text)
        entry = self._tag_by_row.get(row)
        if entry is not None:
            snapshot_entry(entry, self._segments[row])
            self._save_subtitle_tags()
        item = self.subtitle_list.item(row)
        if item is not None:
            self._apply_subtitle_item_payload(item, self._segments[row], row)
        self._persist_subtitle_edits()

    def _current_subtitle_save_path(self) -> Path | None:
        if not self._media_path:
            return None
        index = self.subtitle_combo.currentIndex()
        if index < 0:
            return None
        path_value = self.subtitle_combo.itemData(index)
        if not path_value:
            return None
        return Path(str(path_value))

    def _subtitle_format_for_path(self, path: Path) -> str:
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
            self._error_box("保存失败", str(exc))

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
        if self._open_frame_nudge or self._open_preview_should_pause:
            self._release_open_preview(keep_playing=True)
            return
        if self._open_pause_pending:
            self._play_after_open_pause = True
            return
        if self._holding_open_pause and not self._open_pause_ui_done:
            self._open_pause_ui_done = True
            self._open_nudge_timer.stop()
            self._holding_open_pause = False
            self._maybe_show_restart_link()
            self._stop_subtitle_repeat()
            self._play_media()
            return
        self._open_pause_pending = False
        self._holding_open_pause = False
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._stop_subtitle_repeat()
            self._player.pause()
        else:
            self._stop_subtitle_repeat()
            self._play_media()

    def _on_playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        if self._open_frame_nudge:
            self._set_play_button_state(False)
        else:
            self._set_play_button_state(playing)
        if playing and not self._open_frame_nudge and not self._open_preview_should_pause:
            self._holding_open_pause = False
            self._open_pause_pending = False
        if (
            state == QMediaPlayer.PlaybackState.PausedState
            and not self._open_frame_nudge
            and not self._open_preview_should_pause
        ):
            self._remember_playback_position()
        if playing and hasattr(self, "_media_viewport") and not self._open_frame_nudge:
            QTimer.singleShot(0, self._media_viewport.refresh_stacking)

    def _on_duration_changed(self, duration_ms: int) -> None:
        self.position_slider.setRange(0, max(0, duration_ms))
        if (
            self._holding_open_pause
            and duration_ms > 0
            and self._open_pause_applied_ms > 0
            and self._open_pause_applied_ms >= max(0, duration_ms - _PLAYBACK_END_MARGIN_MS)
        ):
            self._open_pause_applied_ms = 0
            self._open_pause_position_ms = 0
            self._hide_restart_from_start_link()
            self._player.setPosition(0)
        if self._holding_open_pause and not self._open_frame_nudge:
            self._sync_open_pause_ui(self._open_pause_applied_ms)
            return
        if self._holding_open_pause:
            self._update_time_label(self._open_pause_applied_ms, duration_ms)
            return
        self._update_time_label(self._player.position(), duration_ms)

    def _on_position_changed(self, position_ms: int) -> None:
        if self._holding_open_pause and not self._open_pause_ui_done:
            if self._open_frame_nudge:
                self._maybe_finish_audio_open_nudge(position_ms)
            else:
                self._maybe_finish_paused_open(position_ms)
            return
        if not self._seeking:
            self.position_slider.blockSignals(True)
            self.position_slider.setValue(position_ms)
            self.position_slider.blockSignals(False)
            if position_ms >= 0 and not self._open_frame_nudge:
                self._last_good_position_ms = position_ms
                if self._playback_recovery_attempts and not self._recovering_playback:
                    self._playback_recovery_attempts = 0
            if (
                not self._open_frame_nudge
                and not self._holding_open_pause
                and self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
                and not self._playback_save_timer.isActive()
            ):
                self._playback_save_timer.start()
            self._maybe_handle_subtitle_repeat(position_ms)
        self._update_time_label(position_ms, self._player.duration())
        self._sync_subtitle_highlight(position_ms / 1000.0)
        self._update_onscreen_subtitle(position_ms / 1000.0)

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
        if self.subtitle_list.isRowHidden(row) or self._tag_filter_mode == "unmatched":
            return
        self.subtitle_list.blockSignals(True)
        self.subtitle_list.clearSelection()
        self.subtitle_list.setCurrentRow(row)
        current_item = self.subtitle_list.item(row)
        if current_item is not None:
            current_item.setSelected(True)
            self.subtitle_list.scrollToItem(
                current_item,
                QListWidget.ScrollHint.PositionAtCenter,
            )
        self.subtitle_list.blockSignals(False)

    def _update_onscreen_subtitle(self, seconds: float) -> None:
        overlay = getattr(self, "_onscreen_overlay", None)
        if overlay is None:
            return
        if not self._segments:
            overlay.set_text("")
            return
        row = find_segment_index_at_time(self._segments, seconds)
        if row < 0:
            overlay.set_text("")
            return
        overlay.set_text(self._segments[row].text)

    def eventFilter(self, obj, event) -> bool:
        handle = getattr(self, "_immersive_resize_handle", None)
        if handle is not None and obj is handle:
            et = event.type()
            if (
                et == QEvent.Type.MouseButtonPress
                and event.button() == Qt.MouseButton.LeftButton
                and self._immersive_list_active
            ):
                self._immersive_resizing = True
                self._immersive_resize_start_x = int(event.globalPosition().x())
                self._immersive_resize_start_width = self._subtitle_panel.width()
                handle.grabMouse()
                return True
            if et == QEvent.Type.MouseMove and self._immersive_resizing:
                host_w = max(1, self._media_host.width())
                dx = int(event.globalPosition().x()) - self._immersive_resize_start_x
                if getattr(self, "_immersive_list_side", "right") == "right":
                    # Handle is on the left edge: drag left => wider.
                    new_w = self._immersive_resize_start_width - dx
                else:
                    # Handle is on the right edge: drag right => wider.
                    new_w = self._immersive_resize_start_width + dx
                min_w = 180
                max_w = max(min_w, int(host_w * 0.7))
                new_w = max(min_w, min(max_w, new_w))
                self._immersive_list_width_percent = max(
                    18, min(70, int(round(new_w * 100 / host_w)))
                )
                self._layout_immersive_list_panel()
                return True
            if et == QEvent.Type.MouseButtonRelease and self._immersive_resizing:
                self._immersive_resizing = False
                if QWidget.mouseGrabber() is handle:
                    handle.releaseMouse()
                self._persist_immersive_list_geometry()
                return True
            return False

        if obj is getattr(self, "_media_host", None):
            if event.type() == QEvent.Type.Resize and self._immersive_list_active:
                self._layout_immersive_list_panel()
            return super().eventFilter(obj, event)
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
        note_lists = (
            getattr(self, "subtitle_list", None),
            getattr(self, "_unmatched_list", None),
        )
        for note_list in note_lists:
            if note_list is None or obj is not note_list.viewport():
                continue
            if event.type() == QEvent.Type.MouseMove:
                self._note_preview_hide_timer.stop()
                self._hover_subtitle_note(note_list, event.position().toPoint())
            elif event.type() == QEvent.Type.Leave:
                self._note_preview_hide_timer.start()
        if subtitle_list is not None and obj is subtitle_list.viewport():
            if event.type() == QEvent.Type.Resize:
                width = subtitle_list.viewport().width()
                if width != self._subtitle_list_layout_width:
                    self._subtitle_list_layout_width = width
                    QTimer.singleShot(0, self._refresh_subtitle_list_item_layout)
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
        preview = getattr(self, "_note_preview", None)
        if preview is not None and obj is preview:
            if event.type() == QEvent.Type.Enter:
                self._note_preview_hide_timer.stop()
            elif event.type() == QEvent.Type.Leave:
                self._note_preview_hide_timer.start()
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

    def _playback_position_key(self, path: Path) -> str:
        return str(path.resolve())

    def _saved_playback_position_ms(self, path: Path) -> int:
        raw = self._config.media_playback_positions.get(self._playback_position_key(path), 0)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return 0
        if value < _PLAYBACK_RESUME_MIN_MS:
            return 0
        return value

    def _forget_playback_position(self, path: Path) -> None:
        key = self._playback_position_key(path)
        if key not in self._config.media_playback_positions:
            return
        positions = dict(self._config.media_playback_positions)
        positions.pop(key, None)
        self._config.media_playback_positions = positions
        save_config(self._config)

    def _remember_playback_position(self) -> None:
        path = self._media_path
        if path is None or self._open_frame_nudge or self._open_preview_should_pause:
            return
        playing = self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        if self._holding_open_pause and not playing:
            return
        if self._open_pause_pending and not playing:
            return
        position = int(self._player.position())
        duration = int(self._player.duration())
        key = self._playback_position_key(path)
        positions = dict(self._config.media_playback_positions)
        finished = duration > 0 and position >= max(0, duration - _PLAYBACK_END_MARGIN_MS)
        forget = position < _PLAYBACK_RESUME_MIN_MS or finished
        if forget:
            if key not in positions:
                return
            positions.pop(key, None)
        else:
            if positions.get(key) == position:
                return
            positions.pop(key, None)
            positions[key] = position
            if len(positions) > _PLAYBACK_POSITION_LIMIT:
                positions = dict(list(positions.items())[-_PLAYBACK_POSITION_LIMIT:])
        self._config.media_playback_positions = positions
        save_config(self._config)

    def _resume_point_is_meaningful(self, position_ms: int) -> bool:
        if position_ms < _PLAYBACK_RESUME_MIN_MS:
            return False
        duration = self._player.duration()
        if duration > 0 and position_ms >= max(0, duration - _PLAYBACK_END_MARGIN_MS):
            return False
        return True

    def _sync_open_pause_ui(self, position_ms: int) -> None:
        position_ms = max(0, int(position_ms))
        self.position_slider.blockSignals(True)
        self.position_slider.setValue(position_ms)
        self.position_slider.blockSignals(False)
        self._update_time_label(position_ms, self._player.duration())
        self._sync_subtitle_highlight(position_ms / 1000.0, force=True)
        self._update_onscreen_subtitle(position_ms / 1000.0)

    def _show_restart_from_start_link(self) -> None:
        if not hasattr(self, "restart_from_start_btn"):
            return
        self.restart_from_start_btn.show()
        self._restart_link_timer.start()

    def _hide_restart_from_start_link(self) -> None:
        self._restart_link_timer.stop()
        if hasattr(self, "restart_from_start_btn"):
            self.restart_from_start_btn.hide()

    def _maybe_show_restart_link(self) -> None:
        if not hasattr(self, "restart_from_start_btn"):
            return
        if self._resume_point_is_meaningful(self._open_pause_applied_ms):
            if not self.restart_from_start_btn.isVisible():
                self._show_restart_from_start_link()
        else:
            self._hide_restart_from_start_link()

    def _complete_open_pause_ui(self) -> None:
        self._maybe_show_restart_link()
        if not self._play_after_open_pause:
            return
        self._play_after_open_pause = False
        self._holding_open_pause = False
        self._play_media()

    def _restore_open_nudge_audio(self) -> None:
        if not self._open_nudge_audio_overridden:
            return
        self._open_nudge_audio_overridden = False
        self._audio_output.setMuted(self._open_nudge_restore_muted)

    def _cancel_open_preview(self) -> None:
        self._open_pause_pending = False
        self._holding_open_pause = False
        self._open_pause_ui_done = True
        self._open_frame_nudge = False
        self._open_preview_should_pause = False
        self._play_after_open_pause = False
        self._open_nudge_timer.stop()
        self._restore_open_nudge_audio()
        self._hide_restart_from_start_link()

    def _begin_open_pause(self) -> None:
        if not self._open_pause_pending or self._media_path is None:
            return
        self._open_pause_pending = False
        position_ms = self._clamp_position_ms(self._open_pause_position_ms)
        if not self._resume_point_is_meaningful(position_ms):
            position_ms = 0
        self._open_pause_applied_ms = position_ms
        self._last_good_position_ms = position_ms
        self._pending_seek_ms = position_ms
        self._pending_play_after_seek = False
        self._holding_open_pause = True
        self._open_pause_ui_done = False
        self._open_frame_seen = False
        self._player.setPlaybackRate(self._saved_playback_rate)
        self._set_play_button_state(False)
        self._sync_open_pause_ui(position_ms)

        is_audio = self._media_path.suffix.lower() in AUDIO_EXTENSIONS
        if is_audio and position_ms <= 0:
            self._player.pause()
            self._mark_open_pause_settled()
            return

        self._seeking = True
        self._player.setPosition(position_ms)
        self._seeking = False
        self._player.pause()
        self._open_nudge_timer.setInterval(_OPEN_FRAME_WAIT_MS)
        self._open_nudge_timer.start()

    def _maybe_finish_paused_open(self, position_ms: int) -> None:
        if self._open_pause_ui_done or self._media_path is None:
            return
        if self._media_path.suffix.lower() not in AUDIO_EXTENSIONS:
            return
        target = self._open_pause_applied_ms
        if target >= _PLAYBACK_RESUME_MIN_MS and position_ms + 400 < target:
            return
        if self._open_frame_seen:
            return
        self._open_frame_seen = True
        QTimer.singleShot(0, self._mark_open_pause_settled)

    def _mark_open_pause_settled(self) -> None:
        if self._open_pause_ui_done or not self._holding_open_pause:
            return
        self._open_pause_ui_done = True
        self._open_nudge_timer.stop()
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        self._sync_open_pause_ui(self._open_pause_applied_ms)
        self._complete_open_pause_ui()

    def _nudge_open_frame_if_needed(self) -> None:
        if not self._holding_open_pause or self._open_pause_ui_done or self._media_path is None:
            return
        if self._play_after_open_pause:
            self._open_pause_ui_done = True
            self._open_nudge_timer.stop()
            self._holding_open_pause = False
            self._play_after_open_pause = False
            self._maybe_show_restart_link()
            self._play_media()
            return
        if self._open_frame_seen:
            self._mark_open_pause_settled()
            return
        is_audio = self._media_path.suffix.lower() in AUDIO_EXTENSIONS
        target = self._open_pause_applied_ms
        if is_audio and (
            target < _PLAYBACK_RESUME_MIN_MS
            or abs(int(self._player.position()) - target) <= 400
        ):
            self._mark_open_pause_settled()
            return

        self._open_nudge_restore_muted = self._audio_output.isMuted()
        self._audio_output.setMuted(True)
        self._open_nudge_audio_overridden = True
        self._open_frame_nudge = True
        self._open_preview_should_pause = True
        self._player.play()
        self._open_nudge_timer.setInterval(_OPEN_FRAME_NUDGE_MS)
        self._open_nudge_timer.start()

    def _maybe_finish_audio_open_nudge(self, position_ms: int) -> None:
        if self._media_path is None or self._media_path.suffix.lower() not in AUDIO_EXTENSIONS:
            return
        target = self._open_pause_applied_ms
        if target >= _PLAYBACK_RESUME_MIN_MS and position_ms + 400 < target:
            return
        self._open_frame_nudge = False
        self._open_nudge_timer.stop()
        QTimer.singleShot(0, self._pause_after_open_preview)

    def _on_preview_video_frame(self, frame) -> None:
        if not frame.isValid():
            return
        if (
            self._holding_open_pause
            and not self._open_frame_nudge
            and not self._open_pause_ui_done
        ):
            target = self._open_pause_applied_ms
            if target >= _PLAYBACK_RESUME_MIN_MS and self._player.position() + 400 < target:
                return
            if self._open_frame_seen:
                return
            self._open_frame_seen = True
            QTimer.singleShot(0, self._mark_open_pause_settled)
            return
        if not self._open_frame_nudge:
            return
        target = self._open_pause_applied_ms
        if target >= _PLAYBACK_RESUME_MIN_MS and self._player.position() + 400 < target:
            return
        self._open_frame_nudge = False
        self._open_nudge_timer.stop()
        QTimer.singleShot(0, self._pause_after_open_preview)

    def _on_open_nudge_timeout(self) -> None:
        if not self._open_frame_nudge:
            self._nudge_open_frame_if_needed()
            return
        self._open_frame_nudge = False
        target = self._open_pause_applied_ms
        if target > 0:
            self._player.setPosition(target)
        self._pause_after_open_preview()

    def _pause_after_open_preview(self) -> None:
        if not self._open_preview_should_pause:
            return
        self._open_preview_should_pause = False
        self._open_frame_nudge = False
        self._open_nudge_timer.stop()
        target = max(0, int(self._open_pause_applied_ms))
        self._player.pause()
        if target > 0 and abs(int(self._player.position()) - target) > 500:
            self._seeking = True
            self._player.setPosition(target)
            self._seeking = False
        self._restore_open_nudge_audio()
        self._sync_open_pause_ui(target)
        self._open_pause_ui_done = True
        self._complete_open_pause_ui()

    def _release_open_preview(self, *, keep_playing: bool) -> None:
        self._open_frame_nudge = False
        self._open_preview_should_pause = False
        self._open_pause_ui_done = True
        self._open_nudge_timer.stop()
        self._restore_open_nudge_audio()
        self._holding_open_pause = False
        self._open_pause_pending = False
        self._play_after_open_pause = False
        if keep_playing:
            self._set_play_button_state(True)
            if self._player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
                self._play_media()
            self._maybe_show_restart_link()
            return
        self._player.pause()
        self._set_play_button_state(False)

    def _on_restart_from_start_clicked(self) -> None:
        self._hide_restart_from_start_link()
        self._open_frame_nudge = False
        self._open_preview_should_pause = False
        self._open_pause_ui_done = True
        self._open_nudge_timer.stop()
        self._restore_open_nudge_audio()
        self._holding_open_pause = False
        self._open_pause_pending = False
        self._play_after_open_pause = False
        if self._media_path is not None:
            self._forget_playback_position(self._media_path)
        self._open_pause_applied_ms = 0
        self._seek_to(0.0, play=True)

    def _clamp_position_ms(self, position_ms: int) -> int:
        position_ms = max(0, int(position_ms))
        duration = self._player.duration()
        if duration > 0:
            position_ms = min(position_ms, max(0, duration - 1))
        return position_ms

    def _seek_to(self, seconds: float, *, play: bool | None = None) -> None:
        if self._media_path is None:
            return

        self._open_pause_pending = False
        self._holding_open_pause = False
        self._play_after_open_pause = False
        self._open_pause_ui_done = True
        self._open_nudge_timer.stop()
        nudging = self._open_frame_nudge or self._open_preview_should_pause
        if nudging:
            self._open_frame_nudge = False
            self._open_preview_should_pause = False
            self._open_nudge_timer.stop()
            self._restore_open_nudge_audio()
        position_ms = self._clamp_position_ms(int(round(float(seconds) * 1000)))
        was_playing = (
            not nudging
            and self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
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

        self._cancel_open_preview()
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
            self._player.setVideoSink(self._video_widget.video_sink())
            QTimer.singleShot(0, self._media_viewport.refresh_stacking)

    def _on_media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        if status in (
            QMediaPlayer.MediaStatus.LoadedMedia,
            QMediaPlayer.MediaStatus.BufferedMedia,
        ) and self._open_pause_pending:
            self._begin_open_pause()
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._remember_playback_position()
        if not self._awaiting_reload_seek:
            if status == QMediaPlayer.MediaStatus.InvalidMedia:
                self._open_pause_pending = False
                self._hide_restart_from_start_link()
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
        if self._holding_open_pause and not should_play:
            should_play = False
        elif (
            self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
            or should_play
        ) and not self._open_frame_nudge:
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
            self._set_mute_button_state(False)
            self.mute_btn.blockSignals(False)
            self._audio_output.setMuted(False)

    def _on_mute_toggled(self, muted: bool) -> None:
        self._audio_output.setMuted(muted)
        self._set_mute_button_state(muted)

    def _open_cloud_account_settings(self) -> None:
        if getattr(self, "_settings_menu", None) is not None:
            self._settings_menu.close()
        CloudAccountDialog(self._config, self).exec()

    def _account_snapshot(self) -> tuple[str, str, str]:
        return (
            self._config.cloud_server_url or DEFAULT_CLOUD_SERVER,
            (self._config.cloud_username or "").strip(),
            self._config.cloud_password or "",
        )

    def _require_cloud_account(self) -> bool:
        _url, username, password = self._account_snapshot()
        if username and password:
            return True
        QMessageBox.information(
            self,
            "用户设置",
            "还没有用户名。请先在「设置 → 用户设置」填写用户名和密码并注册。\n"
            "不同设备使用同一个用户名，即为同一用户。",
        )
        return False

    def _start_cloud_task(self, work, on_success, title: str | None) -> None:
        progress = None
        host = QApplication.activeModalWidget() or self
        if title:
            progress = QProgressDialog(title, None, 0, 0, host)
            progress.setWindowTitle("云同步")
            progress.setWindowModality(Qt.WindowModality.WindowModal)
            progress.setCancelButton(None)
            progress.setMinimumDuration(0)
            progress.show()
        task = CloudTask(work, self)

        def ok(result) -> None:
            if progress is not None:
                progress.close()
            on_success(result)

        def fail(message: str) -> None:
            if progress is not None:
                progress.close()
            QMessageBox.warning(QApplication.activeModalWidget() or self, "云同步", message)

        task.succeeded.connect(ok)
        task.failed.connect(fail)
        task.finished.connect(task.deleteLater)
        self._cloud_task = task
        task.start()

    def _upload_current_subtitles(self) -> None:
        if not self._require_cloud_account():
            return
        if self._media_path is None:
            QMessageBox.information(self, "上传字幕", "请先打开视频。")
            return
        media = self._media_path
        account = self._account_snapshot()

        def work():
            items, skipped = collect_media_subtitles(media)
            if not items:
                return {"plan": [], "skipped": skipped}
            client = load_account_client(*account)
            remote = fetch_remote_index(client, {item.video_hash for item in items})
            return {"plan": classify_subtitles(items, remote), "skipped": skipped}

        self._start_cloud_task(work, self._confirm_subtitle_upload, "正在查看云端字幕…")

    def _upload_folder_subtitles(self) -> None:
        if not self._require_cloud_account():
            return
        chosen = QFileDialog.getExistingDirectory(
            self,
            "选择要上传字幕的文件夹",
            str(self._config.resolved_last_media_dir()),
        )
        if not chosen:
            return
        folder = Path(chosen)
        account = self._account_snapshot()

        def work():
            items, skipped = collect_folder_subtitles(folder)
            if not items:
                return {"plan": [], "skipped": skipped}
            client = load_account_client(*account)
            remote = fetch_remote_index(client, {item.video_hash for item in items})
            return {"plan": classify_subtitles(items, remote), "skipped": skipped}

        self._start_cloud_task(work, self._confirm_subtitle_upload, "正在查看云端字幕…")

    def _confirm_subtitle_upload(self, result: dict) -> None:
        plan = result.get("plan") or []
        skipped = list(result.get("skipped") or [])
        if not plan:
            QMessageBox.information(self, "上传字幕", "\n".join(skipped) or "没有可上传的字幕。")
            return
        conflicts = [
            (item.item.subtitle_name, item.remote_updated_at)
            for item in plan
            if item.state == "changed"
        ]
        choice = confirm_subtitle_replace(self, conflicts, allow_skip=len(plan) > 1) if conflicts else "replace"
        if choice == "cancel":
            return
        selected = []
        same: list[str] = []
        for candidate in plan:
            if candidate.state == "same":
                same.append(f"{candidate.item.subtitle_name}：已是最新")
            elif candidate.state == "changed" and choice != "replace":
                skipped.append(f"{candidate.item.subtitle_name}：已跳过")
            else:
                selected.append(candidate.item)
        if not selected:
            QMessageBox.information(self, "上传字幕", "\n".join(same + skipped) or "没有需要上传的字幕。")
            return
        shared = confirm_share_upload(self)
        if shared is None:
            return
        account = self._account_snapshot()

        def work():
            client = load_account_client(*account)
            uploaded = upload_subtitles(client, selected, shared)
            return uploaded, same, skipped, shared

        self._start_cloud_task(work, self._show_subtitle_upload_result, "正在上传字幕…")

    def _show_subtitle_upload_result(self, result: tuple) -> None:
        uploaded, same, skipped, shared = result
        lines = [f"已上传 {len(uploaded)} 个字幕" + ("，并共享给其他用户" if shared else "")]
        lines.extend(uploaded)
        if same:
            lines.append("")
            lines.extend(same)
        if skipped:
            lines.append("")
            lines.extend(skipped)
        QMessageBox.information(self, "上传字幕", "\n".join(lines))

    def _upload_current_tags(self) -> None:
        if not self._require_cloud_account():
            return
        if self._media_path is None:
            QMessageBox.information(self, "上传标签", "请先打开视频。")
            return
        items, skipped = collect_media_tags(self._media_path)
        if not items:
            QMessageBox.information(self, "上传标签", "\n".join(skipped) or "当前视频没有可上传的标签。")
            return
        self._upload_tag_items(items, skipped)

    def _upload_folder_tags(self) -> None:
        if not self._require_cloud_account():
            return
        chosen = QFileDialog.getExistingDirectory(
            self,
            "选择要上传标签的文件夹",
            str(self._config.resolved_last_media_dir()),
        )
        if not chosen:
            return
        items, skipped = collect_folder_tags(Path(chosen))
        if not items:
            QMessageBox.information(self, "上传标签", "\n".join(skipped) or "这个文件夹里没有可上传的标签。")
            return
        self._upload_tag_items(items, skipped)

    def _upload_tag_items(self, items, skipped: list[str]) -> None:
        account = self._account_snapshot()

        def work():
            client = load_account_client(*account)
            uploaded, unchanged = upload_tags(client, items)
            return uploaded, unchanged, skipped

        self._start_cloud_task(work, self._show_tag_upload_result, "正在上传标签…")

    def _show_tag_upload_result(self, result: tuple) -> None:
        uploaded, unchanged, skipped = result
        lines = [f"已同步 {len(uploaded)} 个标签文件" if uploaded else "没有新的标签变更"]
        lines.extend(uploaded)
        if unchanged:
            lines.append("")
            lines.extend(f"{name}：和云端相比没有变化" for name in unchanged)
        if skipped:
            lines.append("")
            lines.extend(skipped)
        QMessageBox.information(self, "上传标签", "\n".join(lines))

    def _schedule_cloud_check(self) -> None:
        if self._media_path is None:
            return
        if not self._config.cloud_username.strip() or not self._config.cloud_password:
            return
        media = self._media_path
        account = (
            self._config.cloud_server_url or DEFAULT_CLOUD_SERVER,
            self._config.cloud_username,
            self._config.cloud_password,
        )

        def work():
            client = load_account_client(*account)
            return inspect_open_media(client, media)

        task = CloudTask(work, self)
        task.succeeded.connect(lambda result, media=media, username=account[1]: self._offer_cloud_updates(media, username, result))
        task.failed.connect(lambda _message: None)
        task.finished.connect(task.deleteLater)
        self._cloud_check_task = task
        task.start()

    def _offer_cloud_updates(self, media: Path, username: str, update) -> None:
        if self._media_path is None or self._media_path != media:
            return
        if update.subtitles and confirm_subtitle_download(self, update.subtitles):
            for item in update.subtitles:
                apply_subtitle_download(username, item)
            current = None
            index = self.subtitle_combo.currentIndex()
            if index >= 0:
                current = self.subtitle_combo.itemData(index)
            self._refresh_subtitle_options()
            if current:
                self._select_subtitle_path(Path(str(current)))
            elif self.subtitle_combo.count() > 0:
                self.subtitle_combo.setCurrentIndex(0)
                self._load_subtitle_at_index(0)
        if self._media_path != media:
            return
        if update.tags and confirm_tag_download(self, update.tags):
            for item in update.tags:
                apply_tag_download(username, item)
            self._reload_current_subtitle_tags()
        if self._media_path != media:
            return
        if update.shares and not find_valid_subtitles(media):
            chosen = choose_shared_subtitle(self, update.shares)
            if chosen and chosen.get("username"):
                self._download_shared_subtitles(media, str(chosen["username"]))

    def _download_shared_subtitles(self, media: Path, sharer: str) -> None:
        account = self._account_snapshot()

        def work():
            client = load_account_client(*account)
            return client.get_share(video_content_hash(media), sharer)

        def done(payload: dict) -> None:
            if self._media_path != media:
                return
            shares = payload.get("shares") if isinstance(payload, dict) else None
            subtitles = []
            if shares:
                subtitles = shares[0].get("subtitles") or []
            written = apply_shared_download(media, subtitles)
            if not written:
                QMessageBox.information(self, "共享字幕", "没有下载到字幕。")
                return
            self._refresh_subtitle_options()
            self._select_subtitle_path(written[0])
            QMessageBox.information(self, "共享字幕", f"已同步 {sharer} 分享的 {len(written)} 个字幕。")

        self._start_cloud_task(work, done, "正在同步共享字幕…")

    def _sync_tag_files(self) -> None:
        if self._media_path is None:
            QMessageBox.information(self, "同步标签文件", "请先打开要接收标签的视频。")
            return
        folder = self._media_path.parent
        answer = QMessageBox.question(
            self,
            "同步标签文件",
            "把其它设备上复制来的标签文件，合并到当前视频所在的文件夹。\n\n"
            "使用方式：\n"
            "1. 在这台设备上打开要接收标签的视频。\n"
            "2. 确认后选择一个或多个标签文件（文件名形如 字幕名.srt.tags.json）。\n"
            "3. 只有当前视频文件夹里存在同名字幕时，这个标签文件才会被接受。\n\n"
            "注意事项：\n"
            "• 文件夹里还没有对应标签文件时，会直接复制过去。\n"
            "• 已经有标签文件时，会按时间和字幕正文合并，不会删掉这边已有的标签。\n"
            "• 个别对不上的标签会保留，打开字幕后出现在「未挂上」里。\n"
            "• 不会修改字幕正文，也不会改动你选中的那些源文件。",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Ok:
            return
        selected, _chosen_filter = QFileDialog.getOpenFileNames(
            self,
            "选择标签文件",
            str(folder),
            "标签文件 (*.tags.json)",
        )
        if not selected:
            return
        copied: list[str] = []
        merged: list[str] = []
        skipped: list[str] = []
        for name in selected:
            status, detail = sync_tag_file_into_folder(Path(name), folder)
            if status == "copied":
                copied.append(detail)
            elif status == "merged":
                merged.append(detail)
            else:
                skipped.append(detail)
        self._reload_current_subtitle_tags()
        lines = [f"已处理 {len(selected)} 个文件。"]
        if copied:
            lines.append("\n复制：\n" + "\n".join(copied))
        if merged:
            lines.append("\n合并：\n" + "\n".join(merged))
        if skipped:
            lines.append("\n跳过：\n" + "\n".join(skipped))
        QMessageBox.information(self, "同步标签文件", "\n".join(lines))

    def _batch_sync_tag_files(self) -> None:
        start_dir = str(self._media_path.parent) if self._media_path is not None else ""
        dialog = BatchTagSyncDialog(self, start_dir=start_dir)
        dialog.exec()
        affected = dialog.affected_video_dirs()
        if self._media_path is not None and self._media_path.parent.resolve() in affected:
            self._reload_current_subtitle_tags()

    def _extract_all_tags(self) -> None:
        start_dir = str(self._media_path.parent) if self._media_path is not None else ""
        dialog = ExtractTagsDialog(self, start_dir=start_dir)
        dialog.exec()
        output = dialog.output_dir()
        if output is None or self._media_path is None:
            return
        try:
            same_folder = self._media_path.parent.resolve() == output.resolve()
        except OSError:
            return
        if same_folder:
            self._reload_current_subtitle_tags()

    def _reload_current_subtitle_tags(self) -> None:
        if self._media_path is None or not self._segments:
            return
        self._load_tag_document_for_current_subtitle()
        self._bind_subtitle_tags()
        self._refresh_subtitle_list_texts()
        self._refresh_tag_filter_bar()
        self._apply_tag_row_filter()
        self._fill_unmatched_list()
        self._refresh_subtitle_list_item_layout()

    def _open_media_directory(self) -> None:
        if self._media_path is None:
            return
        folder = self._media_path.parent
        if not folder.is_dir():
            QMessageBox.information(self, "打开目录", f"找不到文件夹：\n{folder}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder.resolve())))

    def _open_transcribe_tool(self) -> None:
        """启动转写工具为独立进程，避免共用 CMD：关闭转写后台不会关掉播放器。"""
        if self._transcribe_proc is not None and self._transcribe_proc.poll() is None:
            if not focus_window_by_title(TRANSCRIBE_WINDOW_TITLE):
                QMessageBox.information(self, "提示", "音视频转字幕工具已在运行。")
            return

        app_root = app_dir()
        if is_frozen():
            cmd = [sys.executable, "--transcribe"]
        else:
            main_py = app_root / "main.py"
            if not main_py.is_file():
                self._error_box("无法启动", f"未找到转写工具入口：\n{main_py}")
                return
            cmd = [sys.executable, str(main_py), "--transcribe"]
        if self._media_path:
            cmd.append(str(self._media_path))
        try:
            self._transcribe_proc = spawn_hidden_console_process(cmd, cwd=app_root)
        except OSError as exc:
            self._error_box("无法启动", f"启动转写工具失败：\n{exc}")
            return
        self._transcribe_poll_timer.start()

    def _poll_transcribe_tool(self) -> None:
        proc = self._transcribe_proc
        if proc is None:
            self._transcribe_poll_timer.stop()
            return
        self._refresh_subtitles_keep_selection(load_new=True)
        if proc.poll() is not None:
            self._transcribe_proc = None
            self._transcribe_poll_timer.stop()
            self._on_transcription_finished()

    def _refresh_subtitles_keep_selection(self, load_new: bool = False) -> None:
        previous = self.subtitle_combo.currentData()
        old_count = self.subtitle_combo.count()
        self._refresh_subtitle_options()
        if previous:
            for index in range(self.subtitle_combo.count()):
                if self.subtitle_combo.itemData(index) == previous:
                    self.subtitle_combo.setCurrentIndex(index)
                    return
        if load_new and self.subtitle_combo.count() > old_count:
            self.subtitle_combo.setCurrentIndex(0)
            self._load_subtitle_at_index(0)

    def _open_video_download_tool(self) -> None:
        """启动视频下载器（独立进程，避免 CustomTkinter 与 Qt 事件循环冲突）。"""
        if self._video_download_proc is not None and self._video_download_proc.poll() is None:
            QMessageBox.information(self, "提示", "视频下载工具已在运行。")
            return

        if is_frozen():
            cmd = [sys.executable, "--download"]
            cwd = app_dir()
        else:
            tool_root = app_dir() / "video_downloader"
            main_py = tool_root / "main.py"
            if not main_py.is_file():
                self._error_box("无法启动", f"未找到视频下载工具：\n{main_py}")
                return
            cmd = [sys.executable, str(main_py)]
            cwd = tool_root

        try:
            self._video_download_proc = spawn_hidden_console_process(cmd, cwd=cwd)
        except OSError as exc:
            self._error_box("无法启动", f"启动视频下载工具失败：\n{exc}")
            return

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

    def _on_translate_hotkey_changed(self) -> None:
        translator = get_translator(self._config.translate_app)
        text = normalize_hotkey_text(
            self.translate_hotkey_edit.text(), translator.default_hotkey
        )
        if self.translate_hotkey_edit.text() != text:
            self.translate_hotkey_edit.blockSignals(True)
            self.translate_hotkey_edit.setText(text)
            self.translate_hotkey_edit.blockSignals(False)
        if text == self._config.translate_hotkey:
            return
        self._config.translate_hotkey = text
        save_config(self._config)

    def _open_translate_hotkey_help(self) -> None:
        self._on_translate_hotkey_changed()
        if getattr(self, "_settings_menu", None) is not None:
            self._settings_menu.close()
        translator = get_translator(self._config.translate_app)
        TranslateHotkeyHelpDialog(translator, self).exec()

    def _open_llm_settings(self) -> None:
        self._config = load_config()
        updated = LlmSettingsDialog.open_settings(self._config, self)
        if updated is not None:
            self._config = updated

    def _open_onscreen_subtitle_settings(self) -> None:
        self._config = load_config()
        current = OnScreenSubtitleStyle.from_config(self._config)
        current.subtitle_list_visible = self._subtitle_list_visible
        previous = OnScreenSubtitleStyle.from_config(self._config)
        previous.subtitle_list_visible = self._subtitle_list_visible

        def on_preview(style: OnScreenSubtitleStyle) -> None:
            # Keep width from live drag unless dialog carries a newer value.
            style.immersive_list_width_percent = int(self._immersive_list_width_percent)
            self._onscreen_overlay.set_style(style)
            self._apply_immersive_from_style(style)

        result = OnScreenSubtitleSettingsDialog.edit_style(
            current, self, on_preview=on_preview
        )
        if result is None:
            self._onscreen_overlay.set_style(previous)
            self._apply_immersive_from_style(previous)
            return
        result.immersive_list_width_percent = int(self._immersive_list_width_percent)
        apply_onscreen_style_to_config(self._config, result)
        self._onscreen_overlay.set_style(result)
        self._apply_immersive_from_style(result)
        self._update_onscreen_subtitle(self._player.position() / 1000.0)
        self._media_viewport.refresh_stacking()

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
            self._error_box("无法生成", "未找到有效字幕文件，无法生成笔记。")
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
            self._error_box("纯文字导出失败", str(exc))
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

        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()

        self._action_vocabulary.setEnabled(False)
        self._update_live_status("生词表：分析中…")
        self._show_vocabulary_progress()

        self._vocabulary_worker = VocabularyWorker(self._media_path, options, self._config)
        self._vocabulary_worker.status_changed.connect(self._on_vocabulary_status)
        self._vocabulary_worker.finished_ok.connect(self._on_vocabulary_finished)
        self._vocabulary_worker.failed.connect(self._on_vocabulary_failed)
        self._vocabulary_worker.start()

    def _on_vocabulary_status(self, message: str) -> None:
        self._update_live_status(f"生词表：{message}")
        if self._vocabulary_progress is not None:
            self._vocabulary_progress.set_status(message)

    def _on_vocabulary_finished(self, markdown_path: str, csv_path: str) -> None:
        self._action_vocabulary.setEnabled(True)
        self._vocabulary_worker = None
        self._update_live_status("")
        if self._vocabulary_progress is not None:
            self._vocabulary_progress.finish_success()
            self._pending_vocabulary_paths = (markdown_path, csv_path)
            QTimer.singleShot(700, self._close_vocabulary_progress_and_notify_success)
        else:
            self._notify_vocabulary_success(markdown_path, csv_path)

    def _on_vocabulary_failed(self, message: str) -> None:
        self._action_vocabulary.setEnabled(True)
        self._vocabulary_worker = None
        self._update_live_status("")
        if self._vocabulary_progress is not None:
            self._vocabulary_progress.finish_failure(message)
            self._pending_vocabulary_error = message
            QTimer.singleShot(500, self._close_vocabulary_progress_and_notify_failure)
        else:
            self._error_box("生词表生成失败", message)

    def _show_vocabulary_progress(self) -> None:
        self._close_vocabulary_progress()
        if not self._media_path:
            return
        self._vocabulary_progress = VocabularyProgressDialog(self._media_path.name, self)
        self._vocabulary_progress.start()

    def _close_vocabulary_progress(self) -> None:
        if self._vocabulary_progress is not None:
            self._vocabulary_progress.allow_close()
            self._vocabulary_progress.close()
            self._vocabulary_progress.deleteLater()
            self._vocabulary_progress = None

    def _notify_vocabulary_success(self, markdown_path: str, csv_path: str) -> None:
        QMessageBox.information(
            self,
            "生词表已生成",
            f"Markdown：\n{markdown_path}\n\nCSV：\n{csv_path}",
        )
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(markdown_path).resolve())))

    def _close_vocabulary_progress_and_notify_success(self) -> None:
        paths = self._pending_vocabulary_paths
        self._pending_vocabulary_paths = None
        self._close_vocabulary_progress()
        if paths:
            self._notify_vocabulary_success(paths[0], paths[1])

    def _close_vocabulary_progress_and_notify_failure(self) -> None:
        message = self._pending_vocabulary_error
        self._pending_vocabulary_error = ""
        self._close_vocabulary_progress()
        if message:
            self._error_box("生词表生成失败", message)

    def _stop_vocabulary_worker(self) -> None:
        if self._vocabulary_worker and self._vocabulary_worker.isRunning():
            self._vocabulary_worker.cancel()
            self._vocabulary_worker.wait(3000)
        self._vocabulary_worker = None
        if hasattr(self, "_action_vocabulary"):
            self._action_vocabulary.setEnabled(True)
        self._close_vocabulary_progress()

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
            self._error_box("AI 笔记生成失败", full_message)

    def _close_ai_notes_progress_and_notify_failure(self) -> None:
        message = self._pending_notes_output
        self._pending_notes_output = ""
        self._close_ai_notes_progress()
        if message:
            self._error_box("AI 笔记生成失败", message)

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
        if self._open_frame_nudge or self._open_preview_should_pause:
            self._open_frame_nudge = False
            self._open_preview_should_pause = False
            self._open_nudge_timer.stop()
            self._restore_open_nudge_audio()
        detail = message or self._player.errorString() or "未知错误"
        if self._try_recover_playback(detail):
            return
        now = time.monotonic()
        if now < self._error_dialog_suppressed_until:
            return
        self._error_dialog_suppressed_until = now + _ERROR_DIALOG_COOLDOWN_SEC
        self._error_box(
            "媒体播放失败",
            f"无法播放该文件：\n{detail}\n\n"
            "可尝试：重新打开该媒体文件后再点击字幕跳转。\n"
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
        self._playback_save_timer.stop()
        self._remember_playback_position()
        self._cancel_open_preview()
        self._stop_subtitle_repeat()
        self._persist_last_media_dir()
        self._stop_ai_notes_worker()
        self._stop_vocabulary_worker()
        self._player.stop()
        super().closeEvent(event)
