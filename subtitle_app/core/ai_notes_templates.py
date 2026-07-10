"""AI 笔记 Prompt 模板注册表。

一级「字幕类型」见 `ai_notes_subtitle_types`；此处定义各类型对应的 Prompt 模板。
新增模板：实例化 `AiNotesPromptTemplate` 并调用 `register_ai_notes_template()`。
"""

from __future__ import annotations

from dataclasses import dataclass

from core.ai_notes_subtitle_types import (
    DEFAULT_SUBTITLE_TYPE_ID,
    LEGACY_TEMPLATE_IDS,
)

DEFAULT_AI_NOTES_TEMPLATE_ID = DEFAULT_SUBTITLE_TYPE_ID

_COMMON_OUTPUT_RULES = (
    "不要编造字幕中未出现的内容；\n"
    "只输出 Markdown 正文，不要输出 JSON 或额外解释。"
)


@dataclass(frozen=True)
class AiNotesPromptTemplate:
    """单种笔记场景的 Prompt 模板定义。"""

    id: str
    name: str
    description: str
    notes_title_suffix: str
    system_prompt: str
    user_intro: str
    subcategory_label: str | None = None
    user_context_label: str = "补充说明"
    user_context_hint: str = ""
    corpus_intro: str = "以下为该视频的全部有效字幕语料："

    def build_user_content(
        self,
        media_name: str,
        corpus_text: str,
        *,
        subcategory: str = "",
        user_context: str = "",
    ) -> str:
        intro = self.user_intro.format(media_name=media_name)
        parts = [intro]
        sub = subcategory.strip()
        if sub and self.subcategory_label:
            parts.append(f"{self.subcategory_label}：{sub}")
        context = user_context.strip()
        if context:
            parts.append(f"{self.user_context_label}：{context}")
        parts.append(f"{self.corpus_intro}\n\n{corpus_text}")
        return "\n\n".join(parts)


_TEMPLATES: dict[str, AiNotesPromptTemplate] = {}


def register_ai_notes_template(template: AiNotesPromptTemplate) -> None:
    _TEMPLATES[template.id] = template


def list_ai_notes_templates() -> list[AiNotesPromptTemplate]:
    return list(_TEMPLATES.values())


def get_ai_notes_template(template_id: str) -> AiNotesPromptTemplate:
    resolved = normalize_ai_notes_template_id(template_id)
    return _TEMPLATES[resolved]


def normalize_ai_notes_template_id(template_id: str) -> str:
    value = LEGACY_TEMPLATE_IDS.get(template_id.strip(), template_id.strip())
    if value in _TEMPLATES:
        return value
    return DEFAULT_AI_NOTES_TEMPLATE_ID


def _register_builtin_templates() -> None:
    register_ai_notes_template(
        AiNotesPromptTemplate(
            id="learning",
            name="学习",
            description="整理课程/学习类字幕，生成便于复习的学习笔记。",
            notes_title_suffix="AI 学习笔记",
            system_prompt=(
                "你是一位专业的学习笔记整理助手。"
                "请根据用户提供的视频字幕语料，整理一份结构清晰、便于复习的 Markdown 学习笔记。\n"
                "要求：\n"
                "1. 结合用户提供的学科/语种信息理解内容；使用中文撰写（保留原文外语词汇或例句时可双语对照）；\n"
                "2. 包含：课程概述、核心知识点、重点词汇/表达、例句或要点、学习建议等；\n"
                "3. 适当使用 Markdown 标题、列表、表格；\n"
                "4. " + _COMMON_OUTPUT_RULES
            ),
            user_intro="视频文件名：{media_name}",
            subcategory_label="学科/语种",
            user_context_hint="可选，例如：中文老师讲日语、第 3 讲",
            corpus_intro="以下为该视频的全部有效字幕语料：",
        )
    )

    register_ai_notes_template(
        AiNotesPromptTemplate(
            id="movie",
            name="电影",
            description="根据电影字幕快速了解剧情梗概、人物与主题。",
            notes_title_suffix="AI 观影笔记",
            system_prompt=(
                "你是一位专业的影视内容分析助手。"
                "请根据用户提供的电影字幕语料，撰写一份便于快速了解影片的中文 Markdown 梗概。\n"
                "要求：\n"
                "1. 结合用户提供的字幕语言信息理解台词；使用中文撰写；\n"
                "2. 包含：影片概要（2–3 句，尽量不剧透）、主要人物与关系、"
                "故事脉络（按时间线简述主要情节）、主题与风格、关键转折/高潮（可标注「剧透」）、观后要点；\n"
                "3. 若字幕不完整，请基于已有内容整理，并注明「字幕不完整，部分情节可能缺失」；\n"
                "4. " + _COMMON_OUTPUT_RULES
            ),
            user_intro="电影文件名：{media_name}",
            subcategory_label="字幕语言",
            user_context_hint="可选，例如：片名别名、系列第几部、希望重点关注的角色",
            corpus_intro="以下为该电影的字幕语料：",
        )
    )

    register_ai_notes_template(
        AiNotesPromptTemplate(
            id="other",
            name="其它",
            description="通用字幕整理：请在补充说明中描述你的需求。",
            notes_title_suffix="AI 笔记",
            system_prompt=(
                "你是一位专业的内容整理助手。"
                "请根据用户提供的视频字幕语料及补充说明，整理一份结构清晰的中文 Markdown 笔记。\n"
                "要求：\n"
                "1. 优先遵循用户在补充说明中提出的整理目标；\n"
                "2. 包含：内容概述、主要要点、结构化的章节或主题归纳；\n"
                "3. 适当使用 Markdown 标题、列表；\n"
                "4. " + _COMMON_OUTPUT_RULES
            ),
            user_intro="视频文件名：{media_name}",
            subcategory_label=None,
            user_context_label="补充说明",
            user_context_hint="请描述整理目标，例如：演讲摘要、会议纪要、访谈要点",
            corpus_intro="以下为该视频的全部有效字幕语料：",
        )
    )


_register_builtin_templates()
