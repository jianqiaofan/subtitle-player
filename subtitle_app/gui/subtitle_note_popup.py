"""备注悬停预览，以及可以停留着修改的备注窗口。"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QPlainTextEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from gui.styles import DARK_STYLE

_PREVIEW_STYLE = """
QTextEdit {
    color: #ffd56a;
    background-color: #1e1e1e;
    border: 1px solid #ffd56a;
    border-radius: 6px;
    padding: 8px;
    font-size: 13px;
}
"""


class NotePreviewPopup(QTextEdit):
    """鼠标停在备注上时显示全文。点击正文后发出 open_requested。"""

    open_requested = pyqtSignal()

    def __init__(self) -> None:
        super().__init__(None)
        self.setReadOnly(True)
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet(_PREVIEW_STYLE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
        self.viewport().installEventFilter(self)
        self.setFixedWidth(360)

    def eventFilter(self, obj, event) -> bool:
        if (
            obj is self.viewport()
            and event.type() == QEvent.Type.MouseButtonRelease
            and event.button() == Qt.MouseButton.LeftButton
        ):
            self.open_requested.emit()
            return True
        return super().eventFilter(obj, event)

    def show_note(self, text: str, global_pos) -> None:
        self.setPlainText(text)
        self.document().setTextWidth(340)
        height = int(self.document().size().height()) + 24
        self.setFixedHeight(max(48, min(280, height)))
        self.move(global_pos)
        self.show()
        self.raise_()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.open_requested.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class SubtitleNoteDialog(QDialog):
    """移开鼠标后仍然保留，可查看并修改备注。"""

    saved = pyqtSignal(str, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("备注")
        self.setModal(False)
        self.setMinimumSize(440, 280)
        self.setStyleSheet(DARK_STYLE)
        self._entry_id = ""
        layout = QVBoxLayout(self)
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText("在这里查看或修改备注")
        layout.addWidget(self.editor)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        save_button = QPushButton("保存")
        close_button = QPushButton("关闭")
        save_button.clicked.connect(self._save)
        close_button.clicked.connect(self.close)
        buttons.addWidget(save_button)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

    def edit_note(self, entry_id: str, note: str) -> None:
        self._entry_id = entry_id
        self.editor.setPlainText(note)
        self.editor.moveCursor(self.editor.textCursor().MoveOperation.Start)

    def _save(self) -> None:
        self.saved.emit(self._entry_id, self.editor.toPlainText().strip())
