from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

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

def build_notes_prompt(media_name: str, corpus_text: str) -> list[dict[str, str]]:
    system = (
        "你是一位专业的学习笔记整理助手。"
        "请根据用户提供的视频字幕语料，整理一份结构清晰、便于复习的 Markdown 学习笔记。"
        "要求：\n"
        "1. 使用中文撰写（保留原文中的外语词汇或例句时可双语对照）；\n"
        "2. 包含：课程概述、核心知识点、重点词汇/表达、例句或要点、学习建议等；\n"
        "3. 适当使用 Markdown 标题、列表、表格；\n"
        "4. 不要编造字幕中未出现的内容；\n"
        "5. 只输出 Markdown 正文，不要输出 JSON 或额外解释。"
    )
    user = (
        f"视频文件名：{media_name}\n\n"
        f"以下为该视频的全部有效字幕语料：\n\n"
        f"{corpus_text}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


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
    generated_at: datetime | None = None,
) -> str:
    when = generated_at or datetime.now()
    time_str = when.strftime("%Y-%m-%d %H:%M:%S")
    subtitle_sources = "、".join(f"{item.label}({item.filename})" for item in corpus_items)
    body = _strip_markdown_fence(model_content)

    return (
        f"---\n"
        f"title: {media_path.stem} 学习笔记\n"
        f"source_media: {media_path.name}\n"
        f"subtitle_sources: {subtitle_sources}\n"
        f"generated_at: {time_str}\n"
        f"model: {model_name}\n"
        f"---\n\n"
        f"# {media_path.stem} — AI 学习笔记\n\n"
        f"> 生成时间：{time_str}  \n"
        f"> 源视频：{media_path.name}  \n"
        f"> 字幕来源：{subtitle_sources}  \n"
        f"> 模型：{model_name}\n\n"
        f"{body}\n"
    )


def generate_ai_notes(media_path: Path, config: AppConfig) -> Path:
    corpus_items = collect_valid_subtitle_corpus(media_path)
    if not corpus_items:
        raise RuntimeError("未找到有效字幕文件，无法生成笔记。")

    corpus_text = corpus_to_text(corpus_items)
    messages = build_notes_prompt(media_path.name, corpus_text)
    model_name = config.deepseek_model.strip() or "deepseek-v4-flash"
    model_content = call_deepseek_chat(config, messages)

    output_path = build_notes_output_path(media_path)
    content = compose_notes_markdown(
        media_path,
        corpus_items,
        model_content,
        model_name=model_name,
    )
    output_path.write_text(content, encoding="utf-8")
    return output_path
