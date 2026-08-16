from __future__ import annotations

import json
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = ROOT / "config" / "default_settings.json"
USER_PATH = ROOT / "config" / "user_settings.json"
DOWNLOADS_DIR = ROOT / "downloads"


class Settings:
    """读写用户配置，缺失项回落到默认值。"""

    def __init__(self) -> None:
        self._defaults = self._load_json(DEFAULT_PATH)
        self._data = deepcopy(self._defaults)
        if USER_PATH.exists():
            self._data.update(self._load_json(USER_PATH))
        save_dir = str(self._data.get("save_dir") or "").strip()
        if save_dir:
            # 恢复上次选择的目录（即使暂时不存在也保留，下载时再创建）
            self._data["save_dir"] = str(Path(save_dir).expanduser())
        else:
            DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
            self._data["save_dir"] = str(DOWNLOADS_DIR)

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, self._defaults.get(key, default))

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def update(self, values: dict[str, Any]) -> None:
        self._data.update(values)

    def as_dict(self) -> dict[str, Any]:
        return deepcopy(self._data)

    def save(self) -> None:
        USER_PATH.parent.mkdir(parents=True, exist_ok=True)
        with USER_PATH.open("w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def resolve_ffmpeg(self) -> str | None:
        custom = (self.get("ffmpeg_path") or "").strip()
        if custom and Path(custom).exists():
            return custom
        # 优先 PATH 上的较新 ffmpeg（安装目录自带旧版可能无法处理 AV1）
        found = shutil.which("ffmpeg")
        if found:
            return found
        bundled = Path(r"D:\Program Files\Shandou\转码程序\ffmpeg.exe")
        if bundled.exists():
            return str(bundled)
        return None