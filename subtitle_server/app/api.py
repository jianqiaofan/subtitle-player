"""云同步 HTTP 接口。

POST /api/auth/register
POST /api/auth/login
GET  /api/auth/me
PUT  /api/subtitles          同一种语言覆盖，其它语言保留
PUT  /api/tags               按标签操作时间合并，未出现的标签保持原样
GET  /api/sync?video_hash=  一次取回这部视频的各语言字幕和标签
GET  /api/shares?video_hash= 其他用户分享的同一视频字幕；带 username 时返回正文
GET  /api/library/subtitles  本人字幕列表
GET  /api/library/tags       本人标签列表
"""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import User
from app.names import (
    InvalidName,
    validate_password,
    validate_subtitle_name,
    validate_username,
    validate_video_hash,
    validate_video_stem,
)
from app.catalog import (
    SUBTITLE_LIMIT,
    Conflict,
    delete_stored_row,
    list_shared_subtitles,
    list_user_subtitles,
    list_user_tags,
    owned_subtitle,
    owned_tag,
    save_subtitle,
    save_tags,
    set_subtitle_shared,
    sync_bundle,
)
from app.security import AuthError, create_token, hash_password, read_user_id, verify_password
from app.storage import StorageError, absolute_path, load_document, read_bytes
from app.tagmerge import InvalidTagDocument, document_to_dict, parse_document
from app.timeutil import iso_z_seconds, utc_now_naive

router = APIRouter()


class Credentials(BaseModel):
    username: str
    password: str


class SubtitleUpload(BaseModel):
    video_hash: str
    video_stem: str
    subtitle_name: str
    content: str
    shared: bool = False


class TagUpload(BaseModel):
    video_hash: str
    video_stem: str
    subtitle_name: str
    document: dict


class ShareFlag(BaseModel):
    shared: bool


def get_db(request: Request):
    db = request.app.state.session_factory()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="请先登录", headers={"WWW-Authenticate": "Bearer"})
    try:
        user_id = read_user_id(authorization[7:].strip(), request.app.state.settings.jwt_secret)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail="登录已失效，请重新登录") from exc
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="登录已失效，请重新登录")
    return user


def _token_body(user: User, settings: Settings) -> dict:
    return {
        "access_token": create_token(user.id, settings.jwt_secret, settings.token_ttl_seconds),
        "token_type": "bearer",
        "username": user.username,
    }


def _bad_name(exc: InvalidName) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/api/health")
def health(db: Session = Depends(get_db)) -> dict:
    db.execute(select(User.id).limit(1))
    return {"status": "ok"}


@router.post("/api/auth/register", status_code=201)
def register(
    body: Credentials,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    try:
        username = validate_username(body.username)
        password = validate_password(body.password)
    except InvalidName as exc:
        raise _bad_name(exc) from exc
    existing = db.scalar(select(User).where(User.username == username))
    if existing is not None:
        raise HTTPException(status_code=409, detail="用户名已存在")
    user = User(
        username=username,
        password_hash=hash_password(password, settings.password_rounds),
        created_at=utc_now_naive(),
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="用户名已存在") from exc
    return _token_body(user, settings)


@router.post("/api/auth/login")
def login(
    body: Credentials,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    username = body.username.strip() if isinstance(body.username, str) else ""
    password = body.password if isinstance(body.password, str) else ""
    user = db.scalar(select(User).where(User.username == username)) if username else None
    if user is None or len(password) > 72 or not verify_password(password, user.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码不正确")
    return _token_body(user, settings)


@router.get("/api/auth/me")
def me(user: User = Depends(get_current_user)) -> dict:
    return {"username": user.username}


@router.put("/api/subtitles")
def put_subtitle(
    body: SubtitleUpload,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    try:
        video_hash = validate_video_hash(body.video_hash)
        video_stem = validate_video_stem(body.video_stem)
        subtitle_name = validate_subtitle_name(body.subtitle_name, video_stem)
        row = save_subtitle(
            db,
            request.app.state.settings,
            user,
            video_hash,
            video_stem,
            subtitle_name,
            body.content,
            body.shared,
        )
    except InvalidName as exc:
        raise _bad_name(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Conflict as exc:
        raise HTTPException(status_code=409, detail="正在被另一台设备写入，请重试") from exc
    except StorageError as exc:
        raise HTTPException(status_code=500, detail="云端文件写入失败") from exc
    return {
        "video_hash": row.video_hash,
        "video_stem": row.video_stem,
        "subtitle_name": row.subtitle_name,
        "subtitle_suffix": row.subtitle_suffix,
        "content_hash": row.content_hash,
        "updated_at": iso_z_seconds(row.updated_at),
    }


@router.put("/api/tags")
def put_tags(
    body: TagUpload,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    try:
        video_hash = validate_video_hash(body.video_hash)
        video_stem = validate_video_stem(body.video_stem)
        subtitle_name = validate_subtitle_name(body.subtitle_name, video_stem)
        encoded = json.dumps(body.document, ensure_ascii=False).encode("utf-8")
        if len(encoded) > SUBTITLE_LIMIT:
            raise HTTPException(status_code=413, detail="标签文档超过 8MB")
        document = parse_document(body.document, subtitle_name)
        row, merged = save_tags(
            db, request.app.state.settings, user, video_hash, video_stem, subtitle_name, document
        )
    except InvalidName as exc:
        raise _bad_name(exc) from exc
    except InvalidTagDocument as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Conflict as exc:
        raise HTTPException(status_code=409, detail="正在被另一台设备写入，请重试") from exc
    except StorageError as exc:
        raise HTTPException(status_code=500, detail="云端标签文件写入失败") from exc
    return {
        "video_hash": row.video_hash,
        "video_stem": row.video_stem,
        "subtitle_name": row.subtitle_name,
        "subtitle_suffix": row.subtitle_suffix,
        "content_hash": row.content_hash,
        "updated_at": iso_z_seconds(row.updated_at),
        "document": document_to_dict(merged),
    }


def _share_payload(share: dict, *, include_content: bool) -> dict:
    subtitles = []
    for item in share["subtitles"]:
        payload = {
            "subtitle_name": item["subtitle_name"],
            "subtitle_suffix": item["subtitle_suffix"],
            "content_hash": item["content_hash"],
            "updated_at": iso_z_seconds(item["updated_at"]),
        }
        if include_content:
            payload["content"] = item.get("content") or ""
        subtitles.append(payload)
    return {
        "username": share["username"],
        "updated_at": iso_z_seconds(share["updated_at"]),
        "subtitles": subtitles,
    }


def _subtitle_brief(row) -> dict:
    return {
        "id": row.id,
        "video_hash": row.video_hash,
        "video_stem": row.video_stem,
        "subtitle_name": row.subtitle_name,
        "subtitle_suffix": row.subtitle_suffix,
        "shared": bool(row.shared),
        "updated_at": iso_z_seconds(row.updated_at),
    }


def _tag_brief(row) -> dict:
    return {
        "id": row.id,
        "video_hash": row.video_hash,
        "video_stem": row.video_stem,
        "subtitle_name": row.subtitle_name,
        "subtitle_suffix": row.subtitle_suffix,
        "updated_at": iso_z_seconds(row.updated_at),
    }


@router.get("/api/library/subtitles")
def library_subtitles(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    return {"subtitles": [_subtitle_brief(row) for row in list_user_subtitles(db, user)]}


@router.get("/api/library/subtitles/{item_id}")
def library_subtitle_content(
    item_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    row = owned_subtitle(db, user, item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="没有这条字幕")
    try:
        data = read_bytes(absolute_path(request.app.state.settings.data_dir, row.disk_path))
        content = data.decode("utf-8")
    except (StorageError, UnicodeError) as exc:
        raise HTTPException(status_code=500, detail="云端字幕文件无法读取") from exc
    payload = _subtitle_brief(row)
    payload["content"] = content
    return payload


@router.patch("/api/library/subtitles/{item_id}")
def library_subtitle_share(
    item_id: int,
    body: ShareFlag,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    row = owned_subtitle(db, user, item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="没有这条字幕")
    set_subtitle_shared(db, row, body.shared)
    return _subtitle_brief(row)


@router.delete("/api/library/subtitles/{item_id}")
def library_subtitle_delete(
    item_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    row = owned_subtitle(db, user, item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="没有这条字幕")
    try:
        delete_stored_row(db, request.app.state.settings, row)
    except StorageError as exc:
        raise HTTPException(status_code=500, detail="云端文件删除失败") from exc
    return {"deleted": item_id}


@router.get("/api/library/tags")
def library_tags(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    return {"tags": [_tag_brief(row) for row in list_user_tags(db, user)]}


@router.get("/api/library/tags/{item_id}")
def library_tag_content(
    item_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    row = owned_tag(db, user, item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="没有这份标签")
    try:
        document = load_document(absolute_path(request.app.state.settings.data_dir, row.disk_path), row.subtitle_name)
    except StorageError as exc:
        raise HTTPException(status_code=500, detail="云端标签文件无法读取") from exc
    payload = _tag_brief(row)
    payload["document"] = document_to_dict(document)
    return payload


@router.delete("/api/library/tags/{item_id}")
def library_tag_delete(
    item_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    row = owned_tag(db, user, item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="没有这份标签")
    try:
        delete_stored_row(db, request.app.state.settings, row)
    except StorageError as exc:
        raise HTTPException(status_code=500, detail="云端文件删除失败") from exc
    return {"deleted": item_id}


@router.get("/api/shares")
def read_shares(
    video_hash: str,
    request: Request,
    username: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    try:
        digest = validate_video_hash(video_hash)
        shares = list_shared_subtitles(db, request.app.state.settings, user, digest, username or None)
    except InvalidName as exc:
        raise _bad_name(exc) from exc
    except StorageError as exc:
        raise HTTPException(status_code=500, detail="云端文件读取失败") from exc
    if username and not shares:
        raise HTTPException(status_code=404, detail="没有可共享的字幕")
    include_content = bool(username)
    return {
        "video_hash": digest,
        "shares": [_share_payload(share, include_content=include_content) for share in shares],
    }


@router.get("/api/sync")
def read_sync(
    video_hash: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    try:
        digest = validate_video_hash(video_hash)
        bundle = sync_bundle(db, request.app.state.settings, user, digest)
    except InvalidName as exc:
        raise _bad_name(exc) from exc
    except StorageError as exc:
        raise HTTPException(status_code=500, detail="云端文件读取失败") from exc
    return {
        "video_hash": bundle["video_hash"],
        "subtitles": [
            {
                "subtitle_name": item["subtitle_name"],
                "subtitle_suffix": item["subtitle_suffix"],
                "content": item["content"],
                "content_hash": item["content_hash"],
                "updated_at": iso_z_seconds(item["updated_at"]),
            }
            for item in bundle["subtitles"]
        ],
        "tags": [
            {
                "subtitle_name": item["subtitle_name"],
                "subtitle_suffix": item["subtitle_suffix"],
                "content_hash": item["content_hash"],
                "updated_at": iso_z_seconds(item["updated_at"]),
                "document": document_to_dict(item["document"]),
            }
            for item in bundle["tags"]
        ],
    }
