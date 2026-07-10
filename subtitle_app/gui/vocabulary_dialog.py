from __future__ import annotations

from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
)

from core.vocabulary import LANGUAGE_OPTIONS, VocabularyOptions
from gui.styles import DARK_STYLE


class VocabularyDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("生成生词表")
        self.setMinimumWidth(420)
        self.setStyleSheet(DARK_STYLE)

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "从当前视频字幕中提取词汇，统计频次并生成生词表。\n"
                "英语：音标 + 词性 + 释义（优先 ECDict 英汉词典，否则 WordNet 英文释义）。\n"
                "日语：注音 + 词性 + 释义（JMdict / jamdict）。"
            )
        )

        form = QFormLayout()
        self.language_combo = QComboBox()
        for label, code in LANGUAGE_OPTIONS:
            self.language_combo.addItem(label, code)
        form.addRow("词汇语言", self.language_combo)

        self.min_frequency_spin = QSpinBox()
        self.min_frequency_spin.setRange(1, 999)
        self.min_frequency_spin.setValue(1)
        self.min_frequency_spin.setToolTip("只保留至少出现该次数的词汇")
        form.addRow("最少出现次数", self.min_frequency_spin)

        self.max_items_spin = QSpinBox()
        self.max_items_spin.setRange(0, 10000)
        self.max_items_spin.setValue(500)
        self.max_items_spin.setSpecialValueText("不限制")
        self.max_items_spin.setToolTip("0 表示导出全部符合条件的词汇")
        form.addRow("最多导出词汇数", self.max_items_spin)

        layout.addLayout(form)

        hint = QLabel(
            "将生成 Markdown 生词表与同名的 CSV 文件（可用 Excel / Anki 导入）。\n"
            "可选：将 ECDict 的 ecdict.db 放到 subtitle_app/data/ 以获得英语中文释义；\n"
            "将 jamdict.db 放到同目录以获得更完整的日语释义（否则使用 Janome 注音/词性）。"
        )
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("生成")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def options(self) -> VocabularyOptions:
        max_items = self.max_items_spin.value()
        return VocabularyOptions(
            language=str(self.language_combo.currentData()),
            min_frequency=self.min_frequency_spin.value(),
            max_items=max_items,
        )

    @staticmethod
    def get_options(parent=None) -> VocabularyOptions | None:
        dialog = VocabularyDialog(parent)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.options()
