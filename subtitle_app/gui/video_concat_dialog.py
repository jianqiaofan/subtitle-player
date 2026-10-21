"""选择多个视频、排序后拼成一个文件。"""

from __future__ import annotations

import threading
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QVBoxLayout,
)

from core.video_concat import ConcatCancelled, concat_videos, default_output_path
from gui.styles import DARK_STYLE

_OPEN_FILTER = "视频文件 (*.mp4 *.mkv *.mov *.m4v *.webm *.avi *.flv *.wmv *.ts *.mpeg *.mpg);;所有文件 (*.*)"
_SAVE_FILTER = "视频 (*.mp4 *.mkv *.mov *.m4v);;所有文件 (*.*)"


def start_video_concat(parent, initial_dir: str = "") -> None:
    paths, _selected = QFileDialog.getOpenFileNames(
        parent,
        "选择要拼接的视频",
        initial_dir,
        _OPEN_FILTER,
    )
    files = [Path(path) for path in paths if path]
    if not files:
        return
    if len(files) < 2:
        QMessageBox.information(parent, "视频拼接", "请至少选择 2 个视频。")
        return
    VideoConcatDialog(files, parent).exec()


class _ConcatThread(QThread):
    succeeded = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, sources: list[Path], output: Path, parent=None) -> None:
        super().__init__(parent)
        self._sources = sources
        self._output = output
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            concat_videos(self._sources, self._output, cancel_event=self._cancel)
        except ConcatCancelled:
            self.failed.emit("已取消")
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(str(self._output))


class VideoConcatDialog(QDialog):
    def __init__(self, files: list[Path], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("视频拼接")
        self.setStyleSheet(DARK_STYLE)
        self.resize(560, 460)
        self._thread: _ConcatThread | None = None
        self._progress: QProgressDialog | None = None

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("拖动或用上移、下移调整顺序。排在上面的视频会先播放。"))
        self._list = QListWidget()
        self._list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self._list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self._list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        for path in files:
            item = QListWidgetItem(path.name)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            item.setToolTip(str(path))
            self._list.addItem(item)
        self._list.model().rowsMoved.connect(lambda *_args: self._renumber())
        self._renumber()
        if self._list.count():
            self._list.setCurrentRow(0)
        layout.addWidget(self._list, stretch=1)

        buttons = QHBoxLayout()
        up = QPushButton("上移")
        up.clicked.connect(lambda: self._move(-1))
        down = QPushButton("下移")
        down.clicked.connect(lambda: self._move(1))
        buttons.addWidget(up)
        buttons.addWidget(down)
        buttons.addStretch(1)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        self._join = QPushButton("拼接")
        self._join.clicked.connect(self._choose_output)
        buttons.addWidget(cancel)
        buttons.addWidget(self._join)
        layout.addLayout(buttons)

    def ordered_paths(self) -> list[Path]:
        paths: list[Path] = []
        for index in range(self._list.count()):
            item = self._list.item(index)
            raw = item.data(Qt.ItemDataRole.UserRole) if item is not None else ""
            if raw:
                paths.append(Path(str(raw)))
        return paths

    def closeEvent(self, event) -> None:  # noqa: N802
        thread = self._thread
        if thread is not None and thread.isRunning():
            thread.cancel()
            thread.wait(5000)
        super().closeEvent(event)

    def _move(self, delta: int) -> None:
        row = self._list.currentRow()
        target = row + delta
        if row < 0 or target < 0 or target >= self._list.count():
            return
        item = self._list.takeItem(row)
        if item is None:
            return
        self._list.insertItem(target, item)
        self._list.setCurrentRow(target)
        self._renumber()

    def _renumber(self) -> None:
        for index in range(self._list.count()):
            item = self._list.item(index)
            if item is None:
                continue
            raw = item.data(Qt.ItemDataRole.UserRole)
            name = Path(str(raw)).name if raw else item.text()
            item.setText(f"{index + 1}. {name}")

    def _choose_output(self) -> None:
        files = self.ordered_paths()
        if len(files) < 2:
            QMessageBox.information(self, "视频拼接", "请至少选择 2 个视频。")
            return
        default = default_output_path(files[0])
        target, _selected = QFileDialog.getSaveFileName(
            self,
            "保存拼接后的视频",
            str(default),
            _SAVE_FILTER,
        )
        if not target:
            return
        output = Path(target)
        if not output.suffix:
            output = output.with_suffix(default.suffix or ".mp4")
        self._start(files, output)

    def _start(self, files: list[Path], output: Path) -> None:
        self._join.setEnabled(False)
        progress = QProgressDialog("正在拼接，请稍候…", "取消", 0, 0, self)
        progress.setWindowTitle("视频拼接")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        self._progress = progress
        thread = _ConcatThread(files, output, self)
        self._thread = thread
        progress.canceled.connect(thread.cancel)
        thread.succeeded.connect(self._on_success)
        thread.failed.connect(self._on_fail)
        thread.finished.connect(thread.deleteLater)
        progress.show()
        thread.start()

    def _on_success(self, path: str) -> None:
        self._close_progress()
        QMessageBox.information(self, "视频拼接", f"已保存：\n{path}")
        self.accept()

    def _on_fail(self, message: str) -> None:
        self._close_progress()
        self._join.setEnabled(True)
        if message == "已取消":
            return
        QMessageBox.warning(self, "视频拼接", message)

    def _close_progress(self) -> None:
        progress = self._progress
        self._progress = None
        if progress is not None:
            progress.close()
