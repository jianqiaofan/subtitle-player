"""云同步标签：version 2。

每条标签记录名字、是否还在、最后一次勾选或取消的时间。
播放器只显示 present 为 true 的名字；取消勾选后记录仍留在文件里。
同一句、同一个标签名，时间新的那次操作生效。上传里没提到的标签视为没改过，不会被删掉。
备注以最后一次编辑为准，不把两段备注拼在一起。

句子对齐与桌面版 subtitle_app/core/subtitle_tags.py 相同：先看编号，再看时间和正文，再看序号和正文。
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher

from app.timeutil import iso_z, parse_utc

TAG_DOCUMENT_VERSION = 2
_TIME_TOLERANCE_MS = 1
_MAX_ENTRIES = 20_000
_MAX_OPS = 100
_MAX_TAG_NAME = 48
_MAX_TEXT = 20_000
_ENTRY_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")

# 与桌面版 PRESET_TAGS 的顺序一致，用来排列仍然显示的标签名。
PRESET_TAGS = (
    "重点",
    "难点",
    "易错",
    "新章节",
    "新页面",
    "重要断点",
    "已掌握",
    "待复习",
    "存疑",
    "真题",
    "得分点",
    "技巧",
    "必背",
    "口诀",
    "案例",
    "单词",
    "语法",
    "发音",
    "短语",
    "地道表达",
    "佳句",
    "反复练听",
    "跟读",
    "长难句",
    "俚语",
    "文化背景",
    "名场面",
)


class InvalidTagDocument(ValueError):
    pass


@dataclass
class TagOp:
    name: str
    present: bool
    at: datetime


@dataclass
class TagEntry:
    id: str
    index: int
    start: float
    end: float
    text: str
    ops: list[TagOp] = field(default_factory=list)
    note: str = ""
    note_at: datetime | None = None


@dataclass
class TagDocument:
    subtitle_file: str
    entries: list[TagEntry] = field(default_factory=list)
    version: int = TAG_DOCUMENT_VERSION


def new_entry_id() -> str:
    return uuid.uuid4().hex[:12]


def empty_document(subtitle_name: str) -> TagDocument:
    return TagDocument(subtitle_file=subtitle_name, entries=[])


def time_ms(seconds: float) -> int:
    return int(round(float(seconds) * 1000 + 1e-9))


def normalize_tag_text(text: str) -> str:
    return " ".join((text or "").replace("\r\n", "\n").replace("\n", " ").split())


def text_similarity(left: str, right: str) -> float:
    a = normalize_tag_text(left)
    b = normalize_tag_text(right)
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def text_close(left: str, right: str) -> bool:
    a = normalize_tag_text(left)
    b = normalize_tag_text(right)
    if a == b:
        return True
    if min(len(a), len(b)) < 8:
        return False
    return text_similarity(left, right) >= 0.82


def times_close(left: float, right: float) -> bool:
    return abs(time_ms(left) - time_ms(right)) <= _TIME_TOLERANCE_MS


def order_tags(tags: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for name in PRESET_TAGS:
        if name in tags and name not in seen:
            ordered.append(name)
            seen.add(name)
    for name in tags:
        if name not in seen:
            ordered.append(name)
            seen.add(name)
    return ordered


def visible_tag_names(ops: list[TagOp]) -> list[str]:
    return order_tags([op.name for op in ops if op.present])


def _find_merge_target(
    incoming: TagEntry,
    local_entries: list[TagEntry],
    claimed: set[str],
) -> TagEntry | None:
    pool = [entry for entry in local_entries if entry.id not in claimed]
    for entry in pool:
        if entry.id == incoming.id:
            return entry
    time_hits = [
        entry
        for entry in pool
        if times_close(entry.start, incoming.start) and text_close(entry.text, incoming.text)
    ]
    if len(time_hits) == 1:
        return time_hits[0]
    if len(time_hits) > 1:
        return max(time_hits, key=lambda entry: text_similarity(entry.text, incoming.text))
    index_hits = [
        entry
        for entry in pool
        if int(entry.index) == int(incoming.index) and text_close(entry.text, incoming.text)
    ]
    if len(index_hits) == 1:
        return index_hits[0]
    key = normalize_tag_text(incoming.text)
    if not key:
        return None
    text_hits = [entry for entry in pool if normalize_tag_text(entry.text) == key]
    if len(text_hits) == 1:
        return text_hits[0]
    return None


def _collapse_ops(ops: list[TagOp]) -> list[TagOp]:
    order: list[str] = []
    by_name: dict[str, TagOp] = {}
    for op in ops:
        current = by_name.get(op.name)
        if current is None:
            order.append(op.name)
            by_name[op.name] = op
            continue
        if op.at >= current.at:
            by_name[op.name] = op
    return [by_name[name] for name in order]


def _merge_ops(stored: list[TagOp], incoming: list[TagOp]) -> tuple[list[TagOp], bool]:
    order = [op.name for op in stored]
    by_name = {op.name: op for op in stored}
    changed = False
    for op in incoming:
        current = by_name.get(op.name)
        if current is None:
            order.append(op.name)
            by_name[op.name] = op
            changed = True
            continue
        if op.at > current.at or (op.at == current.at and op.present != current.present):
            by_name[op.name] = op
            changed = True
    return [by_name[name] for name in order], changed


def _clone_entry(entry: TagEntry, entry_id: str | None = None) -> TagEntry:
    return TagEntry(
        id=entry_id or entry.id,
        index=entry.index,
        start=entry.start,
        end=entry.end,
        text=entry.text,
        ops=_collapse_ops(list(entry.ops)),
        note=entry.note,
        note_at=entry.note_at,
    )


def _entry_keeps_history(entry: TagEntry) -> bool:
    return bool(entry.ops) or entry.note_at is not None


def _apply_incoming(target: TagEntry, incoming: TagEntry) -> None:
    merged_ops, ops_changed = _merge_ops(target.ops, incoming.ops)
    note_changed = False
    if incoming.note_at is not None and (target.note_at is None or incoming.note_at >= target.note_at):
        if target.note != incoming.note or target.note_at != incoming.note_at:
            note_changed = True
        target.note = incoming.note
        target.note_at = incoming.note_at
    target.ops = merged_ops
    if ops_changed or note_changed:
        target.index = incoming.index
        target.start = incoming.start
        target.end = incoming.end
        target.text = incoming.text


def merge_documents(stored: TagDocument, incoming: TagDocument) -> TagDocument:
    """把这次上传并入云端。上传中没有出现的标签保持原样。"""
    merged = [_clone_entry(entry) for entry in stored.entries if _entry_keeps_history(entry)]
    claimed: set[str] = set()
    used_ids = {entry.id for entry in merged}
    for incoming_entry in incoming.entries:
        if not incoming_entry.ops and incoming_entry.note_at is None:
            continue
        target = _find_merge_target(incoming_entry, merged, claimed)
        if target is not None:
            _apply_incoming(target, incoming_entry)
            claimed.add(target.id)
            continue
        entry_id = incoming_entry.id if incoming_entry.id not in used_ids else new_entry_id()
        created = _clone_entry(incoming_entry, entry_id)
        merged.append(created)
        used_ids.add(created.id)
    return TagDocument(
        subtitle_file=stored.subtitle_file or incoming.subtitle_file,
        entries=[entry for entry in merged if _entry_keeps_history(entry)],
    )


def document_to_dict(document: TagDocument) -> dict:
    return {
        "version": TAG_DOCUMENT_VERSION,
        "subtitle_file": document.subtitle_file,
        "entries": [_entry_to_dict(entry) for entry in document.entries],
    }


def _entry_to_dict(entry: TagEntry) -> dict:
    payload = {
        "id": entry.id,
        "index": entry.index,
        "start": entry.start,
        "end": entry.end,
        "text": entry.text,
        "tags": visible_tag_names(entry.ops),
        "tag_ops": [
            {"name": op.name, "present": op.present, "at": iso_z(op.at)}
            for op in entry.ops
        ],
        "note": entry.note,
    }
    if entry.note_at is not None:
        payload["note_at"] = iso_z(entry.note_at)
    return payload


def parse_document(payload: object, subtitle_name: str) -> TagDocument:
    if not isinstance(payload, dict):
        raise InvalidTagDocument("标签文档必须是 JSON 对象")
    if payload.get("version") != TAG_DOCUMENT_VERSION:
        raise InvalidTagDocument("标签文档 version 必须为 2，这样删除记录才会保留")
    recorded = payload.get("subtitle_file")
    if recorded != subtitle_name:
        raise InvalidTagDocument("subtitle_file 必须与字幕文件名一致")
    raw_entries = payload.get("entries", [])
    if not isinstance(raw_entries, list):
        raise InvalidTagDocument("entries 必须是数组")
    if len(raw_entries) > _MAX_ENTRIES:
        raise InvalidTagDocument("标签条目过多")
    entries = [_parse_entry(item) for item in raw_entries]
    return TagDocument(subtitle_file=subtitle_name, entries=entries)


def _parse_entry(item: object) -> TagEntry:
    if not isinstance(item, dict):
        raise InvalidTagDocument("标签条目必须是对象")
    try:
        index = int(item["index"])
        start = float(item["start"])
        end = float(item["end"])
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidTagDocument("标签条目缺少有效的序号或时间") from exc
    if isinstance(item.get("index"), bool) or isinstance(item.get("start"), bool) or isinstance(item.get("end"), bool):
        raise InvalidTagDocument("标签条目的序号或时间无效")
    if index < 0 or index > 1_000_000 or start < 0 or end < start or end > 10_000_000:
        raise InvalidTagDocument("标签条目的序号或时间超出范围")
    if start != start or end != end:
        raise InvalidTagDocument("标签条目的时间无效")
    text = item.get("text", "")
    if not isinstance(text, str) or len(text) > _MAX_TEXT:
        raise InvalidTagDocument("字幕正文无效")
    raw_id = item.get("id")
    if raw_id in (None, ""):
        entry_id = new_entry_id()
    elif isinstance(raw_id, str) and _ENTRY_ID.fullmatch(raw_id):
        entry_id = raw_id
    else:
        raise InvalidTagDocument("标签条目 id 无效")
    ops = _parse_ops(item.get("tag_ops", []))
    raw_tags = item.get("tags")
    if isinstance(raw_tags, list) and any(isinstance(tag, str) and tag.strip() for tag in raw_tags) and not ops:
        raise InvalidTagDocument("请用 tag_ops 提交标签操作，不要只提交 tags")
    note, note_at = _parse_note(item)
    return TagEntry(
        id=entry_id,
        index=index,
        start=start,
        end=end,
        text=text,
        ops=ops,
        note=note,
        note_at=note_at,
    )


def _parse_ops(raw_ops: object) -> list[TagOp]:
    if raw_ops is None:
        raw_ops = []
    if not isinstance(raw_ops, list):
        raise InvalidTagDocument("tag_ops 必须是数组")
    if len(raw_ops) > _MAX_OPS:
        raise InvalidTagDocument("同一句的标签过多")
    ops: list[TagOp] = []
    for item in raw_ops:
        if not isinstance(item, dict):
            raise InvalidTagDocument("标签操作必须是对象")
        name = item.get("name")
        present = item.get("present")
        if not isinstance(name, str) or not isinstance(present, bool):
            raise InvalidTagDocument("标签操作需要名字和 present")
        cleaned = name.strip()
        if not cleaned or cleaned != name or len(cleaned) > _MAX_TAG_NAME or "\n" in cleaned or "\r" in cleaned:
            raise InvalidTagDocument("标签名无效")
        try:
            at = parse_utc(item.get("at"), "标签操作时间")
        except ValueError as exc:
            raise InvalidTagDocument(str(exc)) from exc
        ops.append(TagOp(name=cleaned, present=present, at=at))
    return _collapse_ops(ops)


def _parse_note(item: dict) -> tuple[str, datetime | None]:
    if "note" not in item and "note_at" not in item:
        return "", None
    note = item.get("note", "")
    if not isinstance(note, str) or len(note) > _MAX_TEXT:
        raise InvalidTagDocument("备注无效")
    note = note.strip()
    raw_at = item.get("note_at")
    if raw_at in (None, ""):
        if note:
            raise InvalidTagDocument("有备注时必须带 note_at")
        return "", None
    try:
        note_at = parse_utc(raw_at, "备注时间")
    except ValueError as exc:
        raise InvalidTagDocument(str(exc)) from exc
    return note, note_at
