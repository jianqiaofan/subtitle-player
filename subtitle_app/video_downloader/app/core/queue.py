from __future__ import annotations

import threading
from collections import deque
from typing import Callable

from .downloader import YtDlpDownloader
from .settings import Settings
from .task import DownloadTask, TaskStatus


class TaskQueue:
    """简单的线程池任务队列：解析串行入队，下载并发受控。"""

    def __init__(
        self,
        settings: Settings,
        on_task_update: Callable[[DownloadTask], None] | None = None,
        on_log: Callable[[str], None] | None = None,
    ) -> None:
        self.settings = settings
        self.on_task_update = on_task_update
        self.on_log = on_log
        self.downloader = YtDlpDownloader(
            settings,
            on_progress=self._emit,
            on_log=self._log,
        )
        self.tasks: dict[str, DownloadTask] = {}
        self._pending: deque[str] = deque()
        self._lock = threading.Lock()
        self._active = 0
        self._workers: list[threading.Thread] = []
        self._stopped = False

    def _emit(self, task: DownloadTask) -> None:
        if self.on_task_update:
            self.on_task_update(task)

    def _log(self, msg: str) -> None:
        if self.on_log:
            self.on_log(msg)

    def add_url(self, url: str, auto_start: bool = True) -> DownloadTask:
        url = url.strip()
        task = DownloadTask(url=url)
        with self._lock:
            self.tasks[task.id] = task
        self._emit(task)

        def worker() -> None:
            try:
                self.downloader.extract_info(task)
                if auto_start and task.status == TaskStatus.QUEUED:
                    self.enqueue(task.id)
            except Exception as e:  # noqa: BLE001
                task.status = TaskStatus.FAILED
                task.error = str(e)
                self._log(f"[{task.id}] 解析失败: {e}")
                self._emit(task)

        threading.Thread(target=worker, daemon=True, name=f"parse-{task.id}").start()
        return task

    def expand_collection(
        self,
        task_id: str,
        *,
        auto_start: bool = True,
        remove_seed: bool = True,
    ) -> list[str]:
        """把任务上的合集剧集展开为多个独立下载任务，返回新建任务 id。"""
        with self._lock:
            seed = self.tasks.get(task_id)
        if not seed:
            return []
        episodes = list(seed.collection_episodes)
        if not episodes and seed.collection_url:
            self._log(f"[{task_id}] 使用合集链接下载: {seed.collection_url}")
            t = self.add_url(seed.collection_url, auto_start=auto_start)
            if remove_seed:
                self.remove(task_id)
            return [t.id]
        if not episodes:
            self._log(f"[{task_id}] 当前任务没有可展开的合集信息")
            return []

        safe = "".join(
            c for c in (seed.collection_title or "合集") if c not in '\\/:*?"<>|'
        ).strip() or "合集"
        self._log(
            f"[{task_id}] 展开合集「{seed.collection_title}」，共 {len(episodes)} 集 → 目录 {safe}/"
        )

        created_ids: list[str] = []
        for idx, ep in enumerate(episodes, start=1):
            url = ep.get("url") or ""
            if not url:
                continue
            task = DownloadTask(
                url=url,
                title=f"{idx:03d}. {ep.get('title') or url}",
                status=TaskStatus.QUEUED,
                save_subdir=safe,
            )
            with self._lock:
                self.tasks[task.id] = task
            self._emit(task)
            created_ids.append(task.id)
            if auto_start:
                self.enqueue(task.id)

        if remove_seed:
            self.remove(task_id)
        return created_ids

    def enqueue(self, task_id: str) -> None:
        with self._lock:
            task = self.tasks.get(task_id)
            if not task:
                return
            if task.status in {
                TaskStatus.DOWNLOADING,
                TaskStatus.POSTPROCESSING,
                TaskStatus.COMPLETED,
            }:
                return
            task.status = TaskStatus.QUEUED
            self._pending.append(task_id)
        self._emit(self.tasks[task_id])
        self._pump()

    def cancel(self, task_id: str) -> None:
        with self._lock:
            task = self.tasks.get(task_id)
            if not task:
                return
            if task_id in self._pending:
                self._pending = deque(x for x in self._pending if x != task_id)
                task.status = TaskStatus.CANCELLED
                task.error = "已取消"
                self._emit(task)
                return
        self.downloader.request_cancel(task_id)

    def remove(self, task_id: str) -> None:
        self.cancel(task_id)
        with self._lock:
            self.tasks.pop(task_id, None)
            self._pending = deque(x for x in self._pending if x != task_id)

    def clear_finished(self) -> None:
        with self._lock:
            done = [
                tid
                for tid, t in self.tasks.items()
                if t.status
                in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
            ]
            for tid in done:
                self.tasks.pop(tid, None)

    def list_tasks(self) -> list[DownloadTask]:
        with self._lock:
            return list(self.tasks.values())

    def _pump(self) -> None:
        max_n = int(self.settings.get("max_concurrent_tasks") or 2)
        with self._lock:
            while self._active < max_n and self._pending:
                task_id = self._pending.popleft()
                task = self.tasks.get(task_id)
                if not task or task.status == TaskStatus.CANCELLED:
                    continue
                self._active += 1
                t = threading.Thread(
                    target=self._run_download,
                    args=(task_id,),
                    daemon=True,
                    name=f"dl-{task_id}",
                )
                self._workers.append(t)
                t.start()

    def _run_download(self, task_id: str) -> None:
        try:
            task = self.tasks.get(task_id)
            if not task:
                return
            self.downloader.download(task)
        finally:
            with self._lock:
                self._active = max(0, self._active - 1)
            self._pump()
