"""打开视频时同步截图。原图留在本机，缺图时先抽帧，抽不出再下载压缩图。"""

from __future__ import annotations

from pathlib import Path

from core.cloud_api import CloudClient, CloudError
from core.cloud_sync import record_screenshot_baseline, screenshot_baseline_ids, screenshot_catalog_was_cleared
from core.screenshot_image import compress_png_to_webp, extract_video_frame
from core.screenshots import (
    Screenshot,
    ScreenshotDocument,
    _note_payload,
    _parse_screenshot,
    _safe_id,
    delete_screenshot_images,
    load_screenshots,
    save_screenshots,
    screenshot_content_matches,
    screenshot_dir,
    screenshot_document_path,
    screenshot_image_file,
    screenshot_image_path,
)
from core.video_hash import video_content_hash


def sync_screenshots(client: CloudClient, media_path: Path) -> bool:
    """把这部视频的截图说明和缺的图片补齐。本地文件有变化时返回 True。"""
    digest = video_content_hash(media_path)
    snapshot = load_screenshots(media_path)
    baseline = _deletion_baseline(client.username, digest, media_path)
    response = client.put_screenshots(
        digest,
        media_path.stem,
        [_cloud_shot(shot) for shot in snapshot.entries],
        baseline,
    )
    remote = _remote_shots(response)
    changed = _apply_remote(media_path, {shot.id for shot in snapshot.entries}, remote)
    current = load_screenshots(media_path)
    known = {str(shot.get("id") or "") for shot in remote}
    if _server_is_behind(current.entries, remote):
        response = client.put_screenshots(
            digest,
            media_path.stem,
            [_cloud_shot(shot) for shot in current.entries],
            sorted(shot_id for shot_id in known if shot_id),
        )
        remote = _remote_shots(response)
        changed = _apply_remote(media_path, {shot.id for shot in current.entries}, remote) or changed
        current = load_screenshots(media_path)
    remote_by_id = {str(shot.get("id") or ""): shot for shot in remote}
    if _fill_images(client, media_path, digest, current.entries, remote_by_id):
        changed = True
    record_screenshot_baseline(
        client.username,
        digest,
        [shot.id for shot in load_screenshots(media_path).entries],
        media_path,
    )
    return changed


def _deletion_baseline(username: str, video_hash: str, media_path: Path) -> list[str]:
    """只在本机还留着截图清单，或播放器里主动删光时，才通知云端删除。

    删掉整个配套文件夹后再打开，和字幕、标签一样，应当重新下载。
    这时本地没有 screenshots.json，不能把上次见过的编号当成删除。
    """
    ids = sorted(screenshot_baseline_ids(username, video_hash))
    if not ids:
        return []
    if screenshot_document_path(media_path).is_file():
        return ids
    if screenshot_catalog_was_cleared(username, video_hash, media_path):
        return ids
    return []


def _server_is_behind(shots: list[Screenshot], remote: list[dict]) -> bool:
    remote_by_id = {str(item.get("id") or ""): item for item in remote}
    for shot in shots:
        item = remote_by_id.get(shot.id)
        if item is None:
            return True
        try:
            remote_updated = int(item.get("updated_at"))
        except (TypeError, ValueError):
            return True
        if remote_updated < shot.updated_at:
            return True
    return False


def _remote_shots(payload: dict) -> list[dict]:
    shots = payload.get("shots") if isinstance(payload, dict) else None
    if not isinstance(shots, list):
        return []
    return [item for item in shots if isinstance(item, dict) and item.get("id")]


def _apply_remote(media_path: Path, snapshot_ids: set[str], remote: list[dict]) -> bool:
    """用云端合并结果更新说明，并保留同步过程中新截下的图。"""
    current = load_screenshots(media_path)
    current_by_id = {shot.id: shot for shot in current.entries}
    chosen: list[Screenshot] = []
    seen: set[str] = set()
    for item in remote:
        shot_id = str(item.get("id") or "")
        local = current_by_id.get(shot_id)
        parsed = _parse_remote_shot(item, local)
        if parsed is None:
            continue
        if local is not None and local.updated_at > parsed.updated_at:
            chosen.append(local)
        else:
            chosen.append(parsed)
        seen.add(parsed.id)
    for shot in current.entries:
        if shot.id not in seen:
            chosen.append(shot)
            seen.add(shot.id)
    previous_ids = {shot.id for shot in current.entries}
    removed = (previous_ids & snapshot_ids) - seen
    for shot_id in removed:
        delete_screenshot_images(media_path, shot_id)
    changed = _documents_differ(current.entries, chosen) or bool(removed)
    if changed:
        save_screenshots(media_path, ScreenshotDocument(entries=chosen))
    return changed


def _fill_images(
    client: CloudClient,
    media_path: Path,
    video_hash: str,
    shots: list[Screenshot],
    remote_by_id: dict[str, dict],
) -> bool:
    changed = False
    for shot in shots:
        remote = remote_by_id.get(shot.id) or {}
        has_remote = bool(remote.get("has_image"))
        image = screenshot_image_file(media_path, shot.id)
        if image is None and shot.frame is not None:
            png = screenshot_image_path(media_path, shot.id)
            if extract_video_frame(media_path, shot.time, png):
                image = png
                changed = True
        if image is None and has_remote:
            try:
                data = client.get_screenshot_image(video_hash, shot.id)
            except CloudError:
                data = b""
            if _write_webp(media_path, shot.id, data):
                image = screenshot_image_file(media_path, shot.id)
                changed = True
        if image is not None and image.suffix.lower() == ".png" and not has_remote:
            try:
                client.put_screenshot_image(video_hash, shot.id, compress_png_to_webp(image))
            except (CloudError, OSError):
                continue
    return changed


def _write_webp(media_path: Path, shot_id: str, data: bytes) -> bool:
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return False
    safe = _safe_id(shot_id)
    if not safe:
        return False
    folder = screenshot_dir(media_path)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{safe}.webp"
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(path)
    document = load_screenshots(media_path)
    for shot in document.entries:
        if shot.id == safe and shot.image != path.name:
            shot.image = path.name
            save_screenshots(media_path, document)
            break
    return True


def _parse_remote_shot(item: dict, local: Screenshot | None) -> Screenshot | None:
    parsed = _parse_screenshot(item)
    if parsed is None:
        return None
    if local is not None:
        parsed.image = local.image
    return parsed


def _documents_differ(previous: list[Screenshot], chosen: list[Screenshot]) -> bool:
    if [shot.id for shot in previous] != [shot.id for shot in chosen]:
        return True
    for old, new in zip(previous, chosen):
        if old.updated_at != new.updated_at or old.time != new.time or old.frame != new.frame:
            return True
        if old.title != new.title or not screenshot_content_matches(old, new.title, new.notes):
            return True
        if old.image != new.image:
            return True
    return False


def _cloud_shot(shot: Screenshot) -> dict:
    return {
        "id": shot.id,
        "title": shot.title,
        "time": shot.time,
        "frame": shot.frame,
        "created_at": shot.created_at,
        "updated_at": shot.updated_at,
        "notes": [_note_payload(note) for note in shot.notes],
    }
