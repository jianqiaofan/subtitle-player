"""把本地标签和云端标签按操作时间对齐。本地三项同步仍走原来的追加合并。"""

from __future__ import annotations

from core.subtitle_tags import (
    SubtitleTagDocument,
    SubtitleTagEntry,
    _find_merge_target,
    order_tags,
    tag_timestamp,
)


def document_from_server(payload: dict, subtitle_name: str) -> SubtitleTagDocument:
    document = SubtitleTagDocument(subtitle_file=subtitle_name)
    raw_entries = payload.get("entries") if isinstance(payload, dict) else None
    if not isinstance(raw_entries, list):
        return document
    for item in raw_entries:
        parsed = _entry_from_server(item)
        if parsed is not None:
            document.entries.append(parsed)
    return document


def build_upload_document(
    local: SubtitleTagDocument,
    baseline: SubtitleTagDocument | None,
) -> dict | None:
    """只上传和上次同步结果相比有变化的标签和备注。"""
    base_entries = list(baseline.entries) if baseline is not None else []
    claimed: set[str] = set()
    matched_ids: set[str] = set()
    entries: list[dict] = []
    for local_entry in local.entries:
        target = _find_merge_target(local_entry, base_entries, claimed) if base_entries else None
        if target is not None:
            claimed.add(target.id)
            matched_ids.add(target.id)
        payload = _diff_entry(local_entry, target)
        if payload is not None:
            entries.append(payload)
    for base_entry in base_entries:
        if base_entry.id in matched_ids or not base_entry.tags:
            continue
        payload = _diff_entry(_cleared_copy(base_entry), base_entry)
        if payload is not None:
            entries.append(payload)
    if not entries:
        return None
    return {
        "version": 2,
        "subtitle_file": local.subtitle_file,
        "entries": entries,
    }


def merge_server_document(local: SubtitleTagDocument, server_payload: dict) -> SubtitleTagDocument:
    """同一句、同一个标签名，时间新的那次操作生效。备注也以较新的编辑为准。"""
    incoming = document_from_server(server_payload, local.subtitle_file or "")
    merged = [_copy_entry(entry) for entry in local.entries if entry.should_keep()]
    claimed: set[str] = set()
    for remote in incoming.entries:
        target = _find_merge_target(remote, merged, claimed)
        if target is None:
            merged.append(_copy_entry(remote))
            continue
        _apply_remote(target, remote)
        claimed.add(target.id)
    local.entries = [entry for entry in merged if entry.should_keep()]
    return local


def server_has_visible_tags(payload: dict) -> bool:
    document = document_from_server(payload, "")
    return any(entry.tags or entry.note.strip() for entry in document.entries)


def _entry_from_server(item: object) -> SubtitleTagEntry | None:
    if not isinstance(item, dict):
        return None
    try:
        index = int(item.get("index"))
        start = float(item.get("start"))
        end = float(item.get("end"))
    except (TypeError, ValueError):
        return None
    ops = []
    raw_ops = item.get("tag_ops") or []
    if isinstance(raw_ops, list):
        for op in raw_ops:
            if not isinstance(op, dict):
                continue
            name = str(op.get("name") or "").strip()
            if not name or not isinstance(op.get("present"), bool):
                continue
            ops.append({"name": name, "present": op["present"], "at": str(op.get("at") or "")})
    visible = [op["name"] for op in ops if op["present"]]
    if not ops:
        raw_tags = item.get("tags") or []
        if isinstance(raw_tags, list):
            visible = [str(tag).strip() for tag in raw_tags if str(tag).strip()]
    return SubtitleTagEntry(
        id=str(item.get("id") or "").strip() or "remote",
        index=index,
        start=start,
        end=end,
        text=str(item.get("text") or ""),
        tags=order_tags(visible),
        note=str(item.get("note") or "").strip(),
        tag_ops=ops,
        note_at=str(item.get("note_at") or "").strip(),
    )


def _copy_entry(entry: SubtitleTagEntry) -> SubtitleTagEntry:
    return SubtitleTagEntry(
        id=entry.id,
        index=int(entry.index),
        start=float(entry.start),
        end=float(entry.end),
        text=entry.text or "",
        tags=order_tags(list(entry.tags)),
        note=(entry.note or "").strip(),
        tag_ops=[dict(op) for op in entry.tag_ops],
        note_at=entry.note_at or "",
    )


def _cleared_copy(entry: SubtitleTagEntry) -> SubtitleTagEntry:
    cleared = _copy_entry(entry)
    cleared.tags = []
    cleared.note = ""
    return cleared


def _diff_entry(local: SubtitleTagEntry, base: SubtitleTagEntry | None) -> dict | None:
    base_names = set(base.tags) if base is not None else set()
    local_names = set(local.tags)
    now = tag_timestamp()
    ops: list[dict] = []
    for name in local.tags:
        if name in base_names:
            continue
        ops.append({"name": name, "present": True, "at": _op_time(local, name, True) or now})
    for name in base.tags if base is not None else []:
        if name in local_names:
            continue
        ops.append({"name": name, "present": False, "at": _op_time(local, name, False) or now})
    note_changed = False
    if base is None:
        note_changed = bool((local.note or "").strip())
    elif (local.note or "") != (base.note or ""):
        note_changed = True
    if not ops and not note_changed:
        return None
    payload = {
        "id": local.id,
        "index": int(local.index),
        "start": float(local.start),
        "end": float(local.end),
        "text": local.text or "",
        "tag_ops": ops,
    }
    if note_changed:
        payload["note"] = local.note or ""
        payload["note_at"] = local.note_at or now
    return payload


def _op_time(entry: SubtitleTagEntry, name: str, present: bool) -> str:
    for op in entry.tag_ops:
        if op.get("name") == name and op.get("present") is present:
            return str(op.get("at") or "")
    return ""


def _apply_remote(target: SubtitleTagEntry, remote: SubtitleTagEntry) -> None:
    by_name = {
        str(op.get("name")): dict(op)
        for op in target.tag_ops
        if isinstance(op, dict) and op.get("name")
    }
    order = [str(op.get("name")) for op in target.tag_ops if isinstance(op, dict) and op.get("name")]
    for op in remote.tag_ops:
        name = str(op.get("name") or "")
        if not name:
            continue
        current = by_name.get(name)
        if current is None or _is_newer(str(current.get("at") or ""), str(op.get("at") or "")):
            if name not in by_name:
                order.append(name)
            by_name[name] = dict(op)
    target.tag_ops = [by_name[name] for name in order if name in by_name]
    target.tags = order_tags([op["name"] for op in target.tag_ops if op.get("present")])
    if remote.note_at and _is_newer(target.note_at, remote.note_at):
        target.note = remote.note or ""
        target.note_at = remote.note_at
    if remote.text:
        target.text = remote.text
        target.index = remote.index
        target.start = remote.start
        target.end = remote.end


def _is_newer(current: str, incoming: str) -> bool:
    if not incoming:
        return False
    if not current:
        return True
    return incoming >= current
