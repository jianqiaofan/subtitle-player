from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QVBoxLayout,
)

from core.subtitle_text_export import PlainTextExportOptions
from gui.styles import DARK_STYLE


class SubtitleTextDialog(QDialog):
    def __init__(self, subtitle_label: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("导出纯文字版")
        self.setMinimumWidth(420)
        self.setStyleSheet(DARK_STYLE)

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "去掉字幕时间轴，按停顿间隔整理为段落，并导出 Markdown 文件。\n"
                "合并时会自动补充标点，并将繁体中文转为简体（OpenCC）。"
            )
        )

        form = QFormLayout()
        source_label = QLabel(subtitle_label or "（未选择）")
        source_label.setWordWrap(True)
        form.addRow("当前字幕", source_label)

        self.gap_spin = QDoubleSpinBox()
        self.gap_spin.setRange(0.5, 30.0)
        self.gap_spin.setSingleStep(0.5)
        self.gap_spin.setDecimals(1)
        self.gap_spin.setValue(2.0)
        self.gap_spin.setSuffix(" 秒")
        self.gap_spin.setToolTip("相邻两条字幕的开始/结束间隔超过该值时，另起一段")
        form.addRow("分段间隔", self.gap_spin)

        layout.addLayout(form)

        hint = QLabel("Markdown 文件将保存在视频同目录下。")
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("导出")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def options(self) -> PlainTextExportOptions:
        return PlainTextExportOptions(gap_seconds=self.gap_spin.value())

    @staticmethod
    def get_options(subtitle_label: str, parent=None) -> PlainTextExportOptions | None:
        dialog = SubtitleTextDialog(subtitle_label, parent)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.options()
