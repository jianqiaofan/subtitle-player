from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path

from app.tagmerge import InvalidTagDocument, TagDocument, document_to_dict, parse_document
from app.timeutil import utc_now_naive


class StorageError(Exception):
    pass


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def relative_path(user_id: int, kind: str, video_hash: str, suffix: str) -> str:
    filename = "subtitle" + suffix
    if "/" in filename or "\\" in filename or ".." in Path(filename).parts:
        raise StorageError("文件路径越界")
    return f"users/{user_id}/{kind}/{video_hash}/{filename}"


def absolute_path(data_dir: Path, relative: str) -> Path:
    parts = Path(relative).parts
    if not relative or relative.startswith(("/", "\\")) or ".." in parts:
        raise StorageError("文件路径越界")
    root = data_dir.resolve()
    path = (root / relative).resolve()
    if path != root and root not in path.parents:
        raise StorageError("文件路径越界")
    return path


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        raise StorageError("无法写入云端文件") from exc
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def read_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise StorageError("无法读取云端文件") from exc


def dump_document(document: TagDocument) -> bytes:
    text = json.dumps(document_to_dict(document), ensure_ascii=False, indent=2) + "\n"
    return text.encode("utf-8")


def load_document(path: Path, subtitle_name: str) -> TagDocument:
    try:
        payload = json.loads(read_bytes(path).decode("utf-8"))
    except (StorageError, UnicodeError, json.JSONDecodeError) as exc:
        raise StorageError("云端标签文件无法读取") from exc
    try:
        return parse_document(payload, subtitle_name)
    except InvalidTagDocument as exc:
        raise StorageError("云端标签文件无法读取") from exc


def touched_at():
    return utc_now_naive()
