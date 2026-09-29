from __future__ import annotations

from app.tagmerge import (
    InvalidTagDocument,
    TagDocument,
    TagEntry,
    TagOp,
    merge_documents,
    parse_document,
    visible_tag_names,
)
from app.timeutil import parse_utc

NAME = "第1课.srt"
MONDAY = "2026-09-21T01:00:00Z"
TUESDAY = "2026-09-22T01:00:00Z"
WEDNESDAY = "2026-09-23T01:00:00Z"


def at(text: str):
    return parse_utc(text, "时间")


def entry(ops: list[TagOp], **kwargs) -> TagEntry:
    return TagEntry(
        id=kwargs.get("id", "sentence1"),
        index=kwargs.get("index", 1),
        start=kwargs.get("start", 1.0),
        end=kwargs.get("end", 2.0),
        text=kwargs.get("text", "同一句"),
        ops=ops,
        note=kwargs.get("note", ""),
        note_at=kwargs.get("note_at"),
    )


def document(*entries: TagEntry) -> TagDocument:
    return TagDocument(subtitle_file=NAME, entries=list(entries))


def present_names(result: TagDocument) -> list[str]:
    return visible_tag_names(result.entries[0].ops)


def test_newer_delete_beats_older_add():
    stored = document(entry([TagOp("难点", False, at(TUESDAY))]))
    incoming = document(entry([TagOp("难点", True, at(MONDAY))]))
    assert present_names(merge_documents(stored, incoming)) == []


def test_later_recheck_restores_tag():
    stored = document(entry([TagOp("难点", False, at(TUESDAY))]))
    incoming = document(entry([TagOp("难点", True, at(WEDNESDAY))]))
    assert present_names(merge_documents(stored, incoming)) == ["难点"]


def test_omitted_tag_is_not_deleted():
    stored = document(
        entry(
            [
                TagOp("重点", True, at(MONDAY)),
                TagOp("难点", False, at(TUESDAY)),
            ]
        )
    )
    incoming = document(entry([TagOp("易错", True, at(WEDNESDAY))]))
    names = present_names(merge_documents(stored, incoming))
    assert names == ["重点", "易错"]


def test_old_upload_does_not_resurrect_deleted_tag():
    stored = document(entry([TagOp("难点", False, at(TUESDAY)), TagOp("重点", True, at(MONDAY))]))
    incoming = document(entry([TagOp("难点", True, at(MONDAY))]))
    assert present_names(merge_documents(stored, incoming)) == ["重点"]


def test_newer_note_replaces_and_clear_sticks():
    stored = document(entry([], note="旧备注", note_at=at(MONDAY)))
    cleared = merge_documents(stored, document(entry([], note="", note_at=at(TUESDAY))))
    assert cleared.entries[0].note == ""
    assert cleared.entries[0].note_at == at(TUESDAY)
    resurrect = merge_documents(cleared, document(entry([], note="旧备注", note_at=at(MONDAY))))
    assert resurrect.entries[0].note == ""


def test_older_edit_does_not_overwrite_sentence_text():
    stored = document(entry([TagOp("重点", True, at(TUESDAY))], text="新句子"))
    incoming = document(entry([TagOp("重点", True, at(MONDAY))], text="旧句子"))
    assert merge_documents(stored, incoming).entries[0].text == "新句子"


def test_same_sentence_with_different_id_merges():
    stored = document(entry([TagOp("重点", True, at(MONDAY))], id="aaaa"))
    incoming = document(entry([TagOp("难点", True, at(TUESDAY))], id="bbbb"))
    merged = merge_documents(stored, incoming)
    assert len(merged.entries) == 1
    assert merged.entries[0].id == "aaaa"
    assert present_names(merged) == ["重点", "难点"]


def test_different_sentence_stays_separate():
    stored = document(entry([TagOp("重点", True, at(MONDAY))], text="第一句", id="a"))
    incoming = document(entry([TagOp("难点", True, at(TUESDAY))], text="第二句", id="b", index=2, start=5, end=6))
    merged = merge_documents(stored, incoming)
    assert len(merged.entries) == 2
    assert visible_tag_names(merged.entries[0].ops) == ["重点"]
    assert visible_tag_names(merged.entries[1].ops) == ["难点"]


def test_version_1_and_tags_only_are_rejected():
    try:
        parse_document({"version": 1, "subtitle_file": NAME, "entries": []}, NAME)
    except InvalidTagDocument as exc:
        assert "version" in str(exc)
    else:
        raise AssertionError("version 1 should be rejected")
    try:
        parse_document(
            {
                "version": 2,
                "subtitle_file": NAME,
                "entries": [
                    {
                        "index": 1,
                        "start": 1,
                        "end": 2,
                        "text": "句子",
                        "tags": ["难点"],
                    }
                ],
            },
            NAME,
        )
    except InvalidTagDocument as exc:
        assert "tag_ops" in str(exc)
    else:
        raise AssertionError("tags without tag_ops should be rejected")
