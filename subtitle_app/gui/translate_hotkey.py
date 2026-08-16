from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeyEvent, QKeySequence
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from core.external_translate import ExternalTranslator
from gui.styles import DARK_STYLE


class HotkeyLineEdit(QLineEdit):
    """Shows a hotkey such as Ctrl+Alt+C; click and press a combo, or type it."""

    hotkeyEdited = pyqtSignal()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key in (
            Qt.Key.Key_Control,
            Qt.Key.Key_Shift,
            Qt.Key.Key_Alt,
            Qt.Key.Key_Meta,
        ):
            event.accept()
            return
        mods = event.modifiers() & (
            Qt.KeyboardModifier.ControlModifier
            | Qt.KeyboardModifier.AltModifier
            | Qt.KeyboardModifier.ShiftModifier
            | Qt.KeyboardModifier.MetaModifier
        )
        if mods and key not in (Qt.Key.Key_unknown, 0, Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
            sequence = QKeySequence(int(mods.value) | int(key))
            text = sequence.toString(QKeySequence.SequenceFormat.PortableText)
            if text:
                self.setText(text)
                self.hotkeyEdited.emit()
                event.accept()
                return
        super().keyPressEvent(event)

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self.hotkeyEdited.emit()


class TranslateHotkeyHelpDialog(QDialog):
    def __init__(self, translator: ExternalTranslator, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(translator.help_title)
        self.setMinimumWidth(520)
        self.setStyleSheet(DARK_STYLE)

        layout = QVBoxLayout(self)
        body = QLabel(translator.help_html)
        body.setWordWrap(True)
        body.setTextFormat(Qt.TextFormat.RichText)
        body.setOpenExternalLinks(True)
        layout.addWidget(body)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)
