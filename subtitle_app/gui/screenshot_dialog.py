"""截图标题和笔记。窗口本身透明，方便看着后面的画面写笔记。"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from core.screenshots import ScreenshotNote, format_local_time, new_screenshot_id, now_ms

_CENTER_ROLE = int(Qt.ItemDataRole.UserRole) + 1
_CENTER_COLOR = QColor("#7a4eb5")


class _SubtitlePickDelegate(QStyledItemDelegate):
    """中心那一条自己铺底色，避免样式表把背景盖掉。"""

    def paint(self, painter, option, index) -> None:
        if not index.data(_CENTER_ROLE):
            super().paint(painter, option, index)
            return
        painter.save()
        painter.fillRect(option.rect, _CENTER_COLOR)
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        widget = option.widget
        style = widget.style() if widget is not None else None
        check_rect = option.rect
        if style is not None:
            check_rect = style.subElementRect(
                QStyle.SubElement.SE_ItemViewItemCheckIndicator,
                opt,
                widget,
            )
            opt.rect = check_rect
            style.drawPrimitive(
                QStyle.PrimitiveElement.PE_IndicatorItemViewItemCheck,
                opt,
                painter,
                widget,
            )
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        text_left = check_rect.right() + 8
        text_rect = option.rect.adjusted(text_left - option.rect.left(), 2, -4, -2)
        painter.setPen(QColor("#ffffff"))
        painter.setFont(option.font)
        painter.drawText(
            text_rect,
            int(Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap),
            text,
        )
        painter.restore()


class ScreenshotDialog(QDialog):
    def __init__(
        self,
        parent,
        *,
        title: str,
        notes: list[ScreenshotNote],
        can_delete: bool,
        anchor: QWidget | None = None,
        subtitles: list[tuple[str, str, bool]] | None = None,
    ) -> None:
        super().__init__(parent)
        self._deleted = False
        self._anchor = anchor
        self._placed = False
        self._drag_offset: QPoint | None = None
        self._rows: list[_NoteEditor] = []
        self._active_note: _NoteEditor | None = None
        self._subtitle_list: QListWidget | None = None
        self._insert_subtitle_button: QPushButton | None = None
        self._current_pick_row = -1
        subtitle_rows = list(subtitles or [])

        self.setWindowTitle("截图")
        self.setModal(True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint
        )
        self.resize(460, 660 if subtitle_rows else 520)
        self.setStyleSheet("QDialog { background: transparent; }")

        panel = QFrame()
        panel.setObjectName("screenshotDialogPanel")
        panel.setStyleSheet(
            """
            QFrame#screenshotDialogPanel {
                background-color: rgba(16, 16, 20, 148);
                border: 1px solid rgba(255, 255, 255, 0.28);
                border-radius: 12px;
            }
            QLabel { color: #f3f3f3; background: transparent; }
            QLineEdit, QPlainTextEdit {
                background-color: rgba(0, 0, 0, 165);
                color: #ffffff;
                border: 1px solid rgba(255, 255, 255, 0.28);
                border-radius: 4px;
                padding: 4px;
            }
            QPushButton {
                background-color: rgba(0, 0, 0, 120);
                color: #ffffff;
                border: 1px solid rgba(255, 255, 255, 0.35);
                border-radius: 4px;
                padding: 4px 10px;
            }
            QPushButton:hover { background-color: rgba(40, 40, 48, 170); }
            """
        )
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 12, 16, 14)
        layout.setSpacing(8)

        heading = QLabel("截图")
        heading.setStyleSheet("font-size: 16px; color: #ffffff; background: transparent;")
        layout.addWidget(heading)

        self._title_edit = QLineEdit()
        self._title_edit.setPlaceholderText("标题")
        self._title_edit.setText(title)
        layout.addWidget(self._title_edit)

        if subtitle_rows:
            layout.addWidget(QLabel("从附近字幕加入"))
            hint = QLabel("以离截图最近的一句为中心，前后各 100 条。勾选后加入当前笔记。")
            hint.setWordWrap(True)
            hint.setStyleSheet("color: rgba(255,255,255,0.78); font-size: 11px; background: transparent;")
            layout.addWidget(hint)
            self._subtitle_list = QListWidget()
            self._subtitle_list.setWordWrap(True)
            self._subtitle_list.setMinimumHeight(168)
            self._subtitle_list.setMaximumHeight(210)
            self._subtitle_list.setStyleSheet(
                """
                QListWidget {
                    background-color: rgba(0, 0, 0, 150);
                    color: #ffffff;
                    border: 1px solid rgba(255, 255, 255, 0.28);
                    border-radius: 4px;
                }
                QListWidget::item { padding: 3px 2px; }
                QListWidget::item:selected { background: rgba(185, 128, 255, 0.28); }
                """
            )
            self._subtitle_list.itemChanged.connect(self._sync_insert_button)
            self._subtitle_list.setItemDelegate(_SubtitlePickDelegate(self._subtitle_list))
            for index, (clock, text, current) in enumerate(subtitle_rows):
                label = f"[{clock}]  {text}"
                item = QListWidgetItem(label)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Unchecked)
                item.setData(Qt.ItemDataRole.UserRole, text)
                item.setData(_CENTER_ROLE, bool(current))
                item.setToolTip(text)
                if current:
                    self._current_pick_row = index
                self._subtitle_list.addItem(item)
            layout.addWidget(self._subtitle_list)
            self._insert_subtitle_button = QPushButton("加入当前笔记")
            self._insert_subtitle_button.setEnabled(False)
            self._insert_subtitle_button.clicked.connect(self._insert_checked_subtitles)
            layout.addWidget(self._insert_subtitle_button, alignment=Qt.AlignmentFlag.AlignLeft)

        notes_caption = QLabel("笔记")
        layout.addWidget(notes_caption)

        self._notes_host = QWidget()
        self._notes_host.setStyleSheet("background: transparent;")
        self._notes_layout = QVBoxLayout(self._notes_host)
        self._notes_layout.setContentsMargins(0, 0, 0, 0)
        self._notes_layout.setSpacing(8)
        self._notes_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self._notes_host)
        scroll.setStyleSheet(
            "QScrollArea { background: transparent; }"
            "QScrollArea > QWidget > QWidget { background: transparent; }"
        )
        scroll.viewport().setStyleSheet("background: transparent;")
        layout.addWidget(scroll, stretch=1)

        for note in notes:
            self._append_note(note, original_text=note.text)
        if self._rows and self._active_note is None:
            self._active_note = self._rows[-1]

        add_button = QPushButton("添加笔记")
        add_button.clicked.connect(self._add_blank_note)
        layout.addWidget(add_button, alignment=Qt.AlignmentFlag.AlignLeft)

        actions = QHBoxLayout()
        if can_delete:
            delete_button = QPushButton("删除截图")
            delete_button.clicked.connect(self._delete_screenshot)
            actions.addWidget(delete_button)
        actions.addStretch(1)
        cancel_button = QPushButton("取消")
        cancel_button.clicked.connect(self.reject)
        save_button = QPushButton("保存")
        save_button.setDefault(True)
        save_button.clicked.connect(self.accept)
        actions.addWidget(cancel_button)
        actions.addWidget(save_button)
        layout.addLayout(actions)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(panel)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self._subtitle_list is not None and self._current_pick_row >= 0:
            current = self._subtitle_list.item(self._current_pick_row)
            if current is not None:
                self._subtitle_list.scrollToItem(
                    current,
                    QListWidget.ScrollHint.PositionAtCenter,
                )
        if self._placed or self._anchor is None:
            return
        self._placed = True
        anchor = self._anchor
        origin = anchor.mapToGlobal(anchor.rect().topLeft())
        x = origin.x() + max(12, anchor.width() - self.width() - 16)
        y = origin.y() + 16
        screen = self.screen().availableGeometry() if self.screen() is not None else None
        if screen is not None:
            x = min(max(screen.left(), x), max(screen.left(), screen.right() - self.width()))
            y = min(max(screen.top(), y), max(screen.top(), screen.bottom() - self.height()))
        self.move(x, y)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and event.position().y() <= 42:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def deleted(self) -> bool:
        return self._deleted

    def title_text(self) -> str:
        return self._title_edit.text().strip()

    def notes(self) -> list[ScreenshotNote]:
        saved_at = now_ms()
        result: list[ScreenshotNote] = []
        for row in self._rows:
            note = row.result(saved_at)
            if note is not None:
                result.append(note)
        return result

    def _add_blank_note(self) -> None:
        moment = now_ms()
        note = ScreenshotNote(
            id=new_screenshot_id(),
            text="",
            created_at=moment,
            updated_at=moment,
        )
        editor = self._append_note(note, original_text="")
        self._active_note = editor
        editor.focus_editor()

    def _append_note(self, note: ScreenshotNote, *, original_text: str) -> _NoteEditor:
        editor = _NoteEditor(note, original_text)
        editor.remove_requested.connect(lambda row=editor: self._remove_note(row))
        editor.focused.connect(self._remember_active_note)
        self._rows.append(editor)
        self._notes_layout.insertWidget(self._notes_layout.count() - 1, editor)
        return editor

    def _remove_note(self, editor: _NoteEditor) -> None:
        if editor not in self._rows:
            return
        self._rows.remove(editor)
        if editor is self._active_note:
            self._active_note = self._rows[-1] if self._rows else None
        editor.setParent(None)
        editor.deleteLater()

    def _remember_active_note(self, editor: _NoteEditor) -> None:
        if editor in self._rows:
            self._active_note = editor

    def _sync_insert_button(self) -> None:
        button = self._insert_subtitle_button
        listing = self._subtitle_list
        if button is None or listing is None:
            return
        button.setEnabled(any(self._checked_subtitle_texts()))

    def _checked_subtitle_texts(self) -> list[str]:
        listing = self._subtitle_list
        if listing is None:
            return []
        texts: list[str] = []
        for index in range(listing.count()):
            item = listing.item(index)
            if item is None or item.checkState() != Qt.CheckState.Checked:
                continue
            text = str(item.data(Qt.ItemDataRole.UserRole) or "").strip()
            if text:
                texts.append(text)
        return texts

    def _insert_checked_subtitles(self) -> None:
        texts = self._checked_subtitle_texts()
        if not texts:
            return
        editor = self._active_note if self._active_note in self._rows else None
        if editor is None and self._rows:
            editor = self._rows[-1]
        if editor is None:
            self._add_blank_note()
            editor = self._active_note
        if editor is None:
            return
        editor.append_text("\n".join(texts))
        listing = self._subtitle_list
        if listing is None:
            return
        listing.blockSignals(True)
        for index in range(listing.count()):
            item = listing.item(index)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                item.setCheckState(Qt.CheckState.Unchecked)
        listing.blockSignals(False)
        self._sync_insert_button()

    def _delete_screenshot(self) -> None:
        answer = QMessageBox.question(
            self,
            "删除截图",
            "删除这张截图和它的笔记？",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._deleted = True
        self.accept()


class _NoteEditor(QWidget):
    remove_requested = pyqtSignal()
    focused = pyqtSignal(object)

    def __init__(self, note: ScreenshotNote, original_text: str) -> None:
        super().__init__()
        self._note = note
        self._original_text = original_text
        self.setStyleSheet("background: transparent;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        header = QHBoxLayout()
        created = format_local_time(note.created_at)
        updated = format_local_time(note.updated_at)
        times = QLabel(f"创建 {created}    最后修改 {updated}")
        times.setStyleSheet("color: rgba(255,255,255,0.78); font-size: 11px; background: transparent;")
        header.addWidget(times, stretch=1)
        remove = QPushButton("删除")
        remove.clicked.connect(self.remove_requested.emit)
        header.addWidget(remove)
        layout.addLayout(header)

        self._editor = QPlainTextEdit()
        self._editor.setPlaceholderText("笔记")
        self._editor.setPlainText(note.text)
        self._editor.setFixedHeight(72)
        self._editor.installEventFilter(self)
        layout.addWidget(self._editor)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if watched is self._editor and event.type() == QEvent.Type.FocusIn:
            self.focused.emit(self)
        return super().eventFilter(watched, event)

    def focus_editor(self) -> None:
        self._editor.setFocus(Qt.FocusReason.OtherFocusReason)

    def append_text(self, text: str) -> None:
        addition = text.strip()
        if not addition:
            return
        existing = self._editor.toPlainText().rstrip()
        merged = f"{existing}\n{addition}" if existing else addition
        self._editor.setPlainText(merged)
        cursor = self._editor.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self._editor.setTextCursor(cursor)
        self.focus_editor()

    def result(self, saved_at: int) -> ScreenshotNote | None:
        text = self._editor.toPlainText().strip()
        if not text:
            return None
        updated_at = self._note.updated_at
        if text != self._original_text:
            updated_at = saved_at
        return ScreenshotNote(
            id=self._note.id,
            text=text,
            created_at=self._note.created_at,
            updated_at=updated_at,
            frame=self._note.frame,
        )
