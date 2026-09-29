from __future__ import annotations

import re

_USERNAME = re.compile(r"[\w.-]{3,32}")
_HAS_LETTER_OR_DIGIT = re.compile(r"[^\W_]")
_SUBTITLE_SUFFIXES = (".srt", ".vtt")


class InvalidName(ValueError):
    pass


def validate_username(username: str) -> str:
    if not isinstance(username, str):
        raise InvalidName("用户名需要 3 到 32 个字符，只能包含字母、数字、下划线、点或短横线")
    cleaned = username.strip()
    if _USERNAME.fullmatch(cleaned) is None or _HAS_LETTER_OR_DIGIT.search(cleaned) is None:
        raise InvalidName("用户名需要 3 到 32 个字符，只能包含字母、数字、下划线、点或短横线")
    return cleaned


def validate_password(password: str) -> str:
    if not isinstance(password, str) or len(password) < 8 or len(password) > 72:
        raise InvalidName("密码需要 8 到 72 个字符")
    return password


def validate_video_hash(video_hash: str) -> str:
    if not isinstance(video_hash, str) or re.fullmatch(r"[0-9a-f]{64}", video_hash) is None:
        raise InvalidName("视频哈希无效")
    return video_hash


def validate_video_stem(video_stem: str) -> str:
    if not isinstance(video_stem, str):
        raise InvalidName("视频主文件名无效")
    cleaned = video_stem.strip()
    if (
        not cleaned
        or len(cleaned) > 255
        or cleaned in {".", ".."}
        or "/" in cleaned
        or "\\" in cleaned
        or "\x00" in cleaned
    ):
        raise InvalidName("视频主文件名无效")
    return cleaned


def subtitle_stem(subtitle_name: str) -> str:
    lower = subtitle_name.lower()
    for suffix in _SUBTITLE_SUFFIXES:
        if lower.endswith(suffix):
            return subtitle_name[: -len(suffix)]
    raise InvalidName("字幕文件名必须以 .srt 或 .vtt 结尾")


def validate_subtitle_name(subtitle_name: str, video_stem: str) -> str:
    if not isinstance(subtitle_name, str):
        raise InvalidName("字幕文件名无效")
    if subtitle_name != subtitle_name.strip():
        raise InvalidName("字幕文件名无效")
    cleaned = subtitle_name.strip()
    if (
        not cleaned
        or len(cleaned) > 255
        or cleaned in {".", ".."}
        or "/" in cleaned
        or "\\" in cleaned
        or "\x00" in cleaned
    ):
        raise InvalidName("字幕文件名无效")
    stem = subtitle_stem(cleaned)
    if not stem or stem == cleaned:
        raise InvalidName("字幕文件名必须以 .srt 或 .vtt 结尾，且不能包含路径")
    if stem != video_stem and not stem.startswith(f"{video_stem}_"):
        raise InvalidName("字幕文件名与视频主文件名不匹配")
    return cleaned


def subtitle_suffix(subtitle_name: str, video_stem: str) -> str:
    """语言部分。第1课.srt -> .srt，第1课_中文.srt -> _中文.srt。改名后用它认同一份字幕。"""
    if not subtitle_name.startswith(video_stem):
        raise InvalidName("字幕文件名与视频主文件名不匹配")
    suffix = subtitle_name[len(video_stem) :]
    lower = suffix.lower()
    if suffix.startswith((".", "_")) and (lower.endswith(".srt") or lower.endswith(".vtt")):
        return suffix
    raise InvalidName("字幕文件名与视频主文件名不匹配")
