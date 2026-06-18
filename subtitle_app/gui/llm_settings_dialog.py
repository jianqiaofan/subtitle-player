from __future__ import annotations

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
)

from core.config import (
    CONFIG_PATH,
    DEFAULT_DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL_OPTIONS,
    AppConfig,
    is_deepseek_configured,
    save_config,
)
from gui.styles import DARK_STYLE


class LlmSettingsDialog(QDialog):
    def __init__(self, config: AppConfig, parent=None) -> None:
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("大模型配置（AI 笔记）")
        self.setMinimumWidth(520)
        self.setStyleSheet(DARK_STYLE)

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "配置 DeepSeek 大模型用于「AI 笔记」功能。\n"
                "API Key 仅保存在本机 config.json，不会上传到 GitHub。\n"
                "申请密钥：https://platform.deepseek.com/api_keys"
            )
        )

        form = QFormLayout()
        self.api_key_edit = QLineEdit(config.deepseek_api_key)
        self.api_key_edit.setPlaceholderText("在此填写 DeepSeek API Key")
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)

        show_key = QCheckBox("显示 Key")
        show_key.toggled.connect(self._toggle_key_visibility)
        key_row = QHBoxLayout()
        key_row.addWidget(self.api_key_edit, stretch=1)
        key_row.addWidget(show_key)

        self.base_url_edit = QLineEdit(config.deepseek_base_url or DEFAULT_DEEPSEEK_BASE_URL)
        self.base_url_edit.setPlaceholderText(DEFAULT_DEEPSEEK_BASE_URL)

        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        current_model = config.deepseek_model.strip() or "deepseek-v4-flash"
        seen_models: set[str] = set()
        selected_index = 0
        for index, (label, value) in enumerate(DEEPSEEK_MODEL_OPTIONS):
            self.model_combo.addItem(label, value)
            seen_models.add(value)
            if value == current_model:
                selected_index = index
        if current_model not in seen_models:
            self.model_combo.addItem(current_model, current_model)
            selected_index = self.model_combo.count() - 1
        self.model_combo.setCurrentIndex(selected_index)
        if current_model not in seen_models:
            self.model_combo.setEditText(current_model)

        form.addRow("API Key", key_row)
        form.addRow("接口地址", self.base_url_edit)
        form.addRow("模型", self.model_combo)
        layout.addLayout(form)

        self.status_label = QLabel()
        self.status_label.setObjectName("hintLabel")
        self._refresh_status()
        layout.addWidget(self.status_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _toggle_key_visibility(self, visible: bool) -> None:
        self.api_key_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
        )

    def _refresh_status(self) -> None:
        if is_deepseek_configured(self.config):
            self.status_label.setText("当前状态：已配置，可使用 AI 笔记。")
        else:
            self.status_label.setText("当前状态：未配置 API Key，AI 笔记不可用。")

    def _save(self) -> None:
        api_key = self.api_key_edit.text().strip()
        base_url = self.base_url_edit.text().strip() or DEFAULT_DEEPSEEK_BASE_URL
        model = self.model_combo.currentData()
        if not model:
            model = self.model_combo.currentText().strip()
        if not api_key:
            QMessageBox.warning(self, "配置不完整", "请填写 API Key。")
            return
        if not base_url.startswith("http"):
            QMessageBox.warning(self, "配置不完整", "接口地址格式不正确。")
            return
        if not model:
            QMessageBox.warning(self, "配置不完整", "请填写或选择模型名称。")
            return

        self.config.deepseek_api_key = api_key
        self.config.deepseek_base_url = base_url.rstrip("/")
        self.config.deepseek_model = model
        save_config(self.config)
        self._refresh_status()
        QMessageBox.information(self, "已保存", f"大模型配置已保存到：\n{CONFIG_PATH}")
        self.accept()

    @classmethod
    def open_settings(cls, config: AppConfig, parent=None) -> AppConfig | None:
        dialog = cls(config, parent=parent)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.config
