"""可复用的 tip 提示框：带「下回不再提醒」，按 tip_id 记在本机配置里。"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.config import AppConfig, save_config
from gui.styles import DARK_STYLE

# 各功能固定 tip_id，方便多处复用同一条提示
TIP_SCREENSHOT_EXPORT = "screenshot_export"

_TIP_DIALOG_STYLE = (
    DARK_STYLE
    + """
QDialog#tipDialog {
    background-color: #2b2b2b;
}
QLabel#tipBody {
    color: #f3f3f3;
    font-size: 14px;
}
QCheckBox {
    color: #e0e0e0;
    spacing: 8px;
}
QPushButton#tipAckButton {
    min-width: 96px;
    padding: 6px 16px;
}
"""
)


class TipDialog(QDialog):
    """标题默认 tip，正文可传；右下角「我知道了」，可选「下回不再提醒」。"""

    def __init__(
        self,
        parent: QWidget | None,
        *,
        message: str,
        title: str = "tip",
        checkbox_text: str = "下回不再提醒",
        ack_text: str = "我知道了",
    ) -> None:
        super().__init__(parent)
        self.setObjectName("tipDialog")
        self.setWindowTitle(title)
        self.setModal(True)
        self.setStyleSheet(_TIP_DIALOG_STYLE)
        self.setMinimumWidth(360)
        self._dont_remind = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(14)

        body = QLabel(message)
        body.setObjectName("tipBody")
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(body)

        self._checkbox = QCheckBox(checkbox_text)
        layout.addWidget(self._checkbox)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        ack = QPushButton(ack_text)
        ack.setObjectName("tipAckButton")
        ack.setDefault(True)
        ack.clicked.connect(self._accept)
        buttons.addWidget(ack)
        layout.addLayout(buttons)

        self.adjustSize()

    def dont_remind(self) -> bool:
        return self._dont_remind

    def _accept(self) -> None:
        self._dont_remind = self._checkbox.isChecked()
        self.accept()


def tip_is_dismissed(config: AppConfig, tip_id: str) -> bool:
    tip_id = str(tip_id or "").strip()
    if not tip_id:
        return False
    return tip_id in list(getattr(config, "dismissed_tips", None) or [])


def dismiss_tip(config: AppConfig, tip_id: str, *, persist: bool = True) -> None:
    tip_id = str(tip_id or "").strip()
    if not tip_id:
        return
    tips = list(getattr(config, "dismissed_tips", None) or [])
    if tip_id in tips:
        return
    tips.append(tip_id)
    config.dismissed_tips = tips
    if persist:
        save_config(config)


def show_tip_once(
    parent: QWidget | None,
    tip_id: str,
    message: str,
    *,
    title: str = "tip",
    config: AppConfig | None = None,
) -> bool:
    """若本机尚未对该 tip_id 勾选「下回不再提醒」，则弹出 tip。

    返回 True 表示这次弹出了提示；False 表示已跳过。
    无论是否弹出，调用方都可以继续后续操作。
    """
    if config is not None and tip_is_dismissed(config, tip_id):
        return False
    dialog = TipDialog(parent, message=message, title=title)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return True
    if dialog.dont_remind() and config is not None:
        dismiss_tip(config, tip_id, persist=True)
    return True
