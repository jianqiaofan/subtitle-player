from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from core.audio import find_ffmpeg, get_media_duration


@dataclass(frozen=True)
class SpeechRegion:
    start_sec: float
    end_sec: float

    @property
    def duration_sec(self) -> float:
        return max(0.0, self.end_sec - self.start_sec)


def detect_speech_regions(
    audio: np.ndarray,
    wav_path: str | None = None,
    sample_rate: int = 16000,
    noise_db: float = -35.0,
    min_silence_sec: float = 0.35,
    min_speech_sec: float = 0.5,
    max_speech_sec: float = 40.0,
    padding_sec: float = 0.06,
) -> list[SpeechRegion]:
    """
    按语音停顿（静音断点）切分音频，返回若干连续语音区间。
    优先使用 ffmpeg silencedetect，失败时回退到 RMS 能量检测。
    """
    silences: list[tuple[float, float]] = []
    if wav_path:
        silences = _detect_silences_ffmpeg(
            wav_path,
            noise_db=noise_db,
            min_silence_sec=min_silence_sec,
        )
    if not silences:
        silences = _detect_silences_rms(
            audio,
            sample_rate=sample_rate,
            noise_db=noise_db,
            min_silence_sec=min_silence_sec,
        )

    duration_sec = len(audio) / sample_rate
    regions = _silences_to_speech_regions(
        silences,
        duration_sec,
        min_speech_sec=min_speech_sec,
        padding_sec=padding_sec,
    )
    return _split_long_regions(regions, silences, max_speech_sec, min_silence_sec)


def slice_audio(audio: np.ndarray, region: SpeechRegion, sample_rate: int = 16000) -> np.ndarray:
    start = max(0, int(region.start_sec * sample_rate))
    end = min(len(audio), int(region.end_sec * sample_rate))
    if end <= start:
        return np.array([], dtype=audio.dtype)
    return audio[start:end]


def get_wav_duration(wav_path: Path, sample_rate: int = 16000) -> float:
    try:
        return get_media_duration(wav_path)
    except RuntimeError:
        size = wav_path.stat().st_size
        # 16-bit mono PCM
        return max(0.0, (size - 44) / (sample_rate * 2))


def detect_speech_regions_from_wav_file(
    wav_path: Path,
    sample_rate: int = 16000,
    offset_sec: float = 0.0,
    noise_db: float = -35.0,
    min_silence_sec: float = 0.35,
    min_speech_sec: float = 0.5,
    max_speech_sec: float = 40.0,
    padding_sec: float = 0.06,
) -> list[SpeechRegion]:
    """基于 WAV 文件检测语音区间，不将整个音频载入内存。"""
    duration_sec = get_wav_duration(wav_path, sample_rate)
    if duration_sec <= 0:
        return []

    silences = _detect_silences_ffmpeg(
        str(wav_path),
        noise_db=noise_db,
        min_silence_sec=min_silence_sec,
    )
    if not silences:
        import numpy as np

        raw = wav_path.read_bytes()
        # 跳过 WAV 头，粗略加载（仅短分片会走到此分支）
        pcm = np.frombuffer(raw[44:], dtype=np.int16).astype(np.float32) / 32768.0
        silences = _detect_silences_rms(
            pcm,
            sample_rate=sample_rate,
            noise_db=noise_db,
            min_silence_sec=min_silence_sec,
        )

    regions = _silences_to_speech_regions(
        silences,
        duration_sec,
        min_speech_sec=min_speech_sec,
        padding_sec=padding_sec,
        offset_sec=offset_sec,
    )
    return _split_long_regions(regions, silences, max_speech_sec, min_silence_sec)


def build_speech_regions_for_media(
    media_path: Path,
    work_dir: Path,
    *,
    # 2 小时内整段提取一次，避免边播边转时反复打开源文件与播放器抢句柄。
    long_video_threshold_sec: float = 7200.0,
    window_sec: float = 600.0,
    on_progress: Callable[[str], None] | None = None,
    on_regions_batch: Callable[[list[SpeechRegion]], None] | None = None,
) -> list[SpeechRegion]:
    """
    为媒体构建语音分片列表。
    常规时长：提取完整 WAV 后一次性分析；
    超长视频（默认 >=2h）：按窗口逐段提取并分析，避免长时间等待与过高内存占用。
    """
    all_regions: list[SpeechRegion] = []
    for batch in iter_speech_regions_for_media(
        media_path,
        work_dir,
        long_video_threshold_sec=long_video_threshold_sec,
        window_sec=window_sec,
        on_progress=on_progress,
    ):
        all_regions.extend(batch)
        if on_regions_batch and batch:
            on_regions_batch(batch)
    return all_regions


def iter_speech_regions_for_media(
    media_path: Path,
    work_dir: Path,
    *,
    # 2 小时内整段提取一次，避免边播边转时反复打开源文件与播放器抢句柄。
    long_video_threshold_sec: float = 7200.0,
    window_sec: float = 600.0,
    on_progress: Callable[[str], None] | None = None,
):
    """按批次 yield 语音区间，便于边播边转时长视频增量启动。"""
    from core.audio import extract_audio, extract_audio_segment

    duration = get_media_duration(media_path)
    if duration <= long_video_threshold_sec:
        if on_progress:
            on_progress("正在提取音频并分析语音结构…")
        wav_path = extract_audio(media_path, work_dir)
        regions = detect_speech_regions_from_wav_file(wav_path)
        if on_progress:
            on_progress(f"语音结构分析完成，共 {len(regions)} 个分片")
        yield regions
        return

    if on_progress:
        on_progress(
            f"长视频（{_fmt_duration(duration)}），按 {_fmt_duration(window_sec)} 分段分析语音结构…"
        )

    offset = 0.0
    window_index = 0
    total = 0
    while offset < duration - 0.05:
        window_index += 1
        chunk_duration = min(window_sec, duration - offset)
        chunk_wav = work_dir / f"scan_{window_index:04d}.wav"
        if on_progress:
            on_progress(
                f"分析语音结构 {window_index}："
                f"{_fmt_duration(offset)} → {_fmt_duration(offset + chunk_duration)}"
            )
        extract_audio_segment(media_path, offset, chunk_duration, chunk_wav)
        window_regions = detect_speech_regions_from_wav_file(
            chunk_wav,
            offset_sec=offset,
        )
        try:
            chunk_wav.unlink(missing_ok=True)
        except OSError:
            pass
        total += len(window_regions)
        if on_progress:
            on_progress(f"已发现 {total} 个语音分片，继续分析…")
        yield window_regions
        offset += window_sec

    if on_progress:
        on_progress(f"语音结构分析完成，共 {total} 个分片")


def _fmt_duration(seconds: float) -> str:
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _detect_silences_ffmpeg(
    wav_path: str,
    noise_db: float,
    min_silence_sec: float,
) -> list[tuple[float, float]]:
    cmd = [
        find_ffmpeg(),
        "-hide_banner",
        "-loglevel",
        "info",
        "-i",
        wav_path,
        "-af",
        f"silencedetect=noise={noise_db}dB:d={min_silence_sec}",
        "-f",
        "null",
        "-",
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = (result.stderr or "") + (result.stdout or "")
    silences: list[tuple[float, float]] = []
    starts: list[float] = []
    for line in output.splitlines():
        if "silence_start:" in line:
            match = re.search(r"silence_start:\s*([0-9.]+)", line)
            if match:
                starts.append(float(match.group(1)))
        elif "silence_end:" in line:
            match = re.search(r"silence_end:\s*([0-9.]+)", line)
            if match and starts:
                start = starts.pop(0)
                end = float(match.group(1))
                if end > start:
                    silences.append((start, end))
    return silences


def _detect_silences_rms(
    audio: np.ndarray,
    sample_rate: int,
    noise_db: float,
    min_silence_sec: float,
    frame_ms: int = 30,
    hop_ms: int = 10,
) -> list[tuple[float, float]]:
    if audio.size == 0:
        return []

    frame_size = int(sample_rate * frame_ms / 1000)
    hop_size = int(sample_rate * hop_ms / 1000)
    if frame_size <= 0 or hop_size <= 0:
        return []

    threshold = 10 ** (noise_db / 20.0)
    mins = max(1, int(min_silence_sec * 1000 / hop_ms))

    silent = []
    for start in range(0, len(audio) - frame_size + 1, hop_size):
        frame = audio[start : start + frame_size]
        rms = float(np.sqrt(np.mean(frame * frame)))
        silent.append(rms < threshold)

    silences: list[tuple[float, float]] = []
    idx = 0
    while idx < len(silent):
        if not silent[idx]:
            idx += 1
            continue
        run_start = idx
        while idx < len(silent) and silent[idx]:
            idx += 1
        run_len = idx - run_start
        if run_len >= mins:
            start_sec = run_start * hop_ms / 1000.0
            end_sec = idx * hop_ms / 1000.0
            silences.append((start_sec, end_sec))
    return silences


def _silences_to_speech_regions(
    silences: list[tuple[float, float]],
    duration_sec: float,
    min_speech_sec: float,
    padding_sec: float,
    offset_sec: float = 0.0,
) -> list[SpeechRegion]:
    if duration_sec <= 0:
        return []

    silences = sorted(silences)
    merged_silences: list[tuple[float, float]] = []
    for start, end in silences:
        if not merged_silences or start > merged_silences[-1][1]:
            merged_silences.append((start, end))
        else:
            merged_silences[-1] = (merged_silences[-1][0], max(merged_silences[-1][1], end))

    speech: list[SpeechRegion] = []
    cursor = 0.0
    for silence_start, silence_end in merged_silences:
        if silence_start > cursor:
            region = SpeechRegion(
                offset_sec + max(0.0, cursor - padding_sec),
                offset_sec + min(duration_sec, silence_start + padding_sec),
            )
            if region.duration_sec >= min_speech_sec:
                speech.append(region)
        cursor = max(cursor, silence_end)

    if cursor < duration_sec:
        region = SpeechRegion(
            offset_sec + max(0.0, cursor - padding_sec),
            offset_sec + duration_sec,
        )
        if region.duration_sec >= min_speech_sec:
            speech.append(region)

    if not speech:
        return [SpeechRegion(offset_sec, offset_sec + duration_sec)]
    return speech


def _split_long_regions(
    regions: list[SpeechRegion],
    silences: list[tuple[float, float]],
    max_speech_sec: float,
    min_silence_sec: float,
) -> list[SpeechRegion]:
    result: list[SpeechRegion] = []
    for region in regions:
        if region.duration_sec <= max_speech_sec:
            result.append(region)
            continue

        inner = [
            (s, e)
            for s, e in silences
            if s > region.start_sec + 0.2
            and e < region.end_sec - 0.2
            and (e - s) >= min_silence_sec * 0.6
        ]
        if inner:
            relative = [(s - region.start_sec, e - region.start_sec) for s, e in inner]
            pieces = _silences_to_speech_regions(
                relative,
                region.duration_sec,
                min_speech_sec=0.5,
                padding_sec=0.04,
                offset_sec=region.start_sec,
            )
            if pieces:
                result.extend(pieces)
                continue

        start = region.start_sec
        while start < region.end_sec:
            end = min(region.end_sec, start + max_speech_sec)
            if end - start >= 0.5:
                result.append(SpeechRegion(start, end))
            start = end
    return result
