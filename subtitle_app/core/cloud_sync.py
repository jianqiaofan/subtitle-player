"""收集要上传的字幕和标签，并记住每台设备上次同步后的云端版本。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from core.app_paths import app_dir
from core.cloud_api import CloudClient, CloudError
from core.cloud_tags import (
    build_upload_document,
    document_from_server,
    merge_server_document,
    server_has_visible_tags,
)
from core.config import MEDIA_EXTENSIONS
from core.subtitle_loader import SUBTITLE_EXTENSIONS, load_subtitles
from core.subtitle_tags import (
    SubtitleTagDocument,
    load_tag_document,
    save_tag_document,
    subtitle_belongs_to_media,
    subtitle_name_for_tag_path,
    tag_path_for_subtitle,
)
from core.video_hash import cached_video_for_subtitle, language_suffix, video_content_hash

DEFAULT_CLOUD_SERVER = "https://subtitle.gcsfg.work"


@dataclass
class SubtitleFile:
    path: Path
    video_hash: str
    video_stem: str
    subtitle_name: str
    content: str


@dataclass
class TagFile:
    path: Path
    video_hash: str
    video_stem: str
    subtitle_name: str
    document: SubtitleTagDocument


@dataclass
class SubtitleCandidate:
    item: SubtitleFile
    remote_hash: str = ""
    remote_updated_at: str = ""
    state: str = "new"  # new | changed | same


@dataclass
class OpenMediaUpdate:
    subtitles: list[dict] = field(default_factory=list)
    tags: list[dict] = field(default_factory=list)
    shares: list[dict] = field(default_factory=list)


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_subtitle_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def paired_video(subtitle_path: Path) -> Path | None:
    folder = subtitle_path.parent
    try:
        entries = list(folder.iterdir())
    except OSError:
        return None
    for item in entries:
        if not item.is_file() or item.suffix.lower() not in MEDIA_EXTENSIONS:
            continue
        if subtitle_belongs_to_media(subtitle_path, item):
            return item
    return None


def collect_media_subtitles(media_path: Path) -> tuple[list[SubtitleFile], list[str]]:
    from core.subtitle_loader import find_valid_subtitles

    found: list[SubtitleFile] = []
    skipped: list[str] = []
    try:
        digest = video_content_hash(media_path)
    except OSError:
        return [], [f"{media_path.name}：无法计算视频哈希"]
    for path, _label in find_valid_subtitles(media_path):
        found.append(
            SubtitleFile(
                path=path,
                video_hash=digest,
                video_stem=media_path.stem,
                subtitle_name=path.name,
                content=read_subtitle_text(path),
            )
        )
    if not found:
        skipped.append(f"{media_path.name}：没有可上传的字幕")
    return found, skipped


def collect_media_tags(media_path: Path) -> tuple[list[TagFile], list[str]]:
    from core.subtitle_loader import find_valid_subtitles

    found: list[TagFile] = []
    skipped: list[str] = []
    try:
        digest = video_content_hash(media_path)
    except OSError:
        return [], [f"{media_path.name}：无法计算视频哈希"]
    for path, _label in find_valid_subtitles(media_path):
        tag_path = tag_path_for_subtitle(path)
        document = load_tag_document(tag_path, path.name)
        if not document.entries:
            continue
        document.subtitle_file = path.name
        found.append(
            TagFile(
                path=tag_path,
                video_hash=digest,
                video_stem=media_path.stem,
                subtitle_name=path.name,
                document=document,
            )
        )
    if not found:
        skipped.append(f"{media_path.name}：没有可上传的标签")
    return found, skipped


def prepare_subtitle_from_hash_file(path: Path) -> tuple[SubtitleFile | None, str]:
    """用字幕同目录的哈希文件组装一条可上传字幕。失败时返回原因。"""
    identity, reason = cached_video_for_subtitle(path)
    if identity is None:
        return None, reason
    if not language_suffix(path.name, identity.video_stem):
        return None, (
            f"「{path.name}」和视频「{identity.media_name}」的文件名对不上，无法上传。"
            "字幕名需要和视频主文件名相同，或只在后面加语言后缀。"
        )
    try:
        content = read_subtitle_text(path)
    except OSError:
        return None, f"「{path.name}」无法读取，无法上传。"
    if not content.strip():
        return None, f"「{path.name}」是空的，无法上传。"
    return (
        SubtitleFile(
            path=path,
            video_hash=identity.video_hash,
            video_stem=identity.video_stem,
            subtitle_name=path.name,
            content=content,
        ),
        "",
    )


def collect_hashed_subtitles(folder: Path) -> tuple[list[SubtitleFile], list[str]]:
    """收集文件夹里能从同目录哈希文件确定视频的字幕。"""
    found: list[SubtitleFile] = []
    skipped: list[str] = []
    saw_subtitle = False
    for path in _walk(folder):
        if path.suffix.lower() not in SUBTITLE_EXTENSIONS:
            continue
        saw_subtitle = True
        item, reason = prepare_subtitle_from_hash_file(path)
        if item is None:
            skipped.append(reason)
            continue
        found.append(item)
    if not saw_subtitle and not skipped:
        skipped.append("这个文件夹里没有字幕文件。")
    return found, skipped


def collect_folder_subtitles(folder: Path) -> tuple[list[SubtitleFile], list[str]]:
    found: list[SubtitleFile] = []
    skipped: list[str] = []
    for path in _walk(folder):
        if path.suffix.lower() not in SUBTITLE_EXTENSIONS:
            continue
        try:
            segments = load_subtitles(path)
        except (OSError, ValueError):
            skipped.append(f"{path.name}：字幕无法读取")
            continue
        if not segments:
            skipped.append(f"{path.name}：字幕是空的")
            continue
        media = paired_video(path)
        if media is None:
            skipped.append(f"{path.name}：旁边没有对应的视频")
            continue
        try:
            content = read_subtitle_text(path)
            digest = video_content_hash(media)
        except OSError:
            skipped.append(f"{path.name}：字幕或视频无法读取")
            continue
        found.append(
            SubtitleFile(
                path=path,
                video_hash=digest,
                video_stem=media.stem,
                subtitle_name=path.name,
                content=content,
            )
        )
    return found, skipped


def collect_folder_tags(folder: Path) -> tuple[list[TagFile], list[str]]:
    found: list[TagFile] = []
    skipped: list[str] = []
    for path in _walk(folder):
        if not path.name.endswith(".tags.json"):
            continue
        subtitle_name = subtitle_name_for_tag_path(path)
        if not subtitle_name:
            skipped.append(f"{path.name}：不是标签文件")
            continue
        subtitle_path = path.parent / subtitle_name
        if not subtitle_path.is_file():
            skipped.append(f"{path.name}：旁边没有同名字幕")
            continue
        media = paired_video(subtitle_path)
        if media is None:
            skipped.append(f"{path.name}：旁边没有对应的视频")
            continue
        try:
            digest = video_content_hash(media)
        except OSError:
            skipped.append(f"{path.name}：无法计算视频哈希")
            continue
        document = load_tag_document(path, subtitle_name)
        if not any(entry.should_keep() for entry in document.entries):
            skipped.append(f"{path.name}：标签文件无效")
            continue
        document.subtitle_file = subtitle_name
        found.append(
            TagFile(
                path=path,
                video_hash=digest,
                video_stem=media.stem,
                subtitle_name=subtitle_name,
                document=document,
            )
        )
    return found, skipped


def classify_subtitles(items: list[SubtitleFile], remote_by_name: dict[str, dict]) -> list[SubtitleCandidate]:
    result: list[SubtitleCandidate] = []
    for item in items:
        remote = remote_by_name.get((item.video_hash, language_suffix(item.subtitle_name, item.video_stem)))
        if not remote:
            result.append(SubtitleCandidate(item=item, state="new"))
            continue
        remote_hash = str(remote.get("content_hash") or "")
        updated_at = str(remote.get("updated_at") or "")
        if remote_hash and remote_hash == content_hash(item.content):
            result.append(
                SubtitleCandidate(item=item, remote_hash=remote_hash, remote_updated_at=updated_at, state="same")
            )
        else:
            result.append(
                SubtitleCandidate(
                    item=item,
                    remote_hash=remote_hash,
                    remote_updated_at=updated_at,
                    state="changed",
                )
            )
    return result


def fetch_remote_index(client: CloudClient, video_hashes: set[str]) -> dict[tuple[str, str], dict]:
    """(视频哈希, 语言后缀) -> 云端字幕或标签。标签键前面加 tag:。"""
    index: dict[tuple[str, str], dict] = {}
    for video_hash in video_hashes:
        bundle = client.get_sync(video_hash)
        for item in bundle.get("subtitles") or []:
            suffix = str(item.get("subtitle_suffix") or "")
            if suffix:
                index[(video_hash, suffix)] = item
        for item in bundle.get("tags") or []:
            suffix = str(item.get("subtitle_suffix") or "")
            if suffix:
                index[(video_hash, "tag:" + suffix)] = item
    return index


def upload_subtitles(client: CloudClient, items: list[SubtitleFile], shared: bool = False) -> list[str]:
    done: list[str] = []
    for item in items:
        client.put_subtitle(item.video_hash, item.video_stem, item.subtitle_name, item.content, shared)
        record_subtitle_baseline(
            client.username,
            item.video_hash,
            language_suffix(item.subtitle_name, item.video_stem),
            content_hash(item.content),
            "",
        )
        done.append(item.subtitle_name)
    return done


def upload_tags(client: CloudClient, items: list[TagFile]) -> tuple[list[str], list[str]]:
    uploaded: list[str] = []
    unchanged: list[str] = []
    for item in items:
        suffix = language_suffix(item.subtitle_name, item.video_stem)
        baseline = load_tag_baseline(client.username, item.video_hash, suffix)
        payload = build_upload_document(item.document, baseline)
        if payload is None:
            unchanged.append(item.subtitle_name)
            continue
        result = client.put_tags(item.video_hash, item.video_stem, item.subtitle_name, payload)
        document = result.get("document") if isinstance(result.get("document"), dict) else {}
        record_tag_baseline(
            client.username,
            item.video_hash,
            suffix,
            str(result.get("content_hash") or ""),
            str(result.get("updated_at") or ""),
            document,
        )
        uploaded.append(item.subtitle_name)
    return uploaded, unchanged


def inspect_open_media(client: CloudClient, media_path: Path) -> OpenMediaUpdate:
    digest = video_content_hash(media_path)
    bundle = client.get_sync(digest)
    update = OpenMediaUpdate()
    for item in bundle.get("subtitles") or []:
        suffix = str(item.get("subtitle_suffix") or "")
        content = str(item.get("content") or "")
        if not suffix or not content:
            continue
        name = media_path.stem + suffix
        local_path = media_path.parent / name
        remote_hash = str(item.get("content_hash") or "")
        updated_at = str(item.get("updated_at") or "")
        seen = subtitle_baseline_hash(client.username, digest, suffix)
        if remote_hash and remote_hash == seen and local_path.is_file():
            continue
        if local_path.is_file():
            try:
                local_text = read_subtitle_text(local_path)
            except OSError:
                local_text = ""
            if content_hash(local_text) == content_hash(content):
                record_subtitle_baseline(client.username, digest, suffix, remote_hash, updated_at)
                continue
            state = "changed"
        else:
            state = "new"
        update.subtitles.append(
            {
                "subtitle_name": name,
                "subtitle_suffix": suffix,
                "video_hash": digest,
                "content": content,
                "updated_at": updated_at,
                "content_hash": remote_hash,
                "path": str(local_path),
                "state": state,
            }
        )
    for item in bundle.get("tags") or []:
        suffix = str(item.get("subtitle_suffix") or "")
        document = item.get("document") if isinstance(item.get("document"), dict) else {}
        remote_hash = str(item.get("content_hash") or "")
        updated_at = str(item.get("updated_at") or "")
        if not suffix or not remote_hash:
            continue
        name = media_path.stem + suffix
        subtitle_path = media_path.parent / name
        tag_path = tag_path_for_subtitle(subtitle_path)
        seen = tag_baseline_hash(client.username, digest, suffix)
        local = load_tag_document(tag_path, name)
        local_has_tags = any(entry.should_keep() for entry in local.entries)
        # 和字幕一样：云端没变且本机文件还在，就不再问。本机标签不在时要重新下载。
        if remote_hash == seen and local_has_tags:
            continue
        if not server_has_visible_tags(document) and not local_has_tags:
            continue
        update.tags.append(
            {
                "subtitle_name": name,
                "subtitle_suffix": suffix,
                "video_hash": digest,
                "document": document,
                "updated_at": updated_at,
                "content_hash": remote_hash,
                "path": str(tag_path_for_subtitle(subtitle_path)),
                "subtitle_path": str(subtitle_path),
            }
        )
    from core.subtitle_loader import find_valid_subtitles

    if not find_valid_subtitles(media_path):
        try:
            update.shares = client.list_shares(digest)
        except CloudError:
            update.shares = []
    return update


def apply_shared_download(media_path: Path, subtitles: list[dict]) -> list[Path]:
    written: list[Path] = []
    for item in subtitles:
        suffix = str(item.get("subtitle_suffix") or "")
        content = str(item.get("content") or "")
        if not suffix or not content:
            continue
        path = media_path.parent / f"{media_path.stem}{suffix}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode("utf-8"))
        written.append(path)
    return written


def apply_subtitle_download(username: str, item: dict) -> Path:
    path = Path(item["path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(str(item.get("content") or "").encode("utf-8"))
    record_subtitle_baseline(
        username,
        str(item.get("video_hash") or ""),
        str(item.get("subtitle_suffix") or ""),
        str(item.get("content_hash") or ""),
        str(item.get("updated_at") or ""),
    )
    return path


def apply_tag_download(username: str, item: dict) -> Path:
    subtitle_name = str(item.get("subtitle_name") or "")
    tag_path = Path(item["path"])
    local = load_tag_document(tag_path, subtitle_name)
    local.subtitle_file = subtitle_name
    document = item.get("document") if isinstance(item.get("document"), dict) else {}
    merged = merge_server_document(local, document)
    save_tag_document(tag_path, merged)
    # 基线记云端版本。本机多出来的标签会在下次上传时送上去。
    record_tag_baseline(
        username,
        str(item.get("video_hash") or ""),
        str(item.get("subtitle_suffix") or ""),
        str(item.get("content_hash") or ""),
        str(item.get("updated_at") or ""),
        document,
    )
    return tag_path


def load_account_client(base_url: str, username: str, password: str) -> CloudClient:
    client = CloudClient(base_url or DEFAULT_CLOUD_SERVER, username, password)
    client.login()
    return client


def _store_path(username: str) -> Path:
    digest = hashlib.sha256(username.encode("utf-8")).hexdigest()[:16]
    return app_dir() / "cloud_baselines" / f"{digest}.json"


def _load_store(username: str) -> dict:
    path = _store_path(username)
    if not path.is_file():
        return {"subtitles": {}, "tags": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"subtitles": {}, "tags": {}}
    if not isinstance(payload, dict):
        return {"subtitles": {}, "tags": {}}
    payload.setdefault("subtitles", {})
    payload.setdefault("tags", {})
    return payload


def _save_store(username: str, payload: dict) -> None:
    path = _store_path(username)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _baseline_key(video_hash: str, suffix: str) -> str:
    return f"{video_hash}/{suffix}"


def record_subtitle_baseline(
    username: str,
    video_hash: str,
    suffix: str,
    content_hash_value: str,
    updated_at: str,
) -> None:
    if not username or not video_hash or not suffix:
        return
    store = _load_store(username)
    store["subtitles"][_baseline_key(video_hash, suffix)] = {
        "content_hash": content_hash_value,
        "updated_at": updated_at,
    }
    _save_store(username, store)


def record_tag_baseline(
    username: str,
    video_hash: str,
    suffix: str,
    content_hash_value: str,
    updated_at: str,
    document: dict,
) -> None:
    if not username or not video_hash or not suffix:
        return
    store = _load_store(username)
    store["tags"][_baseline_key(video_hash, suffix)] = {
        "content_hash": content_hash_value,
        "updated_at": updated_at,
        "document": document,
    }
    _save_store(username, store)


def subtitle_baseline_hash(username: str, video_hash: str, suffix: str) -> str:
    item = _load_store(username)["subtitles"].get(_baseline_key(video_hash, suffix)) or {}
    return str(item.get("content_hash") or "")


def tag_baseline_hash(username: str, video_hash: str, suffix: str) -> str:
    item = _load_store(username)["tags"].get(_baseline_key(video_hash, suffix)) or {}
    return str(item.get("content_hash") or "")


def load_tag_baseline(username: str, video_hash: str, suffix: str) -> SubtitleTagDocument | None:
    item = _load_store(username)["tags"].get(_baseline_key(video_hash, suffix)) or {}
    document = item.get("document")
    if not isinstance(document, dict):
        return None
    parsed = document_from_server(document, "")
    if not parsed.entries:
        return None
    return parsed


def _walk(folder: Path) -> list[Path]:
    found: list[Path] = []
    try:
        walker = folder.rglob("*")
    except OSError:
        return found
    for path in walker:
        if path.is_file():
            found.append(path)
    found.sort(key=lambda item: str(item).casefold())
    return found
