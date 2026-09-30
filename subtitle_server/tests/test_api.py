from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("JWT_SECRET", "unit-test-secret-unit-test-secret")
os.environ.setdefault("DATA_DIR", os.environ.get("TEMP", "/tmp"))

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

CHINESE = "1\n00:00:01,000 --> 00:00:02,000\n中文\n"
ENGLISH = "1\n00:00:01,000 --> 00:00:02,000\nEnglish\n"
VIDEO_HASH = "a" * 64
OTHER_HASH = "b" * 64
MONDAY = "2026-09-21T01:00:00Z"
TUESDAY = "2026-09-22T01:00:00Z"
WEDNESDAY = "2026-09-23T01:00:00Z"


def make_client(tmp_path):
    settings = Settings(
        database_url="sqlite://",
        data_dir=tmp_path,
        jwt_secret="unit-test-secret-unit-test-secret",
        password_rounds=1_000,
    )
    return TestClient(create_app(settings))


def register(client: TestClient, username: str, password: str = "password-1") -> str:
    response = client.post("/api/auth/register", json={"username": username, "password": password})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["username"] == username
    assert "pbkdf2" not in response.text
    assert password not in response.text
    return body["access_token"]


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def tag_body(subtitle_name: str, ops: list[dict], video_stem: str = "第1课", **entry) -> dict:
    row = {
        "id": entry.get("id", "sentence1"),
        "index": entry.get("index", 1),
        "start": entry.get("start", 1.0),
        "end": entry.get("end", 2.0),
        "text": entry.get("text", "同一句"),
        "tag_ops": ops,
        "note": entry.get("note", ""),
    }
    if "note_at" in entry:
        row["note_at"] = entry["note_at"]
    return {
        "video_hash": VIDEO_HASH,
        "video_stem": video_stem,
        "subtitle_name": subtitle_name,
        "document": {"version": 2, "subtitle_file": subtitle_name, "entries": [row]},
    }


def test_register_login_and_reject_bad_password(tmp_path):
    with make_client(tmp_path) as client:
        token = register(client, "张三丰")
        me = client.get("/api/auth/me", headers=auth(token))
        assert me.status_code == 200
        assert me.json()["username"] == "张三丰"
        login = client.post("/api/auth/login", json={"username": "张三丰", "password": "password-1"})
        assert login.status_code == 200
        wrong = client.post("/api/auth/login", json={"username": "张三丰", "password": "wrong-password"})
        assert wrong.status_code == 401
        duplicate = client.post("/api/auth/register", json={"username": "张三丰", "password": "password-1"})
        assert duplicate.status_code == 409
        short = client.post("/api/auth/register", json={"username": "ab", "password": "password-1"})
        assert short.status_code == 400
        assert client.put(
            "/api/subtitles",
            json={"video_hash": VIDEO_HASH, "video_stem": "第1课", "subtitle_name": "第1课.srt", "content": "x"},
        ).status_code == 401


def test_subtitle_overwrite_keeps_other_languages_and_other_users(tmp_path):
    with make_client(tmp_path) as client:
        alice = register(client, "alice_1")
        bob = register(client, "bob_user")
        first = client.put(
            "/api/subtitles",
            headers=auth(alice),
            json={"video_hash": VIDEO_HASH, "video_stem": "第1课", "subtitle_name": "第1课_中文.srt", "content": CHINESE},
        )
        assert first.status_code == 200, first.text
        english = client.put(
            "/api/subtitles",
            headers=auth(alice),
            json={"video_hash": VIDEO_HASH, "video_stem": "第1课", "subtitle_name": "第1课_英文.srt", "content": ENGLISH},
        )
        assert english.status_code == 200, english.text
        replaced = client.put(
            "/api/subtitles",
            headers=auth(alice),
            json={"video_hash": VIDEO_HASH, "video_stem": "第1课", "subtitle_name": "第1课_中文.srt", "content": CHINESE + "第二版\n"},
        )
        assert replaced.status_code == 200
        assert replaced.json()["content_hash"] != first.json()["content_hash"]
        bundle = client.get("/api/sync", headers=auth(alice), params={"video_hash": VIDEO_HASH})
        assert bundle.status_code == 200, bundle.text
        by_name = {item["subtitle_name"]: item["content"] for item in bundle.json()["subtitles"]}
        assert by_name["第1课_中文.srt"] == CHINESE + "第二版\n"
        assert by_name["第1课_英文.srt"] == ENGLISH
        bob_put = client.put(
            "/api/subtitles",
            headers=auth(bob),
            json={"video_hash": VIDEO_HASH, "video_stem": "第1课", "subtitle_name": "第1课_中文.srt", "content": "bob"},
        )
        assert bob_put.status_code == 200
        alice_again = client.get("/api/sync", headers=auth(alice), params={"video_hash": VIDEO_HASH})
        alice_names = {item["subtitle_name"]: item["content"] for item in alice_again.json()["subtitles"]}
        assert alice_names["第1课_中文.srt"] == CHINESE + "第二版\n"
        bob_sync = client.get("/api/sync", headers=auth(bob), params={"video_hash": VIDEO_HASH})
        assert [item["content"] for item in bob_sync.json()["subtitles"]] == ["bob"]
        rejected = client.put(
            "/api/subtitles",
            headers=auth(alice),
            json={"video_hash": VIDEO_HASH, "video_stem": "第1课", "subtitle_name": "../第1课.srt", "content": "x"},
        )
        assert rejected.status_code == 400
        mismatch = client.put(
            "/api/subtitles",
            headers=auth(alice),
            json={"video_hash": VIDEO_HASH, "video_stem": "第1课", "subtitle_name": "别的课.srt", "content": "x"},
        )
        assert mismatch.status_code == 400


def test_tag_delete_survives_older_upload_and_empty_follow_up(tmp_path):
    with make_client(tmp_path) as client:
        token = register(client, "tag_user")
        headers = auth(token)
        created = client.put(
            "/api/tags",
            headers=headers,
            json=tag_body(
                "第1课.srt",
                [
                    {"name": "重点", "present": True, "at": MONDAY},
                    {"name": "难点", "present": True, "at": MONDAY},
                ],
            ),
        )
        assert created.status_code == 200, created.text
        deleted = client.put(
            "/api/tags",
            headers=headers,
            json=tag_body("第1课.srt", [{"name": "难点", "present": False, "at": TUESDAY}]),
        )
        assert deleted.status_code == 200, deleted.text
        visible = deleted.json()["document"]["entries"][0]
        assert visible["tags"] == ["重点"]
        assert any(op["name"] == "难点" and op["present"] is False for op in visible["tag_ops"])
        older = client.put(
            "/api/tags",
            headers=headers,
            json=tag_body("第1课.srt", [{"name": "难点", "present": True, "at": MONDAY}]),
        )
        assert older.json()["document"]["entries"][0]["tags"] == ["重点"]
        restored = client.put(
            "/api/tags",
            headers=headers,
            json=tag_body("第1课.srt", [{"name": "难点", "present": True, "at": WEDNESDAY}]),
        )
        assert restored.json()["document"]["entries"][0]["tags"] == ["重点", "难点"]
        empty = client.put(
            "/api/tags",
            headers=headers,
            json={
                "video_hash": VIDEO_HASH,
                "video_stem": "第1课",
                "subtitle_name": "第1课.srt",
                "document": {"version": 2, "subtitle_file": "第1课.srt", "entries": []},
            },
        )
        assert empty.status_code == 200, empty.text
        synced = client.get("/api/sync", headers=headers, params={"video_hash": VIDEO_HASH})
        entry = synced.json()["tags"][0]["document"]["entries"][0]
        assert entry["tags"] == ["重点", "难点"]
        legacy = client.put(
            "/api/tags",
            headers=headers,
            json={
                "video_hash": VIDEO_HASH,
                "video_stem": "第1课",
                "subtitle_name": "第1课.srt",
                "document": {
                    "version": 1,
                    "subtitle_file": "第1课.srt",
                    "entries": [],
                },
            },
        )
        assert legacy.status_code == 400


def test_same_hash_shares_subtitle_after_rename_and_other_hash_stays_separate(tmp_path):
    with make_client(tmp_path) as client:
        token = register(client, "hash_user")
        headers = auth(token)
        original = client.put(
            "/api/subtitles",
            headers=headers,
            json={
                "video_hash": VIDEO_HASH,
                "video_stem": "第1课",
                "subtitle_name": "第1课_中文.srt",
                "content": CHINESE,
            },
        )
        assert original.status_code == 200, original.text
        assert original.json()["subtitle_suffix"] == "_中文.srt"
        renamed = client.put(
            "/api/subtitles",
            headers=headers,
            json={
                "video_hash": VIDEO_HASH,
                "video_stem": "复习",
                "subtitle_name": "复习_中文.srt",
                "content": CHINESE + "改名后\n",
            },
        )
        assert renamed.status_code == 200, renamed.text
        bundle = client.get("/api/sync", headers=headers, params={"video_hash": VIDEO_HASH})
        subtitles = bundle.json()["subtitles"]
        assert len(subtitles) == 1
        assert subtitles[0]["subtitle_suffix"] == "_中文.srt"
        assert subtitles[0]["content"] == CHINESE + "改名后\n"
        other = client.put(
            "/api/subtitles",
            headers=headers,
            json={
                "video_hash": OTHER_HASH,
                "video_stem": "第1课",
                "subtitle_name": "第1课_中文.srt",
                "content": "另一部视频",
            },
        )
        assert other.status_code == 200, other.text
        first = client.get("/api/sync", headers=headers, params={"video_hash": VIDEO_HASH})
        second = client.get("/api/sync", headers=headers, params={"video_hash": OTHER_HASH})
        assert first.json()["subtitles"][0]["content"] == CHINESE + "改名后\n"
        assert second.json()["subtitles"][0]["content"] == "另一部视频"


def test_shared_subtitle_is_visible_to_other_users_of_the_same_video(tmp_path):
    with make_client(tmp_path) as client:
        alice = register(client, "alice_share")
        bob = register(client, "bob_share")
        private = client.put(
            "/api/subtitles",
            headers=auth(alice),
            json={
                "video_hash": VIDEO_HASH,
                "video_stem": "电影",
                "subtitle_name": "电影.srt",
                "content": "不公开",
                "shared": False,
            },
        )
        assert private.status_code == 200, private.text
        hidden = client.get("/api/shares", headers=auth(bob), params={"video_hash": VIDEO_HASH})
        assert hidden.status_code == 200, hidden.text
        assert hidden.json()["shares"] == []
        shared = client.put(
            "/api/subtitles",
            headers=auth(alice),
            json={
                "video_hash": VIDEO_HASH,
                "video_stem": "电影",
                "subtitle_name": "电影_中文.srt",
                "content": CHINESE,
                "shared": True,
            },
        )
        assert shared.status_code == 200, shared.text
        listed = client.get("/api/shares", headers=auth(bob), params={"video_hash": VIDEO_HASH})
        shares = listed.json()["shares"]
        assert [item["username"] for item in shares] == ["alice_share"]
        assert "content" not in shares[0]["subtitles"][0]
        assert shares[0]["subtitles"][0]["subtitle_suffix"] == "_中文.srt"
        own = client.get("/api/shares", headers=auth(alice), params={"video_hash": VIDEO_HASH})
        assert own.json()["shares"] == []
        other_video = client.get("/api/shares", headers=auth(bob), params={"video_hash": OTHER_HASH})
        assert other_video.json()["shares"] == []
        body = client.get(
            "/api/shares",
            headers=auth(bob),
            params={"video_hash": VIDEO_HASH, "username": "alice_share"},
        )
        assert body.status_code == 200, body.text
        assert body.json()["shares"][0]["subtitles"][0]["content"] == CHINESE
        refused = client.get(
            "/api/shares",
            headers=auth(bob),
            params={"video_hash": VIDEO_HASH, "username": "不存在的人"},
        )
        assert refused.status_code == 404
        client.put(
            "/api/subtitles",
            headers=auth(alice),
            json={
                "video_hash": VIDEO_HASH,
                "video_stem": "电影",
                "subtitle_name": "电影_中文.srt",
                "content": CHINESE,
                "shared": False,
            },
        )
        after = client.get("/api/shares", headers=auth(bob), params={"video_hash": VIDEO_HASH})
        assert after.json()["shares"] == []


def test_library_lists_edits_share_flag_and_deletes_only_own_files(tmp_path):
    with make_client(tmp_path) as client:
        alice = register(client, "alice_lib")
        bob = register(client, "bob_lib")
        created = client.put(
            "/api/subtitles",
            headers=auth(alice),
            json={
                "video_hash": VIDEO_HASH,
                "video_stem": "电影",
                "subtitle_name": "电影.srt",
                "content": CHINESE,
                "shared": False,
            },
        )
        assert created.status_code == 200, created.text
        tagged = client.put("/api/tags", headers=auth(alice), json=tag_body("电影.srt", [{"name": "重点", "present": True, "at": MONDAY}], video_stem="电影"))
        assert tagged.status_code == 200, tagged.text
        listed = client.get("/api/library/subtitles", headers=auth(alice))
        assert listed.status_code == 200, listed.text
        rows = listed.json()["subtitles"]
        assert len(rows) == 1
        assert rows[0]["subtitle_name"] == "电影.srt"
        assert rows[0]["shared"] is False
        item_id = rows[0]["id"]
        detail = client.get(f"/api/library/subtitles/{item_id}", headers=auth(alice))
        assert detail.json()["content"] == CHINESE
        opened = client.patch(f"/api/library/subtitles/{item_id}", headers=auth(alice), json={"shared": True})
        assert opened.status_code == 200, opened.text
        assert opened.json()["shared"] is True
        visible = client.get("/api/shares", headers=auth(bob), params={"video_hash": VIDEO_HASH})
        assert visible.json()["shares"][0]["username"] == "alice_lib"
        forbidden = client.delete(f"/api/library/subtitles/{item_id}", headers=auth(bob))
        assert forbidden.status_code == 404
        tags = client.get("/api/library/tags", headers=auth(alice))
        assert tags.status_code == 200, tags.text
        tag_id = tags.json()["tags"][0]["id"]
        tag_detail = client.get(f"/api/library/tags/{tag_id}", headers=auth(alice))
        assert tag_detail.json()["document"]["entries"][0]["tags"] == ["重点"]
        removed_tag = client.delete(f"/api/library/tags/{tag_id}", headers=auth(alice))
        assert removed_tag.status_code == 200
        assert client.get("/api/library/tags", headers=auth(alice)).json()["tags"] == []
        removed = client.delete(f"/api/library/subtitles/{item_id}", headers=auth(alice))
        assert removed.status_code == 200
        assert client.get("/api/library/subtitles", headers=auth(alice)).json()["subtitles"] == []
        assert client.get("/api/shares", headers=auth(bob), params={"video_hash": VIDEO_HASH}).json()["shares"] == []


def test_playback_log_appends_for_the_owner_only(tmp_path):
    with make_client(tmp_path) as client:
        alice = register(client, "alice_play")
        bob = register(client, "bob_play")
        first = {
            "id": "a" * 32,
            "started_at": 1_758_000_000_000,
            "ended_at": 1_758_000_060_000,
        }
        created = client.put(
            "/api/playback",
            headers=auth(alice),
            json={"video_hash": VIDEO_HASH, "video_stem": "电脑", "sessions": [first]},
        )
        assert created.status_code == 200, created.text
        assert created.json()["sessions"] == [first]
        again = client.put(
            "/api/playback",
            headers=auth(alice),
            json={
                "video_hash": VIDEO_HASH,
                "video_stem": "电脑",
                "sessions": [
                    first,
                    {"id": "b" * 32, "started_at": 1_758_000_100_000, "ended_at": 1_758_000_130_000},
                ],
            },
        )
        assert again.status_code == 200, again.text
        assert [item["id"] for item in again.json()["sessions"]] == ["b" * 32, "a" * 32]
        assert again.json()["sessions"][1] == first
        hidden = client.get("/api/playback", headers=auth(bob), params={"video_hash": VIDEO_HASH})
        assert hidden.status_code == 200
        assert hidden.json()["sessions"] == []
        own = client.get("/api/playback", headers=auth(alice), params={"video_hash": VIDEO_HASH})
        assert len(own.json()["sessions"]) == 2
        bad = client.put(
            "/api/playback",
            headers=auth(alice),
            json={"video_hash": VIDEO_HASH, "video_stem": "电脑", "sessions": [{"id": "c" * 32, "started_at": 10, "ended_at": 20}]},
        )
        assert bad.status_code == 400


def _shot(shot_id: str, updated_at: int, title: str = "画面", frame: int | None = 12) -> dict:
    return {
        "id": shot_id,
        "title": title,
        "time": 3.5,
        "frame": frame,
        "created_at": 1_700_000_000_000,
        "updated_at": updated_at,
        "notes": [
            {
                "id": "b" * 12,
                "text": "笔记",
                "created_at": 1_700_000_000_000,
                "updated_at": updated_at,
                "box": {"x": 0.2, "y": 0.3, "width": 0.4, "height": 0.2, "font": 0.06, "align": "center"},
            }
        ],
    }


def _webp() -> bytes:
    body = b"WEBPTEST"
    return b"RIFF" + len(body).to_bytes(4, "little") + body


def test_screenshot_merge_keeps_newer_notes_and_deletes_only_baseline(tmp_path):
    with make_client(tmp_path) as client:
        alice = register(client, "shot_alice")
        bob = register(client, "shot_bob")
        first = client.put(
            "/api/screenshots",
            headers=auth(alice),
            json={
                "video_hash": VIDEO_HASH,
                "video_stem": "第1课",
                "shots": [_shot("a" * 12, 20, "新标题"), _shot("c" * 12, 20, "保留")],
                "baseline_ids": [],
            },
        )
        assert first.status_code == 200, first.text
        assert [item["title"] for item in first.json()["shots"]] == ["新标题", "保留"]
        older = client.put(
            "/api/screenshots",
            headers=auth(alice),
            json={
                "video_hash": VIDEO_HASH,
                "video_stem": "第1课",
                "shots": [_shot("a" * 12, 10, "旧标题")],
                "baseline_ids": ["a" * 12],
            },
        )
        assert older.status_code == 200, older.text
        titles = {item["id"]: item["title"] for item in older.json()["shots"]}
        assert titles["a" * 12] == "新标题"
        assert titles["c" * 12] == "保留"
        removed = client.put(
            "/api/screenshots",
            headers=auth(alice),
            json={
                "video_hash": VIDEO_HASH,
                "video_stem": "第1课",
                "shots": [_shot("a" * 12, 20, "新标题")],
                "baseline_ids": ["a" * 12, "c" * 12],
            },
        )
        assert [item["id"] for item in removed.json()["shots"]] == ["a" * 12]
        hidden = client.get("/api/screenshots", headers=auth(bob), params={"video_hash": VIDEO_HASH})
        assert hidden.json()["shots"] == []


def test_screenshot_image_is_private_and_not_replaced(tmp_path):
    with make_client(tmp_path) as client:
        alice = register(client, "img_alice")
        bob = register(client, "img_bob")
        created = client.put(
            "/api/screenshots",
            headers=auth(alice),
            json={
                "video_hash": VIDEO_HASH,
                "video_stem": "第1课",
                "shots": [_shot("d" * 12, 30, frame=8)],
                "baseline_ids": [],
            },
        )
        assert created.status_code == 200
        image = _webp()
        uploaded = client.put(
            f"/api/screenshots/{VIDEO_HASH}/{'d' * 12}/image",
            headers={**auth(alice), "Content-Type": "image/webp"},
            content=image,
        )
        assert uploaded.status_code == 200, uploaded.text
        assert uploaded.json()["has_image"] is True
        replaced = client.put(
            f"/api/screenshots/{VIDEO_HASH}/{'d' * 12}/image",
            headers={**auth(alice), "Content-Type": "image/webp"},
            content=_webp() + b"MORE",
        )
        assert replaced.json()["image_hash"] == uploaded.json()["image_hash"]
        downloaded = client.get(
            f"/api/screenshots/{VIDEO_HASH}/{'d' * 12}/image",
            headers=auth(alice),
        )
        assert downloaded.status_code == 200
        assert downloaded.content == image
        forbidden = client.get(
            f"/api/screenshots/{VIDEO_HASH}/{'d' * 12}/image",
            headers=auth(bob),
        )
        assert forbidden.status_code == 404
        manifest = tmp_path / "users" / "1" / "screenshots" / VIDEO_HASH / "screenshots.json"
        assert manifest.is_file()
        assert (manifest.parent / f"{'d' * 12}.webp").read_bytes() == image
