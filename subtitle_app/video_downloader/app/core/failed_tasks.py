from __future__ import annotations

import json
import threading
from pathlib import Path

from .settings import USER_PATH
from .task import DownloadTask, TaskStatus

_lock = threading.Lock()


def failed_tasks_path() -> Path:
    return USER_PATH.parent / "failed_tasks.json"


def load_failed_tasks(path: Path | None = None) -> list[DownloadTask]:
    """读出上次未成功、需要留到下次打开的任务。"""
    path = path or failed_tasks_path()
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, list):
        return []

    tasks: list[DownloadTask] = []
    seen: set[str] = set()
    for item in raw:
        task = _task_from_record(item)
        if task is None or task.id in seen:
            continue
        seen.add(task.id)
        tasks.append(task)
    return tasks


def remember_tasks(tasks: list[DownloadTask], path: Path | None = None) -> None:
    """按 id 写入或更新失败记录，不删掉文件里其余条目。"""
    incoming = [_record_from_task(task) for task in tasks if task.status == TaskStatus.FAILED and task.url]
    if not incoming:
        return
    path = path or failed_tasks_path()
    with _lock:
        records = {item["id"]: item for item in _read_records(path)}
        for item in incoming:
            records[item["id"]] = item
        _write_records(path, list(records.values()))


def forget_ids(task_ids: set[str], path: Path | None = None) -> None:
    """下载成功、取消或被用户删掉后，从失败记录里去掉。"""
    ids = {task_id for task_id in task_ids if task_id}
    if not ids:
        return
    path = path or failed_tasks_path()
    with _lock:
        records = [item for item in _read_records(path) if item.get("id") not in ids]
        _write_records(path, records)


def _read_records(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict) and item.get("id") and item.get("url")]


def _write_records(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _record_from_task(task: DownloadTask) -> dict:
    return {
        "id": task.id,
        "url": task.url,
        "title": task.title,
        "progress": task.progress,
        "error": task.error,
        "filepath": task.filepath,
        "extractor": task.extractor,
        "filesize": task.filesize,
        "save_subdir": task.save_subdir,
    }


def _task_from_record(item: object) -> DownloadTask | None:
    if not isinstance(item, dict):
        return None
    url = str(item.get("url") or "").strip()
    if not url:
        return None
    try:
        progress = float(item.get("progress") or 0)
    except (TypeError, ValueError):
        progress = 0.0
    return DownloadTask(
        id=str(item.get("id") or "").strip() or url,
        url=url,
        title=str(item.get("title") or url),
        status=TaskStatus.FAILED,
        progress=progress,
        speed="-",
        eta="-",
        filesize=str(item.get("filesize") or "-"),
        error=str(item.get("error") or ""),
        filepath=str(item.get("filepath") or ""),
        extractor=str(item.get("extractor") or ""),
        save_subdir=str(item.get("save_subdir") or ""),
    )
