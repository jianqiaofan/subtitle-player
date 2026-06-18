from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QEvent, Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QDragEnterEvent, QDropEvent
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from core.ai_notes import find_notes_path
from core.ai_notes_worker import AiNotesWorker
from core.config import CONFIG_PATH, LIVE_SYNC_FILENAME_LABEL, MEDIA_EXTENSIONS, is_deepseek_configured, load_config, save_config
from core.live_worker import LiveTranscribeWorker
from core.subtitle import SubtitleSegment, find_segment_index_at_time
from core.subtitle_loader import find_subtitles_for_media, load_subtitles
from core.subtitle_resolve import SubtitleAction, SubtitleChoice, auto_load_choice
from gui.llm_settings_dialog import LlmSettingsDialog
from gui.main_window import TranscribeWindow
from gui.styles import DARK_STYLE, PLAYER_LIST_STYLE
from gui.subtitle_choice_dialog import SubtitleChoiceDialog

AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma", ".opus"}


class PlayerWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self._media_path: Path | None = None
        self._segments: list[SubtitleSegment] = []
        self._current_subtitle_row = -1
        self._seeking = False
        self._subtitle_auto_follow = True
        self._transcribe_window: TranscribeWindow | None = None
        self._live_worker: LiveTranscribeWorker | None = None
        self._ai_notes_worker: AiNotesWorker | None = None
        self._live_mode = False
        self._transcribed_until = 0.0
        self._config = load_config()

        self.setWindowTitle("字幕播放器")
        self.setMinimumSize(1000, 640)
        self.resize(1180, 720)
        self.setAcceptDrops(True)
        self.setStyleSheet(DARK_STYLE + PLAYER_LIST_STYLE)

        self._player = QMediaPlayer()
        self._audio_output = QAudioOutput()
        self._audio_output.setVolume(1.0)
        self._player.setAudioOutput(self._audio_output)
        self._player.positionChanged.connect(self._on_position_changed)
        self._player.durationChanged.connect(self._on_duration_changed)
        self._player.playbackStateChanged.connect(self._on_playback_state_changed)
        self._player.errorOccurred.connect(self._on_player_error)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        root.addLayout(self._build_toolbar())
        root.addWidget(self._build_main_splitter(), stretch=1)
        root.addLayout(self._build_controls())

    def _build_toolbar(self) -> QHBoxLayout:
        bar = QHBoxLayout()

        open_btn = QPushButton("打开媒体")
        open_btn.clicked.connect(self._open_media)
        bar.addWidget(open_btn)

        bar.addWidget(QLabel("字幕"))
        self.subtitle_combo = QComboBox()
        self.subtitle_combo.setMinimumWidth(180)
        self.subtitle_combo.currentIndexChanged.connect(self._on_subtitle_selected)
        bar.addWidget(self.subtitle_combo, stretch=1)

        transcribe_btn = QPushButton("音视频转字幕")
        transcribe_btn.setObjectName("primaryButton")
        transcribe_btn.clicked.connect(self._open_transcribe_tool)
        bar.addWidget(transcribe_btn)

        live_btn = QPushButton("边播边转")
        live_btn.clicked.connect(self._start_live_transcribe_manual)
        bar.addWidget(live_btn)

        self.media_label = QLabel("未加载媒体文件")
        self.media_label.setObjectName("hintLabel")
        bar.addWidget(self.media_label, stretch=1)

        bar.addStretch(1)
        self.llm_settings_btn = QPushButton("大模型配置")
        self.llm_settings_btn.clicked.connect(self._open_llm_settings)
        bar.addWidget(self.llm_settings_btn)

        self.view_notes_btn = QPushButton("查看笔记")
        self.view_notes_btn.clicked.connect(self._view_ai_notes)
        self.view_notes_btn.setEnabled(False)
        bar.addWidget(self.view_notes_btn)

        self.ai_notes_btn = QPushButton("AI笔记")
        self.ai_notes_btn.setObjectName("primaryButton")
        self.ai_notes_btn.clicked.connect(self._generate_ai_notes)
        bar.addWidget(self.ai_notes_btn)
        return bar

    def _build_main_splitter(self) -> QSplitter:
        splitter = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)

        self._video_widget = QVideoWidget()
        self._video_widget.setMinimumSize(480, 270)
        self._video_widget.setAutoFillBackground(False)
        self._player.setVideoOutput(self._video_widget)
        left_layout.addWidget(self._video_widget, stretch=1)

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
        self.subtitle_list.itemClicked.connect(self._on_subtitle_clicked)
        right_layout.addWidget(self.subtitle_list)
        self._subtitle_panel.installEventFilter(self)

        splitter.addWidget(left)
        splitter.addWidget(self._subtitle_panel)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
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
        return bar

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
        self._player.setSource(QUrl.fromLocalFile(str(media_path)))

        is_audio = media_path.suffix.lower() in AUDIO_EXTENSIONS
        self._video_widget.setVisible(not is_audio)
        self._audio_placeholder.setVisible(is_audio)
        if not is_audio:
            self._player.setVideoOutput(self._video_widget)

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
            self.view_notes_btn.setEnabled(False)
            return
        self.view_notes_btn.setEnabled(find_notes_path(self._media_path) is not None)

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

        if choice.action == SubtitleAction.LIVE_TRANSCRIBE:
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

        self._live_mode = False
        self._refresh_subtitle_options()
        if self.subtitle_combo.count() > 0:
            self.subtitle_combo.setCurrentIndex(0)
            self._load_subtitle_at_index(0)
        else:
            self._begin_live_transcribe()
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
            self._player.play()

    def _start_live_transcribe_manual(self) -> None:
        if not self._media_path:
            QMessageBox.information(self, "提示", "请先打开媒体文件。")
            return
        choice = SubtitleChoiceDialog.ask(self._media_path, self)
        if choice is None:
            return
        if choice.action == SubtitleAction.LIVE_TRANSCRIBE:
            self._stop_live_transcribe()
        self._apply_subtitle_choice(choice)

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
            item = QListWidgetItem(self._format_subtitle_item(seg))
            item.setData(Qt.ItemDataRole.UserRole, seg.start)
            self.subtitle_list.insertItem(row, item)

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
            item = QListWidgetItem(self._format_subtitle_item(seg))
            item.setData(Qt.ItemDataRole.UserRole, seg.start)
            self.subtitle_list.addItem(item)
        self._current_subtitle_row = -1

    @staticmethod
    def _format_subtitle_item(seg: SubtitleSegment) -> str:
        start = PlayerWindow._format_clock(seg.start)
        end = PlayerWindow._format_clock(seg.end)
        text = seg.text.replace("\n", " / ")
        return f"[{start} → {end}] {text}"

    @staticmethod
    def _format_clock(seconds: float) -> str:
        total = int(seconds)
        hours, rem = divmod(total, 3600)
        minutes, secs = divmod(rem, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"

    def _on_subtitle_clicked(self, item: QListWidgetItem) -> None:
        start = item.data(Qt.ItemDataRole.UserRole)
        if start is None:
            return
        self._seeking = True
        self._player.setPosition(int(float(start) * 1000))
        self._seeking = False
        self._notify_live_seek(float(start))
        if self._player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            self._player.play()

    def _toggle_play(self) -> None:
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

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
        if obj is self._subtitle_panel:
            if event.type() == QEvent.Type.Enter:
                self._subtitle_auto_follow = False
            elif event.type() == QEvent.Type.Leave:
                self._subtitle_auto_follow = True
                self._current_subtitle_row = -1
                if self._player.duration() > 0:
                    self._sync_subtitle_highlight(self._player.position() / 1000.0, force=True)
        return super().eventFilter(obj, event)

    def _on_slider_pressed(self) -> None:
        self._seeking = True

    def _on_slider_released(self) -> None:
        self._player.setPosition(self.position_slider.value())
        self._seeking = False
        self._notify_live_seek(self.position_slider.value() / 1000.0)

    def _on_slider_moved(self, value: int) -> None:
        if self._seeking:
            self._update_time_label(value, self._player.duration())

    def _on_speed_changed(self, index: int) -> None:
        if index < 0:
            return
        rate = self.speed_combo.itemData(index)
        if rate is not None:
            self._player.setPlaybackRate(float(rate))

    def _on_volume_changed(self, value: int) -> None:
        self._audio_output.setVolume(value / 100.0)
        self.volume_label.setText(f"{value}%")
        if value > 0 and self.mute_btn.isChecked():
            self.mute_btn.blockSignals(True)
            self.mute_btn.setChecked(False)
            self.mute_btn.setText("静音")
            self.mute_btn.blockSignals(False)
            self._audio_output.setMuted(False)

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

        self.ai_notes_btn.setEnabled(False)
        self._update_live_status("AI 笔记：准备中…")

        self._ai_notes_worker = AiNotesWorker(self._media_path, self._config)
        self._ai_notes_worker.status_changed.connect(self._on_ai_notes_status)
        self._ai_notes_worker.finished_ok.connect(self._on_ai_notes_finished)
        self._ai_notes_worker.failed.connect(self._on_ai_notes_failed)
        self._ai_notes_worker.start()

    def _on_ai_notes_status(self, message: str) -> None:
        self._update_live_status(f"AI 笔记：{message}")

    def _on_ai_notes_finished(self, output_path: str) -> None:
        self.ai_notes_btn.setEnabled(True)
        self._ai_notes_worker = None
        self._update_live_status("")
        self._update_notes_buttons()
        QMessageBox.information(
            self,
            "AI 笔记已生成",
            f"笔记已保存至：\n{output_path}",
        )

    def _on_ai_notes_failed(self, message: str) -> None:
        self.ai_notes_btn.setEnabled(True)
        self._ai_notes_worker = None
        self._update_live_status("")
        hint = ""
        if "API Key" in message:
            hint = f"\n\n请编辑配置文件填写密钥：\n{CONFIG_PATH}"
        QMessageBox.warning(self, "AI 笔记生成失败", f"{message}{hint}")

    def _stop_ai_notes_worker(self) -> None:
        if self._ai_notes_worker and self._ai_notes_worker.isRunning():
            self._ai_notes_worker.cancel()
            self._ai_notes_worker.wait(3000)
        self._ai_notes_worker = None
        self.ai_notes_btn.setEnabled(True)

    def _on_player_error(self, error: QMediaPlayer.Error, message: str = "") -> None:
        if error == QMediaPlayer.Error.NoError:
            return
        detail = message or self._player.errorString() or "未知错误"
        QMessageBox.warning(
            self,
            "媒体播放失败",
            f"无法播放该文件：\n{detail}\n\n"
            "若为视频黑屏，请确认已安装 Windows「HEVC 视频扩展」或「媒体功能包」，"
            "或尝试将视频转为 H.264 编码后再播放。",
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
        self._persist_last_media_dir()
        self._stop_live_transcribe()
        self._stop_ai_notes_worker()
        self._player.stop()
        if self._transcribe_window is not None:
            self._transcribe_window.close()
        super().closeEvent(event)
