from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

from core.ai_notes import generate_ai_notes
from core.config import AppConfig, load_config


class AiNotesWorker(QThread):
    status_changed = pyqtSignal(str)
    finished_ok = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(
        self,
        media_path: Path,
        config: AppConfig | None = None,
        *,
        messages: list[dict[str, str]] | None = None,
        template_id: str | None = None,
        subcategory: str = "",
    ) -> None:
        super().__init__()
        self.media_path = media_path.resolve()
        self.config = config or load_config()
        self.messages_override = messages
        self.template_id = template_id
        self.subcategory = subcategory
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        if self._cancelled:
            return
        try:
            if self.messages_override is None:
                self.status_changed.emit("正在整理字幕语料…")
                if self._cancelled:
                    return
            self.status_changed.emit("正在调用 DeepSeek 生成笔记…")
            output_path = generate_ai_notes(
                self.media_path,
                self.config,
                messages=self.messages_override,
                template_id=self.template_id,
                subcategory=self.subcategory,
            )
            if self._cancelled:
                return
            self.finished_ok.emit(str(output_path))
        except Exception as exc:
            self.failed.emit(str(exc))
