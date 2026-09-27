from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.config import load_config, save_config
from core.subtitle_tags import collect_tag_files
from gui.styles import DARK_STYLE

_HELP_TEXT = (
    "使用说明\n"
    "这个功能把这台设备上分散在各个文件夹里的标签，集中复制到一个地方。"
    "然后把那个文件夹拷到另一台设备，再用「批量同步标签」写回视频旁边。\n\n"
    "1. 「来源文件夹」选这台设备上存放视频和字幕的目录。子文件夹里的标签也会一起查找。\n"
    "2. 「保存到」另选一个文件夹，例如 U 盘，或一个新建的空文件夹。"
    "标签会平铺放在这里，不再按原来的子目录分开放。\n"
    "3. 点「开始提取」。来源文件夹里的原文件不会被修改，字幕正文也不会被改动。\n"
    "4. 到另一台设备后，打开「批量同步标签」，选择这里面的标签文件，再选择那台设备上的视频文件夹。\n\n"
    "不同子文件夹里如果有同名字幕，例如都叫 lesson_中文.srt，它们的标签会合并成一个文件："
    "对得上的句子合并标签和备注，对不上的句子会保留。保存位置里如果已经有同名标签，也会合并进去，不会覆盖。"
)


class ExtractTagsDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, start_dir: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("提取全部标签")
        self.setMinimumSize(640, 460)
        self.resize(720, 520)
        self.setStyleSheet(DARK_STYLE)
        self._start_dir = start_dir
        self._output_dir: Path | None = None

        layout = QVBoxLayout(self)
        help_label = QLabel(_HELP_TEXT)
        help_label.setWordWrap(True)
        help_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(help_label)

        self.source_edit = QLineEdit()
        self.source_edit.setReadOnly(True)
        self.source_edit.setPlaceholderText("存放视频、字幕和标签的文件夹")
        source_btn = QPushButton("浏览…")
        source_btn.clicked.connect(self._browse_source)
        layout.addLayout(self._folder_row("来源文件夹", self.source_edit, source_btn))

        self.dest_edit = QLineEdit()
        self.dest_edit.setReadOnly(True)
        self.dest_edit.setPlaceholderText("用来集中存放标签的另一个文件夹")
        dest_btn = QPushButton("浏览…")
        dest_btn.clicked.connect(self._browse_dest)
        layout.addLayout(self._folder_row("保存到", self.dest_edit, dest_btn))

        layout.addStretch(1)
        action_row = QHBoxLayout()
        action_row.addStretch(1)
        extract_btn = QPushButton("开始提取")
        extract_btn.setObjectName("primaryButton")
        extract_btn.clicked.connect(self._extract)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.reject)
        action_row.addWidget(extract_btn)
        action_row.addWidget(close_btn)
        layout.addLayout(action_row)
        self._restore_last_selection()

    def output_dir(self) -> Path | None:
        return self._output_dir

    def _folder_row(self, title: str, edit: QLineEdit, button: QPushButton) -> QHBoxLayout:
        row = QHBoxLayout()
        label = QLabel(title)
        label.setMinimumWidth(84)
        row.addWidget(label)
        row.addWidget(edit, stretch=1)
        row.addWidget(button)
        return row

    def _restore_last_selection(self) -> None:
        config = load_config()
        source = config.tag_extract_source_dir.strip()
        dest = config.tag_extract_dest_dir.strip()
        if source and Path(source).is_dir():
            self.source_edit.setText(source)
        if dest and Path(dest).is_dir():
            self.dest_edit.setText(dest)

    def _remember_selection(self) -> None:
        config = load_config()
        source = self.source_edit.text().strip()
        dest = self.dest_edit.text().strip()
        if source and Path(source).is_dir():
            config.tag_extract_source_dir = source
        if dest and Path(dest).is_dir():
            config.tag_extract_dest_dir = dest
        save_config(config)

    def _dialog_start_dir(self, current: str) -> str:
        if current and Path(current).is_dir():
            return current
        if self._start_dir and Path(self._start_dir).is_dir():
            return self._start_dir
        return ""

    def _browse_source(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self,
            "选择来源文件夹",
            self._dialog_start_dir(self.source_edit.text().strip()),
        )
        if not chosen:
            return
        self.source_edit.setText(chosen)
        self._remember_selection()

    def _browse_dest(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self,
            "选择保存位置",
            self._dialog_start_dir(self.dest_edit.text().strip()),
        )
        if not chosen:
            return
        self.dest_edit.setText(chosen)
        self._remember_selection()

    def _extract(self) -> None:
        source = Path(self.source_edit.text().strip())
        dest = Path(self.dest_edit.text().strip())
        if not source.is_dir() or not dest.is_dir():
            QMessageBox.information(self, "提取全部标签", "请先选择来源文件夹和保存位置。")
            return
        self._remember_selection()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            result = collect_tag_files(source, dest)
        finally:
            QApplication.restoreOverrideCursor()
        if result.error:
            QMessageBox.information(self, "提取全部标签", result.error)
            return
        if not result.copied and not result.merged and not result.skipped:
            QMessageBox.information(
                self,
                "提取全部标签",
                "来源文件夹及子文件夹里没有找到标签文件。",
            )
            return
        self._output_dir = dest.resolve()
        QMessageBox.information(self, "提取全部标签", self._result_text(result))

    def _result_text(self, result) -> str:
        lines = [
            f"已提取到：{self.dest_edit.text().strip()}",
            f"直接复制 {len(result.copied)} 个，合并 {len(result.merged)} 个，跳过 {len(result.skipped)} 个。",
        ]
        if result.merged:
            lines.append("\n合并：\n" + self._preview(result.merged))
        if result.skipped:
            lines.append("\n跳过：\n" + self._preview(result.skipped))
        lines.append("\n可以把这个文件夹拷到另一台设备，再用「批量同步标签」写回视频旁边。")
        return "\n".join(lines)

    def _preview(self, items: list[str], limit: int = 12) -> str:
        shown = items[:limit]
        text = "\n".join(shown)
        extra = len(items) - len(shown)
        if extra:
            text += f"\n…还有 {extra} 个"
        return text
