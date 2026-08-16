from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BasePlugin(ABC):
    """站点解析插件基类。当前默认走 yt-dlp；可在此扩展自定义站点。"""

    name: str = "base"
    domains: list[str] = []

    def match(self, url: str) -> bool:
        return any(d in url for d in self.domains)

    @abstractmethod
    def extract(self, url: str) -> dict[str, Any]:
        raise NotImplementedError
