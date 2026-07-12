"""Web crawler service for Rootfetch."""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.config import settings
from app.services.extractor import extract_content, fetch_url
from app.utils import extract_domain

logger = logging.getLogger(__name__)


class WebCrawler:
    """BFS-based web crawler with configurable depth and filtering."""

    def __init__(
        self,
        start_url: str,
        max_depth: int = 2,
        max_pages: int = 50,
        include_paths: Optional[list[str]] = None,
        exclude_paths: Optional[list[str]] = None,
        include_domains: Optional[list[str]] = None,
        exclude_domains: Optional[list[str]] = None,
        extract_format: str = "markdown",
    ) -> None:
        self.start_url = start_url
        self.max_depth = max_depth
        self.max_pages = max_pages
        self.include_paths = include_paths
        self.exclude_paths = exclude_paths
        self.include_domains = include_domains
        self.exclude_domains = exclude_domains
        self.extract_format = extract_format

        self.visited: set[str] = set()
        self.results: list[dict] = []
        self.total_pages = 0

    def _should_follow(self, url: str) -> bool:
        """Determine if a URL should be crawled based on filters."""
        parsed = urlparse(url)

        # Skip non-HTTP(S)
        if parsed.scheme not in ("http", "https"):
            return False

        # Domain filters
        domain = parsed.netloc.lower()
        if self.include_domains:
            if not any(d in domain for d in self.include_domains):
                return False
        if self.exclude_domains:
            if any(d in domain for d in self.exclude_domains):
                return False

        # Path filters
        path = parsed.path
        if self.include_paths:
            if not any(re.search(p, path) for p in self.include_paths):
                return False
        if self.exclude_paths:
            if any(re.search(p, path) for p in self.exclude_paths):
                return False

        return True

    def _extract_links(self, html: str, base_url: str) -> list[str]:
        """Extract and normalize all links from HTML."""
        links = []
        try:
            soup = BeautifulSoup(html, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                if href and not href.startswith("#") and not href.startswith("javascript:"):
                    absolute = urljoin(base_url, href)
                    if self._should_follow(absolute):
                        links.append(absolute)
        except Exception as e:
            logger.error("Link extraction error: %s", e)
        return links

    async def _crawl_single(self, url: str, depth: int) -> Optional[str]:
        """Crawl a single URL and extract content."""
        if url in self.visited:
            return None
        self.visited.add(url)

        html = await fetch_url(url)
        if not html:
            return None

        content = extract_content(html, url=url, extract_format=self.extract_format)
        if content:
            title = None
            try:
                soup = BeautifulSoup(html, "html.parser")
                title = soup.title.string.strip() if soup.title else None
            except Exception:
                pass

            self.results.append({
                "url": url,
                "title": title or "",
                "content": content,
                "depth": depth,
            })
            self.total_pages += 1

        return html  # Return HTML for link extraction on non-leaf depths

    async def crawl(self) -> list[dict]:
        """Run BFS crawl starting from start_url."""
        queue: list[tuple[str, int]] = [(self.start_url, 0)]

        while queue and self.total_pages < self.max_pages:
            url, depth = queue.pop(0)

            if depth > self.max_depth:
                continue

            html = await self._crawl_single(url, depth)
            if html is not None and depth < self.max_depth:
                links = self._extract_links(html, url)
                for link in links:
                    if link not in self.visited and self.total_pages < self.max_pages:
                        queue.append((link, depth + 1))

        return self.results

    async def map_site(self, search: Optional[str] = None) -> list[str]:
        """Discover URLs from start_url without extracting content."""
        queue: list[tuple[str, int]] = [(self.start_url, 0)]
        discovered: list[str] = []
        visited = set()

        while queue and len(discovered) < self.max_pages:
            url, depth = queue.pop(0)

            if url in visited:
                continue
            visited.add(url)

            if depth > self.max_depth:
                continue

            html = await fetch_url(url)
            if not html:
                continue

            if depth == 0 or search is None:
                if self._should_follow(url) and url not in discovered:
                    discovered.append(url)

            if depth < self.max_depth:
                links = self._extract_links(html, url)
                for link in links:
                    if link not in visited and len(discovered) < self.max_pages:
                        queue.append((link, depth + 1))

        return discovered
