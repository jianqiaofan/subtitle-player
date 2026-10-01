"""截图标题和笔记。窗口本身透明，方便看着后面的画面写笔记。"""

from __future__ import annotations

import re

from PyQt6.QtCore import QPoint, QPointF, QRect, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPen
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from core.screenshots import (
    ScreenshotNote,
    append_screenshot_title_tags,
    format_local_time,
    new_screenshot_id,
    now_ms,
    title_has_known_tag,
)
from gui.subtitle_tag_dialog import SubtitleTagDialog

_CENTER_ROLE = int(Qt.ItemDataRole.UserRole) + 1
_CENTER_COLOR = QColor("#7a4eb5")
_CHECK_COLOR = QColor("#ffe14a")


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
        checked = index.data(Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked.value
        _paint_center_checkbox(painter, check_rect, checked)
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


def _paint_center_checkbox(painter, rect: QRect, checked: bool) -> None:
    """紫底上的对勾用亮黄色，空框保持白边，选中与否一眼能分开。"""
    if rect.isNull() or rect.isEmpty():
        return
    side = min(15, rect.width(), rect.height())
    if side < 8:
        return
    box = QRect(
        rect.left() + (rect.width() - side) // 2,
        rect.top() + (rect.height() - side) // 2,
        side,
        side,
    )
    painter.save()
    painter.setRenderHint(painter.RenderHint.Antialiasing, True)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(QColor("#ffffff"), 1.4))
    painter.drawRoundedRect(box.adjusted(1, 1, -1, -1), 2, 2)
    if checked:
        pen = QPen(_CHECK_COLOR, 2.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawPolyline([
            QPointF(box.left() + side * 0.22, box.top() + side * 0.54),
            QPointF(box.left() + side * 0.42, box.top() + side * 0.74),
            QPointF(box.left() + side * 0.80, box.top() + side * 0.28),
        ])
        painter.restore()


class _NoteTag(QFrame):
    """一条笔记的标签：上为内容前 12 字，下为创建或修改时间。"""

    clicked = pyqtSignal()
    scrolled = pyqtSignal(int)
    delete_requested = pyqtSignal()

    def __init__(self, preview: str, when: str, full_text: str) -> None:
        super().__init__()
        self.setObjectName("noteTag")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setToolTip(full_text)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(0)
        title = QLabel(preview)
        title.setStyleSheet("color: #ffffff; font-size: 13px; background: transparent;")
        title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        clock = QLabel(when)
        clock.setStyleSheet("color: rgba(255,255,255,0.78); font-size: 11px; background: transparent;")
        clock.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(title)
        layout.addWidget(clock)
        self._title = title
        self._clock = clock
        self._press_x = 0.0
        self._last_x = 0.0
        self._dragged = False
        self.set_selected(False)

    def refresh(self, preview: str, when: str, full_text: str) -> None:
        self._title.setText(preview)
        self._clock.setText(when)
        self.setToolTip(full_text)

    def set_selected(self, selected: bool) -> None:
        border = "#ffe14a" if selected else "rgba(255, 255, 255, 0.35)"
        fill = "rgba(122, 78, 181, 230)" if selected else "rgba(58, 36, 88, 210)"
        self.setStyleSheet(
            "QFrame#noteTag {"
            f"background-color: {fill};"
            f"border: 1px solid {border};"
            "border-radius: 6px;"
            "}"
        )

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_x = event.globalPosition().x()
            self._last_x = self._press_x
            self._dragged = False
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if not event.buttons() & Qt.MouseButton.LeftButton:
            super().mouseMoveEvent(event)
            return
        x = event.globalPosition().x()
        if not self._dragged and abs(x - self._press_x) >= 6:
            self._dragged = True
            self._last_x = self._press_x
        if self._dragged:
            delta = x - self._last_x
            self._last_x = x
            if delta:
                self.scrolled.emit(int(delta))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            if not self._dragged:
                self.clicked.emit()
            self._dragged = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        menu = QMenu(self)
        delete_action = menu.addAction("删除笔记")
        if menu.exec(event.globalPos()) == delete_action:
            self.delete_requested.emit()
        event.accept()


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
        custom_tags: list[str] | None = None,
    ) -> None:
        super().__init__(parent)
        self._deleted = False
        self._anchor = anchor
        self._placed = False
        self._drag_offset: QPoint | None = None
        self._notes = [
            ScreenshotNote(note.id, note.text, note.created_at, note.updated_at, frame=note.frame)
            for note in notes
            if note.text.strip()
        ]
        self._editing_index: int | None = None
        self._tags: list[_NoteTag] = []
        self._insert_subtitle_button: QPushButton | None = None
        self._current_pick_row = -1
        self._custom_tags = list(custom_tags or [])
        subtitle_rows = list(subtitles or [])

        dialog_title = "创建/编辑截图"
        self.setWindowTitle(dialog_title)
        self.setModal(True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint
        )
        self.resize(920, 640)
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

        heading = QLabel(dialog_title)
        heading.setStyleSheet("font-size: 16px; color: #ffffff; background: transparent;")
        layout.addWidget(heading)

        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title_label = QLabel("截图标题")
        title_label.setStyleSheet("font-size: 14px; color: #ffffff; background: transparent;")
        title_row.addWidget(title_label)
        self._title_edit = QLineEdit()
        self._title_edit.setPlaceholderText("标题")
        self._title_edit.setText(title)
        self._title_edit.setMinimumHeight(36)
        title_row.addWidget(self._title_edit, stretch=1)
        choose_tags = QPushButton("选择标签")
        choose_tags.clicked.connect(lambda: self._choose_title_tags())
        title_row.addWidget(choose_tags)
        layout.addLayout(title_row)

        label_row = QHBoxLayout()
        label_row.setSpacing(12)
        extract_header = QHBoxLayout()
        extract_header.setSpacing(8)
        extract_header.addWidget(QLabel("从笔记中提取"))
        extract_header.addStretch(1)
        self._center_button = QPushButton("回到中心")
        self._center_button.setEnabled(False)
        self._center_button.clicked.connect(self._scroll_to_center_subtitle)
        extract_header.addWidget(self._center_button)
        label_row.addLayout(extract_header, stretch=1)
        label_row.addWidget(QLabel("笔记内容"), stretch=1)
        layout.addLayout(label_row)

        self._subtitle_list = QListWidget()
        self._subtitle_list.setWordWrap(True)
        self._subtitle_list.setMinimumWidth(280)
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
        self._center_button.setEnabled(self._current_pick_row >= 0)
        self._note_edit = QPlainTextEdit()
        self._note_edit.setPlaceholderText("笔记内容")
        self._note_edit.setMinimumWidth(280)
        self._note_edit.textChanged.connect(self._sync_note_buttons)
        content_row = QHBoxLayout()
        content_row.setSpacing(12)
        content_row.addWidget(self._subtitle_list, stretch=1)
        content_row.addWidget(self._note_edit, stretch=1)
        layout.addLayout(content_row, stretch=1)

        command_row = QHBoxLayout()
        command_row.setSpacing(8)
        self._insert_subtitle_button = QPushButton("传入右侧输入框内")
        self._insert_subtitle_button.setEnabled(False)
        self._insert_subtitle_button.clicked.connect(self._insert_checked_subtitles)
        command_row.addWidget(self._insert_subtitle_button, alignment=Qt.AlignmentFlag.AlignLeft)
        command_row.addStretch(1)
        self._add_note_button = QPushButton("添加笔记")
        self._add_note_button.setEnabled(False)
        self._add_note_button.clicked.connect(self._add_note)
        self._update_note_button = QPushButton("更新笔记")
        self._update_note_button.setEnabled(False)
        self._update_note_button.clicked.connect(self._update_note)
        self._update_note_button.hide()
        self._save_new_note_button = QPushButton("保存为新笔记")
        self._save_new_note_button.setEnabled(False)
        self._save_new_note_button.clicked.connect(self._save_as_new_note)
        self._save_new_note_button.hide()
        self._note_actions = QWidget()
        self._note_actions.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        note_actions_layout = QHBoxLayout(self._note_actions)
        note_actions_layout.setContentsMargins(0, 0, 0, 0)
        note_actions_layout.setSpacing(8)
        note_actions_layout.addStretch(1)
        note_actions_layout.addWidget(self._update_note_button)
        note_actions_layout.addWidget(self._save_new_note_button)
        note_actions_layout.addWidget(self._add_note_button)
        command_row.addWidget(self._note_actions)
        button_width = (
            self._update_note_button.sizeHint().width()
            + note_actions_layout.spacing()
            + self._save_new_note_button.sizeHint().width()
        )
        self._note_actions.setMinimumWidth(button_width)
        layout.addLayout(command_row)

        self._tag_host = QWidget()
        self._tag_host.setStyleSheet("background: transparent;")
        self._tag_layout = QHBoxLayout(self._tag_host)
        self._tag_layout.setContentsMargins(0, 0, 0, 0)
        self._tag_layout.setSpacing(8)
        self._tag_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self._tag_scroll = QScrollArea()
        self._tag_scroll.setWidget(self._tag_host)
        self._tag_scroll.setWidgetResizable(False)
        self._tag_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._tag_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._tag_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._tag_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self._tag_scroll.viewport().setAutoFillBackground(False)
        self._tag_scroll.viewport().setStyleSheet("background: transparent;")
        self._tag_scroll.hide()
        self._drag_hint = QLabel("按住标签左右拖动")
        self._drag_hint.setStyleSheet(
            "color: rgba(255,255,255,0.78); font-size: 11px; background: transparent;"
        )
        self._drag_hint.hide()
        tag_column = QVBoxLayout()
        tag_column.setContentsMargins(0, 0, 0, 0)
        tag_column.setSpacing(2)
        tag_column.addWidget(self._tag_scroll)
        tag_column.addWidget(self._drag_hint)
        cancel_button = QPushButton("取消")
        cancel_button.clicked.connect(self.reject)
        save_button = QPushButton("保存")
        save_button.setDefault(True)
        save_button.clicked.connect(self._save_screenshot)
        self._dialog_actions = QWidget()
        self._dialog_actions.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        dialog_actions_layout = QHBoxLayout(self._dialog_actions)
        dialog_actions_layout.setContentsMargins(0, 0, 0, 0)
        dialog_actions_layout.setSpacing(8)
        dialog_actions_layout.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom
        )
        dialog_actions_layout.addWidget(cancel_button)
        dialog_actions_layout.addWidget(save_button)
        tag_row = QHBoxLayout()
        tag_row.setSpacing(0)
        tag_row.setAlignment(Qt.AlignmentFlag.AlignTop)
        tag_row.addLayout(tag_column, stretch=1)
        tag_row.addWidget(self._dialog_actions, alignment=Qt.AlignmentFlag.AlignTop)
        layout.addLayout(tag_row)
        self._rebuild_tags()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(panel)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._sync_tag_gap()
        self._update_drag_hint()
        QTimer.singleShot(0, self._update_drag_hint)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if hasattr(self, "_drag_hint"):
            self._update_drag_hint()
        if self._subtitle_list is not None and self._current_pick_row >= 0:
            self._scroll_to_center_subtitle()
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

    def _scroll_to_center_subtitle(self) -> None:
        listing = self._subtitle_list
        if listing is None or self._current_pick_row < 0:
            return
        current = listing.item(self._current_pick_row)
        if current is None:
            return
        listing.scrollToItem(current, QListWidget.ScrollHint.PositionAtCenter)

    def deleted(self) -> bool:
        return self._deleted

    def title_text(self) -> str:
        return self._title_edit.text().strip()

    def _save_screenshot(self) -> None:
        if self._title_has_tag():
            self.accept()
            return
        choice = self._ask_for_title_tag()
        if choice == "tag":
            self._choose_title_tags(save_after=True)
            return
        if choice == "skip":
            self.accept()

    def _title_has_tag(self) -> bool:
        return title_has_known_tag(self._title_edit.text(), self._custom_tags)

    def _ask_for_title_tag(self) -> str:
        box = QMessageBox(self)
        box.setWindowTitle("截图标题")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText("系统检测到你没有为此截图主题备注标签，这将很不利于后期复习整理，你确定不需要加上标签备注吗？")
        box.setStyleSheet(
            """
            QMessageBox { background-color: #2b2b2b; }
            QLabel { color: #f3f3f3; background: transparent; }
            QPushButton {
                color: #ffffff;
                background-color: #3c3c3c;
                border: 1px solid rgba(255, 255, 255, 0.35);
                border-radius: 4px;
                padding: 4px 14px;
                min-width: 88px;
            }
            """
        )
        tag_button = box.addButton("我要打标签", QMessageBox.ButtonRole.AcceptRole)
        skip_button = box.addButton("不需要标签", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(tag_button)
        box.exec()
        clicked = box.clickedButton()
        if clicked is tag_button:
            return "tag"
        if clicked is skip_button:
            return "skip"
        return ""

    def _choose_title_tags(self, save_after: bool = False) -> None:
        dialog = SubtitleTagDialog(
            self,
            selected_tags=[],
            note="",
            custom_tags=self._custom_tags,
            row_count=1,
            notes_differ=False,
            intro="选择标签，确认后接到标题后面",
            show_note=False,
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        chosen = dialog.tags()
        if not chosen:
            return
        self._title_edit.setText(append_screenshot_title_tags(self._title_edit.text(), chosen))
        if save_after:
            self.accept()

    def notes(self) -> list[ScreenshotNote]:
        return [
            ScreenshotNote(
                note.id,
                note.text,
                note.created_at,
                note.updated_at,
                frame=note.frame,
            )
            for note in self._notes
            if note.text.strip()
        ]

    def _note_text(self) -> str:
        return self._note_edit.toPlainText().strip()

    def _sync_note_buttons(self) -> None:
        ready = bool(self._note_text())
        self._add_note_button.setEnabled(ready)
        self._update_note_button.setEnabled(ready)
        self._save_new_note_button.setEnabled(ready)

    def _show_add_mode(self) -> None:
        self._editing_index = None
        self._add_note_button.show()
        self._update_note_button.hide()
        self._save_new_note_button.hide()
        self._restyle_tags()
        self._sync_tag_gap()

    def _show_edit_mode(self, index: int) -> None:
        self._editing_index = index
        self._add_note_button.hide()
        self._update_note_button.show()
        self._save_new_note_button.show()
        self._restyle_tags()
        self._sync_tag_gap()

    def _add_note(self) -> None:
        text = self._note_text()
        if not text:
            return
        moment = now_ms()
        self._notes.append(
            ScreenshotNote(
                id=new_screenshot_id(),
                text=text,
                created_at=moment,
                updated_at=moment,
            )
        )
        self._note_edit.clear()
        self._append_tag(len(self._notes) - 1)
        self._show_add_mode()

    def _update_note(self) -> None:
        index = self._editing_index
        if index is None or not 0 <= index < len(self._notes):
            return
        text = self._note_text()
        if not text:
            return
        note = self._notes[index]
        note.text = text
        note.updated_at = now_ms()
        self._tags[index].refresh(_note_preview(note.text), _note_time_line(note), note.text)
        self._show_edit_mode(index)

    def _save_as_new_note(self) -> None:
        text = self._note_text()
        if not text:
            return
        moment = now_ms()
        self._notes.append(
            ScreenshotNote(
                id=new_screenshot_id(),
                text=text,
                created_at=moment,
                updated_at=moment,
            )
        )
        self._append_tag(len(self._notes) - 1)
        self._show_edit_mode(len(self._notes) - 1)
        self._note_edit.setFocus(Qt.FocusReason.OtherFocusReason)

    def _edit_tag(self, index: int) -> None:
        if not 0 <= index < len(self._notes):
            return
        if self._editing_index == index:
            self._note_edit.clear()
            self._show_add_mode()
            return
        self._note_edit.setPlainText(self._notes[index].text)
        self._show_edit_mode(index)
        self._note_edit.setFocus(Qt.FocusReason.OtherFocusReason)

    def _rebuild_tags(self) -> None:
        for index in range(len(self._notes)):
            self._append_tag(index)

    def _append_tag(self, index: int) -> None:
        note = self._notes[index]
        tag = _NoteTag(_note_preview(note.text), _note_time_line(note), note.text)
        tag.set_selected(index == self._editing_index)
        tag.clicked.connect(lambda widget=tag: self._edit_tag_widget(widget))
        tag.scrolled.connect(self._scroll_tags)
        tag.delete_requested.connect(lambda widget=tag: self._confirm_delete_note(widget))
        self._tag_layout.addWidget(tag)
        self._tags.append(tag)
        self._fit_tag_host()
        self._tag_scroll.ensureWidgetVisible(tag)

    def _fit_tag_host(self) -> None:
        margins = self._tag_layout.contentsMargins()
        width = margins.left() + margins.right()
        height = margins.top() + margins.bottom()
        spacing = self._tag_layout.spacing()
        for index, tag in enumerate(self._tags):
            hint = tag.sizeHint()
            width += hint.width()
            if index:
                width += spacing
            height = max(height, hint.height() + margins.top() + margins.bottom())
        width = max(width, 1)
        height = max(height, 1)
        self._tag_host.setMinimumSize(width, height)
        self._tag_host.resize(width, height)
        visible = bool(self._tags)
        self._tag_scroll.setVisible(visible)
        if visible:
            self._tag_scroll.setFixedHeight(height)
            self._dialog_actions.setFixedHeight(height)
            self._sync_tag_gap()
        self._update_drag_hint()

    def _edit_tag_widget(self, tag: _NoteTag) -> None:
        try:
            index = self._tags.index(tag)
        except ValueError:
            return
        self._edit_tag(index)

    def _scroll_tags(self, delta: int) -> None:
        bar = self._tag_scroll.horizontalScrollBar()
        bar.setValue(bar.value() - int(delta))

    def _sync_tag_gap(self) -> None:
        width = max(
            self._note_actions.width(),
            self._note_actions.sizeHint().width(),
            self._note_actions.minimumWidth(),
        )
        self._dialog_actions.setFixedWidth(max(width, 0))

    def _update_drag_hint(self) -> None:
        if not self._tags:
            self._drag_hint.hide()
            return
        viewport_width = self._tag_scroll.viewport().width()
        overflow = viewport_width > 0 and self._tag_host.width() > viewport_width + 2
        self._drag_hint.setVisible(overflow)

    def _confirm_delete_note(self, tag: _NoteTag) -> None:
        try:
            index = self._tags.index(tag)
        except ValueError:
            return
        if not _confirm_delete_note_dialog(self):
            return
        self._remove_note_at(index)

    def _remove_note_at(self, index: int) -> None:
        if not 0 <= index < len(self._notes):
            return
        tag = self._tags.pop(index)
        del self._notes[index]
        self._tag_layout.removeWidget(tag)
        tag.setParent(None)
        tag.deleteLater()
        if self._editing_index == index:
            self._note_edit.clear()
            self._editing_index = None
            self._add_note_button.show()
            self._update_note_button.hide()
            self._save_new_note_button.hide()
        elif self._editing_index is not None and self._editing_index > index:
            self._editing_index -= 1
        self._restyle_tags()
        self._fit_tag_host()

    def _restyle_tags(self) -> None:
        for index, tag in enumerate(self._tags):
            tag.set_selected(index == self._editing_index)

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
        addition = _format_subtitle_line(texts)
        if not addition:
            return
        existing = self._note_edit.toPlainText().rstrip()
        merged = f"{existing}\n{addition}" if existing else addition
        self._note_edit.setPlainText(merged)
        cursor = self._note_edit.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self._note_edit.setTextCursor(cursor)
        self._note_edit.setFocus(Qt.FocusReason.OtherFocusReason)
        self._subtitle_list.blockSignals(True)
        for index in range(self._subtitle_list.count()):
            item = self._subtitle_list.item(index)
            if item is not None and item.checkState() == Qt.CheckState.Checked:
                item.setCheckState(Qt.CheckState.Unchecked)
        self._subtitle_list.blockSignals(False)
        self._sync_insert_button()

class _DeleteNoteConfirm(QDialog):
    """紧凑的删除确认。不用系统消息框，避免被拉出大片空白。"""

    def __init__(self, parent) -> None:
        super().__init__(parent)
        self.setObjectName("deleteNoteConfirm")
        self.setWindowTitle("删除笔记")
        self.setModal(True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.setStyleSheet(
            """
            QDialog#deleteNoteConfirm { background-color: #2b2b2b; color: #f3f3f3; }
            QLabel { color: #f3f3f3; background: transparent; }
            QPushButton {
                color: #ffffff;
                background-color: #3c3c3c;
                border: 1px solid rgba(255, 255, 255, 0.35);
                border-radius: 4px;
                padding: 4px 14px;
            }
            """
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        title = QLabel("删除这条笔记？")
        title.setStyleSheet("font-size: 14px; color: #ffffff; background: transparent;")
        detail = QLabel("删除后，这条笔记会从当前截图中去掉。")
        detail.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(detail)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        confirm = QPushButton("删除")
        confirm.setDefault(True)
        confirm.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(confirm)
        layout.addLayout(buttons)
        self.setFixedWidth(280)
        self.adjustSize()
        self.setFixedHeight(self.sizeHint().height())


def _confirm_delete_note_dialog(parent) -> bool:
    dialog = _DeleteNoteConfirm(parent)
    return dialog.exec() == QDialog.DialogCode.Accepted


def _note_preview(text: str) -> str:
    return " ".join(text.split())[:12]


def _note_time_line(note: ScreenshotNote) -> str:
    if note.updated_at != note.created_at:
        return f"修改 {format_local_time(note.updated_at)}"
    return f"创建 {format_local_time(note.created_at)}"


_NON_ENGLISH = re.compile(
    "["
    "\u3000-\u303f"
    "\u3040-\u30ff"
    "\u31f0-\u31ff"
    "\u3400-\u4dbf"
    "\u4e00-\u9fff"
    "\uf900-\ufaff"
    "\uac00-\ud7af"
    "\uff00-\uffef"
    "]"
)


def _format_subtitle_line(texts: list[str]) -> str:
    """多条字幕拼成一行：中间用逗号，最后一条用句号。全英文用半角标点。"""
    pieces = [text.strip().rstrip("，,。.") for text in texts]
    pieces = [piece for piece in pieces if piece]
    if not pieces:
        return ""
    english = not any(_NON_ENGLISH.search(piece) for piece in pieces)
    comma = ", " if english else "，"
    period = "." if english else "。"
    if len(pieces) == 1:
        return pieces[0] + period
    return comma.join(pieces) + period
