from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_url: str
    data_dir: Path
    jwt_secret: str
    token_ttl_seconds: int = 30 * 24 * 3600
    password_rounds: int = 600_000

    @classmethod
    def from_env(cls) -> Settings:
        database_url = os.environ.get("DATABASE_URL", "").strip()
        jwt_secret = os.environ.get("JWT_SECRET", "").strip()
        data_dir = Path(os.environ.get("DATA_DIR", "/var/lib/subtitle-sync"))
        if not database_url:
            raise RuntimeError("缺少环境变量 DATABASE_URL")
        if len(jwt_secret) < 32:
            raise RuntimeError("JWT_SECRET 至少需要 32 个字符")
        return cls(
            database_url=database_url,
            data_dir=data_dir,
            jwt_secret=jwt_secret,
        )
