"""设置菜单中的「用户设置」对话框。"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from core.cloud_api import CloudClient, CloudError
from core.cloud_sync import DEFAULT_CLOUD_SERVER, load_account_client
from core.config import AppConfig, save_config
from gui.cloud_library_dialog import CloudLibraryDialog
from gui.styles import DARK_STYLE


class CloudAccountDialog(QDialog):
    def __init__(self, config: AppConfig, parent=None) -> None:
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("用户设置")
        self.setMinimumWidth(360)
        self.setStyleSheet(DARK_STYLE)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        self._hint = QLabel()
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet("color: #d0d0d0;")
        layout.addWidget(self._hint)

        self.username_edit = QLineEdit()
        self.username_edit.setPlaceholderText("用户名")
        self.username_edit.setText(config.cloud_username)
        self.username_edit.textChanged.connect(self._on_username_changed)
        self.username_edit.editingFinished.connect(self._save_fields)
        layout.addWidget(self.username_edit)

        self.password_edit = QLineEdit()
        self.password_edit.setPlaceholderText("密码")
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setText(config.cloud_password)
        self.password_edit.textChanged.connect(self._enable_register)
        self.password_edit.editingFinished.connect(self._save_fields)
        layout.addWidget(self.password_edit)

        self.server_edit = QLineEdit()
        self.server_edit.setPlaceholderText(DEFAULT_CLOUD_SERVER)
        self.server_edit.setText(config.cloud_server_url or DEFAULT_CLOUD_SERVER)
        self.server_edit.setToolTip("云端地址是 https://subtitle.gcsfg.work。")
        self.server_edit.textChanged.connect(self._enable_register)
        self.server_edit.editingFinished.connect(self._save_fields)
        layout.addWidget(self.server_edit)

        buttons = QHBoxLayout()
        self._register_btn = QPushButton("注册")
        self._register_btn.setAutoDefault(False)
        self._register_btn.setDefault(False)
        self._register_btn.clicked.connect(self._register)
        login_btn = QPushButton("登录")
        login_btn.setAutoDefault(False)
        login_btn.setDefault(False)
        login_btn.clicked.connect(self._login)
        library_btn = QPushButton("云端管理")
        library_btn.setAutoDefault(False)
        library_btn.setDefault(False)
        library_btn.clicked.connect(self._open_library)
        buttons.addWidget(self._register_btn)
        buttons.addWidget(login_btn)
        buttons.addWidget(library_btn)
        layout.addLayout(buttons)
        self._refresh_hint()

    def _on_username_changed(self, text: str) -> None:
        trimmed = text.strip()
        if trimmed != text:
            cursor = self.username_edit.cursorPosition()
            removed = len(text) - len(trimmed)
            self.username_edit.blockSignals(True)
            self.username_edit.setText(trimmed)
            self.username_edit.setCursorPosition(max(0, cursor - removed))
            self.username_edit.blockSignals(False)
        self._enable_register()

    def _enable_register(self, *_args) -> None:
        self._register_btn.setEnabled(True)

    def _username_or_reject(self) -> str | None:
        name = self.username_edit.text().strip()
        if any(char.isspace() for char in name):
            QMessageBox.information(self, "用户设置", "用户名中间不能有空格。")
            return None
        return name

    def _save_fields(self) -> None:
        name = self.username_edit.text().strip()
        if name != self.username_edit.text():
            self.username_edit.setText(name)
        if not any(char.isspace() for char in name):
            self.config.cloud_username = name
        self.config.cloud_password = self.password_edit.text()
        self.config.cloud_server_url = self.server_edit.text().strip() or DEFAULT_CLOUD_SERVER
        save_config(self.config)
        self._refresh_hint()

    def _refresh_hint(self) -> None:
        if self.config.cloud_username:
            self._hint.setText(
                f"当前用户：{self.config.cloud_username}。其他设备填写同一个用户名，就是同一个用户。"
            )
        else:
            self._hint.setText(
                "还没有用户名，请先注册。不同设备填写同一个用户名，就是同一个用户。"
            )

    def _account(self) -> tuple[str, str, str]:
        self._save_fields()
        return (
            self.config.cloud_server_url,
            self.config.cloud_username,
            self.config.cloud_password,
        )

    def _register(self) -> None:
        name = self._username_or_reject()
        if name is None:
            return
        if not name or not self.password_edit.text():
            QMessageBox.information(self, "用户设置", "请先填写用户名和密码，再注册。")
            return
        account = self._account()

        def work():
            client = CloudClient(*account)
            try:
                client.register()
            except CloudError as exc:
                if exc.status != 409:
                    raise
                return ""
            return account[1]

        self._run_cloud_task(work, self._on_registered, "正在注册…")

    def _on_registered(self, username: str) -> None:
        if not username:
            self._register_btn.setEnabled(False)
            self._hint.setText("这个用户名已被占用，不能再注册。修改用户名、密码或服务器地址后可以再试。")
            QMessageBox.information(self, "用户设置", "这个用户名已被占用，不能再注册。")
            return
        self._refresh_hint()
        QMessageBox.information(self, "用户设置", f"注册成功：{username}")

    def _login(self) -> None:
        if self._username_or_reject() is None:
            return
        account = self._account()
        if not account[1] or not account[2]:
            QMessageBox.information(
                self,
                "用户设置",
                "还没有用户名。请先填写用户名和密码并注册。\n"
                "不同设备使用同一个用户名，即为同一用户。",
            )
            return

        def work():
            load_account_client(*account)
            return account[1]

        self._run_cloud_task(work, self._on_logged_in, "正在登录…")

    def _open_library(self) -> None:
        if self._username_or_reject() is None:
            return
        account = self._account()
        if not account[1] or not account[2]:
            QMessageBox.information(
                self,
                "用户设置",
                "还没有用户名。请先填写用户名和密码并注册。\n"
                "不同设备使用同一个用户名，即为同一用户。",
            )
            return

        def work():
            return load_account_client(*account)

        def open_dialog(client: CloudClient) -> None:
            CloudLibraryDialog(client, self).exec()

        self._run_cloud_task(work, open_dialog, "正在连接云端…")

    def _on_logged_in(self, username: str) -> None:
        self._refresh_hint()
        QMessageBox.information(self, "用户设置", f"已登录：{username}")

    def _run_cloud_task(self, work, on_success, title: str) -> None:
        host = self.parent()
        start = getattr(host, "_start_cloud_task", None)
        if start is None:
            QMessageBox.warning(self, "用户设置", "无法发起云同步请求。")
            return
        start(work, on_success, title)

    def closeEvent(self, event) -> None:
        self._save_fields()
        super().closeEvent(event)
