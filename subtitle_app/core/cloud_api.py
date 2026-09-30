"""字幕云同步的 HTTP 客户端。"""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


class CloudError(Exception):
    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


class CloudClient:
    def __init__(self, base_url: str, username: str, password: str) -> None:
        self.base_url = (base_url or "").strip().rstrip("/")
        self.username = username.strip()
        self.password = password
        self.token = ""

    def register(self) -> None:
        payload = self._request(
            "POST",
            "/api/auth/register",
            {"username": self.username, "password": self.password},
            auth=False,
        )
        self.token = str(payload.get("access_token") or "")

    def login(self) -> None:
        payload = self._request(
            "POST",
            "/api/auth/login",
            {"username": self.username, "password": self.password},
            auth=False,
        )
        self.token = str(payload.get("access_token") or "")

    def get_sync(self, video_hash: str) -> dict:
        return self._request("GET", "/api/sync?video_hash=" + quote(video_hash, safe=""))

    def put_subtitle(
        self,
        video_hash: str,
        video_stem: str,
        subtitle_name: str,
        content: str,
        shared: bool = False,
    ) -> dict:
        return self._request(
            "PUT",
            "/api/subtitles",
            {
                "video_hash": video_hash,
                "video_stem": video_stem,
                "subtitle_name": subtitle_name,
                "content": content,
                "shared": bool(shared),
            },
        )

    def list_shares(self, video_hash: str) -> list:
        payload = self._request("GET", "/api/shares?video_hash=" + quote(video_hash, safe=""))
        shares = payload.get("shares")
        return shares if isinstance(shares, list) else []

    def get_share(self, video_hash: str, username: str) -> dict:
        return self._request(
            "GET",
            "/api/shares?video_hash=" + quote(video_hash, safe="") + "&username=" + quote(username, safe=""),
        )

    def list_library_subtitles(self) -> list:
        payload = self._request("GET", "/api/library/subtitles")
        rows = payload.get("subtitles")
        return rows if isinstance(rows, list) else []

    def read_library_subtitle(self, item_id: int) -> dict:
        return self._request("GET", f"/api/library/subtitles/{int(item_id)}")

    def set_library_subtitle_shared(self, item_id: int, shared: bool) -> dict:
        return self._request("PATCH", f"/api/library/subtitles/{int(item_id)}", {"shared": bool(shared)})

    def delete_library_subtitle(self, item_id: int) -> None:
        self._request("DELETE", f"/api/library/subtitles/{int(item_id)}")

    def list_library_tags(self) -> list:
        payload = self._request("GET", "/api/library/tags")
        rows = payload.get("tags")
        return rows if isinstance(rows, list) else []

    def read_library_tag(self, item_id: int) -> dict:
        return self._request("GET", f"/api/library/tags/{int(item_id)}")

    def delete_library_tag(self, item_id: int) -> None:
        self._request("DELETE", f"/api/library/tags/{int(item_id)}")

    def put_tags(self, video_hash: str, video_stem: str, subtitle_name: str, document: dict) -> dict:
        return self._request(
            "PUT",
            "/api/tags",
            {
                "video_hash": video_hash,
                "video_stem": video_stem,
                "subtitle_name": subtitle_name,
                "document": document,
            },
        )

    def put_playback(self, video_hash: str, video_stem: str, sessions: list[dict]) -> dict:
        return self._request(
            "PUT",
            "/api/playback",
            {"video_hash": video_hash, "video_stem": video_stem, "sessions": sessions},
        )

    def get_playback(self, video_hash: str) -> dict:
        return self._request("GET", "/api/playback?video_hash=" + quote(video_hash, safe=""))

    def put_screenshots(
        self,
        video_hash: str,
        video_stem: str,
        shots: list[dict],
        baseline_ids: list[str],
    ) -> dict:
        return self._request(
            "PUT",
            "/api/screenshots",
            {
                "video_hash": video_hash,
                "video_stem": video_stem,
                "shots": shots,
                "baseline_ids": baseline_ids,
            },
        )

    def put_screenshot_image(self, video_hash: str, shot_id: str, data: bytes) -> dict:
        raw = self._request_bytes(
            "PUT",
            f"/api/screenshots/{video_hash}/{shot_id}/image",
            data,
            "image/webp",
        )
        if not raw:
            return {}
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise CloudError("服务器返回的内容无法识别") from exc
        if not isinstance(payload, dict):
            raise CloudError("服务器返回的内容无法识别")
        return payload

    def get_screenshot_image(self, video_hash: str, shot_id: str) -> bytes:
        return self._request_bytes("GET", f"/api/screenshots/{video_hash}/{shot_id}/image")

    def _request(self, method: str, path: str, body: dict | None = None, auth: bool = True) -> dict:
        if not self.base_url.startswith(("http://", "https://")):
            raise CloudError("服务器地址需要以 http:// 或 https:// 开头")
        if auth and not self.token:
            self.login()
        try:
            return self._send(method, path, body, auth)
        except CloudError as exc:
            if auth and exc.status == 401:
                self.token = ""
                self.login()
                return self._send(method, path, body, auth)
            raise

    def _send(self, method: str, path: str, body: dict | None, auth: bool) -> dict:
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = Request(self.base_url + path, data=data, method=method)
        if body is not None:
            request.add_header("Content-Type", "application/json")
        if auth and self.token:
            request.add_header("Authorization", "Bearer " + self.token)
        try:
            with urlopen(request, timeout=30) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise CloudError(_error_message(detail, exc.code), exc.code) from exc
        except URLError as exc:
            raise CloudError(f"无法连接服务器：{exc.reason}") from exc
        if not raw:
            return {}
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CloudError("服务器返回的内容无法识别") from exc
        if not isinstance(payload, dict):
            raise CloudError("服务器返回的内容无法识别")
        return payload

    def _request_bytes(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        content_type: str = "",
    ) -> bytes:
        if not self.base_url.startswith(("http://", "https://")):
            raise CloudError("服务器地址需要以 http:// 或 https:// 开头")
        if not self.token:
            self.login()
        try:
            return self._send_bytes(method, path, body, content_type)
        except CloudError as exc:
            if exc.status == 401:
                self.token = ""
                self.login()
                return self._send_bytes(method, path, body, content_type)
            raise

    def _send_bytes(self, method: str, path: str, body: bytes | None, content_type: str) -> bytes:
        request = Request(self.base_url + path, data=body, method=method)
        if content_type:
            request.add_header("Content-Type", content_type)
        if self.token:
            request.add_header("Authorization", "Bearer " + self.token)
        try:
            with urlopen(request, timeout=60) as response:
                return response.read()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise CloudError(_error_message(detail, exc.code), exc.code) from exc
        except URLError as exc:
            raise CloudError(f"无法连接服务器：{exc.reason}") from exc


def _error_message(raw: str, status: int) -> str:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
    if status == 401:
        return "用户名或密码不正确"
    return "服务器没有完成这次请求"
