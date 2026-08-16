from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = APP_DIR.parent
CONFIG_PATH = APP_DIR / "config.json"
EXAMPLE_CONFIG_PATH = APP_DIR / "config.json.example"

MEDIA_EXTENSIONS = {
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v",
    ".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma", ".opus",
}

LANGUAGE_OPTIONS = [
    ("自动检测", "auto"),
    ("原文混排（多语言）", "mixed"),
    ("中文", "zh"),
    ("英语", "en"),
    ("日语", "ja"),
    ("韩语", "ko"),
    ("法语", "fr"),
    ("德语", "de"),
    ("西班牙语", "es"),
    ("俄语", "ru"),
]

OUTPUT_OPTIONS = [
    ("SRT 字幕", "srt"),
    ("VTT 字幕", "vtt"),
    ("纯文本 TXT", "txt"),
]

INFERENCE_DEVICE_OPTIONS = [
    ("自动", "auto"),
    ("CPU", "cpu"),
    ("GPU (CUDA)", "gpu"),
]

# 字幕文件名中的语种后缀（视频名_后缀.扩展名）
LANGUAGE_FILENAME_LABELS = {
    "auto": "自动检测",
    "mixed": "原文混排(多语言)",
    "zh": "中文",
    "en": "英文",
    "ja": "日语",
    "ko": "韩语",
    "fr": "法语",
    "de": "德语",
    "es": "西班牙语",
    "ru": "俄语",
}

# 边播边转输出文件名后缀（视频名_同步.srt）
LIVE_SYNC_FILENAME_LABEL = "同步"

DEEPSEEK_API_KEY_PLACEHOLDERS = (
    "请在此填写 DeepSeek API Key",
    "YOUR_DEEPSEEK_API_KEY",
    "在此填写你的 DeepSeek API Key",
    "请填写 Whisper GGML 模型路径，例如 ../win系统模型中等.bin",
)

DEEPSEEK_MODEL_OPTIONS = [
    ("deepseek-v4-flash（较快）", "deepseek-v4-flash"),
    ("deepseek-v4-pro（效果更好）", "deepseek-v4-pro"),
]

DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"


def is_deepseek_api_key_configured(api_key: str) -> bool:
    value = api_key.strip()
    if not value:
        return False
    if value in DEEPSEEK_API_KEY_PLACEHOLDERS:
        return False
    if value.startswith("请") and "填写" in value:
        return False
    if value.startswith("<") and value.endswith(">"):
        return False
    return True


def is_deepseek_configured(config: "AppConfig") -> bool:
    return (
        is_deepseek_api_key_configured(config.deepseek_api_key)
        and bool(config.deepseek_base_url.strip())
        and bool(config.deepseek_model.strip())
    )


@dataclass
class AppConfig:
    output_dir: str = ""
    language: str = "zh"
    output_format: str = "srt"
    model_path: str = ""
    inference_device: str = "auto"
    n_threads: int = 0
    deepseek_api_key: str = "请在此填写 DeepSeek API Key"
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-flash"
    ai_notes_subtitle_type: str = "learning"
    ai_notes_subcategory: dict[str, str] = field(default_factory=dict)
    ai_notes_user_context: dict[str, str] = field(default_factory=dict)
    ai_notes_template: str = "learning"
    ecdict_db_path: str = ""
    last_media_dir: str = ""
    translate_app: str = "baidu"
    translate_hotkey: str = "Ctrl+Alt+C"

    def get_ai_notes_user_context(self, subtitle_type: str) -> str:
        return str(self.ai_notes_user_context.get(subtitle_type, "") or "").strip()

    def set_ai_notes_user_context(self, subtitle_type: str, context: str) -> None:
        value = context.strip()
        if value:
            self.ai_notes_user_context[subtitle_type] = value
        elif subtitle_type in self.ai_notes_user_context:
            del self.ai_notes_user_context[subtitle_type]

    def get_ai_notes_subcategory(self, subtitle_type: str) -> str:
        from core.ai_notes_subtitle_types import normalize_subcategory, normalize_subtitle_type_id

        type_id = normalize_subtitle_type_id(subtitle_type)
        return normalize_subcategory(type_id, str(self.ai_notes_subcategory.get(type_id, "") or ""))

    def set_ai_notes_subcategory(self, subtitle_type: str, subcategory: str) -> None:
        from core.ai_notes_subtitle_types import (
            get_subtitle_type,
            normalize_subcategory,
            normalize_subtitle_type_id,
        )

        type_id = normalize_subtitle_type_id(subtitle_type)
        category = get_subtitle_type(type_id)
        if not category.has_subcategory():
            self.ai_notes_subcategory.pop(type_id, None)
            return
        value = normalize_subcategory(type_id, subcategory) if subcategory.strip() else ""
        if value:
            self.ai_notes_subcategory[type_id] = value
        elif type_id in self.ai_notes_subcategory:
            del self.ai_notes_subcategory[type_id]

    def resolved_last_media_dir(self) -> Path:
        if self.last_media_dir.strip():
            folder = Path(self.last_media_dir)
            if folder.is_dir():
                return folder
        return Path.home()

    def resolved_output_dir(self, media_path: Path | None = None) -> Path:
        if self.output_dir.strip():
            return Path(self.output_dir)
        if media_path is not None:
            return media_path.parent
        return Path.home()

    def language_filename_label(self) -> str:
        return LANGUAGE_FILENAME_LABELS.get(self.language, self.language)

    def build_output_path(self, media_path: Path) -> Path:
        """生成输出路径：默认与视频同目录，文件名为 视频名_语种模式.扩展名"""
        out_dir = self.resolved_output_dir(media_path)
        filename = f"{media_path.stem}_{self.language_filename_label()}.{self.output_format}"
        return out_dir / filename

    def build_live_output_path(self, media_path: Path) -> Path:
        """边播边转输出路径：视频名_同步.扩展名"""
        out_dir = self.resolved_output_dir(media_path)
        filename = f"{media_path.stem}_{LIVE_SYNC_FILENAME_LABEL}.{self.output_format}"
        return out_dir / filename

    def resolved_model_path(self) -> Path:
        if self.model_path.strip():
            return Path(self.model_path)
        default = ROOT_DIR / "win系统模型中等.bin"
        return default

    def resolved_n_threads(self) -> int:
        if self.n_threads > 0:
            return self.n_threads
        return os.cpu_count() or 4

    def validate_model(self) -> Path:
        model = self.resolved_model_path()
        if not model.is_file():
            raise RuntimeError(f"模型文件不存在：{model}")
        return model


def _default_config() -> AppConfig:
    cfg = AppConfig()
    default_model = ROOT_DIR / "win系统模型中等.bin"
    if default_model.exists():
        cfg.model_path = str(default_model)
    return cfg


def load_config() -> AppConfig:
    if not CONFIG_PATH.exists():
        if EXAMPLE_CONFIG_PATH.is_file():
            shutil.copy(EXAMPLE_CONFIG_PATH, CONFIG_PATH)
        else:
            cfg = _default_config()
            save_config(cfg)
            return cfg

    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    # 兼容旧版配置
    if not data.get("model_path"):
        data["model_path"] = data.get("engine_model") or data.get("whisper_cpp_model") or ""

    cfg = AppConfig(**{k: v for k, v in data.items() if k in AppConfig.__dataclass_fields__})
    if not isinstance(cfg.ai_notes_user_context, dict):
        cfg.ai_notes_user_context = {}
    if not isinstance(cfg.ai_notes_subcategory, dict):
        cfg.ai_notes_subcategory = {}

    from core.ai_notes_subtitle_types import (
        LEGACY_TEMPLATE_IDS,
        normalize_subtitle_type_id,
    )

    if not str(getattr(cfg, "ai_notes_subtitle_type", "") or "").strip():
        legacy = str(data.get("ai_notes_template") or cfg.ai_notes_template or "learning")
        cfg.ai_notes_subtitle_type = normalize_subtitle_type_id(
            LEGACY_TEMPLATE_IDS.get(legacy, legacy)
        )
    cfg.ai_notes_subtitle_type = normalize_subtitle_type_id(cfg.ai_notes_subtitle_type)
    cfg.ai_notes_template = cfg.ai_notes_subtitle_type

    migrated_context: dict[str, str] = {}
    for key, value in cfg.ai_notes_user_context.items():
        new_key = normalize_subtitle_type_id(LEGACY_TEMPLATE_IDS.get(key, key))
        if str(value or "").strip():
            migrated_context[new_key] = str(value).strip()
    cfg.ai_notes_user_context = migrated_context

    migrated_subcategory: dict[str, str] = {}
    for key, value in cfg.ai_notes_subcategory.items():
        new_key = normalize_subtitle_type_id(LEGACY_TEMPLATE_IDS.get(key, key))
        if str(value or "").strip():
            migrated_subcategory[new_key] = str(value).strip()
    cfg.ai_notes_subcategory = migrated_subcategory
    if not cfg.model_path and (ROOT_DIR / "win系统模型中等.bin").exists():
        cfg.model_path = str(ROOT_DIR / "win系统模型中等.bin")

    from core.external_translate import DEFAULT_TRANSLATOR_ID, get_translator, normalize_hotkey_text

    cfg.translate_app = get_translator(cfg.translate_app).id or DEFAULT_TRANSLATOR_ID
    translator = get_translator(cfg.translate_app)
    cfg.translate_hotkey = normalize_hotkey_text(cfg.translate_hotkey, translator.default_hotkey)
    return cfg


def save_config(config: AppConfig) -> None:
    CONFIG_PATH.write_text(
        json.dumps(asdict(config), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
