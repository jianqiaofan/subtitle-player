#!/bin/bash
set -euo pipefail

SRC="${1:-/tmp/subtitle-sync-src}"
APP=/opt/subtitle-sync
DATA=/var/lib/subtitle-sync

cleanup_check_users() {
  ids="$(sudo mysql -N --batch -e "SELECT id FROM subtitleDB.users WHERE username IN ('synccheck','synccheckb')" 2>/dev/null || true)"
  sudo mysql --batch -e "DELETE FROM subtitleDB.users WHERE username IN ('synccheck','synccheckb');" >/dev/null 2>&1 || true
  for id in $ids; do
    sudo rm -rf "$DATA/users/$id"
  done
}

sudo mkdir -p "$APP" "$DATA"
if ! id subtitle-sync >/dev/null 2>&1; then
  sudo useradd --system --home "$DATA" --shell /usr/sbin/nologin subtitle-sync
fi

if ! command -v rsync >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y rsync
fi
sudo rsync -a --delete --exclude .venv "$SRC"/ "$APP"/
sudo find "$APP" -type f -exec sed -i 's/\r$//' {} +

if ! "$APP/.venv/bin/python" -c "import fastapi,jwt,pymysql,sqlalchemy" >/dev/null 2>&1; then
  if ! python3 -c "import ensurepip" >/dev/null 2>&1; then
    sudo apt-get update
    sudo apt-get install -y python3-venv
  fi
  sudo python3 -m venv "$APP/.venv"
  sudo "$APP/.venv/bin/pip" install --upgrade pip
  sudo "$APP/.venv/bin/pip" install -r "$APP/requirements.txt"
fi

PASS="$(openssl rand -hex 24)"
JWT="$(openssl rand -hex 32)"
sudo mysql --batch <<SQL
CREATE DATABASE IF NOT EXISTS subtitleDB CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE USER IF NOT EXISTS 'subtitle_sync'@'127.0.0.1' IDENTIFIED BY '${PASS}';
ALTER USER 'subtitle_sync'@'127.0.0.1' IDENTIFIED BY '${PASS}';
GRANT ALL PRIVILEGES ON subtitleDB.* TO 'subtitle_sync'@'127.0.0.1';
FLUSH PRIVILEGES;
SQL

tmp="$(mktemp)"
cat > "$tmp" <<EOF
DATABASE_URL=mysql+pymysql://subtitle_sync:${PASS}@127.0.0.1:3306/subtitleDB?charset=utf8mb4
DATA_DIR=${DATA}
JWT_SECRET=${JWT}
EOF
unset PASS JWT
sudo cp "$tmp" /etc/subtitle-sync.env
rm -f "$tmp"
sudo chown root:root /etc/subtitle-sync.env
sudo chmod 600 /etc/subtitle-sync.env
sudo chown -R subtitle-sync:subtitle-sync "$APP" "$DATA"

sudo cp "$APP/deploy/subtitle-sync.service" /etc/systemd/system/subtitle-sync.service
sudo systemctl daemon-reload
sudo systemctl enable subtitle-sync.service
sudo systemctl restart subtitle-sync.service

ready=0
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
  if curl -sf http://127.0.0.1:8002/api/health >/dev/null; then
    ready=1
    break
  fi
  sleep 1
done
if [[ "$ready" != 1 ]]; then
  sudo systemctl status subtitle-sync.service --no-pager || true
  exit 1
fi

cleanup_check_users
CHECK_PASSWORD="$(openssl rand -hex 16)"
export CHECK_PASSWORD
python3 - <<'PY'
import json
import os
import urllib.error
import urllib.request

base = "http://127.0.0.1:8002"

def call(method, path, body=None, token=None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(base + path, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode()
        raise SystemExit(f"{method} {path} -> {exc.code} {payload}") from exc

status, health = call("GET", "/api/health")
assert health["status"] == "ok", health
print("health ok")

password = os.environ["CHECK_PASSWORD"]
status, alice = call("POST", "/api/auth/register", {"username": "synccheck", "password": password})
assert status == 201, status
status, bob = call("POST", "/api/auth/register", {"username": "synccheckb", "password": password})
assert status == 201, status
print("register ok")

video_hash = "c" * 64
chinese = "1\n00:00:01,000 --> 00:00:02,000\n中文第一版\n"
english = "1\n00:00:01,000 --> 00:00:02,000\nEnglish\n"
call("PUT", "/api/subtitles", {"video_hash": video_hash, "video_stem": "第1课", "subtitle_name": "第1课_中文.srt", "content": chinese}, alice["access_token"])
call("PUT", "/api/subtitles", {"video_hash": video_hash, "video_stem": "第1课", "subtitle_name": "第1课_英文.srt", "content": english}, alice["access_token"])
call("PUT", "/api/subtitles", {"video_hash": video_hash, "video_stem": "第1课", "subtitle_name": "第1课_中文.srt", "content": chinese + "第二版\n"}, alice["access_token"])
status, bundle = call("GET", "/api/sync?video_hash=" + video_hash, token=alice["access_token"])
by_name = {item["subtitle_name"]: item["content"] for item in bundle["subtitles"]}
assert by_name["第1课_中文.srt"] == chinese + "第二版\n"
assert by_name["第1课_英文.srt"] == english
call("PUT", "/api/subtitles", {"video_hash": video_hash, "video_stem": "第1课", "subtitle_name": "第1课_中文.srt", "content": "bob-only"}, bob["access_token"])
status, alice_again = call("GET", "/api/sync?video_hash=" + video_hash, token=alice["access_token"])
alice_content = {item["subtitle_name"]: item["content"] for item in alice_again["subtitles"]}
assert alice_content["第1课_中文.srt"] == chinese + "第二版\n"
print("subtitle ok")

def tags(ops):
    return {
        "video_hash": "c" * 64,
        "video_stem": "第1课",
        "subtitle_name": "第1课.srt",
        "document": {
            "version": 2,
            "subtitle_file": "第1课.srt",
            "entries": [{
                "id": "sentence1",
                "index": 1,
                "start": 1.0,
                "end": 2.0,
                "text": "同一句",
                "tag_ops": ops,
                "note": "",
            }],
        },
    }

call("PUT", "/api/tags", tags([
    {"name": "重点", "present": True, "at": "2026-09-21T01:00:00Z"},
    {"name": "难点", "present": True, "at": "2026-09-21T01:00:00Z"},
]), alice["access_token"])
status, deleted = call("PUT", "/api/tags", tags([
    {"name": "难点", "present": False, "at": "2026-09-22T01:00:00Z"},
]), alice["access_token"])
assert deleted["document"]["entries"][0]["tags"] == ["重点"]
status, older = call("PUT", "/api/tags", tags([
    {"name": "难点", "present": True, "at": "2026-09-21T01:00:00Z"},
]), alice["access_token"])
assert older["document"]["entries"][0]["tags"] == ["重点"]
status, restored = call("PUT", "/api/tags", tags([
    {"name": "难点", "present": True, "at": "2026-09-23T01:00:00Z"},
]), alice["access_token"])
assert restored["document"]["entries"][0]["tags"] == ["重点", "难点"]
print("tag ok")
print("cleanup_users synccheck synccheckb")
PY
unset CHECK_PASSWORD

cleanup_check_users
echo "cleanup ok"
systemctl is-active nginx fantool.service procurement.service mysql subtitle-sync
