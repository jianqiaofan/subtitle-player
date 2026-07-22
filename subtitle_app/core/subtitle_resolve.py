from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

from core.config import LANGUAGE_FILENAME_LABELS, LIVE_SYNC_FILENAME_LABEL, load_config
from core.subtitle import load_subtitle_file
from core.subtitle_loader import find_subtitles_for_media
from core.sync_subtitle import SyncSubtitleStatus, assess_sync_subtitle

LIVE_DEFAULT_LANGUAGE = "mixed"
LIVE_DEFAULT_LABEL = LANGUAGE_FILENAME_LABELS[LIVE_DEFAULT_LANGUAGE]


class SubtitleAction(Enum):
    USE_EXISTING = auto()
    LIVE_TRANSCRIBE = auto()
    RESUME_LIVE_TRANSCRIBE = auto()
    BATCH_TRANSCRIBE = auto()
    NONE = auto()


@dataclass(frozen=True)
class SubtitleChoice:
    action: SubtitleAction
    subtitle_path: Path | None = None
    label: str = ""


def find_subtitle_by_label(
    media_path: Path,
    label: str,
) -> Path | None:
    for path, item_label in find_subtitles_for_media(media_path):
        if item_label == label:
            return path
    return None


def list_available_subtitles(media_path: Path) -> list[tuple[Path, str]]:
    return find_subtitles_for_media(media_path)


def find_valid_subtitles(media_path: Path) -> list[tuple[Path, str]]:
    """返回可成功加载且含字幕内容的文件。"""
    valid: list[tuple[Path, str]] = []
    for path, label in find_subtitles_for_media(media_path):
        try:
            segments = load_subtitle_file(path)
            if segments:
                valid.append((path, label))
        except (OSError, ValueError):
            continue
    return valid


def auto_load_choice(media_path: Path) -> SubtitleChoice | None:
    """打开媒体时：完整/未完成同步字幕与其它有效字幕均仅加载，不自动开始边播边转。"""
    sync = assess_sync_subtitle(media_path, load_config())

    if sync.status == SyncSubtitleStatus.COMPLETE and sync.subtitle_path:
        return SubtitleChoice(
            SubtitleAction.USE_EXISTING,
            subtitle_path=sync.subtitle_path,
            label=LIVE_SYNC_FILENAME_LABEL,
        )

    if sync.status == SyncSubtitleStatus.INCOMPLETE and sync.subtitle_path:
        return SubtitleChoice(
            SubtitleAction.USE_EXISTING,
            subtitle_path=sync.subtitle_path,
            label=LIVE_SYNC_FILENAME_LABEL,
        )

    valid = find_valid_subtitles(media_path)
    if not valid:
        return None

    for path, label in valid:
        if label == LIVE_SYNC_FILENAME_LABEL:
            continue
        return SubtitleChoice(
            SubtitleAction.USE_EXISTING,
            subtitle_path=path,
            label=label,
        )

    path, label = valid[0]
    return SubtitleChoice(
        SubtitleAction.USE_EXISTING,
        subtitle_path=path,
        label=label,
    )


def default_subtitle_choice(media_path: Path) -> SubtitleChoice:
    """无交互时的默认策略：有可用字幕则加载，否则不做任何转写。"""
    auto = auto_load_choice(media_path)
    if auto is not None:
        return auto
    return SubtitleChoice(SubtitleAction.NONE)


def has_preferred_subtitle(media_path: Path, label: str = LIVE_DEFAULT_LABEL) -> bool:
    return find_subtitle_by_label(media_path, label) is not None
