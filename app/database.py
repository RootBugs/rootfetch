"""Database models and async engine for Rootfetch."""
from __future__ import annotations

import logging

import app.compat  # noqa: F401 - Python 3.14 compat

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Column,
    String,
    Integer,
    Float,
    Boolean,
    DateTime,
    Text,
    ForeignKey,
    select,
    func,
    delete as sa_delete,
)
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.config import settings

logger = logging.getLogger(__name__)


engine = create_async_engine(settings.database_url, echo=False)
async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class APIKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    credits: Mapped[int] = mapped_column(Integer, default=1000)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_premium: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class UsageLog(Base):
    __tablename__ = "usage_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    api_key_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("api_keys.id"), nullable=True)
    endpoint: Mapped[str] = mapped_column(String(128), nullable=False)
    credits_used: Mapped[int] = mapped_column(Integer, default=1)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CrawledPage(Base):
    __tablename__ = "crawled_pages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    url_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    domain: Mapped[str] = mapped_column(String(256), nullable=False)
    title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    markdown: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    page_metadata: Mapped[Optional[str]] = mapped_column("metadata", Text, nullable=True)
    crawl_depth: Mapped[int] = mapped_column(Integer, default=0)
    crawled_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SearchCache(Base):
    __tablename__ = "search_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    query: Mapped[str] = mapped_column(String(512), index=True, nullable=False)
    query_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False, default="")
    results: Mapped[str] = mapped_column(Text, nullable=False)  # JSON string
    provider: Mapped[str] = mapped_column(String(32), default="cache")
    topic: Mapped[str] = mapped_column(String(16), default="general")
    ttl_seconds: Mapped[int] = mapped_column(Integer, default=300)  # 5 min default
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class CrawlJob(Base):
    __tablename__ = "crawl_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending, running, completed, failed
    options: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON
    results: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ResearchJob(Base):
    __tablename__ = "research_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    depth: Mapped[str] = mapped_column(String(16), default="basic")
    options: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON
    result: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


async def init_db() -> None:
    """Create tables and seed default API keys. Also handles migrations."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Migration: add new columns to search_cache if missing
    try:
        async with engine.begin() as conn:
            from sqlalchemy import text as sa_text
            result = await conn.execute(
                sa_text("PRAGMA table_info('search_cache')")
            )
            columns = {row[1] for row in result.fetchall()}
            if "query_hash" not in columns:
                await conn.execute(sa_text(
                    "ALTER TABLE search_cache ADD COLUMN query_hash VARCHAR(64) NOT NULL DEFAULT ''"
                ))
                await conn.execute(sa_text(
                    "ALTER TABLE search_cache ADD COLUMN topic VARCHAR(16) NOT NULL DEFAULT 'general'"
                ))
                await conn.execute(sa_text(
                    "ALTER TABLE search_cache ADD COLUMN ttl_seconds INTEGER NOT NULL DEFAULT 300"
                ))
                logger.info("Migrated search_cache table (added query_hash, topic, ttl_seconds)")
    except Exception as e:
        logger.warning("Migration note: %s", e)

    async with async_session_factory() as session:
        result = await session.execute(select(APIKey).limit(1))
        if result.scalar_one_or_none() is None:
            default_keys = settings.default_api_keys.split(",")
            for entry in default_keys:
                entry = entry.strip()
                if ":" in entry:
                    key, credits = entry.split(":", 1)
                else:
                    key, credits = entry, "1000"
                api_key = APIKey(
                    key=key.strip(),
                    name=f"Auto-seeded {key.strip()}",
                    credits=int(credits.strip()),
                    is_active=True,
                )
                session.add(api_key)
            await session.commit()


async def get_db() -> AsyncSession:
    """Yield an async database session."""
    async with async_session_factory() as session:
        yield session
