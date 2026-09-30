"""一部视频的播放记录。存在配套文件夹里，时间用 Unix 毫秒。"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from core.media_bundle import bundle_dir, ensure_bundle_dir

PLAYBACK_FILE = "playback.json"
MIN_SESSION_MS = 1000
SHANGHAI = "Asia/Shanghai"


@dataclass
class PlaybackSession:
    id: str
    started_at: int
    ended_at: int | None = None

    def completed(self) -> bool:
        return self.ended_at is not None and self.ended_at > self.started_at


def playback_log_path(media_path: Path) -> Path:
    return bundle_dir(media_path) / PLAYBACK_FILE


def now_ms() -> int:
    return int(datetime.now().timestamp() * 1000)


def load_playback_sessions(media_path: Path) -> list[PlaybackSession]:
    path = playback_log_path(media_path)
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = payload.get("sessions") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    sessions: list[PlaybackSession] = []
    for item in rows:
        parsed = _parse_session(item)
        if parsed is not None:
            sessions.append(parsed)
    return sessions


def save_playback_sessions(media_path: Path, sessions: list[PlaybackSession]) -> None:
    folder = ensure_bundle_dir(media_path)
    path = folder / PLAYBACK_FILE
    payload = {
        "version": 1,
        "sessions": [
            {
                "id": item.id,
                "started_at": int(item.started_at),
                **({"ended_at": int(item.ended_at)} if item.ended_at is not None else {}),
            }
            for item in sessions
        ],
    }
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def begin_playback_session(media_path: Path, started_at: int | None = None) -> PlaybackSession:
    sessions = load_playback_sessions(media_path)
    kept: list[PlaybackSession] = []
    for item in sessions:
        if item.ended_at is None:
            continue
        if item.completed() and item.ended_at - item.started_at >= MIN_SESSION_MS:
            kept.append(item)
    session = PlaybackSession(id=uuid.uuid4().hex, started_at=started_at if started_at is not None else now_ms())
    kept.append(session)
    save_playback_sessions(media_path, kept)
    return session


def end_playback_session(media_path: Path, ended_at: int | None = None) -> PlaybackSession | None:
    sessions = load_playback_sessions(media_path)
    open_index = next((index for index, item in enumerate(sessions) if item.ended_at is None), None)
    if open_index is None:
        return None
    session = sessions[open_index]
    finished = ended_at if ended_at is not None else now_ms()
    if finished <= session.started_at or finished - session.started_at < MIN_SESSION_MS:
        del sessions[open_index]
        save_playback_sessions(media_path, sessions)
        return None
    session.ended_at = finished
    sessions[open_index] = session
    save_playback_sessions(media_path, sessions)
    return session


def completed_sessions(sessions: list[PlaybackSession]) -> list[PlaybackSession]:
    return [
        item
        for item in sessions
        if item.completed() and item.ended_at is not None and item.ended_at - item.started_at >= MIN_SESSION_MS
    ]


def merge_playback_sessions(
    local: list[PlaybackSession],
    remote: list[dict],
) -> list[PlaybackSession]:
    """远程记录按编号补进来。还没结束的本地一段保持不动。"""
    by_id = {item.id: item for item in local}
    for item in remote:
        parsed = _parse_session(item)
        if parsed is None or not parsed.completed():
            continue
        current = by_id.get(parsed.id)
        if current is None or current.ended_at is None:
            by_id[parsed.id] = parsed
    merged = list(by_id.values())
    merged.sort(key=lambda item: (item.started_at, item.id))
    return merged


def total_study_ms(sessions: list[PlaybackSession]) -> int:
    total = 0
    for item in completed_sessions(sessions):
        if item.ended_at is not None:
            total += item.ended_at - item.started_at
    return total


def format_duration_ms(duration_ms: int) -> str:
    total = max(0, int(duration_ms) // 1000)
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours:
        return f"{hours}小时{minutes}分{seconds}秒"
    if minutes:
        return f"{minutes}分{seconds}秒"
    return f"{seconds}秒"


def format_timestamp_ms(timestamp_ms: int, zone_name: str | None = None) -> str:
    try:
        zone = ZoneInfo(zone_name or SHANGHAI)
    except Exception:
        zone = ZoneInfo(SHANGHAI)
    moment = datetime.fromtimestamp(int(timestamp_ms) / 1000, tz=zone)
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def apply_remote_playback(media_path: Path, remote: list[dict]) -> list[PlaybackSession]:
    merged = merge_playback_sessions(load_playback_sessions(media_path), remote)
    save_playback_sessions(media_path, merged)
    return merged


def _parse_session(item: object) -> PlaybackSession | None:
    if not isinstance(item, dict):
        return None
    session_id = str(item.get("id") or "").strip().lower()
    if len(session_id) < 16 or any(ch not in "0123456789abcdef" for ch in session_id):
        return None
    try:
        started = int(item.get("started_at"))
    except (TypeError, ValueError):
        return None
    ended_raw = item.get("ended_at")
    ended: int | None
    if ended_raw is None:
        ended = None
    else:
        try:
            ended = int(ended_raw)
        except (TypeError, ValueError):
            return None
    return PlaybackSession(id=session_id, started_at=started, ended_at=ended)
