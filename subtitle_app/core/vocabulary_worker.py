from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

from core.config import AppConfig, load_config
from core.vocabulary import VocabularyOptions, generate_vocabulary_list


class VocabularyWorker(QThread):
    status_changed = pyqtSignal(str)
    finished_ok = pyqtSignal(str, str)
    failed = pyqtSignal(str)

    def __init__(
        self,
        media_path: Path,
        options: VocabularyOptions,
        config: AppConfig | None = None,
    ) -> None:
        super().__init__()
        self.media_path = media_path.resolve()
        self.options = options
        self.config = config or load_config()
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        if self._cancelled:
            return
        try:
            self.status_changed.emit("正在分析字幕词汇…")
            if self._cancelled:
                return
            if self.options.language in {"en", "ja"}:
                self.status_changed.emit("正在查询注音/音标与释义…")
            markdown_path, csv_path = generate_vocabulary_list(
                self.media_path,
                self.options,
                ecdict_db_path=self.config.ecdict_db_path,
            )
            if self._cancelled:
                return
            self.finished_ok.emit(str(markdown_path), str(csv_path))
        except Exception as exc:
            self.failed.emit(str(exc))
