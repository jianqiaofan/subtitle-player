"""截图管理：按视频配套的 screenshots.json 扫描、分页并打开预览。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QRadioButton,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.config import AppConfig, load_config, save_config
from core.screenshots import (
    ManagedScreenshotFile,
    paginate_screenshots_by_path,
    scan_screenshots_under,
)
from gui.styles import DARK_STYLE

_COL_INDEX = 0
_COL_TITLE = 1
_COL_PATH = 2
_COL_NOTES = 3
_COL_CREATED = 4
_COL_MODIFIED = 5
_COL_VIEW = 6
_RECENT_LIMIT = 15


def _format_time(timestamp: float) -> str:
    try:
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return ""


class ScreenshotManageDialog(QDialog):
    view_requested = pyqtSignal(object, int)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        config: AppConfig | None = None,
        start_dir: str = "",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("截图管理")
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowCloseButtonHint
            | Qt.WindowType.WindowSystemMenuHint
        )
        self.setMinimumSize(980, 560)
        self.resize(1100, 640)
        self.setStyleSheet(
            DARK_STYLE
            + """
QToolButton#screenshotViewButton {
    background: transparent;
    border: none;
    padding: 0;
    margin: 0;
}
QToolButton#screenshotViewButton:hover {
    background-color: rgba(185, 128, 255, 0.22);
    border-radius: 4px;
}
"""
        )
        self._config = config if config is not None else load_config()
        self._start_dir = start_dir
        self._root: Path | None = None
        self._entries: list[ManagedScreenshotFile] = []
        self._page_index = 0
        # 每次打开默认显示全部
        self._page_mode = "all"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("文件夹"))
        self._folder_edit = QLineEdit()
        self._folder_edit.setReadOnly(True)
        self._folder_edit.setPlaceholderText("请选择要管理的文件夹")
        folder_row.addWidget(self._folder_edit, stretch=1)
        browse_btn = QPushButton("选择文件夹")
        browse_btn.clicked.connect(self._browse_folder)
        folder_row.addWidget(browse_btn)

        self._recent_btn = QPushButton("最近打开")
        self._recent_btn.setToolTip("从最近打开过的文件夹里选择")
        self._recent_menu = QMenu(self)
        self._recent_menu.aboutToShow.connect(self._rebuild_recent_menu)
        self._recent_btn.setMenu(self._recent_menu)
        folder_row.addWidget(self._recent_btn)
        layout.addLayout(folder_row)

        self._summary = QLabel(
            "请先选择一个文件夹。截屏保存导出的普通图片不在此列表中。"
        )
        self._summary.setObjectName("hintLabel")
        layout.addWidget(self._summary)

        self._table = QTableWidget(0, 7)
        self._table.setHorizontalHeaderLabels(
            ["序号", "截图标题", "路径", "笔记", "创建时间", "修改时间", "查看"]
        )
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(28)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(_COL_INDEX, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(_COL_TITLE, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(_COL_PATH, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(_COL_NOTES, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(_COL_CREATED, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(_COL_MODIFIED, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(_COL_VIEW, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(_COL_VIEW, 44)
        layout.addWidget(self._table, stretch=1)

        pager = QHBoxLayout()
        pager.addWidget(QLabel("分页"))
        self._mode_all = QRadioButton("全部")
        self._mode_path = QRadioButton("按路径")
        self._mode_group = QButtonGroup(self)
        self._mode_group.addButton(self._mode_all)
        self._mode_group.addButton(self._mode_path)
        self._mode_all.setChecked(True)
        self._mode_all.toggled.connect(self._on_mode_toggled)
        self._mode_path.toggled.connect(self._on_mode_toggled)
        pager.addWidget(self._mode_all)
        pager.addWidget(self._mode_path)

        pager.addStretch(1)
        self._prev_page_btn = QPushButton("上一页")
        self._prev_page_btn.clicked.connect(lambda: self._turn_page(-1))
        pager.addWidget(self._prev_page_btn)
        self._page_label = QLabel("第 0/0 页")
        pager.addWidget(self._page_label)
        self._next_page_btn = QPushButton("下一页")
        self._next_page_btn.clicked.connect(lambda: self._turn_page(1))
        pager.addWidget(self._next_page_btn)
        layout.addLayout(pager)

        self._view_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogContentsView)
        self._restore_last_folder()

    def entries(self) -> list[ManagedScreenshotFile]:
        return list(self._entries)

    def refresh_current_folder(self) -> None:
        if self._root is None or not self._root.is_dir():
            return
        self._load_folder(self._root, remember=False, keep_page=True)

    def _dialog_start_dir(self) -> str:
        current = self._folder_edit.text().strip()
        if current and Path(current).is_dir():
            return current
        for item in self._recent_dirs():
            return str(item)
        export_dir = str(getattr(self._config, "screenshot_export_dir", "") or "").strip()
        if export_dir and Path(export_dir).is_dir():
            return export_dir
        if self._start_dir and Path(self._start_dir).is_dir():
            return self._start_dir
        return ""

    def _recent_dirs(self) -> list[Path]:
        paths: list[Path] = []
        seen: set[str] = set()
        for item in list(getattr(self._config, "recent_screenshot_manage_dirs", None) or []):
            path = Path(str(item or "").strip())
            key = str(path)
            if not key or key in seen or not path.is_dir():
                continue
            seen.add(key)
            paths.append(path)
        return paths

    def _recent_label(self, path: Path, paths: list[Path]) -> str:
        same_name = [item for item in paths if item.name == path.name]
        if len(same_name) > 1:
            parent = path.parent.name or str(path.parent)
            return f"{path.name}  ({parent})"
        return path.name or str(path)

    def _rebuild_recent_menu(self) -> None:
        menu = self._recent_menu
        menu.clear()
        recent = self._recent_dirs()
        if not recent:
            empty = menu.addAction("暂无最近打开的文件夹")
            empty.setEnabled(False)
            return
        for path in recent:
            action = menu.addAction(self._recent_label(path, recent))
            action.setToolTip(str(path))
            action.triggered.connect(
                lambda _checked=False, target=path: self._load_folder(target, remember=True)
            )

    def _restore_last_folder(self) -> None:
        folder = self._dialog_start_dir()
        if not folder:
            return
        self._load_folder(Path(folder), remember=False)

    def _browse_folder(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self,
            "选择文件夹",
            self._dialog_start_dir(),
        )
        if not chosen:
            return
        self._load_folder(Path(chosen), remember=True)

    def _remember_folder(self, folder: Path) -> None:
        text = str(folder)
        recent = [text]
        for item in list(getattr(self._config, "recent_screenshot_manage_dirs", None) or []):
            path = str(item or "").strip()
            if path and path != text and path not in recent:
                recent.append(path)
            if len(recent) >= _RECENT_LIMIT:
                break
        self._config.screenshot_manage_dir = text
        self._config.recent_screenshot_manage_dirs = recent
        save_config(self._config)

    def _on_mode_toggled(self, checked: bool) -> None:
        if not checked:
            return
        mode = "path" if self._mode_path.isChecked() else "all"
        if mode == self._page_mode:
            return
        self._page_mode = mode
        self._page_index = 0
        self._render_page()

    def _turn_page(self, step: int) -> None:
        if self._page_mode != "path":
            return
        self._page_index = max(0, self._page_index + step)
        self._render_page()

    def _current_page_slice(self) -> tuple[list[ManagedScreenshotFile], int, int, int]:
        if self._page_mode != "path":
            return list(self._entries), 0, 1, 0
        page_entries, page_index, pages = paginate_screenshots_by_path(
            self._entries,
            self._page_index,
        )
        start = 0
        for earlier in range(page_index):
            earlier_entries, _, _ = paginate_screenshots_by_path(self._entries, earlier)
            start += len(earlier_entries)
        self._page_index = page_index
        return page_entries, page_index, pages, start

    def _load_folder(self, folder: Path, *, remember: bool, keep_page: bool = False) -> None:
        if not folder.is_dir():
            self._root = None
            self._entries = []
            self._folder_edit.clear()
            self._summary.setText("所选路径不是有效文件夹。")
            self._page_index = 0
            self._render_page()
            return
        self._root = folder
        self._folder_edit.setText(str(folder))
        self._entries = scan_screenshots_under(folder)
        count = len(self._entries)
        if count:
            self._summary.setText(
                f"共找到 {count} 个截图（不含截屏保存导出的普通图片）。"
            )
        else:
            self._summary.setText(
                "这个文件夹里没有找到有效截图（需要有对应视频，且配套 screenshots.json 里有登记）。"
                "截屏保存导出的普通图片不在此列表中。"
            )
        if not keep_page:
            self._page_index = 0
        self._render_page()
        if remember:
            self._remember_folder(folder)

    def _render_page(self) -> None:
        page_entries, page_index, pages, start = self._current_page_slice()
        path_mode = self._page_mode == "path"
        if path_mode:
            self._page_label.setText(f"第 {page_index + 1}/{pages} 页")
        else:
            self._page_label.setText(f"全部 {len(self._entries)} 条")
        self._prev_page_btn.setEnabled(path_mode and page_index > 0)
        self._next_page_btn.setEnabled(path_mode and page_index < pages - 1)
        self._fill_table(page_entries, start)

    def _fill_table(self, entries: list[ManagedScreenshotFile], start_index: int) -> None:
        self._table.setRowCount(0)
        self._table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            global_index = start_index + row
            values = [
                str(global_index + 1),
                entry.title,
                entry.relative_path,
                str(entry.note_count),
                _format_time(entry.created_at),
                _format_time(entry.modified_at),
            ]
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                if column in {_COL_INDEX, _COL_NOTES}:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter
                    )
                self._table.setItem(row, column, item)
            view_btn = QToolButton()
            view_btn.setObjectName("screenshotViewButton")
            view_btn.setIcon(self._view_icon)
            view_btn.setIconSize(QSize(16, 16))
            view_btn.setFixedSize(28, 24)
            view_btn.setAutoRaise(True)
            view_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            view_btn.setToolTip("查看")
            view_btn.clicked.connect(
                lambda _checked=False, index=global_index: self._request_view(index)
            )
            holder = QWidget()
            holder_layout = QHBoxLayout(holder)
            holder_layout.setContentsMargins(0, 0, 0, 0)
            holder_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            holder_layout.addWidget(view_btn)
            self._table.setCellWidget(row, _COL_VIEW, holder)
            self._table.setRowHeight(row, 28)

    def _request_view(self, global_index: int) -> None:
        if global_index < 0 or global_index >= len(self._entries):
            return
        self.hide()
        self.view_requested.emit(list(self._entries), global_index)
