from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from .settings import Settings
from .task import DownloadTask, TaskStatus

ProgressCallback = Callable[[DownloadTask], None]
LogCallback = Callable[[str], None]


def _human_size(n: float | None) -> str:
    if not n:
        return "-"
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(n)
    for u in units:
        if size < 1024 or u == units[-1]:
            return f"{size:.1f}{u}"
        size /= 1024
    return "-"


def _human_speed(n: float | None) -> str:
    if not n:
        return "-"
    return f"{_human_size(n)}/s"


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text or "")


def _is_youtube_url(url: str) -> bool:
    return bool(re.search(r"(?:youtube\.com|youtu\.be)/", url or "", re.I))


# B 站优先 H.264，避免旧 FFmpeg/播放器只有声音
_BILIBILI_FORMAT = (
    "bv*[vcodec^=avc1]+ba[acodec^=mp4a]/bv*[vcodec^=avc]+ba/bv*+ba/b"
)
# YouTube：不要强绑 avc1（易命中已失效的 android_sdkless 流导致 403）
_YOUTUBE_FORMAT = "bv*+ba/b"


class YtDlpDownloader:
    """封装 yt-dlp：解析信息 + 下载文件。"""

    def __init__(
        self,
        settings: Settings,
        on_progress: ProgressCallback | None = None,
        on_log: LogCallback | None = None,
    ) -> None:
        self.settings = settings
        self.on_progress = on_progress
        self.on_log = on_log
        self._cancel_flags: dict[str, bool] = {}

    def log(self, msg: str) -> None:
        if self.on_log:
            self.on_log(msg)

    def request_cancel(self, task_id: str) -> None:
        self._cancel_flags[task_id] = True

    def clear_cancel(self, task_id: str) -> None:
        self._cancel_flags.pop(task_id, None)

    def _resolve_format(self, url: str) -> str:
        custom = (self.settings.get("video_format") or "").strip()
        if _is_youtube_url(url):
            # 设置里若仍是 B 站默认表达式，对 YouTube 改用更稳妥的选择器
            if (not custom) or ("avc1" in custom) or custom == _BILIBILI_FORMAT:
                return _YOUTUBE_FORMAT
            return custom
        return custom or _BILIBILI_FORMAT

    def _base_opts(
        self,
        task: DownloadTask | None = None,
        *,
        youtube_fallback: bool = False,
    ) -> dict[str, Any]:
        save_dir = Path(self.settings.get("save_dir"))
        if task and task.save_subdir:
            save_dir = save_dir / task.save_subdir
            save_dir.mkdir(parents=True, exist_ok=True)

        url = (task.url if task else "") or ""
        opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "ignoreerrors": False,
            "retries": 10,
            "fragment_retries": 10,
            "concurrent_fragment_downloads": int(
                self.settings.get("concurrent_fragments") or 8
            ),
            "outtmpl": str(save_dir / self.settings.get("filename_template")),
            "merge_output_format": self.settings.get("merge_output") or "mp4",
            "format": self._resolve_format(url),
            "writethumbnail": bool(self.settings.get("write_thumbnail")),
            "writesubtitles": bool(self.settings.get("write_subs")),
            "writeautomaticsub": bool(self.settings.get("write_subs")),
            "subtitleslangs": ["zh-Hans", "zh-Hant", "zh", "en"],
            "embedmetadata": bool(self.settings.get("embed_metadata")),
            "noplaylist": True,
            # 合并失败时给出更明确错误，而不是静默留下单音轨文件
            "prefer_ffmpeg": True,
            # 默认只启 Deno；本机常见是 Node。一并启用，供 YouTube 解 n-sig。
            "js_runtimes": {"deno": {}, "node": {}},
            # 允许拉取 yt-dlp 的外部 JS 组件（解签名需要）
            "remote_components": ["ejs:github"],
        }

        if _is_youtube_url(url):
            # 避开近期易 403 的客户端格式
            clients = ["default", "-android_sdkless", "-web_safari"]
            if youtube_fallback:
                # 二次尝试：偏 HLS / tv 客户端
                clients = ["tv", "ios", "web", "-android_sdkless", "-web_safari"]
                opts["format"] = (
                    "bv*[protocol^=m3u8]+ba*[protocol^=m3u8]/"
                    "b*[protocol^=m3u8]/bv*+ba/b"
                )
            opts["extractor_args"] = {
                "youtube": {
                    "player_client": clients,
                }
            }

        ffmpeg = self.settings.resolve_ffmpeg()
        if ffmpeg:
            opts["ffmpeg_location"] = str(Path(ffmpeg).parent)

        proxy = (self.settings.get("proxy") or "").strip()
        if proxy:
            opts["proxy"] = proxy

        rate = (self.settings.get("limit_rate") or "").strip()
        if rate:
            opts["ratelimit"] = rate

        cookies_file = (self.settings.get("cookies_file") or "").strip()
        if cookies_file and Path(cookies_file).exists():
            opts["cookiefile"] = cookies_file

        browser = (self.settings.get("cookies_from_browser") or "").strip()
        if browser:
            # 例如 chrome / edge / firefox
            opts["cookiesfrombrowser"] = (browser,)

        return opts

    def extract_info(self, task: DownloadTask) -> DownloadTask:
        import yt_dlp

        task.status = TaskStatus.PARSING
        if self.on_progress:
            self.on_progress(task)

        opts = self._base_opts(task)
        opts["skip_download"] = True

        self.log(f"[{task.id}] 正在解析: {task.url}")
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(task.url, download=False)

        if info is None:
            raise RuntimeError("无法解析该链接（站点可能不受支持或需要登录 Cookie）")

        # 播放列表：取第一条展示，实际下载仍走原 URL
        if info.get("_type") == "playlist":
            entries = [e for e in (info.get("entries") or []) if e]
            task.title = info.get("title") or f"播放列表（{len(entries)} 项）"
            task.extractor = info.get("extractor") or ""
            if entries:
                first = entries[0]
                task.info = info
                task.filesize = f"{len(entries)} 个视频"
                self.log(
                    f"[{task.id}] 播放列表「{task.title}」，共 {len(entries)} 项"
                )
            else:
                task.info = info
        else:
            task.title = info.get("title") or task.url
            task.extractor = info.get("extractor") or info.get("ie_key") or ""
            # 优先汇总已选音视频轨体积；顶层 filesize 在 B 站常只是单轨
            task.filesize = _human_size(
                self._estimate_format_size(info)
                or info.get("filesize")
                or info.get("filesize_approx")
            )
            task.info = info
            self.log(
                f"[{task.id}] 解析成功: {task.title} ({task.extractor})"
            )
            self._attach_bilibili_collection(task)

        task.status = TaskStatus.QUEUED
        task.progress = 0.0
        if self.on_progress:
            self.on_progress(task)
        return task

    def _attach_bilibili_collection(self, task: DownloadTask) -> None:
        """单集 B 站链接若属于合集，附加合集信息供界面一键展开。"""
        from .bilibili_collection import resolve_collection_from_url

        try:
            season = resolve_collection_from_url(task.url)
        except Exception as e:  # noqa: BLE001
            self.log(f"[{task.id}] 合集检测跳过: {e}")
            return
        if not season:
            return
        task.collection_title = season["title"]
        task.collection_url = season["collection_url"]
        task.collection_count = int(season["count"])
        task.collection_episodes = list(season["episodes"])
        self.log(
            f"[{task.id}] 检测到合集「{task.collection_title}」"
            f"共 {task.collection_count} 集（可选中后点「下载合集」）"
        )

    @staticmethod
    def _estimate_format_size(info: dict[str, Any]) -> float | None:
        """估算将下载的体积：优先汇总已选中的音视频轨，避免只用单轨大小。"""
        requested = info.get("requested_formats")
        if isinstance(requested, list) and requested:
            total = 0.0
            found = False
            for f in requested:
                if not isinstance(f, dict):
                    continue
                size = f.get("filesize") or f.get("filesize_approx")
                if size:
                    total += float(size)
                    found = True
            if found:
                return total

        size = info.get("filesize") or info.get("filesize_approx")
        if size:
            return float(size)

        # 回退：按 format_id 匹配，而不是盲目取 formats 末尾两项
        format_id = str(info.get("format_id") or "")
        formats = info.get("formats") or []
        if format_id and isinstance(formats, list):
            wanted = set(format_id.split("+"))
            total = 0.0
            found = False
            for f in formats:
                if not isinstance(f, dict):
                    continue
                if str(f.get("format_id") or "") not in wanted:
                    continue
                size = f.get("filesize") or f.get("filesize_approx")
                if size:
                    total += float(size)
                    found = True
            if found:
                return total
        return None

    @staticmethod
    def _refresh_filesize_from_disk(task: DownloadTask) -> None:
        path = (task.filepath or "").strip()
        if not path:
            return
        try:
            size = Path(path).stat().st_size
        except OSError:
            return
        if size > 0:
            task.filesize = _human_size(float(size))

    def download(self, task: DownloadTask) -> DownloadTask:
        import yt_dlp

        self.clear_cancel(task.id)
        task.status = TaskStatus.DOWNLOADING
        task.progress = 0.0
        task.error = ""
        if self.on_progress:
            self.on_progress(task)

        def hook(d: dict[str, Any]) -> None:
            if self._cancel_flags.get(task.id):
                raise yt_dlp.utils.DownloadCancelled("用户取消")

            status = d.get("status")
            if status == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                downloaded = d.get("downloaded_bytes") or 0
                if total:
                    task.progress = min(99.0, downloaded * 100.0 / total)
                    # B 站等站点音视频分轨下载时，这里的 total 只是当前轨大小。
                    # 不能覆盖列表「大小」，否则完成后会一直显示音轨几 MB。
                else:
                    # 分片下载时用已下载比例字符串
                    pct = d.get("_percent_str") or "0%"
                    m = re.search(r"([\d.]+)", pct)
                    if m:
                        task.progress = float(m.group(1))
                task.speed = _human_speed(d.get("speed"))
                eta = d.get("eta")
                task.eta = f"{eta}s" if eta is not None else "-"
                task.status = TaskStatus.DOWNLOADING
            elif status == "finished":
                task.progress = 99.5
                task.speed = "-"
                task.eta = "-"
                task.status = TaskStatus.POSTPROCESSING
                filename = d.get("filename") or ""
                if filename:
                    task.filepath = filename
                self.log(f"[{task.id}] 下载完成，正在后处理…")
            elif status == "error":
                task.status = TaskStatus.FAILED

            if self.on_progress:
                self.on_progress(task)

        def _run_download(*, youtube_fallback: bool = False) -> None:
            opts = self._base_opts(task, youtube_fallback=youtube_fallback)
            opts["progress_hooks"] = [hook]
            opts["skip_download"] = False
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(task.url, download=True)
                if info:
                    task.info = info
                    task.title = info.get("title") or task.title
                    prepared = ydl.prepare_filename(info)
                    # 合并后扩展名可能变化
                    merge_ext = self.settings.get("merge_output") or "mp4"
                    candidate = Path(prepared)
                    if candidate.suffix.lower() != f".{merge_ext}":
                        alt = candidate.with_suffix(f".{merge_ext}")
                        if alt.exists():
                            candidate = alt
                    if candidate.exists():
                        task.filepath = str(candidate)
                    elif not task.filepath:
                        task.filepath = prepared

        self.log(f"[{task.id}] 开始下载: {task.title}")
        try:
            try:
                _run_download(youtube_fallback=False)
            except Exception as first_error:  # noqa: BLE001
                err_text = _strip_ansi(str(first_error))
                is_403 = "403" in err_text or "Forbidden" in err_text
                if (
                    is_403
                    and _is_youtube_url(task.url)
                    and not self._cancel_flags.get(task.id)
                ):
                    self.log(
                        f"[{task.id}] YouTube 返回 403，切换备用客户端重试…"
                    )
                    task.progress = 0.0
                    task.status = TaskStatus.DOWNLOADING
                    if self.on_progress:
                        self.on_progress(task)
                    _run_download(youtube_fallback=True)
                else:
                    raise

            if self._cancel_flags.get(task.id):
                task.status = TaskStatus.CANCELLED
                task.error = "已取消"
            else:
                task.status = TaskStatus.COMPLETED
                task.progress = 100.0
                task.speed = "-"
                task.eta = "完成"
                self._refresh_filesize_from_disk(task)
                self.log(f"[{task.id}] 全部完成: {task.filepath or task.title}")
        except Exception as e:  # noqa: BLE001
            name = type(e).__name__
            if "DownloadCancelled" in name or "Cancelled" in name:
                task.status = TaskStatus.CANCELLED
                task.error = "已取消"
                self.log(f"[{task.id}] 已取消")
            else:
                task.status = TaskStatus.FAILED
                task.error = _strip_ansi(str(e))
                self.log(f"[{task.id}] 失败: {task.error}")
                if "403" in task.error or "Forbidden" in task.error:
                    self.log(
                        f"[{task.id}] 提示: YouTube 反爬较严。"
                        "可在设置填写代理，或填写「从浏览器读取 Cookie」"
                        "（如 edge / chrome），并确保本机有 Node.js。"
                    )
        finally:
            self.clear_cancel(task.id)
            if self.on_progress:
                self.on_progress(task)
        return task
