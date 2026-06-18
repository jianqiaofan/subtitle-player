from __future__ import annotations

import tempfile
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

from core.audio import extract_audio
from core.config import AppConfig
from core.subtitle import write_subtitle_file
from core.transcriber import transcribe


class TranscribeWorker(QThread):
    log = pyqtSignal(str)
    file_started = pyqtSignal(str)
    file_finished = pyqtSignal(str, str)
    file_failed = pyqtSignal(str, str)
    progress = pyqtSignal(int, int)
    all_done = pyqtSignal()

    def __init__(self, files: list[Path], config: AppConfig) -> None:
        super().__init__()
        self.files = files
        self.config = config
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def _emit_log(self, message: str) -> None:
        self.log.emit(message)

    def run(self) -> None:
        total = len(self.files)

        with tempfile.TemporaryDirectory(prefix="subtitle_app_") as tmp:
            work_dir = Path(tmp)

            try:
                from core.transcriber import get_whisper_model
                get_whisper_model(self.config, self._emit_log)
            except Exception as exc:
                self._emit_log(f"模型加载失败: {exc}")
                self.all_done.emit()
                return

            for index, media_path in enumerate(self.files, start=1):
                if self._cancelled:
                    self._emit_log("任务已取消。")
                    break

                self.progress.emit(index, total)
                self.file_started.emit(str(media_path))
                self._emit_log(f"\n===== 开始处理 ({index}/{total}): {media_path.name} =====")

                try:
                    self._emit_log("正在提取音频...")
                    audio_path = extract_audio(media_path, work_dir)
                    self._emit_log(
                        f"音频已提取: {audio_path.name} ({audio_path.stat().st_size / 1024:.0f} KB)"
                    )
                    segments = transcribe(audio_path, self.config, self._emit_log)

                    output_path = self.config.build_output_path(media_path)
                    output_path.parent.mkdir(parents=True, exist_ok=True)
                    write_subtitle_file(segments, output_path, self.config.output_format)
                    self._emit_log(f"已保存: {output_path}")
                    self.file_finished.emit(str(media_path), str(output_path))
                except Exception as exc:
                    self._emit_log(f"失败: {exc}")
                    self.file_failed.emit(str(media_path), str(exc))

        self.all_done.emit()
