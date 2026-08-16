from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any

_BV_RE = re.compile(r"BV[\w]+", re.I)
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


def extract_bvid(url: str) -> str | None:
    m = _BV_RE.search(url or "")
    return m.group(0) if m else None


def _http_json(url: str) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _UA,
            "Referer": "https://www.bilibili.com",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_ugc_season(bvid: str) -> dict[str, Any] | None:
    """若该稿件属于 UP 主合集，返回合集信息与全部剧集列表。"""
    data = _http_json(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}")
    if data.get("code") != 0:
        raise RuntimeError(data.get("message") or "B 站接口返回错误")
    view = data.get("data") or {}
    season = view.get("ugc_season")
    if not season:
        return None

    owner = view.get("owner") or {}
    mid = owner.get("mid")
    sid = season.get("id")
    episodes: list[dict[str, Any]] = []
    for section in season.get("sections") or []:
        for ep in section.get("episodes") or []:
            ep_bvid = ep.get("bvid")
            if not ep_bvid:
                continue
            title = ep.get("title") or ep.get("page", {}).get("part") or ep_bvid
            episodes.append(
                {
                    "bvid": ep_bvid,
                    "title": title,
                    "url": f"https://www.bilibili.com/video/{ep_bvid}",
                }
            )

    if not episodes or not mid or not sid:
        return None

    return {
        "season_id": sid,
        "title": season.get("title") or "合集",
        "mid": mid,
        "count": len(episodes),
        "episodes": episodes,
        "collection_url": (
            f"https://space.bilibili.com/{mid}/lists/{sid}?type=season"
        ),
        "current_bvid": bvid,
    }


def resolve_collection_from_url(url: str) -> dict[str, Any] | None:
    """单视频链接 → 合集；已是合集链接则交给 yt-dlp，这里返回 None。"""
    if "collectiondetail" in url or "/lists/" in url:
        return None
    bvid = extract_bvid(url)
    if not bvid:
        return None
    try:
        return fetch_ugc_season(bvid)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, RuntimeError):
        return None
