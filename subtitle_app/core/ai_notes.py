from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from core.ai_notes_templates import (
    DEFAULT_AI_NOTES_TEMPLATE_ID,
    get_ai_notes_template,
    normalize_ai_notes_template_id,
)
from core.ai_notes_subtitle_types import get_subtitle_type
from core.config import AppConfig, CONFIG_PATH, is_deepseek_api_key_configured
from core.subtitle import SubtitleSegment, load_subtitle_file
from core.subtitle_loader import find_subtitles_for_media

MAX_CORPUS_CHARS = 120_000
NOTES_FILENAME_SUFFIX = "AI笔记"


@dataclass(frozen=True)
class SubtitleCorpusItem:
    label: str
    filename: str
    path: Path
    segments: tuple[SubtitleSegment, ...]


def is_api_key_configured(api_key: str) -> bool:
    return is_deepseek_api_key_configured(api_key)


def collect_valid_subtitle_corpus(media_path: Path) -> list[SubtitleCorpusItem]:
    """收集同目录下所有可加载的有效字幕。"""
    from core.config import load_config

    items: list[SubtitleCorpusItem] = []
    for path, label in find_subtitles_for_media(media_path):
        try:
            segments = load_subtitle_file(path)
        except (OSError, ValueError):
            continue
        if not segments:
            continue
        items.append(
            SubtitleCorpusItem(
                label=label,
                filename=path.name,
                path=path,
                segments=tuple(segments),
            )
        )

    partial_path = load_config().build_live_output_path(media_path).with_suffix(
        ".srt.partial"
    )
    if partial_path.is_file():
        try:
            segments = load_subtitle_file(partial_path)
            if segments and not any(item.path.resolve() == partial_path.resolve() for item in items):
                items.append(
                    SubtitleCorpusItem(
                        label="同步(未完成)",
                        filename=partial_path.name,
                        path=partial_path,
                        segments=tuple(segments),
                    )
                )
        except (OSError, ValueError):
            pass

    return items


def _format_clock(seconds: float) -> str:
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def corpus_to_text(items: list[SubtitleCorpusItem]) -> str:
    parts: list[str] = []
    for item in items:
        parts.append(f"## 字幕来源：{item.label}（{item.filename}）")
        for seg in item.segments:
            start = _format_clock(seg.start)
            end = _format_clock(seg.end)
            text = seg.text.replace("\n", " / ")
            parts.append(f"[{start} → {end}] {text}")
        parts.append("")
    text = "\n".join(parts).strip()
    if len(text) > MAX_CORPUS_CHARS:
        text = text[:MAX_CORPUS_CHARS] + "\n\n（语料过长，已截断后提交）"
    return text


def build_notes_output_path(media_path: Path) -> Path:
    return media_path.parent / f"{media_path.stem}_{NOTES_FILENAME_SUFFIX}.md"


def find_notes_path(media_path: Path) -> Path | None:
    """若存在有效 AI 笔记文件则返回路径。"""
    path = build_notes_output_path(media_path)
    if not path.is_file():
        return None
    try:
        if path.stat().st_size < 32:
            return None
    except OSError:
        return None
    return path

def build_notes_prompt(
    media_name: str,
    corpus_text: str,
    template_id: str = DEFAULT_AI_NOTES_TEMPLATE_ID,
    *,
    subcategory: str = "",
    user_context: str = "",
) -> list[dict[str, str]]:
    template = get_ai_notes_template(normalize_ai_notes_template_id(template_id))
    return [
        {"role": "system", "content": template.system_prompt},
        {
            "role": "user",
            "content": template.build_user_content(
                media_name,
                corpus_text,
                subcategory=subcategory,
                user_context=user_context,
            ),
        },
    ]


_PROMPT_SECTION_RE = re.compile(r"^=== (system|user) ===\s*$", re.MULTILINE)


def format_messages_for_edit(messages: list[dict[str, str]]) -> str:
    """将 API messages 格式化为可编辑的完整 Prompt 文本。"""
    parts: list[str] = []
    for message in messages:
        role = message.get("role", "").strip()
        content = str(message.get("content", "")).strip()
        parts.append(f"=== {role} ===\n{content}")
    return "\n\n".join(parts)


def parse_messages_from_edit(text: str) -> list[dict[str, str]]:
    """从编辑后的 Prompt 文本解析回 API messages。"""
    stripped = text.strip()
    if not stripped:
        raise ValueError("提示词不能为空。")

    matches = list(_PROMPT_SECTION_RE.finditer(stripped))
    if len(matches) < 2:
        raise ValueError("提示词格式不正确：需包含「=== system ===」与「=== user ===」两个区块。")

    messages: list[dict[str, str]] = []
    for index, match in enumerate(matches):
        role = match.group(1)
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(stripped)
        content = stripped[start:end].strip()
        if not content:
            raise ValueError(f"提示词中 {role} 区块内容不能为空。")
        messages.append({"role": role, "content": content})

    roles = {message["role"] for message in messages}
    if "system" not in roles or "user" not in roles:
        raise ValueError("提示词须同时包含 system 与 user 区块。")
    return messages


def call_deepseek_chat(
    config: AppConfig,
    messages: list[dict[str, str]],
) -> str:
    if not is_api_key_configured(config.deepseek_api_key):
        raise RuntimeError(
            "未配置 DeepSeek API Key。\n"
            f"请编辑配置文件：{CONFIG_PATH}\n"
            "将 deepseek_api_key 替换为你的密钥。\n"
            "申请地址：https://platform.deepseek.com/api_keys"
        )

    base_url = config.deepseek_base_url.strip().rstrip("/") or "https://api.deepseek.com"
    url = f"{base_url}/chat/completions"
    payload = {
        "model": config.deepseek_model.strip() or "deepseek-v4-flash",
        "messages": messages,
        "stream": False,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.deepseek_api_key.strip()}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"DeepSeek API 请求失败（HTTP {exc.code}）：\n{detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"无法连接 DeepSeek API：{exc.reason}") from exc

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"DeepSeek API 返回格式异常：{data}") from exc

    if not str(content).strip():
        raise RuntimeError("DeepSeek API 返回内容为空。")
    return str(content).strip()


def _strip_markdown_fence(text: str) -> str:
    fenced = re.match(r"^```(?:markdown|md)?\s*\n(.*)\n```\s*$", text.strip(), re.DOTALL)
    if fenced:
        return fenced.group(1).strip()
    return text.strip()


def compose_notes_markdown(
    media_path: Path,
    corpus_items: list[SubtitleCorpusItem],
    model_content: str,
    *,
    model_name: str,
    template_id: str = DEFAULT_AI_NOTES_TEMPLATE_ID,
    subcategory: str = "",
    generated_at: datetime | None = None,
) -> str:
    when = generated_at or datetime.now()
    time_str = when.strftime("%Y-%m-%d %H:%M:%S")
    subtitle_sources = "、".join(f"{item.label}({item.filename})" for item in corpus_items)
    body = _strip_markdown_fence(model_content)
    template = get_ai_notes_template(normalize_ai_notes_template_id(template_id))
    category = get_subtitle_type(template.id)
    subtitle_type_line = f"> 字幕类型：{template.name}"
    if subcategory.strip() and category.has_subcategory() and category.subcategory_label:
        subtitle_type_line += f"（{category.subcategory_label}：{subcategory.strip()}）"

    return (
        f"---\n"
        f"title: {media_path.stem} — {template.name}\n"
        f"source_media: {media_path.name}\n"
        f"subtitle_type: {template.name}\n"
        f"subtitle_template: {template.id}\n"
        f"subtitle_subcategory: {subcategory.strip()}\n"
        f"subtitle_sources: {subtitle_sources}\n"
        f"generated_at: {time_str}\n"
        f"model: {model_name}\n"
        f"---\n\n"
        f"# {media_path.stem} — {template.notes_title_suffix}\n\n"
        f"> 生成时间：{time_str}  \n"
        f"> 源视频：{media_path.name}  \n"
        f"{subtitle_type_line}  \n"
        f"> 字幕来源：{subtitle_sources}  \n"
        f"> 模型：{model_name}\n\n"
        f"{body}\n"
    )


def generate_ai_notes(
    media_path: Path,
    config: AppConfig,
    *,
    messages: list[dict[str, str]] | None = None,
    template_id: str | None = None,
    subcategory: str = "",
) -> Path:
    resolved_template_id = normalize_ai_notes_template_id(
        config.ai_notes_subtitle_type if template_id is None else template_id
    )
    corpus_items = collect_valid_subtitle_corpus(media_path)
    if messages is None:
        if not corpus_items:
            raise RuntimeError("未找到有效字幕文件，无法生成笔记。")
        corpus_text = corpus_to_text(corpus_items)
        resolved_subcategory = subcategory.strip() or config.get_ai_notes_subcategory(resolved_template_id)
        messages = build_notes_prompt(
            media_path.name,
            corpus_text,
            template_id=resolved_template_id,
            subcategory=resolved_subcategory,
            user_context=config.get_ai_notes_user_context(resolved_template_id),
        )
    elif not messages:
        raise RuntimeError("提示词为空，无法生成笔记。")

    model_name = config.deepseek_model.strip() or "deepseek-v4-flash"
    model_content = call_deepseek_chat(config, messages)

    output_path = build_notes_output_path(media_path)
    content = compose_notes_markdown(
        media_path,
        corpus_items,
        model_content,
        model_name=model_name,
        template_id=resolved_template_id,
        subcategory=subcategory.strip() or config.get_ai_notes_subcategory(resolved_template_id),
    )
    output_path.write_text(content, encoding="utf-8")
    return output_path
