from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path

from core.app_paths import app_dir, bundle_dir, is_frozen, models_dir, resolve_model_path

APP_DIR = app_dir()
ROOT_DIR = APP_DIR.parent
CONFIG_PATH = APP_DIR / "config.json"
EXAMPLE_CONFIG_PATH = bundle_dir() / "config.json.example"
if not EXAMPLE_CONFIG_PATH.is_file():
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
    recent_media_files: list[str] = field(default_factory=list)
    # 媒体路径 -> 上次播放位置（毫秒），用于再次打开时停在原进度
    media_playback_positions: dict[str, int] = field(default_factory=dict)
    translate_app: str = "baidu"
    translate_hotkey: str = "Ctrl+Alt+C"
    # 画面叠加字幕（半透明底条 + 实心字）
    onscreen_subtitle_enabled: bool = True
    onscreen_subtitle_font_size: int = 28
    onscreen_subtitle_color: str = "#FFFFFF"
    onscreen_subtitle_bg_opacity: float = 0.55
    onscreen_subtitle_width_percent: int = 80
    onscreen_subtitle_position: str = "bottom"  # top | middle | bottom
    # 沉浸列表：字幕列表透明叠在画面上
    immersive_subtitle_list: bool = False
    immersive_subtitle_list_opacity: float = 0.28
    immersive_subtitle_list_side: str = "right"  # left | right
    immersive_subtitle_list_width_percent: int = 36
    # 字幕列表显示密度：normal 普通 | compact 紧凑
    subtitle_list_density: str = "normal"
    # 用户添加过的自定义字幕标签，下次对话框里继续出现
    subtitle_custom_tags: list[str] = field(default_factory=list)
    # 批量同步标签：上次选择的标签文件和视频文件夹
    batch_tag_sync_files: list[str] = field(default_factory=list)
    batch_tag_sync_video_dir: str = ""
    # 提取全部标签：上次的来源文件夹和保存位置
    tag_extract_source_dir: str = ""
    tag_extract_dest_dir: str = ""
    # 云同步账号。同一用户名在不同设备上是同一个用户。
    cloud_server_url: str = "https://subtitle.gcsfg.work"
    cloud_username: str = ""
    cloud_password: str = ""

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

    def resolved_model_path(self) -> Path:
        resolved = resolve_model_path(self.model_path)
        if resolved:
            return Path(resolved)
        return models_dir() / "win系统模型中等.bin"

    def resolved_n_threads(self) -> int:
        if self.n_threads > 0:
            return self.n_threads
        return os.cpu_count() or 4

    def validate_model(self) -> Path:
        model = self.resolved_model_path()
        if not model.is_file():
            raise RuntimeError(
                "未找到 Whisper 模型。\n"
                f"请把 win系统模型最小.bin、win系统模型中等.bin、win系统模型最大.bin 放到：\n"
                f"{models_dir()}\n"
                "如果这个文件夹里没有这些文件，请在转写工具中点击「浏览」指定 .bin 模型路径。"
            )
        return model


def _default_config() -> AppConfig:
    cfg = AppConfig()
    cfg.model_path = resolve_model_path("")
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
    cfg.model_path = resolve_model_path(cfg.model_path)
    if is_frozen():
        cfg.inference_device = "cpu"

    from core.external_translate import DEFAULT_TRANSLATOR_ID, get_translator, normalize_hotkey_text

    cfg.translate_app = get_translator(cfg.translate_app).id or DEFAULT_TRANSLATOR_ID
    translator = get_translator(cfg.translate_app)
    cfg.translate_hotkey = normalize_hotkey_text(cfg.translate_hotkey, translator.default_hotkey)
    cfg.onscreen_subtitle_enabled = bool(cfg.onscreen_subtitle_enabled)
    try:
        cfg.onscreen_subtitle_font_size = max(12, min(72, int(cfg.onscreen_subtitle_font_size)))
    except (TypeError, ValueError):
        cfg.onscreen_subtitle_font_size = 28
    color = str(cfg.onscreen_subtitle_color or "").strip() or "#FFFFFF"
    if not color.startswith("#"):
        color = f"#{color}"
    cfg.onscreen_subtitle_color = color
    try:
        cfg.onscreen_subtitle_bg_opacity = max(0.0, min(1.0, float(cfg.onscreen_subtitle_bg_opacity)))
    except (TypeError, ValueError):
        cfg.onscreen_subtitle_bg_opacity = 0.55
    try:
        cfg.onscreen_subtitle_width_percent = max(30, min(100, int(cfg.onscreen_subtitle_width_percent)))
    except (TypeError, ValueError):
        cfg.onscreen_subtitle_width_percent = 80
    position = str(cfg.onscreen_subtitle_position or "").strip().lower()
    if position not in {"top", "middle", "bottom"}:
        position = "bottom"
    cfg.onscreen_subtitle_position = position
    cfg.immersive_subtitle_list = bool(cfg.immersive_subtitle_list)
    try:
        cfg.immersive_subtitle_list_opacity = max(
            0.0, min(1.0, float(cfg.immersive_subtitle_list_opacity))
        )
    except (TypeError, ValueError):
        cfg.immersive_subtitle_list_opacity = 0.28
    side = str(cfg.immersive_subtitle_list_side or "").strip().lower()
    if side not in {"left", "right"}:
        side = "right"
    cfg.immersive_subtitle_list_side = side
    try:
        cfg.immersive_subtitle_list_width_percent = max(
            18, min(70, int(cfg.immersive_subtitle_list_width_percent))
        )
    except (TypeError, ValueError):
        cfg.immersive_subtitle_list_width_percent = 36
    density = str(cfg.subtitle_list_density or "").strip().lower()
    if density not in {"normal", "compact"}:
        density = "normal"
    cfg.subtitle_list_density = density
    if not isinstance(cfg.subtitle_custom_tags, list):
        cfg.subtitle_custom_tags = []
    custom_tags: list[str] = []
    for name in cfg.subtitle_custom_tags:
        cleaned = str(name or "").strip()
        if cleaned and cleaned not in custom_tags:
            custom_tags.append(cleaned)
    cfg.subtitle_custom_tags = custom_tags
    if not isinstance(cfg.recent_media_files, list):
        cfg.recent_media_files = []
    recent_files: list[str] = []
    for item in cfg.recent_media_files:
        path = str(item or "").strip()
        if path and path not in recent_files:
            recent_files.append(path)
    cfg.recent_media_files = recent_files[:15]
    raw_positions = cfg.media_playback_positions
    positions: dict[str, int] = {}
    if isinstance(raw_positions, dict):
        for key, value in raw_positions.items():
            path = str(key or "").strip()
            try:
                position_ms = int(value)
            except (TypeError, ValueError):
                continue
            if path and position_ms > 0:
                positions[path] = position_ms
    if len(positions) > 200:
        positions = dict(list(positions.items())[-200:])
    cfg.media_playback_positions = positions
    if not isinstance(cfg.batch_tag_sync_files, list):
        cfg.batch_tag_sync_files = []
    tag_files: list[str] = []
    for item in cfg.batch_tag_sync_files:
        path = str(item or "").strip()
        if path and path not in tag_files:
            tag_files.append(path)
    cfg.batch_tag_sync_files = tag_files
    cfg.batch_tag_sync_video_dir = str(cfg.batch_tag_sync_video_dir or "").strip()
    cfg.tag_extract_source_dir = str(cfg.tag_extract_source_dir or "").strip()
    cfg.tag_extract_dest_dir = str(cfg.tag_extract_dest_dir or "").strip()
    cfg.cloud_server_url = str(cfg.cloud_server_url or "").strip() or "https://subtitle.gcsfg.work"
    cfg.cloud_username = str(cfg.cloud_username or "").strip()
    cfg.cloud_password = str(cfg.cloud_password or "")
    return cfg


def save_config(config: AppConfig) -> None:
    CONFIG_PATH.write_text(
        json.dumps(asdict(config), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
