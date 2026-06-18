from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable

from core.config import AppConfig
from core.speech_split import SpeechRegion, detect_speech_regions, slice_audio
from core.subtitle import SubtitleSegment


ProgressCallback = Callable[[str], None]

_model_cache: dict[str, object] = {}
_last_language_mode: str | None = None

SAMPLE_RATE = 16000


def _setup_windows_dll_paths() -> None:
    if sys.platform != "win32":
        return
    import os
    import site

    candidates: list[Path] = []
    for entry in site.getsitepackages() + [site.getusersitepackages()]:
        pkg = Path(entry) / "pywhispercpp"
        if pkg.is_dir():
            candidates.append(pkg)
    for path in candidates:
        try:
            os.add_dll_directory(str(path))
        except (OSError, AttributeError):
            pass


def get_whisper_model(config: AppConfig, on_log: ProgressCallback | None = None):
    global _last_language_mode
    if _last_language_mode is not None and _last_language_mode != config.language:
        _model_cache.clear()
        if on_log:
            on_log("语种模式已变更，重新加载模型...")
    _last_language_mode = config.language

    _setup_windows_dll_paths()
    from pywhispercpp.model import Model

    model_path = config.validate_model()
    key = str(model_path.resolve())
    if key in _model_cache:
        return _model_cache[key]

    if on_log:
        on_log(f"正在加载模型: {model_path.name}")
        on_log(f"推理线程数: {config.resolved_n_threads()}")

    model = Model(str(model_path), n_threads=config.resolved_n_threads())
    _model_cache[key] = model
    if on_log:
        on_log("模型加载完成")
    return model


def _build_transcribe_kwargs(config: AppConfig) -> dict:
    """根据语种设置构建 whisper 转写参数（每次显式设置，避免参数残留）。"""
    lang = config.language
    if lang == "auto":
        return {
            "language": "",
            "detect_language": False,
            "translate": False,
        }
    return {
        "language": lang,
        "detect_language": False,
        "translate": False,
    }


def _language_mode_label(config: AppConfig) -> str:
    labels = {
        "mixed": "原文混排（多语言，按片段识别原文）",
        "auto": "自动检测",
    }
    return labels.get(config.language, config.language)


def _segments_from_raw(raw_segments, time_offset_cs: int = 0) -> list[SubtitleSegment]:
    segments: list[SubtitleSegment] = []
    for seg in raw_segments:
        text = (seg.text or "").strip()
        if not text:
            continue
        segments.append(
            SubtitleSegment(
                0,
                (seg.t0 + time_offset_cs) / 100.0,
                (seg.t1 + time_offset_cs) / 100.0,
                text,
            )
        )
    return segments


def transcribe_mixed(
    model,
    audio_path: Path,
    on_log: ProgressCallback | None = None,
) -> list[SubtitleSegment]:
    """原文混排：按语音停顿断点分片，每片自动检测语种后转写原文。"""
    audio = model._load_audio(str(audio_path))
    duration_sec = len(audio) / SAMPLE_RATE

    regions = detect_speech_regions(
        audio,
        wav_path=str(audio_path),
        sample_rate=SAMPLE_RATE,
    )

    if on_log:
        on_log(
            f"原文混排：音频时长 {_fmt_time(duration_sec)}，"
            f"按语音停顿切分为 {len(regions)} 段"
        )

    merged: list[SubtitleSegment] = []

    for chunk_index, region in enumerate(regions, start=1):
        chunk = slice_audio(audio, region, SAMPLE_RATE)
        if chunk.size < int(0.5 * SAMPLE_RATE):
            continue

        (detected_lang, probability), _ = model.auto_detect_language(chunk)

        if on_log:
            on_log(
                f"分片 {chunk_index}/{len(regions)} "
                f"[{_fmt_time(region.start_sec)} → {_fmt_time(region.end_sec)}] "
                f"时长 {region.duration_sec:.1f}s | 语种: {detected_lang} ({float(probability):.0%})"
            )

        raw_segments = model.transcribe(
            chunk,
            language=detected_lang,
            detect_language=False,
            translate=False,
        )
        time_offset_cs = int(region.start_sec * 100)
        merged.extend(_segments_from_raw(raw_segments, time_offset_cs))

        if on_log:
            on_log(f"  本分片字幕 {len(raw_segments)} 条")

    return [
        SubtitleSegment(index, seg.start, seg.end, seg.text)
        for index, seg in enumerate(merged, start=1)
    ]


def transcribe_region_mixed(
    model,
    media_path: Path,
    region: SpeechRegion,
    work_dir: Path,
    on_log: ProgressCallback | None = None,
) -> list[SubtitleSegment]:
    """对单个语音区间做原文混排转写（按需提取音频，适合边播边转）。"""
    from core.audio import extract_audio_segment

    chunk_path = work_dir / f"chunk_{int(region.start_sec * 1000)}.wav"
    extract_audio_segment(
        media_path,
        region.start_sec,
        region.duration_sec,
        chunk_path,
    )
    chunk = model._load_audio(str(chunk_path))
    try:
        chunk_path.unlink(missing_ok=True)
    except OSError:
        pass

    if chunk.size < int(0.5 * SAMPLE_RATE):
        return []

    (detected_lang, probability), _ = model.auto_detect_language(chunk)
    if on_log:
        on_log(
            f"[{_fmt_time(region.start_sec)} → {_fmt_time(region.end_sec)}] "
            f"语种 {detected_lang} ({float(probability):.0%})"
        )

    raw_segments = model.transcribe(
        chunk,
        language=detected_lang,
        detect_language=False,
        translate=False,
    )
    time_offset_cs = int(region.start_sec * 100)
    return _segments_from_raw(raw_segments, time_offset_cs)


def transcribe(
    audio_path: Path,
    config: AppConfig,
    on_log: ProgressCallback | None = None,
) -> list[SubtitleSegment]:
    model = get_whisper_model(config, on_log)

    if on_log:
        on_log(f"语种模式: {_language_mode_label(config)}")
        on_log(f"开始转写: {audio_path.name}")

    if config.language == "mixed":
        segments = transcribe_mixed(model, audio_path, on_log)
        if on_log:
            on_log(f"合并后片段数: {len(segments)}")
    else:
        kwargs = _build_transcribe_kwargs(config)
        if on_log:
            on_log(f"转写参数: {kwargs}")

        raw_segments = model.transcribe(str(audio_path), **kwargs)
        if on_log:
            on_log(f"原始片段数: {len(raw_segments)}")

        segments = _segments_from_raw(raw_segments)
        for index, seg in enumerate(segments, start=1):
            seg.index = index
            if on_log:
                on_log(f"[{_fmt_time(seg.start)}] {seg.text}")

    if not segments:
        size_kb = audio_path.stat().st_size / 1024 if audio_path.exists() else 0
        raise RuntimeError(
            f"未识别到任何语音内容。"
            f"（音频文件 {size_kb:.0f} KB）。"
            f"请检查视频是否有声音，或尝试切换为「自动检测」/固定语种。"
        )
    return segments


def _fmt_time(seconds: float) -> str:
    minutes, rem = divmod(int(seconds), 60)
    return f"{minutes:02d}:{rem:02d}"
