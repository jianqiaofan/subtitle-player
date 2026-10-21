"""后台翻译一组字幕。界面线程负责写回文件和决定下一组。"""

from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal

from core.config import AppConfig
from core.subtitle_translation import translate_cue_batch


class SubtitleTranslateWorker(QThread):
    succeeded = pyqtSignal(int, object)
    failed = pyqtSignal(int, str)

    def __init__(
        self,
        job_id: int,
        items: list[dict[str, object]],
        target_lang: str,
        config: AppConfig,
    ) -> None:
        super().__init__()
        self.job_id = job_id
        self.items = items
        self.target_lang = target_lang
        self.config = config

    def run(self) -> None:
        try:
            pairs = [
                (int(item["row"]), str(item["source"]))
                for item in self.items
            ]
            translated = translate_cue_batch(self.config, pairs, self.target_lang)
            payload = [
                {"row": row, "source": source, "translation": translation}
                for row, source, translation in translated
            ]
            self.succeeded.emit(self.job_id, payload)
        except Exception as exc:
            message = str(exc).strip() or "翻译失败"
            if len(message) > 240:
                message = message[:240] + "…"
            self.failed.emit(self.job_id, message)
