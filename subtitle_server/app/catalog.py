from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import PlaybackSession, Subtitle, TagDocumentRow, User
from app.names import subtitle_suffix
from app.storage import (
    StorageError,
    absolute_path,
    atomic_write,
    dump_document,
    load_document,
    read_bytes,
    relative_path,
    sha256_hex,
    touched_at,
)
from app.tagmerge import TagDocument, empty_document, merge_documents

SUBTITLE_LIMIT = 8 * 1024 * 1024


class Conflict(Exception):
    pass


def _locked(db: Session, model, user_id: int, video_hash: str, suffix: str):
    statement = select(model).where(
        model.user_id == user_id,
        model.video_hash == video_hash,
        model.subtitle_suffix == suffix,
    )
    if db.get_bind().dialect.name == "mysql":
        statement = statement.with_for_update()
    return db.scalar(statement)


def save_subtitle(
    db: Session,
    settings: Settings,
    user: User,
    video_hash: str,
    video_stem: str,
    subtitle_name: str,
    content: str,
    shared: bool,
) -> Subtitle:
    if content == "" or "\x00" in content:
        raise ValueError("字幕内容无效")
    data = content.encode("utf-8")
    if len(data) > SUBTITLE_LIMIT:
        raise ValueError("字幕文件超过 8MB")
    suffix = subtitle_suffix(subtitle_name, video_stem)
    for _attempt in range(2):
        row = _locked(db, Subtitle, user.id, video_hash, suffix)
        digest = sha256_hex(data)
        now = touched_at()
        if row is None:
            relative = relative_path(user.id, "subtitles", video_hash, suffix)
            atomic_write(absolute_path(settings.data_dir, relative), data)
            row = Subtitle(
                user_id=user.id,
                video_hash=video_hash,
                video_stem=video_stem,
                subtitle_name=subtitle_name,
                subtitle_suffix=suffix,
                content_hash=digest,
                shared=bool(shared),
                updated_at=now,
                disk_path=relative,
            )
            db.add(row)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                continue
            return row
        atomic_write(absolute_path(settings.data_dir, row.disk_path), data)
        row.video_stem = video_stem
        row.subtitle_name = subtitle_name
        row.content_hash = digest
        row.shared = bool(shared)
        row.updated_at = now
        db.commit()
        return row
    raise Conflict


def save_tags(
    db: Session,
    settings: Settings,
    user: User,
    video_hash: str,
    video_stem: str,
    subtitle_name: str,
    incoming: TagDocument,
) -> tuple[TagDocumentRow, TagDocument]:
    suffix = subtitle_suffix(subtitle_name, video_stem)
    for _attempt in range(2):
        row = _locked(db, TagDocumentRow, user.id, video_hash, suffix)
        now = touched_at()
        if row is None:
            relative = relative_path(user.id, "tags", video_hash, suffix)
            path = absolute_path(settings.data_dir, relative)
            stored = load_document(path, subtitle_name) if path.is_file() else empty_document(subtitle_name)
            merged = merge_documents(stored, incoming)
            data = dump_document(merged)
            atomic_write(path, data)
            row = TagDocumentRow(
                user_id=user.id,
                video_hash=video_hash,
                video_stem=video_stem,
                subtitle_name=subtitle_name,
                subtitle_suffix=suffix,
                content_hash=sha256_hex(data),
                updated_at=now,
                disk_path=relative,
            )
            db.add(row)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                continue
            return row, merged
        path = absolute_path(settings.data_dir, row.disk_path)
        stored = load_document(path, row.subtitle_name) if path.is_file() else empty_document(row.subtitle_name)
        merged = merge_documents(stored, incoming)
        data = dump_document(merged)
        atomic_write(path, data)
        row.video_stem = video_stem
        row.subtitle_name = subtitle_name
        row.content_hash = sha256_hex(data)
        row.updated_at = now
        db.commit()
        return row, merged
    raise Conflict


def sync_bundle(db: Session, settings: Settings, user: User, video_hash: str) -> dict:
    subtitles = db.scalars(
        select(Subtitle)
        .where(Subtitle.user_id == user.id, Subtitle.video_hash == video_hash)
        .order_by(Subtitle.subtitle_suffix)
    ).all()
    tags = db.scalars(
        select(TagDocumentRow)
        .where(TagDocumentRow.user_id == user.id, TagDocumentRow.video_hash == video_hash)
        .order_by(TagDocumentRow.subtitle_suffix)
    ).all()
    subtitle_items = []
    for row in subtitles:
        data = read_bytes(absolute_path(settings.data_dir, row.disk_path))
        try:
            content = data.decode("utf-8")
        except UnicodeError as exc:
            raise StorageError("云端字幕文件无法读取") from exc
        subtitle_items.append(
            {
                "subtitle_name": row.subtitle_name,
                "subtitle_suffix": row.subtitle_suffix,
                "content": content,
                "content_hash": row.content_hash,
                "updated_at": row.updated_at,
            }
        )
    tag_items = []
    for row in tags:
        document = load_document(absolute_path(settings.data_dir, row.disk_path), row.subtitle_name)
        tag_items.append(
            {
                "subtitle_name": row.subtitle_name,
                "subtitle_suffix": row.subtitle_suffix,
                "content_hash": row.content_hash,
                "updated_at": row.updated_at,
                "document": document,
            }
        )
    return {"video_hash": video_hash, "subtitles": subtitle_items, "tags": tag_items}


def list_shared_subtitles(
    db: Session,
    settings: Settings,
    viewer: User,
    video_hash: str,
    username: str | None = None,
) -> list[dict]:
    """其他用户愿意公开的同一视频字幕。指定用户名时才带上正文。"""
    statement = (
        select(Subtitle, User)
        .join(User, User.id == Subtitle.user_id)
        .where(
            Subtitle.video_hash == video_hash,
            Subtitle.shared.is_(True),
            Subtitle.user_id != viewer.id,
        )
        .order_by(User.username, Subtitle.subtitle_suffix)
    )
    if username:
        statement = statement.where(User.username == username)
    grouped: dict[str, dict] = {}
    for row, owner in db.execute(statement).all():
        bucket = grouped.get(owner.username)
        if bucket is None:
            bucket = {"username": owner.username, "updated_at": row.updated_at, "subtitles": []}
            grouped[owner.username] = bucket
        elif row.updated_at > bucket["updated_at"]:
            bucket["updated_at"] = row.updated_at
        item = {
            "subtitle_name": row.subtitle_name,
            "subtitle_suffix": row.subtitle_suffix,
            "content_hash": row.content_hash,
            "updated_at": row.updated_at,
        }
        if username:
            data = read_bytes(absolute_path(settings.data_dir, row.disk_path))
            try:
                item["content"] = data.decode("utf-8")
            except UnicodeError as exc:
                raise StorageError("云端字幕文件无法读取") from exc
        bucket["subtitles"].append(item)
    shares = list(grouped.values())
    shares.sort(key=lambda item: item["updated_at"], reverse=True)
    return shares


def list_user_subtitles(db: Session, user: User) -> list[Subtitle]:
    return list(
        db.scalars(
            select(Subtitle)
            .where(Subtitle.user_id == user.id)
            .order_by(Subtitle.updated_at.desc(), Subtitle.subtitle_name)
        ).all()
    )


def list_user_tags(db: Session, user: User) -> list[TagDocumentRow]:
    return list(
        db.scalars(
            select(TagDocumentRow)
            .where(TagDocumentRow.user_id == user.id)
            .order_by(TagDocumentRow.updated_at.desc(), TagDocumentRow.subtitle_name)
        ).all()
    )


def owned_subtitle(db: Session, user: User, item_id: int) -> Subtitle | None:
    row = db.get(Subtitle, item_id)
    if row is None or row.user_id != user.id:
        return None
    return row


def owned_tag(db: Session, user: User, item_id: int) -> TagDocumentRow | None:
    row = db.get(TagDocumentRow, item_id)
    if row is None or row.user_id != user.id:
        return None
    return row


def set_subtitle_shared(db: Session, row: Subtitle, shared: bool) -> Subtitle:
    row.shared = bool(shared)
    row.updated_at = touched_at()
    db.commit()
    return row


def delete_stored_row(db: Session, settings: Settings, row: Subtitle | TagDocumentRow) -> None:
    path = absolute_path(settings.data_dir, row.disk_path)
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        raise StorageError("无法删除云端文件") from exc
    db.delete(row)
    db.commit()


_MIN_PLAYBACK_MS = 1_577_836_800_000
_MAX_PLAYBACK_MS = 4_102_444_800_000
_MAX_PLAYBACK_SPAN_MS = 24 * 60 * 60 * 1000


class InvalidPlayback(ValueError):
    pass


def _session_payload(row: PlaybackSession) -> dict:
    return {
        "id": row.session_id,
        "started_at": int(row.started_at_ms),
        "ended_at": int(row.ended_at_ms),
    }


def list_playback_sessions(db: Session, user: User, video_hash: str) -> list[dict]:
    rows = db.scalars(
        select(PlaybackSession)
        .where(PlaybackSession.user_id == user.id, PlaybackSession.video_hash == video_hash)
        .order_by(PlaybackSession.started_at_ms.desc(), PlaybackSession.id.desc())
    ).all()
    return [_session_payload(row) for row in rows]


def merge_playback_sessions(
    db: Session,
    user: User,
    video_hash: str,
    video_stem: str,
    sessions: list[dict],
) -> list[dict]:
    """只追加当前用户还没有的播放段。已有记录保持原样，其他用户的记录不可见。"""
    if len(sessions) > 500:
        raise InvalidPlayback("一次最多同步 500 条播放记录")
    cleaned: list[tuple[str, int, int]] = []
    seen: set[str] = set()
    for item in sessions:
        if not isinstance(item, dict):
            raise InvalidPlayback("播放记录无效")
        session_id = str(item.get("id") or "").strip().lower()
        if len(session_id) < 16 or len(session_id) > 64 or any(ch not in "0123456789abcdef" for ch in session_id):
            raise InvalidPlayback("播放记录编号无效")
        try:
            started = int(item.get("started_at"))
            ended = int(item.get("ended_at"))
        except (TypeError, ValueError) as exc:
            raise InvalidPlayback("播放时间无效") from exc
        if not (_MIN_PLAYBACK_MS <= started <= _MAX_PLAYBACK_MS and _MIN_PLAYBACK_MS <= ended <= _MAX_PLAYBACK_MS):
            raise InvalidPlayback("播放时间无效")
        if ended <= started or ended - started > _MAX_PLAYBACK_SPAN_MS:
            raise InvalidPlayback("播放时间无效")
        if session_id in seen:
            continue
        seen.add(session_id)
        cleaned.append((session_id, started, ended))

    for _attempt in range(2):
        existing_rows = db.scalars(
            select(PlaybackSession).where(PlaybackSession.user_id == user.id)
        ).all()
        owned = {row.session_id: row for row in existing_rows}
        try:
            for session_id, started, ended in cleaned:
                current = owned.get(session_id)
                if current is not None:
                    continue
                db.add(
                    PlaybackSession(
                        user_id=user.id,
                        session_id=session_id,
                        video_hash=video_hash,
                        video_stem=video_stem,
                        started_at_ms=started,
                        ended_at_ms=ended,
                    )
                )
            db.commit()
            break
        except IntegrityError:
            db.rollback()
            if _attempt == 1:
                raise Conflict from None
    return list_playback_sessions(db, user, video_hash)
