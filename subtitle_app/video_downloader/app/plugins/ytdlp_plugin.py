from __future__ import annotations

from typing import Any

from .base import BasePlugin


class YtDlpPlugin(BasePlugin):
    """通用后备插件：把解析交给 yt-dlp。"""

    name = "yt-dlp"
    domains: list[str] = []

    def match(self, url: str) -> bool:
        return url.startswith("http://") or url.startswith("https://")

    def extract(self, url: str) -> dict[str, Any]:
        import yt_dlp

        with yt_dlp.YoutubeDL({"quiet": True, "skip_download": True}) as ydl:
            info = ydl.extract_info(url, download=False)
        return info or {}
