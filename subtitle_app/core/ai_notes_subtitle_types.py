"""AI 笔记「字幕类型」分类与二级选项。

一级：学习 / 电影 / 其它
二级：学习、电影各有固定选项；其它无二级选项，仅用补充说明。
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_SUBTITLE_TYPE_ID = "learning"

LEGACY_TEMPLATE_IDS = {
    "language_learning": "learning",
    "speech": "other",
}

LEARNING_SUBCATEGORIES: tuple[str, ...] = (
    "英语",
    "日语",
    "韩语",
    "法语",
    "德语",
    "西班牙语",
    "俄语",
    "IT",
    "理工",
    "人文",
    "科教",
    "经济",
    "医学",
    "法律",
    "艺术",
    "商务",
    "历史",
    "心理学",
)

MOVIE_SUBCATEGORIES: tuple[str, ...] = (
    "英文原文",
    "日语原文",
    "中文原文",
)


@dataclass(frozen=True)
class SubtitleTypeCategory:
    id: str
    name: str
    template_id: str
    subcategory_label: str | None
    subcategories: tuple[str, ...]
    default_subcategory: str | None

    def has_subcategory(self) -> bool:
        return bool(self.subcategories)


_SUBTITLE_TYPES: dict[str, SubtitleTypeCategory] = {}


def register_subtitle_type(category: SubtitleTypeCategory) -> None:
    _SUBTITLE_TYPES[category.id] = category


def list_subtitle_types() -> list[SubtitleTypeCategory]:
    return list(_SUBTITLE_TYPES.values())


def get_subtitle_type(type_id: str) -> SubtitleTypeCategory:
    normalized = normalize_subtitle_type_id(type_id)
    return _SUBTITLE_TYPES[normalized]


def normalize_subtitle_type_id(type_id: str) -> str:
    value = LEGACY_TEMPLATE_IDS.get(type_id.strip(), type_id.strip())
    if value in _SUBTITLE_TYPES:
        return value
    return DEFAULT_SUBTITLE_TYPE_ID


def normalize_subtitle_type_id_from_legacy(template_id: str) -> str:
    return normalize_subtitle_type_id(LEGACY_TEMPLATE_IDS.get(template_id.strip(), template_id.strip()))


def default_subcategory_for(type_id: str) -> str:
    category = get_subtitle_type(type_id)
    if category.default_subcategory:
        return category.default_subcategory
    if category.subcategories:
        return category.subcategories[0]
    return ""


def normalize_subcategory(type_id: str, subcategory: str) -> str:
    category = get_subtitle_type(type_id)
    value = subcategory.strip()
    if not category.subcategories:
        return ""
    if value in category.subcategories:
        return value
    return category.default_subcategory or category.subcategories[0]


def _register_builtin_subtitle_types() -> None:
    register_subtitle_type(
        SubtitleTypeCategory(
            id="learning",
            name="学习",
            template_id="learning",
            subcategory_label="学科/语种",
            subcategories=LEARNING_SUBCATEGORIES,
            default_subcategory="英语",
        )
    )
    register_subtitle_type(
        SubtitleTypeCategory(
            id="movie",
            name="电影",
            template_id="movie",
            subcategory_label="字幕语言",
            subcategories=MOVIE_SUBCATEGORIES,
            default_subcategory="中文原文",
        )
    )
    register_subtitle_type(
        SubtitleTypeCategory(
            id="other",
            name="其它",
            template_id="other",
            subcategory_label=None,
            subcategories=(),
            default_subcategory=None,
        )
    )


_register_builtin_subtitle_types()
