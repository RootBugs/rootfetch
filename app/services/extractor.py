"""Content extraction service for Rootfetch."""
from __future__ import annotations

import importlib
import logging
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from app.config import settings

logger = logging.getLogger(__name__)

# Lazy-loaded libraries with graceful fallback
_trafilatura = None
_readability = None


def _get_trafilatura():
    """Lazy import trafilatura to avoid Python 3.14 compat issues at import time."""
    global _trafilatura
    if _trafilatura is None:
        try:
            import app.compat  # noqa: F401
            import trafilatura
            _trafilatura = trafilatura
        except Exception as e:
            logger.warning("trafilatura import failed: %s", e)
            _trafilatura = False  # Sentinel
    return _trafilatura if _trafilatura is not False else None


def _get_readability():
    """Lazy import readability to avoid Python 3.14 compat issues at import time."""
    global _readability
    if _readability is None:
        try:
            import app.compat  # noqa: F401
            from readability import Document
            _readability = Document
        except Exception as e:
            logger.warning("readability import failed: %s", e)
            _readability = False  # Sentinel
    return _readability if _readability is not False else None


async def fetch_url(url: str, timeout: int = 30) -> Optional[str]:
    """Fetch raw HTML from a URL."""
    headers = {
        "User-Agent": settings.crawl_user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
    proxy_url_str = None
    if settings.proxy_url:
        proxy_url_str = settings.proxy_url

    try:
        client_kwargs = dict(
            headers=headers,
            timeout=timeout,
            follow_redirects=True,
        )
        if proxy_url_str:
            client_kwargs["proxy"] = proxy_url_str

        async with httpx.AsyncClient(**client_kwargs) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.text
    except Exception as e:
        logger.error("Failed to fetch %s: %s", url, e)
        return None


def extract_with_trafilatura(html: str, url: str) -> Optional[str]:
    """Extract content using trafilatura."""
    trafilatura = _get_trafilatura()
    if trafilatura is None:
        return None
    try:
        return trafilatura.extract(html, url=url, include_links=True, output_format="markdown")
    except Exception as e:
        logger.warning("trafilatura extraction failed: %s", e)
        return None


def extract_with_readability(html: str) -> Optional[str]:
    """Extract content using readability-lxml."""
    Document = _get_readability()
    if Document is None:
        return None
    try:
        doc = Document(html)
        return doc.summary(html_partial=True)
    except Exception as e:
        logger.warning("readability extraction failed: %s", e)
        return None


def extract_with_soup(html: str) -> str:
    """Extract content using BeautifulSoup as final fallback."""
    try:
        soup = BeautifulSoup(html, "html.parser")
        # Remove noise
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        return soup.get_text(separator="\n", strip=True)
    except Exception as e:
        logger.error("BeautifulSoup extraction failed: %s", e)
        return ""


def html_to_markdown(html: str) -> str:
    """Convert HTML to Markdown using markdownify if available, else fallback."""
    try:
        from markdownify import markdownify as md

        return md(html, heading_style="ATX")
    except ImportError:
        pass

    # Fallback: basic text extraction
    try:
        soup = BeautifulSoup(html, "html.parser")
        return soup.get_text(separator="\n", strip=True)
    except Exception:
        return html


def extract_content(html: str, url: str = "", extract_format: str = "markdown") -> str:
    """Extract clean content from HTML using best available tool."""
    content = None

    # Try trafilatura first (best results)
    if extract_format == "markdown":
        content = extract_with_trafilatura(html, url)

    # Fallback to readability
    if not content:
        raw_html = extract_with_readability(html)
        if raw_html:
            if extract_format == "markdown":
                content = html_to_markdown(raw_html)
            else:
                content = BeautifulSoup(raw_html, "html.parser").get_text(separator="\n", strip=True)

    # Final fallback to BeautifulSoup
    if not content:
        content = extract_with_soup(html)

    return content.strip() if content else ""


async def extract_url(url: str, extract_format: str = "markdown") -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Extract content from a single URL. Returns (title, content, error)."""
    html = await fetch_url(url)
    if not html:
        return None, None, f"Failed to fetch {url}"

    # Extract title
    title = None
    try:
        soup = BeautifulSoup(html, "html.parser")
        title = soup.title.string.strip() if soup.title else None
    except Exception:
        pass

    content = extract_content(html, url=url, extract_format=extract_format)
    if not content:
        return title, None, f"Failed to extract content from {url}"

    return title, content, None


async def extract_urls_batch(
    urls: list[str],
    extract_format: str = "markdown",
    query: Optional[str] = None,
) -> tuple[list[dict], list[dict]]:
    """Extract content from multiple URLs. Returns (results, failed_results)."""
    import asyncio

    tasks = [extract_url(url, extract_format) for url in urls]
    outcomes = await asyncio.gather(*tasks, return_exceptions=True)

    results = []
    failed = []

    for url, outcome in zip(urls, outcomes):
        if isinstance(outcome, Exception):
            failed.append({"url": url, "error": str(outcome)})
            continue
        title, content, error = outcome
        if error or not content:
            failed.append({"url": url, "error": error or "No content extracted"})
        else:
            result = {"url": url, "title": title or "", "content": content}
            results.append(result)

    # Re-rank by query relevance if query provided
    if query and results:
        results = _rerank_by_query(results, query)

    return results, failed


def _rerank_by_query(results: list[dict], query: str) -> list[dict]:
    """Score results by how many query terms appear in the content."""
    query_terms = query.lower().split()
    for r in results:
        content_lower = (r.get("title", "") + " " + r.get("content", "")).lower()
        r["score"] = sum(term in content_lower for term in query_terms) / max(len(query_terms), 1)
    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    return results
