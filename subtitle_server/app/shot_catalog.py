"""截图说明存在数据库，压缩图存在该用户自己的磁盘目录。"""

from __future__ import annotations

import json
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import ScreenshotShot, User
from app.storage import StorageError, absolute_path, atomic_write, sha256_hex

_SHOT_ID = re.compile(r"[0-9a-f]{12}")
_MAX_SHOTS = 500
_MAX_NOTES = 100
_MAX_NOTES_BYTES = 200_000
_MAX_TITLE = 4000
_MAX_TIME_MS = 4_102_444_800_000
IMAGE_LIMIT = 2 * 1024 * 1024


class ScreenshotRejected(ValueError):
    pass


def sync_screenshot_document(
    db: Session,
    settings: Settings,
    user: User,
    video_hash: str,
    video_stem: str,
    shots: list[dict],
    baseline_ids: list[str],
) -> list[dict]:
    incoming = _validate_shots(shots)
    baseline = _validate_ids(baseline_ids)
    rows = _locked_rows(db, user.id, video_hash)
    by_id = {row.shot_id: row for row in rows}
    removed: list[str] = []

    for shot_id, shot in incoming.items():
        row = by_id.get(shot_id)
        if row is None:
            row = ScreenshotShot(
                user_id=user.id,
                video_hash=video_hash,
                shot_id=shot_id,
                video_stem=video_stem,
                title=shot["title"],
                time_seconds=shot["time"],
                frame_index=shot["frame"],
                created_at_ms=shot["created_at"],
                updated_at_ms=shot["updated_at"],
                notes_json=shot["notes_json"],
                image_hash="",
                disk_path="",
            )
            db.add(row)
            by_id[shot_id] = row
            continue
        if shot["updated_at"] > row.updated_at_ms:
            row.video_stem = video_stem
            row.title = shot["title"]
            row.time_seconds = shot["time"]
            row.frame_index = shot["frame"]
            row.updated_at_ms = shot["updated_at"]
            row.notes_json = shot["notes_json"]

    for shot_id, row in list(by_id.items()):
        if shot_id in incoming or shot_id not in baseline:
            continue
        removed.append(row.disk_path)
        db.delete(row)
        del by_id[shot_id]

    db.commit()
    for relative in removed:
        _remove_file(settings, relative)
    kept = [row for row in by_id.values()]
    _write_manifest(settings, user.id, video_hash, kept)
    return [_shot_payload(row) for row in _sorted_rows(kept)]


def list_screenshot_document(db: Session, user: User, video_hash: str) -> list[dict]:
    rows = db.scalars(
        select(ScreenshotShot).where(
            ScreenshotShot.user_id == user.id,
            ScreenshotShot.video_hash == video_hash,
        )
    ).all()
    return [_shot_payload(row) for row in _sorted_rows(rows)]


def save_screenshot_image(
    db: Session,
    settings: Settings,
    user: User,
    video_hash: str,
    shot_id: str,
    data: bytes,
) -> dict:
    if not _is_webp(data) or len(data) > IMAGE_LIMIT:
        raise ScreenshotRejected("截图需要是 2MB 以内的 WebP")
    row = _one_row(db, user.id, video_hash, shot_id)
    if row is None:
        raise ScreenshotRejected("请先同步截图说明")
    if row.disk_path and row.image_hash:
        return _shot_payload(row)
    relative = f"users/{user.id}/screenshots/{video_hash}/{shot_id}.webp"
    atomic_write(absolute_path(settings.data_dir, relative), data)
    row.disk_path = relative
    row.image_hash = sha256_hex(data)
    db.commit()
    _write_manifest(settings, user.id, video_hash, _rows_for_manifest(db, user.id, video_hash))
    return _shot_payload(row)


def read_screenshot_image(
    db: Session,
    settings: Settings,
    user: User,
    video_hash: str,
    shot_id: str,
) -> bytes:
    row = _one_row(db, user.id, video_hash, shot_id)
    if row is None or not row.disk_path:
        raise ScreenshotRejected("没有这张截图")
    from app.storage import read_bytes

    return read_bytes(absolute_path(settings.data_dir, row.disk_path))


def _validate_shots(shots: list[dict]) -> dict[str, dict]:
    if len(shots) > _MAX_SHOTS:
        raise ScreenshotRejected("截图数量过多")
    incoming: dict[str, dict] = {}
    for item in shots:
        shot_id = _shot_id(item.get("id"))
        if not shot_id or shot_id in incoming:
            raise ScreenshotRejected("截图编号无效")
        try:
            moment = float(item.get("time"))
            created_at = int(item.get("created_at"))
            updated_at = int(item.get("updated_at"))
        except (TypeError, ValueError) as exc:
            raise ScreenshotRejected("截图说明无效") from exc
        frame = item.get("frame")
        parsed_frame: int | None
        if frame is None:
            parsed_frame = None
        else:
            try:
                parsed_frame = int(frame)
            except (TypeError, ValueError) as exc:
                raise ScreenshotRejected("截图说明无效") from exc
            if parsed_frame < 0:
                raise ScreenshotRejected("截图说明无效")
        if moment < 0 or created_at < 0 or updated_at < 0 or updated_at > _MAX_TIME_MS:
            raise ScreenshotRejected("截图说明无效")
        title = str(item.get("title") or "")
        if len(title) > _MAX_TITLE:
            raise ScreenshotRejected("截图标题过长")
        notes = item.get("notes")
        if not isinstance(notes, list) or len(notes) > _MAX_NOTES:
            raise ScreenshotRejected("截图笔记无效")
        if any(not isinstance(note, dict) for note in notes):
            raise ScreenshotRejected("截图笔记无效")
        encoded = json.dumps(notes, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > _MAX_NOTES_BYTES:
            raise ScreenshotRejected("截图笔记过长")
        incoming[shot_id] = {
            "title": title,
            "time": moment,
            "frame": parsed_frame,
            "created_at": created_at,
            "updated_at": updated_at,
            "notes_json": encoded,
        }
    return incoming


def _validate_ids(values: list[str]) -> set[str]:
    if len(values) > _MAX_SHOTS:
        raise ScreenshotRejected("截图数量过多")
    cleaned: set[str] = set()
    for value in values:
        shot_id = _shot_id(value)
        if not shot_id:
            raise ScreenshotRejected("截图编号无效")
        cleaned.add(shot_id)
    return cleaned


def _shot_id(value: object) -> str:
    if not isinstance(value, str):
        return ""
    cleaned = value.strip().lower()
    if _SHOT_ID.fullmatch(cleaned) is None:
        return ""
    return cleaned


def _locked_rows(db: Session, user_id: int, video_hash: str) -> list[ScreenshotShot]:
    statement = select(ScreenshotShot).where(
        ScreenshotShot.user_id == user_id,
        ScreenshotShot.video_hash == video_hash,
    )
    if db.get_bind().dialect.name == "mysql":
        statement = statement.with_for_update()
    return list(db.scalars(statement).all())


def _one_row(db: Session, user_id: int, video_hash: str, shot_id: str) -> ScreenshotShot | None:
    cleaned = _shot_id(shot_id)
    if not cleaned:
        raise ScreenshotRejected("截图编号无效")
    return db.scalar(
        select(ScreenshotShot).where(
            ScreenshotShot.user_id == user_id,
            ScreenshotShot.video_hash == video_hash,
            ScreenshotShot.shot_id == cleaned,
        )
    )


def _rows_for_manifest(db: Session, user_id: int, video_hash: str) -> list[ScreenshotShot]:
    return list(
        db.scalars(
            select(ScreenshotShot).where(
                ScreenshotShot.user_id == user_id,
                ScreenshotShot.video_hash == video_hash,
            )
        ).all()
    )


def _sorted_rows(rows: list[ScreenshotShot]) -> list[ScreenshotShot]:
    return sorted(rows, key=lambda row: (round(row.time_seconds * 1000), row.created_at_ms, row.shot_id))


def _shot_payload(row: ScreenshotShot) -> dict:
    try:
        notes = json.loads(row.notes_json)
    except json.JSONDecodeError:
        notes = []
    if not isinstance(notes, list):
        notes = []
    return {
        "id": row.shot_id,
        "title": row.title,
        "time": row.time_seconds,
        "frame": row.frame_index,
        "created_at": row.created_at_ms,
        "updated_at": row.updated_at_ms,
        "notes": notes,
        "has_image": bool(row.disk_path and row.image_hash),
        "image_hash": row.image_hash,
    }


def _write_manifest(settings: Settings, user_id: int, video_hash: str, rows: list[ScreenshotShot]) -> None:
    relative = f"users/{user_id}/screenshots/{video_hash}/screenshots.json"
    payload = {
        "version": 1,
        "screenshots": [
            {
                "id": row.shot_id,
                "title": row.title,
                "time": row.time_seconds,
                "frame": row.frame_index,
                "image": f"{row.shot_id}.webp" if row.disk_path else "",
                "created_at": row.created_at_ms,
                "updated_at": row.updated_at_ms,
                "notes": json.loads(row.notes_json) if row.notes_json else [],
            }
            for row in _sorted_rows(rows)
        ],
    }
    if not payload["screenshots"]:
        _remove_file(settings, relative)
        return
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    atomic_write(absolute_path(settings.data_dir, relative), text.encode("utf-8"))


def _remove_file(settings: Settings, relative: str) -> None:
    if not relative:
        return
    try:
        path = absolute_path(settings.data_dir, relative)
    except StorageError:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _is_webp(data: bytes) -> bool:
    return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
