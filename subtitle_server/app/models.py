from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

_MYSQL = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_ai_ci"}


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("username", name="uq_users_username"),
        _MYSQL,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class Subtitle(Base):
    """同一视频哈希、同一种语言一行。改名后的同一文件仍落在这一行。"""

    __tablename__ = "subtitles"
    __table_args__ = (
        UniqueConstraint("user_id", "video_hash", "subtitle_suffix", name="uq_subtitles_user_hash_suffix"),
        Index("idx_subtitles_user_hash", "user_id", "video_hash"),
        Index("idx_subtitles_hash_shared", "video_hash", "shared"),
        _MYSQL,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    video_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    video_stem: Mapped[str] = mapped_column(String(255), nullable=False)
    subtitle_name: Mapped[str] = mapped_column(String(255), nullable=False)
    subtitle_suffix: Mapped[str] = mapped_column(String(255), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    shared: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    disk_path: Mapped[str] = mapped_column(String(512), nullable=False)


class TagDocumentRow(Base):
    """一份字幕对应一份标签 JSON。同一视频哈希、同一种语言共用这一份。"""

    __tablename__ = "tag_documents"
    __table_args__ = (
        UniqueConstraint("user_id", "video_hash", "subtitle_suffix", name="uq_tags_user_hash_suffix"),
        Index("idx_tags_user_hash", "user_id", "video_hash"),
        _MYSQL,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    video_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    video_stem: Mapped[str] = mapped_column(String(255), nullable=False)
    subtitle_name: Mapped[str] = mapped_column(String(255), nullable=False)
    subtitle_suffix: Mapped[str] = mapped_column(String(255), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    disk_path: Mapped[str] = mapped_column(String(512), nullable=False)
