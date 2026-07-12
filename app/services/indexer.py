"""Search index service for Rootfetch."""
from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select, text, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import SearchCache, CrawledPage
from app.utils import url_hash, extract_domain

logger = logging.getLogger(__name__)

_CACHE_TTL_DEFAULT = 300  # 5 minutes
_CACHE_TTL_NEWS = 120     # 2 minutes for news (more time-sensitive)


def _normalize_query(query: str) -> str:
    """Normalize a query string for cache key generation."""
    return query.lower().strip()


def _query_hash(query: str, topic: str = "general") -> str:
    """Generate a consistent hash for cache lookup."""
    normalized = f"{_normalize_query(query)}|{topic}"
    return hashlib.sha256(normalized.encode()).hexdigest()


class SearchIndex:
    """SQLite FTS-based search index with TTL-aware cache layer."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_cached(
        self,
        query: str,
        topic: str = "general",
        max_results: int = 10,
    ) -> Optional[list[dict]]:
        """Get cached results if they exist and haven't expired.

        Uses exact query hash match + TTL check. Returns None if no
        valid cache entry found.
        """
        qhash = _query_hash(query, topic)
        try:
            stmt = (
                select(SearchCache)
                .where(SearchCache.query_hash == qhash)
                .order_by(SearchCache.created_at.desc())
                .limit(1)
            )
            result = await self.db.execute(stmt)
            row = result.scalar_one_or_none()
            if not row:
                return None

            # Check TTL
            age = datetime.utcnow() - row.created_at
            if age.total_seconds() > row.ttl_seconds:
                logger.debug("Cache expired for '%s' (age: %ss)", query, int(age.total_seconds()))
                return None

            try:
                cached = json.loads(row.results)
                if isinstance(cached, list) and cached:
                    logger.info("Cache HIT for '%s' (%d results, %ds old)", query, len(cached), int(age.total_seconds()))
                    return cached[:max_results]
            except (json.JSONDecodeError, TypeError):
                return None

            return None
        except Exception as e:
            logger.error("Cache lookup error: %s", e)
            return None

    async def search(
        self,
        query: str,
        max_results: int = 10,
        domain: Optional[str] = None,
    ) -> list[dict]:
        """Search across cache and crawled pages.

        Returns up to max_results combined results ranked by relevance.
        """
        results = []

        # 1. Check search cache
        cached = await self._search_cache(query, max_results)
        results.extend(cached)

        # 2. Check crawled pages via FTS
        pages = await self._search_pages(query, max_results, domain)
        results.extend(pages)

        # 3. Deduplicate by URL
        seen_urls = set()
        unique = []
        for r in results:
            if r["url"] not in seen_urls:
                seen_urls.add(r["url"])
                unique.append(r)

        return unique[:max_results]

    async def _search_cache(self, query: str, max_results: int) -> list[dict]:
        """Search cached search results for matching queries."""
        try:
            stmt = (
                select(SearchCache)
                .where(SearchCache.query.ilike(f"%{query}%"))
                .order_by(SearchCache.created_at.desc())
                .limit(max_results)
            )
            result = await self.db.execute(stmt)
            rows = result.scalars().all()
            parsed = []
            for row in rows:
                try:
                    cached_results = json.loads(row.results)
                    if isinstance(cached_results, list):
                        parsed.extend(cached_results)
                except (json.JSONDecodeError, TypeError):
                    pass
            return parsed[:max_results]
        except Exception as e:
            logger.error("Cache search error: %s", e)
            return []

    async def _search_pages(
        self,
        query: str,
        max_results: int,
        domain: Optional[str] = None,
    ) -> list[dict]:
        """Search crawled pages using LIKE-based keyword matching."""
        try:
            stmt = select(CrawledPage).where(
                (CrawledPage.content.ilike(f"%{query}%"))
                | (CrawledPage.title.ilike(f"%{query}%"))
            )
            if domain:
                stmt = stmt.where(CrawledPage.domain == domain)

            stmt = stmt.order_by(CrawledPage.crawled_at.desc()).limit(max_results)
            result = await self.db.execute(stmt)
            rows = result.scalars().all()

            return [
                {
                    "title": r.title or "",
                    "url": r.url,
                    "content": (r.markdown or r.content or "")[:2000],
                    "score": 0.5,
                }
                for r in rows
            ]
        except Exception as e:
            logger.error("Page search error: %s", e)
            return []

    async def index_page(self, page_data: dict) -> None:
        """Index a crawled page for future search."""
        try:
            url = page_data.get("url", "")
            existing = await self.db.execute(
                select(CrawledPage).where(CrawledPage.url_hash == url_hash(url))
            )
            if existing.scalar_one_or_none():
                return  # Already indexed

            page = CrawledPage(
                url=url,
                url_hash=url_hash(url),
                domain=extract_domain(url),
                title=page_data.get("title"),
                content=page_data.get("content"),
                markdown=page_data.get("markdown"),
                metadata=json.dumps(page_data.get("metadata", {})),
                crawl_depth=page_data.get("depth", 0),
            )
            self.db.add(page)
            await self.db.commit()
        except Exception as e:
            logger.error("Indexing error for %s: %s", page_data.get("url", ""), e)
            await self.db.rollback()

    async def cache_search_results(
        self,
        query: str,
        results: list[dict],
        provider: str = "live",
        topic: str = "general",
    ) -> None:
        """Cache search results for a query with TTL.

        TTL is 5 minutes for general, 2 minutes for news.
        """
        try:
            ttl = _CACHE_TTL_NEWS if topic == "news" else _CACHE_TTL_DEFAULT
            cache_entry = SearchCache(
                query=query,
                query_hash=_query_hash(query, topic),
                results=json.dumps(results),
                provider=provider,
                topic=topic,
                ttl_seconds=ttl,
            )
            self.db.add(cache_entry)
            await self.db.commit()
        except Exception as e:
            logger.error("Cache write error: %s", e)
            await self.db.rollback()

    async def clear_cache(self) -> None:
        """Clear all cached search results."""
        try:
            await self.db.execute(delete(SearchCache))
            await self.db.commit()
        except Exception as e:
            logger.error("Cache clear error: %s", e)
            await self.db.rollback()

    async def cleanup_expired(self) -> int:
        """Remove all expired cache entries. Returns count removed."""
        try:
            cutoff = datetime.utcnow()
            stmt = select(SearchCache)
            result = await self.db.execute(stmt)
            rows = result.scalars().all()
            removed = 0
            for row in rows:
                age = cutoff - row.created_at
                if age.total_seconds() > row.ttl_seconds:
                    await self.db.delete(row)
                    removed += 1
            if removed:
                await self.db.commit()
                logger.info("Cleaned %d expired cache entries", removed)
            return removed
        except Exception as e:
            logger.error("Cache cleanup error: %s", e)
            await self.db.rollback()
            return 0
