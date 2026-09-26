from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QProgressBar,
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


class VocabularyProgressDialog(QDialog):
    """生词表生成进度。真实步骤较少，进度条在完成前平滑前进。"""

    def __init__(self, media_name: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("正在生成生词表")
        self.setMinimumWidth(440)
        self.setModal(True)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setStyleSheet(DARK_STYLE)

        self._current = 0
        self._target = 12
        self._finished = False

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        self.title_label = QLabel(f"正在为「{media_name}」生成生词表")
        self.title_label.setWordWrap(True)
        layout.addWidget(self.title_label)

        self.status_label = QLabel("正在准备…")
        self.status_label.setObjectName("hintLabel")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("0%")
        layout.addWidget(self.progress)

        self._timer = QTimer(self)
        self._timer.setInterval(80)
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        self._current = 0
        self._target = 12
        self._finished = False
        self.progress.setValue(0)
        self.progress.setFormat("0%")
        self.status_label.setText("正在准备…")
        self._timer.start()
        self.show()

    def set_status(self, message: str) -> None:
        self.status_label.setText(message)
        if "查询" in message or "释义" in message or "注音" in message:
            self._target = max(self._target, 68)
        elif "分析" in message:
            self._target = max(self._target, 36)

    def finish_success(self) -> None:
        self._finished = True
        self._timer.stop()
        self._current = 100
        self._target = 100
        self.progress.setValue(100)
        self.progress.setFormat("100% — 完成")
        self.status_label.setText("生词表已生成")

    def finish_failure(self, message: str) -> None:
        self._finished = True
        self._timer.stop()
        self.progress.setFormat("已停止")
        self.status_label.setText(message)

    def allow_close(self) -> None:
        self._finished = True

    def closeEvent(self, event) -> None:
        if not self._finished:
            event.ignore()
            return
        super().closeEvent(event)

    def _tick(self) -> None:
        if self._finished:
            return
        if self._current < self._target:
            step = max(1, (self._target - self._current + 2) // 3)
            self._current = min(self._target, self._current + step)
        elif self._target < 92:
            self._target += 1
        self.progress.setValue(self._current)
        self.progress.setFormat(f"{self._current}%")
