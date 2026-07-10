"""播放器与 Prompt 对话框共用的「字幕类型」选择控件。"""

from __future__ import annotations

from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QLineEdit, QWidget

from core.ai_notes_subtitle_types import (
    get_subtitle_type,
    list_subtitle_types,
    normalize_subcategory,
    normalize_subtitle_type_id,
)
from core.ai_notes_templates import get_ai_notes_template


class AiNotesSubtitleTypeControls(QWidget):
    """字幕类型 + 二级选项 + 补充说明。"""

    def __init__(self, parent=None, *, context_min_width: int = 160) -> None:
        super().__init__(parent)
        self._loading = False
        self._subcategory_by_type: dict[str, str] = {}
        self._context_by_type: dict[str, str] = {}

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        layout.addWidget(QLabel("字幕类型"))
        self.type_combo = QComboBox()
        self.type_combo.setMinimumWidth(72)
        for category in list_subtitle_types():
            self.type_combo.addItem(category.name, category.id)
        self.type_combo.currentIndexChanged.connect(self._on_type_changed)
        layout.addWidget(self.type_combo)

        self.subcategory_combo = QComboBox()
        self.subcategory_combo.setMinimumWidth(96)
        self.subcategory_combo.currentIndexChanged.connect(self._on_value_changed)
        layout.addWidget(self.subcategory_combo)

        self.context_edit = QLineEdit()
        self.context_edit.setMinimumWidth(context_min_width)
        self.context_edit.setClearButtonEnabled(True)
        self.context_edit.setPlaceholderText("补充说明")
        self.context_edit.editingFinished.connect(self._on_value_changed)
        layout.addWidget(self.context_edit, stretch=1)

        self._change_callbacks: list = []
        self.set_subtitle_type(normalize_subtitle_type_id("learning"))

    def add_changed_callback(self, callback) -> None:
        self._change_callbacks.append(callback)

    def subtitle_type_id(self) -> str:
        data = self.type_combo.currentData()
        return normalize_subtitle_type_id(str(data or ""))

    def subcategory(self) -> str:
        category = get_subtitle_type(self.subtitle_type_id())
        if not category.has_subcategory():
            return ""
        data = self.subcategory_combo.currentData()
        return normalize_subcategory(self.subtitle_type_id(), str(data or ""))

    def user_context(self) -> str:
        return self.context_edit.text().strip()

    def apply_config(
        self,
        subtitle_type: str,
        subcategories: dict[str, str] | None = None,
        user_contexts: dict[str, str] | None = None,
    ) -> None:
        self._subcategory_by_type = {
            normalize_subtitle_type_id(key): str(value).strip()
            for key, value in (subcategories or {}).items()
            if str(value or "").strip()
        }
        self._context_by_type = {
            normalize_subtitle_type_id(key): str(value).strip()
            for key, value in (user_contexts or {}).items()
            if str(value or "").strip()
        }
        type_id = normalize_subtitle_type_id(subtitle_type)
        self.set_subtitle_type(
            type_id,
            subcategory=self._subcategory_by_type.get(type_id, ""),
            user_context=self._context_by_type.get(type_id, ""),
        )

    def export_state(self) -> tuple[str, dict[str, str], dict[str, str]]:
        self._cache_current()
        return (
            self.subtitle_type_id(),
            dict(self._subcategory_by_type),
            dict(self._context_by_type),
        )

    def set_subtitle_type(
        self,
        type_id: str,
        *,
        subcategory: str = "",
        user_context: str = "",
    ) -> None:
        self._loading = True
        type_id = normalize_subtitle_type_id(type_id)
        if subcategory.strip():
            self._subcategory_by_type[type_id] = subcategory.strip()
        if user_context.strip():
            self._context_by_type[type_id] = user_context.strip()
        type_index = self.type_combo.findData(type_id)
        if type_index >= 0:
            self.type_combo.setCurrentIndex(type_index)
        self._populate_subcategory_combo(
            type_id,
            self._subcategory_by_type.get(type_id, subcategory),
        )
        self.context_edit.setText(self._context_by_type.get(type_id, user_context))
        self._refresh_hints()
        self._update_subcategory_visibility()
        self._loading = False

    def _cache_current(self) -> None:
        type_id = self.subtitle_type_id()
        category = get_subtitle_type(type_id)
        if category.has_subcategory():
            self._subcategory_by_type[type_id] = self.subcategory()
        else:
            self._subcategory_by_type.pop(type_id, None)
        context = self.user_context()
        if context:
            self._context_by_type[type_id] = context
        else:
            self._context_by_type.pop(type_id, None)

    def _populate_subcategory_combo(self, type_id: str, selected: str = "") -> None:
        category = get_subtitle_type(type_id)
        self.subcategory_combo.blockSignals(True)
        self.subcategory_combo.clear()
        if category.subcategories:
            for item in category.subcategories:
                self.subcategory_combo.addItem(item, item)
            normalized = normalize_subcategory(type_id, selected)
            index = self.subcategory_combo.findData(normalized)
            if index >= 0:
                self.subcategory_combo.setCurrentIndex(index)
        self.subcategory_combo.blockSignals(False)

    def _refresh_hints(self) -> None:
        template = get_ai_notes_template(self.subtitle_type_id())
        category = get_subtitle_type(self.subtitle_type_id())
        if category.has_subcategory():
            self.subcategory_combo.setToolTip(category.subcategory_label or "")
            self.context_edit.setPlaceholderText(f"可选 · {template.user_context_hint}")
        else:
            self.context_edit.setPlaceholderText(template.user_context_hint)
        self.context_edit.setToolTip(template.user_context_hint)

    def _update_subcategory_visibility(self) -> None:
        visible = get_subtitle_type(self.subtitle_type_id()).has_subcategory()
        self.subcategory_combo.setVisible(visible)

    def _notify_changed(self) -> None:
        for callback in self._change_callbacks:
            callback()

    def _on_type_changed(self) -> None:
        if self._loading:
            return
        self._cache_current()
        type_id = self.subtitle_type_id()
        self._populate_subcategory_combo(
            type_id,
            self._subcategory_by_type.get(type_id, ""),
        )
        self.context_edit.setText(self._context_by_type.get(type_id, ""))
        self._refresh_hints()
        self._update_subcategory_visibility()
        self._notify_changed()

    def _on_value_changed(self) -> None:
        if self._loading:
            return
        self._cache_current()
        self._notify_changed()
