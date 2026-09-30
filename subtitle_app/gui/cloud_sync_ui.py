"""云同步菜单和确认框。"""

from __future__ import annotations

from datetime import datetime, timezone

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gui.styles import DARK_STYLE


class LeftSubmenu(QMenu):
    """从父菜单左侧弹出，避免工具菜单贴在窗口右边时子菜单超出屏幕。"""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(title, parent)
        self._placing = False

    def showEvent(self, event) -> None:
        super().showEvent(event)
        QTimer.singleShot(0, self._place_left)

    def _place_left(self) -> None:
        parent = self.parentWidget()
        if self._placing or not self.isVisible() or not isinstance(parent, QMenu):
            return
        self._placing = True
        try:
            rect = parent.actionGeometry(self.menuAction())
            anchor = parent.mapToGlobal(rect.topLeft())
            geo = self.frameGeometry()
            geo.moveTop(anchor.y())
            geo.moveRight(parent.frameGeometry().left())
            screen = self.screen().availableGeometry() if self.screen() is not None else None
            if screen is not None:
                if geo.left() < screen.left():
                    geo.moveLeft(screen.left())
                if geo.bottom() > screen.bottom():
                    geo.moveBottom(screen.bottom())
                if geo.top() < screen.top():
                    geo.moveTop(screen.top())
            self.move(geo.topLeft())
        finally:
            self._placing = False


class CloudTask(QThread):
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, work, parent=None) -> None:
        super().__init__(parent)
        self._work = work

    def run(self) -> None:
        try:
            self.succeeded.emit(self._work())
        except Exception as exc:
            self.failed.emit(str(exc))


def format_cloud_time(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return "时间未知"
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone().strftime("%Y-%m-%d %H:%M")


def confirm_subtitle_replace(parent: QWidget, rows: list[tuple[str, str]], *, allow_skip: bool = False) -> str:
    """返回 replace、skip 或 cancel。"""
    if not rows:
        return "replace"
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle("上传字幕")
    lines = ["云端已有同名字幕。确认后才会替换。", ""]
    preview = rows[:12]
    for name, updated_at in preview:
        lines.append(f"{name}    云端更新于 {format_cloud_time(updated_at)}")
    if len(rows) > len(preview):
        lines.append(f"……还有 {len(rows) - len(preview)} 个")
    box.setText("\n".join(lines))
    replace_btn = box.addButton("替换", QMessageBox.ButtonRole.AcceptRole)
    skip_btn = box.addButton("跳过已有", QMessageBox.ButtonRole.ActionRole) if allow_skip else None
    box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
    box.exec()
    clicked = box.clickedButton()
    if clicked == replace_btn:
        return "replace"
    if skip_btn is not None and clicked == skip_btn:
        return "skip"
    return "cancel"


def confirm_share_upload(parent: QWidget) -> bool | None:
    """返回 True 共享，False 仅自己使用，None 取消上传。"""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle("共享字幕")
    box.setText(
        "是否同意把这次上传的字幕共享给其他用户？\n"
        "其他人只有在打开同一部视频、并且自己还没有字幕时，才能选用。"
    )
    share_btn = box.addButton("共享", QMessageBox.ButtonRole.AcceptRole)
    private_btn = box.addButton("仅自己使用", QMessageBox.ButtonRole.ActionRole)
    box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
    box.exec()
    clicked = box.clickedButton()
    if clicked == share_btn:
        return True
    if clicked == private_btn:
        return False
    return None


def choose_shared_subtitle(parent: QWidget, shares: list[dict]) -> dict | None:
    """从其他用户分享的同一视频字幕里选一位。返回选中的分享记录。"""
    if not shares:
        return None
    dialog = QDialog(parent)
    dialog.setWindowTitle("共享字幕")
    dialog.setMinimumWidth(460)
    dialog.setStyleSheet(DARK_STYLE)
    layout = QVBoxLayout(dialog)
    hint = QLabel("本机还没有这部视频的字幕。可以选择一位用户分享的版本同步到本机。")
    hint.setWordWrap(True)
    layout.addWidget(hint)
    listing = QListWidget()
    for share in shares:
        names = "、".join(
            str(item.get("subtitle_name") or "")
            for item in share.get("subtitles") or []
            if item.get("subtitle_name")
        )
        when = format_cloud_time(str(share.get("updated_at") or ""))
        text = f"{share.get('username') or ''}\n更新于 {when}\n{names}"
        row = QListWidgetItem(text)
        row.setData(Qt.ItemDataRole.UserRole, share.get("username") or "")
        listing.addItem(row)
    listing.setCurrentRow(0)
    listing.itemDoubleClicked.connect(dialog.accept)
    layout.addWidget(listing)
    buttons = QHBoxLayout()
    use_btn = QPushButton("同步选中的字幕")
    use_btn.clicked.connect(dialog.accept)
    cancel_btn = QPushButton("取消")
    cancel_btn.clicked.connect(dialog.reject)
    buttons.addWidget(use_btn)
    buttons.addWidget(cancel_btn)
    layout.addLayout(buttons)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    current = listing.currentItem()
    if current is None:
        return None
    username = current.data(Qt.ItemDataRole.UserRole)
    for share in shares:
        if share.get("username") == username:
            return share
    return None


def confirm_subtitle_download(parent: QWidget, rows: list[dict]) -> bool:
    lines = ["云端字幕和本机不一致。", ""]
    for item in rows[:12]:
        name = item.get("subtitle_name") or ""
        when = format_cloud_time(str(item.get("updated_at") or ""))
        if item.get("state") == "new":
            lines.append(f"{name}    本机还没有，云端更新于 {when}")
        else:
            lines.append(f"{name}    云端更新于 {when}")
    if len(rows) > 12:
        lines.append(f"……还有 {len(rows) - 12} 个")
    lines.append("")
    lines.append("是否更新到本机？")
    answer = QMessageBox.question(parent, "同步字幕", "\n".join(lines))
    return answer == QMessageBox.StandardButton.Yes


def confirm_tag_download(parent: QWidget, rows: list[dict]) -> bool:
    lines = ["云端标签有新版本。同步时按各自的修改时间合并，不会把已删除的标签加回来。", ""]
    for item in rows[:12]:
        name = item.get("subtitle_name") or ""
        when = format_cloud_time(str(item.get("updated_at") or ""))
        lines.append(f"{name}    云端更新于 {when}")
    if len(rows) > 12:
        lines.append(f"……还有 {len(rows) - 12} 个")
    lines.append("")
    lines.append("是否同步到本机？")
    answer = QMessageBox.question(parent, "同步标签", "\n".join(lines))
    return answer == QMessageBox.StandardButton.Yes


def _format_local_time(timestamp: float) -> str:
    try:
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
    except (OSError, OverflowError, ValueError):
        return "未知"


class SubtitleConflictDialog(QDialog):
    """同名字幕在配套文件夹和视频旁边各有一份时，让用户留一份。"""

    def __init__(self, conflicts: list, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from PyQt6.QtWidgets import QButtonGroup, QGroupBox, QRadioButton

        self.setWindowTitle("选择字幕")
        self.setStyleSheet(DARK_STYLE)
        self.resize(640, min(520, 180 + 150 * len(conflicts)))
        self._choices: list[tuple[str, QButtonGroup, object, object]] = []
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("同名字幕在两个位置都有。请为每个文件选择要留下的一份。留下的会放到配套文件夹，另一份会删除。"))
        for conflict in conflicts:
            box = QGroupBox(conflict.name)
            box_layout = QVBoxLayout(box)
            group = QButtonGroup(box)
            bundled = QRadioButton(self._spot_text("配套文件夹中的这份", conflict.bundled))
            legacy = QRadioButton(self._spot_text("视频旁边的这份", conflict.legacy))
            bundled.setChecked(conflict.bundled.updated_at >= conflict.legacy.updated_at)
            legacy.setChecked(not bundled.isChecked())
            group.addButton(bundled)
            group.addButton(legacy)
            box_layout.addWidget(bundled)
            box_layout.addWidget(legacy)
            layout.addWidget(box)
            self._choices.append((conflict.name, group, bundled, legacy))
        buttons = QHBoxLayout()
        ok_button = QPushButton("使用选中的字幕")
        cancel_button = QPushButton("取消")
        ok_button.clicked.connect(self.accept)
        cancel_button.clicked.connect(self.reject)
        buttons.addStretch(1)
        buttons.addWidget(ok_button)
        buttons.addWidget(cancel_button)
        layout.addLayout(buttons)

    @staticmethod
    def _spot_text(title: str, spot) -> str:
        return (
            f"{title}\n{spot.path}\n"
            f"创建：{_format_local_time(spot.created_at)}\n"
            f"最后更新：{_format_local_time(spot.updated_at)}"
        )

    def choices(self) -> dict[str, str]:
        for name, _group, bundled, _legacy in self._choices:
            picked[name] = "bundle" if bundled.isChecked() else "legacy"
        return picked


def ask_subtitle_conflicts(parent: QWidget, conflicts: list) -> dict[str, str] | None:
    if not conflicts:
        return {}
    dialog = SubtitleConflictDialog(conflicts, parent)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return dialog.choices()


class ExitSyncDialog(QDialog):
    def __init__(self, *, subtitles: bool, tags: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from PyQt6.QtWidgets import QCheckBox

        self.setWindowTitle("同步到云端")
        self.setStyleSheet(DARK_STYLE)
        self.resize(460, 220)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("观看过程中有内容改过。勾选要同步到云端的项目。"))
        self.subtitle_box = QCheckBox("同步字幕")
        self.tag_box = QCheckBox("同步标签")
        self.subtitle_box.setChecked(subtitles)
        self.subtitle_box.setEnabled(subtitles)
        self.tag_box.setChecked(tags)
        self.tag_box.setEnabled(tags)
        self.remember_box = QCheckBox("不再询问，在本设备，该视频下回采用相同的同步策略")
        layout.addWidget(self.subtitle_box)
        layout.addWidget(self.tag_box)
        layout.addWidget(self.remember_box)
        buttons = QHBoxLayout()
        sync_button = QPushButton("同步")
        later_button = QPushButton("暂不同步")
        sync_button.clicked.connect(self.accept)
        later_button.clicked.connect(self.reject)
        buttons.addStretch(1)
        buttons.addWidget(sync_button)
        buttons.addWidget(later_button)
        layout.addLayout(buttons)

    def result_value(self) -> tuple[bool, bool, bool]:
        return (
            self.subtitle_box.isChecked(),
            self.tag_box.isChecked(),
            self.remember_box.isChecked(),
        )


def ask_exit_sync(parent: QWidget, *, subtitles: bool, tags: bool) -> tuple[bool, bool, bool] | None:
    dialog = ExitSyncDialog(subtitles=subtitles, tags=tags, parent=parent)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return dialog.result_value()

