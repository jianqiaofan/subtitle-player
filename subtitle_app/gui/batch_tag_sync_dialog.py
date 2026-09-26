from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.config import load_config, save_config
from core.subtitle_tags import TagVideoMatch, find_batch_tag_matches, sync_tag_file_into_folder
from gui.styles import DARK_STYLE

_COL_CHECK = 0
_COL_NAME = 1
_COL_PATH = 2
_COL_TAG = 3
_COL_EXISTS = 4


class BatchTagSyncDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, start_dir: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("批量同步标签")
        self.setMinimumSize(860, 560)
        self.resize(980, 640)
        self.setStyleSheet(
            DARK_STYLE
            + """
QTableWidget::indicator {
    width: 16px;
    height: 16px;
    border: 2px solid #d7b6ff;
    border-radius: 3px;
    background-color: #1e1e1e;
}
QTableWidget::indicator:checked {
    border: 2px solid #f3e8ff;
    background-color: #b980ff;
}
QTableWidget::indicator:hover {
    border: 2px solid #ffffff;
}
"""
        )
        self._start_dir = start_dir
        self._tag_files: list[Path] = []
        self._matches: list[TagVideoMatch] = []

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "先选择一个或多个标签文件，再选择要接收标签的视频文件夹（包含子文件夹）。\n"
                "标签按字幕名匹配同名视频。视频旁已有标签时会合并，不会覆盖原来的标签。"
            )
        )

        self.tag_files_edit = QLineEdit()
        self.tag_files_edit.setPlaceholderText("可多选标签文件")
        self.tag_files_edit.setReadOnly(True)
        tag_btn = QPushButton("选择文件…")
        tag_btn.clicked.connect(self._browse_tag_files)
        layout.addLayout(self._folder_row("标签文件", self.tag_files_edit, tag_btn))

        self.video_dir_edit = QLineEdit()
        self.video_dir_edit.setPlaceholderText("视频文件所在文件夹")
        self.video_dir_edit.setReadOnly(True)
        video_btn = QPushButton("浏览…")
        video_btn.clicked.connect(self._browse_video_dir)
        layout.addLayout(self._folder_row("视频文件夹", self.video_dir_edit, video_btn))

        find_row = QHBoxLayout()
        find_btn = QPushButton("查找匹配")
        find_btn.clicked.connect(self._find_matches)
        find_row.addWidget(find_btn)
        find_row.addStretch(1)
        layout.addLayout(find_row)

        self.summary_label = QLabel("请选择标签文件和视频文件夹后查找匹配。")
        self.summary_label.setObjectName("hintLabel")
        layout.addWidget(self.summary_label)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["选择", "视频文件名", "文件路径", "标签文件", "已有标签"]
        )
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(48)
        header.setSectionResizeMode(_COL_CHECK, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(_COL_NAME, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(_COL_PATH, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(_COL_TAG, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(_COL_EXISTS, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemChanged.connect(self._update_summary)
        layout.addWidget(self.table, stretch=1)

        select_row = QHBoxLayout()
        select_all = QPushButton("全选")
        select_all.clicked.connect(lambda: self._set_checks(Qt.CheckState.Checked))
        select_none = QPushButton("全不选")
        select_none.clicked.connect(lambda: self._set_checks(Qt.CheckState.Unchecked))
        invert = QPushButton("反选")
        invert.clicked.connect(self._invert_checks)
        select_row.addWidget(select_all)
        select_row.addWidget(select_none)
        select_row.addWidget(invert)
        select_row.addStretch(1)
        layout.addLayout(select_row)

        action_row = QHBoxLayout()
        action_row.addStretch(1)
        sync_btn = QPushButton("同步勾选项")
        sync_btn.setObjectName("primaryButton")
        sync_btn.clicked.connect(self._sync_checked)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.reject)
        action_row.addWidget(sync_btn)
        action_row.addWidget(close_btn)
        layout.addLayout(action_row)
        self._restore_last_selection()

    def affected_video_dirs(self) -> set[Path]:
        return getattr(self, "_affected_dirs", set())

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
        tag_files = [Path(item) for item in config.batch_tag_sync_files if Path(item).is_file()]
        if tag_files:
            self._apply_tag_files(tag_files)
        video_dir = config.batch_tag_sync_video_dir.strip()
        if video_dir and Path(video_dir).is_dir():
            self.video_dir_edit.setText(video_dir)

    def _remember_selection(self) -> None:
        config = load_config()
        if self._tag_files:
            config.batch_tag_sync_files = [str(path) for path in self._tag_files]
        video_dir = self.video_dir_edit.text().strip()
        if video_dir and Path(video_dir).is_dir():
            config.batch_tag_sync_video_dir = video_dir
        save_config(config)

    def _apply_tag_files(self, files: list[Path]) -> None:
        self._tag_files = files
        self.tag_files_edit.setText("；".join(path.name for path in files))
        self.tag_files_edit.setToolTip("\n".join(str(path) for path in files))

    def _browse_tag_files(self) -> None:
        start = self._dialog_start_dir(self._tag_files[0].parent if self._tag_files else None)
        selected, _chosen_filter = QFileDialog.getOpenFileNames(
            self,
            "选择标签文件",
            start,
            "标签文件 (*.tags.json)",
        )
        if not selected:
            return
        self._apply_tag_files([Path(name) for name in selected])
        self._remember_selection()

    def _dialog_start_dir(self, preferred: Path | None) -> str:
        if preferred is not None and preferred.is_dir():
            return str(preferred)
        if self._start_dir and Path(self._start_dir).is_dir():
            return self._start_dir
        return ""

    def _browse_video_dir(self) -> None:
        self._browse_into(self.video_dir_edit, "选择视频文件夹")

    def _browse_into(self, edit: QLineEdit, title: str) -> None:
        current = self._dialog_start_dir(Path(edit.text().strip()) if edit.text().strip() else None)
        chosen = QFileDialog.getExistingDirectory(self, title, current)
        if chosen:
            edit.setText(chosen)
            self._remember_selection()

    def _find_matches(self) -> None:
        video_dir = Path(self.video_dir_edit.text().strip())
        if not self._tag_files or not video_dir.is_dir():
            QMessageBox.information(self, "批量同步标签", "请先选择标签文件，以及有效的视频文件夹。")
            return
        self._matches = find_batch_tag_matches(self._tag_files, video_dir)
        self._fill_table()

    def _fill_table(self) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(len(self._matches))
        for row, match in enumerate(self._matches):
            check = QTableWidgetItem()
            check.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsSelectable
            )
            check.setCheckState(Qt.CheckState.Unchecked)
            self.table.setItem(row, _COL_CHECK, check)
            self._set_text_item(row, _COL_NAME, match.video_path.name)
            self._set_text_item(row, _COL_PATH, str(match.video_path.parent))
            self._set_text_item(row, _COL_TAG, match.source_tag.name)
            exists_text = "是，将合并" if match.has_existing_tag else "否，将复制"
            self._set_text_item(row, _COL_EXISTS, exists_text)
        self.table.blockSignals(False)
        self._update_summary()

    def _set_text_item(self, row: int, column: int, text: str) -> None:
        item = QTableWidgetItem(text)
        item.setToolTip(text)
        self.table.setItem(row, column, item)

    def _checked_rows(self) -> list[int]:
        rows: list[int] = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, _COL_CHECK)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                rows.append(row)
        return rows

    def _set_checks(self, state: Qt.CheckState) -> None:
        self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            item = self.table.item(row, _COL_CHECK)
            if item is not None:
                item.setCheckState(state)
        self.table.blockSignals(False)
        self._update_summary()

    def _invert_checks(self) -> None:
        self.table.blockSignals(True)
        for row in range(self.table.rowCount()):
            item = self.table.item(row, _COL_CHECK)
            if item is None:
                continue
            item.setCheckState(
                Qt.CheckState.Unchecked
                if item.checkState() == Qt.CheckState.Checked
                else Qt.CheckState.Checked
            )
        self.table.blockSignals(False)
        self._update_summary()

    def _update_summary(self, _item=None) -> None:
        total = len(self._matches)
        checked = len(self._checked_rows()) if total else 0
        if total == 0:
            self.summary_label.setText("没有找到可同步的同名视频。标签需要能对应到视频旁边的字幕文件。")
            return
        self.summary_label.setText(f"找到 {total} 个匹配，已勾选 {checked} 个。默认不选中，勾选后才会同步。")

    def _sync_checked(self) -> None:
        rows = self._checked_rows()
        if not rows:
            QMessageBox.information(self, "批量同步标签", "请先勾选要同步的视频。")
            return
        copied: list[str] = []
        merged: list[str] = []
        skipped: list[str] = []
        affected: set[Path] = set()
        for row in rows:
            match = self._matches[row]
            status, detail = sync_tag_file_into_folder(match.source_tag, match.video_path.parent)
            line = f"{match.video_path.name}：{detail}"
            if status == "copied":
                copied.append(line)
                affected.add(match.video_path.parent.resolve())
            elif status == "merged":
                merged.append(line)
                affected.add(match.video_path.parent.resolve())
            else:
                skipped.append(line)
        self._affected_dirs = set(getattr(self, "_affected_dirs", set())) | affected
        lines = [f"已处理 {len(rows)} 项。"]
        if copied:
            lines.append("\n复制：\n" + "\n".join(copied))
        if merged:
            lines.append("\n合并：\n" + "\n".join(merged))
        if skipped:
            lines.append("\n跳过：\n" + "\n".join(skipped))
        QMessageBox.information(self, "批量同步标签", "\n".join(lines))
        self._matches = find_batch_tag_matches(
            self._tag_files,
            Path(self.video_dir_edit.text().strip()),
        )
        self._fill_table()
