from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

from core.audio import get_media_duration
from core.config import AppConfig, LIVE_SYNC_FILENAME_LABEL, load_config
from core.speech_split import SpeechRegion
from core.subtitle import SubtitleSegment, load_subtitle_file


class SyncSubtitleStatus(Enum):
    MISSING = auto()
    COMPLETE = auto()
    INCOMPLETE = auto()


@dataclass(frozen=True)
class SyncSubtitleAssessment:
    status: SyncSubtitleStatus
    subtitle_path: Path | None = None
    segments: tuple[SubtitleSegment, ...] = ()
    media_duration_sec: float = 0.0
    covered_until_sec: float = 0.0

# 片尾静音容忍：最后一条字幕可早于片尾这么久仍视为完整
TAIL_TOLERANCE_SEC = 60.0
# 或字幕时间轴覆盖片长比例达到此值视为完整
COVERAGE_RATIO_THRESHOLD = 0.92
# 判断语音分片是否已有字幕：重叠时长占分片比例
REGION_COVERAGE_RATIO = 0.35


def sync_subtitle_paths(media_path: Path, config: AppConfig | None = None) -> tuple[Path, Path]:
    cfg = config or load_config()
    output_path = cfg.build_live_output_path(media_path)
    partial_path = output_path.with_suffix(output_path.suffix + ".partial")
    return output_path, partial_path


def _load_segments_safe(path: Path) -> list[SubtitleSegment]:
    try:
        return load_subtitle_file(path)
    except (OSError, ValueError):
        return []


def _covered_until(segments: list[SubtitleSegment]) -> float:
    if not segments:
        return 0.0
    return max(seg.end for seg in segments)


def _is_complete_by_duration(
    covered_until: float,
    media_duration: float,
    partial_exists: bool,
) -> bool:
    if partial_exists:
        return False
    if media_duration <= 0:
        return covered_until > 0
    if covered_until >= media_duration - TAIL_TOLERANCE_SEC:
        return True
    if covered_until / media_duration >= COVERAGE_RATIO_THRESHOLD:
        return True
    return False


def assess_sync_subtitle(
    media_path: Path,
    config: AppConfig | None = None,
) -> SyncSubtitleAssessment:
    """评估边播边转生成的「同步」字幕是否完整。"""
    cfg = config or load_config()
    output_path, partial_path = sync_subtitle_paths(media_path, cfg)

    try:
        media_duration = get_media_duration(media_path)
    except RuntimeError:
        media_duration = 0.0

    partial_exists = partial_path.is_file()
    output_exists = output_path.is_file()

    load_path: Path | None = None
    segments: list[SubtitleSegment] = []

    if partial_exists:
        segments = _load_segments_safe(partial_path)
        load_path = partial_path if segments else None
    if output_exists:
        output_segments = _load_segments_safe(output_path)
        if len(output_segments) >= len(segments):
            segments = output_segments
            load_path = output_path

    if not segments or load_path is None:
        return SyncSubtitleAssessment(
            status=SyncSubtitleStatus.MISSING,
            media_duration_sec=media_duration,
        )

    covered = _covered_until(segments)
    complete = _is_complete_by_duration(covered, media_duration, partial_exists)

    return SyncSubtitleAssessment(
        status=SyncSubtitleStatus.COMPLETE if complete else SyncSubtitleStatus.INCOMPLETE,
        subtitle_path=load_path,
        segments=tuple(segments),
        media_duration_sec=media_duration,
        covered_until_sec=covered,
    )


def region_covered_by_segments(
    region: SpeechRegion,
    segments: list[SubtitleSegment],
) -> bool:
    if not segments:
        return False
    overlap = 0.0
    for seg in segments:
        start = max(seg.start, region.start_sec)
        end = min(seg.end, region.end_sec)
        if end > start:
            overlap += end - start
    if region.duration_sec <= 0:
        return False
    return overlap >= region.duration_sec * REGION_COVERAGE_RATIO


def completed_region_indices(
    regions: list[SpeechRegion],
    segments: list[SubtitleSegment],
) -> set[int]:
    if not segments:
        return set()
    return {
        index
        for index, region in enumerate(regions)
        if region_covered_by_segments(region, segments)
    }
