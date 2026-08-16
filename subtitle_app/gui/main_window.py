from __future__ import annotations

import os
from pathlib import Path

from PyQt6.QtCore import QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QDragEnterEvent, QDropEvent
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.config import (
    INFERENCE_DEVICE_OPTIONS,
    LANGUAGE_OPTIONS,
    MEDIA_EXTENSIONS,
    OUTPUT_OPTIONS,
    load_config,
    save_config,
)
from core.console_window import (
    add_console_visibility_listener,
    console_button_label,
    mirror_log,
    reveal_console_on_error,
    toggle_console,
)
from core.transcriber import is_cuda_available
from core.worker import TranscribeWorker
from gui.styles import DARK_STYLE

STATUS_WAIT = "等待"
STATUS_RUNNING = "转写中"
STATUS_DONE = "完成"
STATUS_FAIL = "失败"
WINDOW_TITLE = "音视频转字幕"


class TranscribeWindow(QMainWindow):
    transcription_finished = pyqtSignal()

    def __init__(self, initial_files: list[Path] | None = None, parent=None) -> None:
        super().__init__(parent)
        self.config = load_config()
        self.worker: TranscribeWorker | None = None
        self._file_rows: dict[str, int] = {}

        self.setWindowTitle(WINDOW_TITLE)
        self.setMinimumSize(860, 640)
        self.resize(920, 700)
        self.setAcceptDrops(True)
        self.setStyleSheet(DARK_STYLE)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        root.addWidget(self._build_settings_group())
        root.addWidget(self._build_file_group(), stretch=2)
        root.addWidget(self._build_action_bar())
        root.addWidget(self._build_log_group(), stretch=1)

        if initial_files:
            self._add_files_to_table(initial_files)

    def _build_settings_group(self) -> QGroupBox:
        group = QGroupBox("转写设置")
        layout = QVBoxLayout(group)

        row_model = QHBoxLayout()
        row_model.addWidget(QLabel("模型文件"))
        self.model_edit = QLineEdit()
        self.model_edit.setPlaceholderText("选择 .bin 模型（如 win系统模型中等.bin）")
        row_model.addWidget(self.model_edit, stretch=1)
        browse_model = QPushButton("浏览")
        browse_model.clicked.connect(self._browse_model)
        row_model.addWidget(browse_model)

        row_model.addWidget(QLabel("线程数"))
        self.threads_spin = QSpinBox()
        self.threads_spin.setRange(0, 64)
        self.threads_spin.setSpecialValueText("自动")
        self.threads_spin.setValue(0)
        self.threads_spin.setToolTip("0 表示自动使用 CPU 核心数")
        row_model.addWidget(self.threads_spin)
        layout.addLayout(row_model)

        row_device = QHBoxLayout()
        row_device.addWidget(QLabel("推理设备"))
        self.inference_combo = QComboBox()
        for label, value in INFERENCE_DEVICE_OPTIONS:
            self.inference_combo.addItem(label, value)
        self.inference_combo.setToolTip(
            "自动：检测到 CUDA 版 pywhispercpp 时使用 GPU，否则 CPU。\n"
            "GPU 需先运行「安装CUDA推理.bat」。"
        )
        self.inference_combo.currentIndexChanged.connect(lambda _: self._refresh_inference_status())
        row_device.addWidget(self.inference_combo)
        self.inference_status = QLabel()
        self.inference_status.setObjectName("hintLabel")
        row_device.addWidget(self.inference_status, stretch=1)
        layout.addLayout(row_device)

        row_opts = QHBoxLayout()
        row_opts.addWidget(QLabel("语言"))
        self.lang_combo = QComboBox()
        for label, value in LANGUAGE_OPTIONS:
            self.lang_combo.addItem(label, value)
        row_opts.addWidget(self.lang_combo, stretch=1)

        row_opts.addWidget(QLabel("输出格式"))
        self.format_combo = QComboBox()
        for label, value in OUTPUT_OPTIONS:
            self.format_combo.addItem(label, value)
        row_opts.addWidget(self.format_combo, stretch=1)

        row_opts.addWidget(QLabel("输出目录"))
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("默认：与视频同目录")
        row_opts.addWidget(self.output_edit, stretch=2)
        browse_out = QPushButton("浏览")
        browse_out.clicked.connect(self._browse_output_dir)
        row_opts.addWidget(browse_out)
        layout.addLayout(row_opts)

        hint = QLabel(
            "内置 Whisper 推理引擎，直接加载 .bin 模型转写，无需 whisper.exe。"
            "「原文混排」将按语音停顿断点分片后逐段识别语种。"
        )
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._load_settings_to_ui()
        return group

    def _build_file_group(self) -> QGroupBox:
        group = QGroupBox("文件列表（支持拖拽）")
        layout = QVBoxLayout(group)

        bar = QHBoxLayout()
        add_btn = QPushButton("添加文件")
        add_btn.clicked.connect(self._add_files)
        add_dir_btn = QPushButton("添加文件夹")
        add_dir_btn.clicked.connect(self._add_folder)
        remove_btn = QPushButton("移除选中")
        remove_btn.clicked.connect(self._remove_selected)
        clear_btn = QPushButton("清空")
        clear_btn.clicked.connect(self._clear_files)
        bar.addWidget(add_btn)
        bar.addWidget(add_dir_btn)
        bar.addWidget(remove_btn)
        bar.addWidget(clear_btn)
        bar.addStretch()
        layout.addLayout(bar)

        self.file_table = QTableWidget(0, 3)
        self.file_table.setHorizontalHeaderLabels(["文件名", "状态", "输出路径"])
        self.file_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.file_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.file_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.file_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.file_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.file_table.setAlternatingRowColors(True)
        layout.addWidget(self.file_table)
        return group

    def _build_action_bar(self) -> QWidget:
        panel = QWidget()
        layout = QHBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("就绪")
        layout.addWidget(self.progress, stretch=1)

        self.start_btn = QPushButton("开始转写")
        self.start_btn.setObjectName("primaryButton")
        self.start_btn.clicked.connect(self._start)
        layout.addWidget(self.start_btn)

        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel)
        layout.addWidget(self.cancel_btn)

        open_btn = QPushButton("打开输出目录")
        open_btn.clicked.connect(self._open_output_dir)
        layout.addWidget(open_btn)

        self._console_btn = QPushButton(console_button_label())
        self._console_btn.setToolTip("显示或隐藏后台命令窗口")
        self._console_btn.clicked.connect(toggle_console)
        layout.addWidget(self._console_btn)
        add_console_visibility_listener(self._on_console_visibility_changed)
        return panel

    def _on_console_visibility_changed(self, visible: bool) -> None:
        def apply() -> None:
            if not hasattr(self, "_console_btn"):
                return
            self._console_btn.setText("隐藏后台" if visible else "查看后台")

        QTimer.singleShot(0, apply)

    def _build_log_group(self) -> QGroupBox:
        group = QGroupBox("运行日志")
        layout = QVBoxLayout(group)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)
        layout.addWidget(self.log_view)
        return group

    def _load_settings_to_ui(self) -> None:
        self.model_edit.setText(self.config.model_path)
        self.threads_spin.setValue(self.config.n_threads)

        idx = self.inference_combo.findData(self.config.inference_device)
        if idx >= 0:
            self.inference_combo.setCurrentIndex(idx)
        self._refresh_inference_status()

        idx = self.lang_combo.findData(self.config.language)
        if idx >= 0:
            self.lang_combo.setCurrentIndex(idx)
        idx = self.format_combo.findData(self.config.output_format)
        if idx >= 0:
            self.format_combo.setCurrentIndex(idx)
        self.output_edit.setText(self.config.output_dir)

    def _save_settings_from_ui(self) -> None:
        self.config.model_path = self.model_edit.text().strip()
        self.config.n_threads = self.threads_spin.value()
        self.config.inference_device = self.inference_combo.currentData()
        self.config.language = self.lang_combo.currentData()
        self.config.output_format = self.format_combo.currentData()
        self.config.output_dir = self.output_edit.text().strip()
        save_config(self.config)

    def _refresh_inference_status(self) -> None:
        if is_cuda_available():
            self.inference_status.setText("当前环境：已安装 CUDA 版 pywhispercpp，可选 GPU。")
        else:
            self.inference_status.setText(
                "当前环境：仅 CPU 版 pywhispercpp；要用 GPU 请运行「安装CUDA推理.bat」。"
            )

    def _browse_model(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择模型文件",
            self.model_edit.text() or str(Path.home()),
            "模型文件 (*.bin *.ggml *.gguf);;所有文件 (*.*)",
        )
        if path:
            self.model_edit.setText(path)

    def _append_log(self, text: str) -> None:
        self.log_view.appendPlainText(text)
        self.log_view.verticalScrollBar().setValue(
            self.log_view.verticalScrollBar().maximum()
        )
        mirror_log(text)

    def _browse_output_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if path:
            self.output_edit.setText(path)

    def _open_output_dir(self) -> None:
        self._save_settings_from_ui()
        files = self._get_selected_files()
        if self.config.output_dir.strip():
            path = Path(self.config.output_dir)
        elif files:
            path = files[0].parent
        else:
            QMessageBox.information(self, "提示", "请先添加视频文件，或指定输出目录。")
            return
        path.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _collect_media_files(self, paths: list[str]) -> list[Path]:
        result: list[Path] = []
        for raw in paths:
            path = Path(raw)
            if path.is_dir():
                for item in sorted(path.rglob("*")):
                    if item.is_file() and item.suffix.lower() in MEDIA_EXTENSIONS:
                        result.append(item)
            elif path.is_file() and path.suffix.lower() in MEDIA_EXTENSIONS:
                result.append(path)
        return result

    def _add_files_to_table(self, files: list[Path]) -> None:
        for file_path in files:
            key = str(file_path.resolve())
            if key in self._file_rows:
                continue
            row = self.file_table.rowCount()
            self.file_table.insertRow(row)
            self.file_table.setItem(row, 0, QTableWidgetItem(file_path.name))
            self.file_table.setItem(row, 1, QTableWidgetItem(STATUS_WAIT))
            self.file_table.setItem(row, 2, QTableWidgetItem(""))
            self.file_table.item(row, 0).setToolTip(key)
            self._file_rows[key] = row

    def _add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择音视频文件",
            "",
            "媒体文件 (*.mp4 *.mkv *.avi *.mov *.mp3 *.wav *.flac *.m4a *.webm);;所有文件 (*.*)",
        )
        if paths:
            self._add_files_to_table(self._collect_media_files(paths))

    def _add_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if path:
            self._add_files_to_table(self._collect_media_files([path]))

    def _remove_selected(self) -> None:
        rows = sorted({idx.row() for idx in self.file_table.selectedIndexes()}, reverse=True)
        for row in rows:
            item = self.file_table.item(row, 0)
            if item and item.toolTip():
                self._file_rows.pop(item.toolTip(), None)
            self.file_table.removeRow(row)

    def _clear_files(self) -> None:
        self.file_table.setRowCount(0)
        self._file_rows.clear()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        files = self._collect_media_files(paths)
        if files:
            self._add_files_to_table(files)
        event.acceptProposedAction()

    def _get_selected_files(self) -> list[Path]:
        files: list[Path] = []
        for row in range(self.file_table.rowCount()):
            item = self.file_table.item(row, 0)
            if item and item.toolTip():
                files.append(Path(item.toolTip()))
        return files

    def _set_row_status(self, file_path: str, status: str, output: str = "") -> None:
        row = self._file_rows.get(str(Path(file_path).resolve()))
        if row is None:
            return
        self.file_table.item(row, 1).setText(status)
        if output:
            self.file_table.item(row, 2).setText(output)

    def _build_transcribe_confirm_message(self, files: list[Path]) -> str:
        language = self.lang_combo.currentText()
        output_format = self.format_combo.currentText()
        suffix = self.config.language_filename_label()
        output_dir = self.config.output_dir.strip() or "与视频同目录"
        example_name = self.config.build_output_path(files[0]).name if files else ""

        lines = [
            "请确认以下转写设置是否正确：",
            "",
            f"识别语种：{language}",
            f"输出格式：{output_format}",
            f"待处理文件：{len(files)} 个",
            f"输出目录：{output_dir}",
            f"字幕命名：视频名_{suffix}.{self.config.output_format}",
        ]
        if example_name:
            lines.append(f"示例文件名：{example_name}")
        if self.config.language == "mixed":
            lines.append("")
            lines.append("原文混排将按语音停顿分片，逐段识别语种并输出原文。")
        lines.extend(["", "确认无误后点击「确定」开始转写。"])
        return "\n".join(lines)

    def _start(self) -> None:
        if self.worker and self.worker.isRunning():
            return

        files = self._get_selected_files()
        if not files:
            QMessageBox.warning(self, "提示", "请先添加至少一个音视频文件。")
            return

        self._save_settings_from_ui()
        try:
            self.config.validate_model()
        except RuntimeError as exc:
            reveal_console_on_error(str(exc))
            QMessageBox.warning(self, "模型无效", str(exc))
            return

        box = QMessageBox(self)
        box.setWindowTitle("确认转写设置")
        box.setText(self._build_transcribe_confirm_message(files))
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
        if box.exec() != QMessageBox.StandardButton.Ok:
            return

        self._begin_transcribe(files)

    def _begin_transcribe(self, files: list[Path]) -> None:
        self.log_view.clear()
        self._append_log("===== 开始批量转写 =====")
        self.start_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.progress.setValue(0)
        self.progress.setFormat("准备中...")

        for file_path in files:
            self._set_row_status(str(file_path), STATUS_WAIT, "")

        self.worker = TranscribeWorker(files, self.config)
        self.worker.log.connect(self._append_log)
        self.worker.file_started.connect(lambda p: self._set_row_status(p, STATUS_RUNNING))
        self.worker.file_finished.connect(
            lambda p, out: self._set_row_status(p, STATUS_DONE, out)
        )
        self.worker.file_failed.connect(
            lambda p, err: self._set_row_status(p, STATUS_FAIL)
        )
        self.worker.progress.connect(self._on_progress)
        self.worker.all_done.connect(self._on_all_done)
        self.worker.start()

    def _cancel(self) -> None:
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self._append_log("正在取消...")

    def _on_progress(self, current: int, total: int) -> None:
        percent = int((current - 1) / total * 100) if total else 0
        self.progress.setValue(max(0, min(100, percent)))
        self.progress.setFormat(f"处理中 {current}/{total}")

    def _on_all_done(self) -> None:
        self.progress.setValue(100)
        self.progress.setFormat("完成")
        self.start_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self._append_log("\n===== 全部任务结束 =====")
        self.transcription_finished.emit()
        QMessageBox.information(self, "完成", "转写任务已结束，请查看日志和输出目录。")


# 兼容旧入口
MainWindow = TranscribeWindow
