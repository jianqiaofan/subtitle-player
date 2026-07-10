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
_last_inference_device: str | None = None

SAMPLE_RATE = 16000


def _site_packages_dir() -> Path:
    import site

    for entry in site.getsitepackages() + [site.getusersitepackages()]:
        root = Path(entry)
        if (root / "pywhispercpp").is_dir() or any(root.glob("ggml*.dll")):
            return root
    return Path(site.getsitepackages()[-1])


def is_cuda_available() -> bool:
    return any(_site_packages_dir().glob("ggml-cuda*.dll"))


def _cuda_backend_label() -> str:
    if is_cuda_available():
        return "CUDA (GPU)"
    if any(_site_packages_dir().glob("ggml-cpu*.dll")):
        return "CPU"
    return "未知"


def resolve_inference_backend(config: AppConfig) -> tuple[bool, str]:
    """根据配置解析是否启用 GPU，返回 (use_gpu, 日志显示标签)。"""
    pref = (config.inference_device or "auto").strip().lower()
    cuda_ok = is_cuda_available()

    if pref == "cpu":
        return False, "CPU"
    if pref == "gpu":
        if cuda_ok:
            return True, "CUDA (GPU)"
        return False, "CPU（未检测到 CUDA 版 pywhispercpp，已回退）"
    if cuda_ok:
        return True, "CUDA (GPU)"
    return False, "CPU"


def clear_model_cache() -> None:
    global _last_language_mode, _last_inference_device
    _model_cache.clear()
    _last_language_mode = None
    _last_inference_device = None


def _model_cache_key(config: AppConfig, use_gpu: bool) -> str:
    model_path = config.validate_model()
    return f"{model_path.resolve()}|gpu={use_gpu}|threads={config.resolved_n_threads()}"


def _setup_windows_dll_paths() -> None:
    if sys.platform != "win32":
        return
    import os
    import site

    candidates: list[Path] = [_site_packages_dir()]
    for entry in site.getsitepackages() + [site.getusersitepackages()]:
        root = Path(entry)
        pkg = root / "pywhispercpp"
        if pkg.is_dir():
            candidates.append(pkg)

    for cuda_root in (
        Path(r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA"),
        Path(os.environ.get("CUDA_PATH", "")),
    ):
        if cuda_root.is_dir():
            versions = sorted(cuda_root.glob("v*/bin"), reverse=True)
            candidates.extend(versions[:1])

    seen: set[str] = set()
    for path in candidates:
        if not path.is_dir():
            continue
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        try:
            os.add_dll_directory(key)
        except (OSError, AttributeError):
            pass


def get_whisper_model(config: AppConfig, on_log: ProgressCallback | None = None):
    global _last_language_mode, _last_inference_device
    device_pref = (config.inference_device or "auto").strip().lower()
    if _last_language_mode is not None and _last_language_mode != config.language:
        clear_model_cache()
        if on_log:
            on_log("语种模式已变更，重新加载模型...")
    if _last_inference_device is not None and _last_inference_device != device_pref:
        clear_model_cache()
        if on_log:
            on_log("推理设备已变更，重新加载模型...")
    _last_language_mode = config.language
    _last_inference_device = device_pref

    _setup_windows_dll_paths()
    from pywhispercpp.model import Model

    use_gpu, backend_label = resolve_inference_backend(config)
    key = _model_cache_key(config, use_gpu)
    if key in _model_cache:
        return _model_cache[key]

    model_path = config.validate_model()
    if on_log:
        on_log(f"正在加载模型: {model_path.name}")
        on_log(f"推理设备: {backend_label}")
        if device_pref == "gpu" and not use_gpu:
            on_log("提示: 请运行 subtitle_app/安装CUDA推理.bat 编译 CUDA 版 pywhispercpp。")
        on_log(f"推理线程数: {config.resolved_n_threads()}")
        installed = _cuda_backend_label()
        if installed != backend_label.split("（")[0]:
            on_log(f"已安装后端库: {installed}")

    context_params = {"use_gpu": use_gpu}
    if use_gpu:
        context_params["flash_attn"] = False

    model = Model(
        str(model_path),
        n_threads=config.resolved_n_threads(),
        context_params=context_params,
    )
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


def _segments_from_raw(raw_segments, time_offset_sec: float = 0.0) -> list[SubtitleSegment]:
    offset_ms = round(time_offset_sec * 1000)
    segments: list[SubtitleSegment] = []
    for seg in raw_segments:
        text = (seg.text or "").strip()
        if not text:
            continue
        segments.append(
            SubtitleSegment(
                0,
                (seg.t0 * 10 + offset_ms) / 1000.0,
                (seg.t1 * 10 + offset_ms) / 1000.0,
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
        time_offset_sec = region.start_sec
        merged.extend(_segments_from_raw(raw_segments, time_offset_sec))

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
    time_offset_sec = region.start_sec
    return _segments_from_raw(raw_segments, time_offset_sec)


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
