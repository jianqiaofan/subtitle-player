from __future__ import annotations

import re
import subprocess
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
