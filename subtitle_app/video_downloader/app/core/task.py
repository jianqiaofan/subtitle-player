from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TaskStatus(str, Enum):
    PENDING = "等待中"
    PARSING = "解析中"
    QUEUED = "队列中"
    DOWNLOADING = "下载中"
    POSTPROCESSING = "后处理"
    COMPLETED = "已完成"
    FAILED = "失败"
    CANCELLED = "已取消"
    PAUSED = "已暂停"


@dataclass
class DownloadTask:
    url: str
    title: str = "解析中…"
    status: TaskStatus = TaskStatus.PENDING
    progress: float = 0.0
    speed: str = "-"
    eta: str = "-"
    filesize: str = "-"
    error: str = ""
    filepath: str = ""
    extractor: str = ""
    format_id: str = ""
    info: dict[str, Any] = field(default_factory=dict)
    # 合集元数据（B 站 ugc_season 等）
    collection_title: str = ""
    collection_url: str = ""
    collection_count: int = 0
    collection_episodes: list[dict[str, Any]] = field(default_factory=list)
    save_subdir: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])

    def to_row(self, checked: bool = False) -> tuple[str, ...]:
        title = self.title
        if self.collection_count and self.collection_title:
            title = f"{title}  〔合集:{self.collection_title}/{self.collection_count}集〕"
        mark = "☑" if checked else "☐"
        return (
            mark,
            title[:72],
            self.status.value,
            f"{self.progress:.1f}%",
            self.speed,
            self.eta,
            self.filesize,
        )