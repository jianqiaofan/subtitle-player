from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from core.ai_notes import build_notes_prompt, format_messages_for_edit, parse_messages_from_edit
from core.ai_notes_subtitle_types import get_subtitle_type
from core.ai_notes_templates import get_ai_notes_template
from gui.ai_notes_subtitle_controls import AiNotesSubtitleTypeControls
from gui.styles import DARK_STYLE


@dataclass(frozen=True)
class AiNotesPromptEditResult:
    messages: list[dict[str, str]]
    subtitle_type: str
    subcategory_by_type: dict[str, str]
    user_context_by_type: dict[str, str]

    @property
    def template_id(self) -> str:
        return self.subtitle_type


class AiNotesCorpusDialog(QDialog):
    """确认完整 Prompt 后提交 DeepSeek 前的可编辑预览对话框。"""

    def __init__(
        self,
        media_name: str,
        corpus_text: str,
        subtitle_type: str,
        subcategories: dict[str, str] | None = None,
        user_contexts: dict[str, str] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._media_name = media_name
        self._corpus_text = corpus_text
        self._subtitle_type = subtitle_type
        self._messages: list[dict[str, str]] | None = None
        self._updating_prompt = False
        self.setWindowTitle("确认发送 Prompt")
        self.setMinimumSize(720, 580)
        self.resize(860, 660)
        self.setStyleSheet(DARK_STYLE)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        layout.addWidget(
            QLabel(
                f"以下为即将发送给 DeepSeek 的完整 Prompt（视频：{media_name}）。\n"
                "可选择字幕类型、填写补充说明并编辑内容；请保留「=== system ===」与「=== user ===」两个区块标题。"
            )
        )

        self.subtitle_controls = AiNotesSubtitleTypeControls(context_min_width=240)
        self.subtitle_controls.apply_config(
            subtitle_type,
            subcategories=subcategories,
            user_contexts=user_contexts,
        )
        self.subtitle_controls.add_changed_callback(self._on_subtitle_prefs_changed)
        layout.addWidget(self.subtitle_controls)

        self.description_label = QLabel()
        self.description_label.setObjectName("hintLabel")
        self.description_label.setWordWrap(True)
        layout.addWidget(self.description_label)

        self.prompt_edit = QPlainTextEdit()
        self.prompt_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        layout.addWidget(self.prompt_edit, stretch=1)

        reset_row = QHBoxLayout()
        reset_row.addStretch(1)
        reset_btn = QPushButton("按当前设置重置 Prompt")
        reset_btn.clicked.connect(self._reset_prompt_from_template)
        reset_row.addWidget(reset_btn)
        layout.addLayout(reset_row)

        hint = QLabel(
            "「学习」「电影」请先选二级分类；「其它」无二级选项，请在补充说明中描述需求。"
            "修改设置后会自动更新 Prompt。"
        )
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确认发送")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._refresh_description()
        self._reset_prompt_from_template()

    def _refresh_description(self) -> None:
        template = get_ai_notes_template(self.subtitle_controls.subtitle_type_id())
        category = get_subtitle_type(self.subtitle_controls.subtitle_type_id())
        extra = ""
        if category.has_subcategory():
            extra = f"请先选择{category.subcategory_label}。"
        else:
            extra = "请在补充说明中描述整理目标。"
        self.description_label.setText(f"{template.description} {extra}")

    def _reset_prompt_from_template(self) -> None:
        if self._updating_prompt:
            return
        self._updating_prompt = True
        messages = build_notes_prompt(
            self._media_name,
            self._corpus_text,
            template_id=self.subtitle_controls.subtitle_type_id(),
            subcategory=self.subtitle_controls.subcategory(),
            user_context=self.subtitle_controls.user_context(),
        )
        self.prompt_edit.setPlainText(format_messages_for_edit(messages))
        self._updating_prompt = False

    def _on_subtitle_prefs_changed(self) -> None:
        self._refresh_description()
        self._reset_prompt_from_template()

    def _accept(self) -> None:
        category = get_subtitle_type(self.subtitle_controls.subtitle_type_id())
        if not category.has_subcategory() and not self.subtitle_controls.user_context():
            QMessageBox.warning(
                self,
                "请填写补充说明",
                "选择「其它」时，请在补充说明中描述希望如何整理字幕。",
            )
            return
        try:
            self._messages = parse_messages_from_edit(self.prompt_edit.toPlainText())
        except ValueError as exc:
            QMessageBox.warning(self, "Prompt 格式错误", str(exc))
            return
        self._subtitle_type = self.subtitle_controls.subtitle_type_id()
        self.accept()

    def result_data(self) -> AiNotesPromptEditResult:
        messages = self._messages
        if messages is None:
            messages = parse_messages_from_edit(self.prompt_edit.toPlainText())
        subtitle_type, subcategories, contexts = self.subtitle_controls.export_state()
        return AiNotesPromptEditResult(
            messages=messages,
            subtitle_type=subtitle_type,
            subcategory_by_type=subcategories,
            user_context_by_type=contexts,
        )

    @staticmethod
    def edit_prompt(
        media_name: str,
        corpus_text: str,
        subtitle_type: str,
        subcategories: dict[str, str] | None = None,
        user_contexts: dict[str, str] | None = None,
        parent=None,
    ) -> AiNotesPromptEditResult | None:
        dialog = AiNotesCorpusDialog(
            media_name,
            corpus_text,
            subtitle_type,
            subcategories=subcategories,
            user_contexts=user_contexts,
            parent=parent,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.result_data()
