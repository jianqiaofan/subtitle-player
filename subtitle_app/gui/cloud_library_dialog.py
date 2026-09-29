"""查看和管理当前用户已经上传的字幕和标签。"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.cloud_api import CloudClient, CloudError
from core.cloud_sync import (
    classify_subtitles,
    collect_hashed_subtitles,
    fetch_remote_index,
    prepare_subtitle_from_hash_file,
    upload_subtitles,
)
from core.cloud_tags import build_upload_document
from core.config import MEDIA_EXTENSIONS
from core.subtitle_tags import load_tag_document, subtitle_belongs_to_media, subtitle_name_for_tag_path
from core.video_hash import video_content_hash
from gui.cloud_sync_ui import confirm_share_upload, confirm_subtitle_replace, format_cloud_time
from gui.styles import DARK_STYLE

_LIBRARY_STYLE = DARK_STYLE + """
QTabWidget::pane { border: 1px solid #555555; }
QTabBar::tab { background: #2b2b2b; color: white; padding: 8px 16px; }
QTabBar::tab:selected { background: #3a3a3a; color: #b980ff; }
QPlainTextEdit { background: #2b2b2b; color: white; }
"""


class CloudLibraryDialog(QDialog):
    def __init__(self, client: CloudClient, parent=None) -> None:
        super().__init__(parent)
        self._client = client
        self.setWindowTitle("云端管理")
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.setSizeGripEnabled(True)
        self.setMinimumSize(860, 420)
        self.resize(980, 520)
        self.setStyleSheet(_LIBRARY_STYLE)

        layout = QVBoxLayout(self)
        hint = QLabel(f"当前用户：{client.username}。这里只显示你自己上传到云端的字幕和标签。")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        tabs = QTabWidget()
        self._subtitle_table = self._make_table(["字幕文件", "视频", "更新时间", "共享"])
        self._subtitle_page, self._subtitle_buttons = self._make_page(
            self._subtitle_table,
            [
                ("字幕文件上传", self._add_subtitle),
                ("批量上传字幕", self._add_subtitle_folder),
                ("查看", self._view_subtitle),
                ("设为共享", self._toggle_share),
                ("删除", self._delete_subtitle),
                ("刷新", self.reload),
            ],
        )
        self._share_button = self._subtitle_buttons["设为共享"]
        self._subtitle_table.itemSelectionChanged.connect(self._update_subtitle_buttons)
        tabs.addTab(self._subtitle_page, "字幕管理")

        self._tag_table = self._make_table(["标签文件", "视频", "更新时间"])
        self._tag_page, self._tag_buttons = self._make_page(
            self._tag_table,
            [
                ("添加", self._add_tag),
                ("查看", self._view_tag),
                ("删除", self._delete_tag),
                ("刷新", self.reload),
            ],
        )
        self._tag_table.itemSelectionChanged.connect(self._update_tag_buttons)
        tabs.addTab(self._tag_page, "标签管理")
        layout.addWidget(tabs, stretch=1)
        self._subtitle_empty = self._empty_label(self._subtitle_page, "还没有上传过字幕。")
        self._tag_empty = self._empty_label(self._tag_page, "还没有上传过标签。")
        self._update_subtitle_buttons()
        self._update_tag_buttons()
        QTimer.singleShot(0, self.reload)

    def reload(self) -> None:
        def work():
            return self._client.list_library_subtitles(), self._client.list_library_tags()

        self._run(work, self._show_lists, "正在读取云端文件…")

    def _show_lists(self, result: tuple) -> None:
        subtitles, tags = result
        self._fill_subtitles(subtitles if isinstance(subtitles, list) else [])
        self._fill_tags(tags if isinstance(tags, list) else [])

    def _add_subtitle(self) -> None:
        chosen, _selected = QFileDialog.getOpenFileName(
            self,
            "选择要上传的字幕",
            self._start_directory(),
            "字幕 (*.srt *.vtt)",
        )
        if not chosen:
            return
        item, reason = prepare_subtitle_from_hash_file(Path(chosen))
        if item is None:
            QMessageBox.information(self, "无法上传", reason)
            return

        def work():
            remote = fetch_remote_index(self._client, {item.video_hash})
            return {"plan": classify_subtitles([item], remote), "skipped": []}

        self._run(work, self._confirm_subtitle_upload, "正在查看云端字幕…")

    def _add_subtitle_folder(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "选择要上传字幕的文件夹", self._start_directory())
        if not chosen:
            return
        folder = Path(chosen)

        def work():
            items, skipped = collect_hashed_subtitles(folder)
            if not items:
                return {"plan": [], "skipped": skipped}
            remote = fetch_remote_index(self._client, {item.video_hash for item in items})
            return {"plan": classify_subtitles(items, remote), "skipped": skipped}

        self._run(work, self._confirm_subtitle_upload, "正在查看云端字幕…")

    def _confirm_subtitle_upload(self, result: dict) -> None:
        plan = result.get("plan") or []
        skipped = list(result.get("skipped") or [])
        if not plan:
            QMessageBox.information(self, "无法上传", "\n".join(skipped) or "没有可上传的字幕。")
            return
        conflicts = [
            (item.item.subtitle_name, item.remote_updated_at) for item in plan if item.state == "changed"
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

        def work():
            uploaded = upload_subtitles(self._client, selected, shared)
            return uploaded, same, skipped, shared

        self._run(work, self._show_subtitle_upload_result, "正在上传字幕…")

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
        self.reload()

    def _start_directory(self) -> str:
        host = self.parent()
        while host is not None:
            config = getattr(host, "_config", None)
            resolver = getattr(config, "resolved_last_media_dir", None)
            if resolver is not None:
                return str(resolver())
            host = host.parent()
        return ""

    def _view_subtitle(self) -> None:
        item_id = self._selected_id(self._subtitle_table)
        if item_id is None:
            return
        name = self._selected_text(self._subtitle_table, 1)

        def work():
            return self._client.read_library_subtitle(item_id)

        self._run(work, lambda payload: self._show_text(name or "字幕", str(payload.get("content") or "")), "正在读取字幕…")

    def _toggle_share(self) -> None:
        targets = self._action_targets(self._subtitle_table)
        if not targets:
            return
        shared_flags = {item["shared"] for item in targets}
        if shared_flags == {True}:
            shared = False
        elif shared_flags == {False}:
            shared = True
        else:
            shared = self._choose_share_state(len(targets))
            if shared is None:
                return

        def work():
            failed: list[str] = []
            for item in targets:
                try:
                    self._client.set_library_subtitle_shared(item["id"], shared)
                except CloudError as exc:
                    failed.append(f"{item['name']}：{exc}")
            return failed

        self._run(work, self._after_batch, "正在更新共享设置…")

    def _delete_subtitle(self) -> None:
        targets = self._action_targets(self._subtitle_table)
        if not targets or not self._confirm_delete_many("字幕", [item["name"] for item in targets]):
            return

        def work():
            return self._delete_targets(targets, self._client.delete_library_subtitle)

        self._run(work, self._after_batch, "正在删除字幕…")

    def _add_tag(self) -> None:
        video = self._pick_video()
        if video is None:
            return
        tag_path_text, _selected = QFileDialog.getOpenFileName(
            self,
            "选择要上传的标签文件",
            str(video.parent),
            "标签 (*.tags.json)",
        )
        if not tag_path_text:
            return
        tag_path = Path(tag_path_text)

        def work():
            subtitle_name = subtitle_name_for_tag_path(tag_path)
            if not subtitle_name:
                raise CloudError("这不是标签文件")
            subtitle_path = tag_path.with_name(subtitle_name)
            if not subtitle_belongs_to_media(subtitle_path, video):
                raise CloudError("标签对应的字幕名与所选视频不匹配。")
            document = load_tag_document(tag_path, subtitle_name)
            document.subtitle_file = subtitle_name
            payload = build_upload_document(document, None)
            if payload is None:
                raise CloudError("没有可上传的标签")
            self._client.put_tags(video_content_hash(video), video.stem, subtitle_name, payload)

        self._run(work, lambda _result: self.reload(), "正在上传标签…")

    def _view_tag(self) -> None:
        item_id = self._selected_id(self._tag_table)
        if item_id is None:
            return
        name = self._selected_text(self._tag_table, 1)

        def work():
            return self._client.read_library_tag(item_id)

        self._run(
            work,
            lambda payload: self._show_text(name or "标签", _format_tag_document(payload.get("document") or {})),
            "正在读取标签…",
        )

    def _delete_tag(self) -> None:
        targets = self._action_targets(self._tag_table)
        if not targets or not self._confirm_delete_many("标签", [item["name"] for item in targets]):
            return

        def work():
            return self._delete_targets(targets, self._client.delete_library_tag)

        self._run(work, self._after_batch, "正在删除标签…")

    def _pick_video(self) -> Path | None:
        pattern = " ".join(f"*{suffix}" for suffix in sorted(MEDIA_EXTENSIONS))
        chosen, _selected = QFileDialog.getOpenFileName(self, "选择视频或音频", "", f"视频和音频 ({pattern})")
        if not chosen:
            return None
        return Path(chosen)

    def _fill_subtitles(self, rows: list) -> None:
        self._fill_table(
            self._subtitle_table,
            rows,
            lambda row: [
                str(row.get("subtitle_name") or ""),
                str(row.get("video_stem") or ""),
                format_cloud_time(str(row.get("updated_at") or "")),
                "共享" if row.get("shared") else "仅自己",
            ],
        )
        self._subtitle_empty.setVisible(not rows)
        self._update_subtitle_buttons()

    def _fill_tags(self, rows: list) -> None:
        self._fill_table(
            self._tag_table,
            rows,
            lambda row: [
                str(row.get("subtitle_name") or ""),
                str(row.get("video_stem") or ""),
                format_cloud_time(str(row.get("updated_at") or "")),
            ],
        )
        self._tag_empty.setVisible(not rows)
        self._update_tag_buttons()

    def _fill_table(self, table: QTableWidget, rows: list, columns) -> None:
        table.blockSignals(True)
        table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            table.setItem(index, 0, self._check_item(row.get("id"), bool(row.get("shared"))))
            for column, text in enumerate(columns(row), start=1):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                table.setItem(index, column, item)
        if not rows:
            table.setRowCount(0)
        table.blockSignals(False)

    def _update_subtitle_buttons(self) -> None:
        self._subtitle_buttons["查看"].setEnabled(self._selected_id(self._subtitle_table) is not None)
        targets = self._action_targets(self._subtitle_table)
        enabled = bool(targets)
        self._subtitle_buttons["删除"].setEnabled(enabled)
        self._share_button.setEnabled(enabled)
        flags = {item["shared"] for item in targets}
        if flags == {True}:
            self._share_button.setText("取消共享")
        elif len(flags) > 1:
            self._share_button.setText("修改共享")
        else:
            self._share_button.setText("设为共享")

    def _update_tag_buttons(self) -> None:
        self._tag_buttons["查看"].setEnabled(self._selected_id(self._tag_table) is not None)
        self._tag_buttons["删除"].setEnabled(bool(self._action_targets(self._tag_table)))

    def _selected_id(self, table: QTableWidget) -> int | None:
        target = self._row_target(table, table.currentRow())
        return None if target is None else target["id"]

    def _check_item(self, item_id: object, shared: bool) -> QTableWidgetItem:
        item = QTableWidgetItem()
        item.setFlags(
            Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable
        )
        item.setCheckState(Qt.CheckState.Unchecked)
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        item.setData(Qt.ItemDataRole.UserRole, item_id)
        item.setData(Qt.ItemDataRole.UserRole + 1, shared)
        return item

    def _row_target(self, table: QTableWidget, row: int) -> dict | None:
        item = table.item(row, 0) if row >= 0 else None
        name_item = table.item(row, 1) if row >= 0 else None
        if item is None:
            return None
        try:
            item_id = int(item.data(Qt.ItemDataRole.UserRole))
        except (TypeError, ValueError):
            return None
        return {
            "id": item_id,
            "name": name_item.text() if name_item is not None else "",
            "shared": bool(item.data(Qt.ItemDataRole.UserRole + 1)),
        }

    def _checked_targets(self, table: QTableWidget) -> list[dict]:
        targets: list[dict] = []
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            if item is None or item.checkState() != Qt.CheckState.Checked:
                continue
            target = self._row_target(table, row)
            if target is not None:
                targets.append(target)
        return targets

    def _action_targets(self, table: QTableWidget) -> list[dict]:
        checked = self._checked_targets(table)
        if checked:
            return checked
        target = self._row_target(table, table.currentRow())
        return [target] if target is not None else []

    def _choose_share_state(self, count: int) -> bool | None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("云端管理")
        box.setText(f"选中的 {count} 个字幕里，有的已经共享，有的仅自己使用。要怎样处理？")
        share_btn = box.addButton("全部设为共享", QMessageBox.ButtonRole.AcceptRole)
        private_btn = box.addButton("全部取消共享", QMessageBox.ButtonRole.ActionRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is share_btn:
            return True
        if clicked is private_btn:
            return False
        return None

    def _confirm_delete_many(self, kind: str, names: list[str]) -> bool:
        if len(names) == 1:
            return self._confirm_delete(f"确定删除{kind}「{names[0]}」？\n删除后，其他设备也不能再同步这份{kind}。")
        preview = names[:12]
        lines = [f"确定删除选中的 {len(names)} 个{kind}？", "删除后，其他设备也不能再同步。", ""]
        lines.extend(preview)
        if len(names) > len(preview):
            lines.append(f"……还有 {len(names) - len(preview)} 个")
        return self._confirm_delete("\n".join(lines))

    def _delete_targets(self, targets: list[dict], delete) -> list[str]:
        failed: list[str] = []
        for item in targets:
            try:
                delete(item["id"])
            except CloudError as exc:
                failed.append(f"{item['name']}：{exc}")
        return failed

    def _after_batch(self, failed: list[str]) -> None:
        if failed:
            QMessageBox.warning(self, "云端管理", "有些文件没有处理成功：\n" + "\n".join(failed))
        self.reload()

    def _on_check_changed(self, table: QTableWidget, item: QTableWidgetItem) -> None:
        if item.column() != 0:
            return
        if table is self._subtitle_table:
            self._update_subtitle_buttons()
        elif table is self._tag_table:
            self._update_tag_buttons()

    def _toggle_all_checks(self, table: QTableWidget, section: int) -> None:
        if section != 0 or table.rowCount() == 0:
            return
        check_all = False
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            if item is None or item.checkState() != Qt.CheckState.Checked:
                check_all = True
                break
        state = Qt.CheckState.Checked if check_all else Qt.CheckState.Unchecked
        table.blockSignals(True)
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            if item is not None:
                item.setCheckState(state)
        table.blockSignals(False)
        self._on_check_changed(table, table.item(0, 0))

    def _selected_text(self, table: QTableWidget, column: int) -> str:
        row = table.currentRow()
        item = table.item(row, column) if row >= 0 else None
        return item.text() if item is not None else ""

    def _confirm_delete(self, text: str) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("云端管理")
        box.setText(text)
        delete_btn = box.addButton("删除", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return box.clickedButton() is delete_btn

    def _show_text(self, title: str, content: str) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(640, 420)
        dialog.setStyleSheet(_LIBRARY_STYLE)
        layout = QVBoxLayout(dialog)
        editor = QPlainTextEdit(content)
        editor.setReadOnly(True)
        layout.addWidget(editor)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(dialog.accept)
        layout.addWidget(close_btn)
        dialog.exec()

    def _run(self, work, on_success, title: str) -> None:
        host = self.parent()
        while host is not None and not hasattr(host, "_start_cloud_task"):
            host = host.parent()
        start = getattr(host, "_start_cloud_task", None) if host is not None else None
        if start is None:
            QMessageBox.warning(self, "云端管理", "无法发起云同步请求。")
            return
        def finish(result) -> None:
            if self.isVisible():
                on_success(result)

        start(work, finish, title)

    def _make_table(self, headers: list[str]) -> QTableWidget:
        headers = ["选", *headers]
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        table.setColumnWidth(0, 46)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for column in range(3, len(headers)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        check_header = table.horizontalHeaderItem(0)
        if check_header is not None:
            check_header.setToolTip("点击全选或取消全选")
        header.sectionClicked.connect(lambda section, table=table: self._toggle_all_checks(table, section))
        table.itemChanged.connect(lambda item, table=table: self._on_check_changed(table, item))
        table.itemDoubleClicked.connect(lambda item, table=table: self._open_current(table, item))
        return table

    def _open_current(self, table: QTableWidget, item: QTableWidgetItem | None = None) -> None:
        if item is not None and item.column() == 0:
            return
        if table is self._subtitle_table:
            self._view_subtitle()
        elif table is self._tag_table:
            self._view_tag()

    def _make_page(self, table: QTableWidget, actions: list[tuple[str, object]]) -> tuple[QWidget, dict[str, QPushButton]]:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(table, stretch=1)
        buttons = QHBoxLayout()
        named: dict[str, QPushButton] = {}
        for label, handler in actions:
            button = QPushButton(label)
            button.setAutoDefault(False)
            button.clicked.connect(handler)
            buttons.addWidget(button)
            named[label] = button
        buttons.addStretch(1)
        layout.addLayout(buttons)
        return page, named

    def _empty_label(self, page: QWidget, text: str) -> QLabel:
        label = QLabel(text)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet("color: #b0b0b0;")
        page.layout().insertWidget(1, label)
        return label


def _format_tag_document(document: dict) -> str:
    entries = document.get("entries") if isinstance(document, dict) else None
    if not isinstance(entries, list):
        return "这份标签无法读取。"
    lines: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        tags = [str(tag) for tag in entry.get("tags") or [] if str(tag).strip()]
        note = str(entry.get("note") or "").strip()
        if not tags and not note:
            continue
        text = str(entry.get("text") or "").replace("\n", " ").strip() or "（无字幕文本）"
        lines.append(text)
        if tags:
            lines.append("标签：" + "、".join(tags))
        if note:
            lines.append("备注：" + note)
        lines.append("")
    return "\n".join(lines).strip() or "这份标签里没有仍在显示的内容。"
