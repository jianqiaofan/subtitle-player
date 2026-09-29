from __future__ import annotations

from datetime import datetime, timedelta, timezone

_MIN_AT = datetime(2000, 1, 1, tzinfo=timezone.utc)
_FUTURE_SKEW = timedelta(hours=48)


def utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def parse_utc(value: object, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} 需要带时区的时间")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{field_name} 不是有效时间") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} 需要带时区，例如 2026-09-29T03:00:00Z")
    current = parsed.astimezone(timezone.utc)
    if current < _MIN_AT or current > datetime.now(timezone.utc) + _FUTURE_SKEW:
        raise ValueError(f"{field_name} 超出允许范围")
    return current


def iso_z(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def iso_z_seconds(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")
