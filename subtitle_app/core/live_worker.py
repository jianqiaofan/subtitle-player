from __future__ import annotations

import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

from core.config import AppConfig, load_config
from core.speech_split import SpeechRegion, iter_speech_regions_for_media
from core.subtitle import SubtitleSegment, write_subtitle_file
from core.sync_subtitle import completed_region_indices
from core.transcriber import get_whisper_model, transcribe_region_mixed

GetPlayhead = Callable[[], float]
GetPriority = Callable[[], float | None]

BUFFER_AHEAD_SEC = 90.0
SAVE_EVERY_CHUNKS = 3
PLAYING_THREAD_RATIO = 0.65


def _pick_next_region_index(
    regions: list[SpeechRegion],
    completed: set[int],
    playhead_sec: float,
    priority_sec: float | None,
    buffer_ahead: float,
) -> int | None:
    if priority_sec is not None:
        best_idx: int | None = None
        best_dist = float("inf")
        for index, region in enumerate(regions):
            if index in completed:
                continue
            if region.start_sec <= priority_sec <= region.end_sec + 2.0:
                return index
            dist = min(
                abs(region.start_sec - priority_sec),
                abs(region.end_sec - priority_sec),
            )
            if dist < best_dist:
                best_dist = dist
                best_idx = index
        if best_idx is not None:
            return best_idx

    for index, region in enumerate(regions):
        if index in completed:
            continue
        if region.start_sec <= playhead_sec + buffer_ahead:
            return index

    for index, region in enumerate(regions):
        if index not in completed:
            return index
    return None


def _transcribed_until_sec(regions: list[SpeechRegion], completed: set[int]) -> float:
    if not completed:
        return 0.0
    return max(regions[i].end_sec for i in completed if i < len(regions))


def _renumber_segments(segments: list[SubtitleSegment]) -> list[SubtitleSegment]:
    ordered = sorted(segments, key=lambda seg: (seg.start, seg.end))
    return [
        SubtitleSegment(index, seg.start, seg.end, seg.text)
        for index, seg in enumerate(ordered, start=1)
    ]


class LiveTranscribeWorker(QThread):
    status_changed = pyqtSignal(str)
    segments_ready = pyqtSignal(list)
    buffer_updated = pyqtSignal(float)
    finished_ok = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(
        self,
        media_path: Path,
        get_playhead: GetPlayhead,
        get_priority: GetPriority | None = None,
        config: AppConfig | None = None,
        is_playing: Callable[[], bool] | None = None,
        resume_segments: list[SubtitleSegment] | None = None,
    ) -> None:
        super().__init__()
        self.media_path = media_path.resolve()
        self._get_playhead = get_playhead
        self._get_priority = get_priority or (lambda: None)
        self._is_playing = is_playing or (lambda: True)
        self.config = config or load_config()
        self.config.language = "mixed"
        self._cancelled = False
        self._priority_sec: float | None = None
        self._resume_segments = list(resume_segments or [])

    def cancel(self) -> None:
        self._cancelled = True

    def set_priority_sec(self, seconds: float) -> None:
        self._priority_sec = seconds

    def _emit_status(self, message: str) -> None:
        self.status_changed.emit(message)

    def _resolved_threads(self) -> int:
        total = self.config.resolved_n_threads()
        if self._is_playing():
            return max(1, int(total * PLAYING_THREAD_RATIO))
        return total

    def run(self) -> None:
        output_path = self.config.build_live_output_path(self.media_path)
        partial_path = output_path.with_suffix(output_path.suffix + ".partial")

        with tempfile.TemporaryDirectory(prefix="subtitle_live_") as tmp:
            work_dir = Path(tmp)
            all_segments = _renumber_segments(list(self._resume_segments))
            completed: set[int] = set()
            regions: list[SpeechRegion] = []
            regions_lock = threading.Lock()
            scan_error: list[str] = []
            scan_done = threading.Event()

            def _scan_regions() -> None:
                try:
                    for batch in iter_speech_regions_for_media(
                        self.media_path,
                        work_dir,
                        on_progress=self._emit_status,
                    ):
                        if self._cancelled:
                            break
                        with regions_lock:
                            regions.extend(batch)
                            if all_segments:
                                completed.update(
                                    completed_region_indices(regions, all_segments)
                                )
                except Exception as exc:
                    scan_error.append(str(exc))
                finally:
                    scan_done.set()

            try:
                if all_segments:
                    covered_end = max(seg.end for seg in all_segments)
                    self._emit_status(
                        f"续转同步字幕（已有 {len(all_segments)} 条，"
                        f"覆盖至 {_fmt_time(covered_end)}）"
                    )
                    self.buffer_updated.emit(covered_end)
                else:
                    self._emit_status("正在加载 Whisper 模型…")
                self.config.n_threads = self._resolved_threads()
                model = get_whisper_model(self.config, self._emit_status)

                scanner = threading.Thread(target=_scan_regions, daemon=True)
                scanner.start()

                chunks_since_save = 0
                if all_segments:
                    write_subtitle_file(all_segments, partial_path, self.config.output_format)
                while True:
                    if self._cancelled:
                        self._emit_status("边播边转已取消。")
                        break

                    with regions_lock:
                        current_len = len(regions)

                    if scan_error:
                        raise RuntimeError(scan_error[0])

                    if current_len == 0:
                        if scan_done.is_set():
                            raise RuntimeError("未检测到可转写的语音片段。")
                        time.sleep(0.2)
                        continue

                    if self._is_playing():
                        self.config.n_threads = max(
                            1, int(self.config.resolved_n_threads() * PLAYING_THREAD_RATIO)
                        )

                    playhead = self._get_playhead()
                    priority = self._priority_sec
                    if priority is not None:
                        self._priority_sec = None

                    with regions_lock:
                        next_index = _pick_next_region_index(
                            regions,
                            completed,
                            playhead,
                            priority,
                            BUFFER_AHEAD_SEC,
                        )

                    if next_index is None:
                        if scan_done.is_set() and len(completed) >= current_len:
                            break
                        time.sleep(0.25)
                        continue

                    with regions_lock:
                        region = regions[next_index]
                    self._emit_status(
                        f"转写分片 {len(completed) + 1}/{current_len}+ "
                        f"[{_fmt_time(region.start_sec)} → {_fmt_time(region.end_sec)}]"
                    )
                    new_segments = transcribe_region_mixed(
                        model,
                        self.media_path,
                        region,
                        work_dir,
                        self._emit_status,
                    )
                    completed.add(next_index)
                    if new_segments:
                        all_segments.extend(new_segments)
                        all_segments = _renumber_segments(all_segments)
                        self.segments_ready.emit(new_segments)

                    transcribed_until = _transcribed_until_sec(regions, completed)
                    self.buffer_updated.emit(transcribed_until)

                    chunks_since_save += 1
                    if all_segments and chunks_since_save >= SAVE_EVERY_CHUNKS:
                        write_subtitle_file(all_segments, partial_path, self.config.output_format)
                        chunks_since_save = 0

                scanner.join(timeout=1.0)

                if self._cancelled:
                    if all_segments:
                        write_subtitle_file(all_segments, partial_path, self.config.output_format)
                    return

                if not all_segments:
                    raise RuntimeError("未识别到任何语音内容。")

                write_subtitle_file(all_segments, output_path, self.config.output_format)
                try:
                    partial_path.unlink(missing_ok=True)
                except OSError:
                    pass
                self._emit_status(f"字幕已保存: {output_path.name}")
                self.finished_ok.emit(str(output_path))

            except Exception as exc:
                if all_segments:
                    try:
                        write_subtitle_file(
                            all_segments,
                            partial_path,
                            self.config.output_format,
                        )
                    except OSError:
                        pass
                self.failed.emit(str(exc))


def _fmt_time(seconds: float) -> str:
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"
