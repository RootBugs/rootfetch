"""Search provider abstraction for Rootfetch.

Primary: multi-engine scraper (Google, Bing, Brave, DuckDuckGo).
Fallbacks: SerpAPI, Bing Web Search API, Google CSE.
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from app.config import settings
from app.services.search_scraper import deep_search

logger = logging.getLogger(__name__)


class SearchProvider:
    """Unified interface for web search.

    Priority:
      1. Multi-engine scraper (always tried first — free, no API key needed)
      2. SerpAPI (if configured)
      3. Bing Web Search API (if configured)
      4. Google CSE (if configured)
      5. DuckDuckGo scrape (final fallback)
    """

    def __init__(self) -> None:
        self.serpapi_key = settings.serpapi_api_key
        self.bing_key = settings.bing_api_key
        self.google_key = settings.google_api_key
        self.google_cse_id = settings.google_cse_id

    async def search(
        self,
        query: str,
        max_results: int = 10,
        topic: str = "general",
        days: Optional[int] = None,
        country: str = "us",
        enrich: bool = False,
        llm_rank: bool = False,
    ) -> list[dict]:
        """Execute a search.

        First tries the free multi-engine scraper. Falls back to paid APIs
        only if the scraper returns zero results.
        """
        # 1. Try multi-engine scraper (always, free)
        try:
            scraper_results = await deep_search(
                query=query,
                max_results=max_results,
                enrich=enrich,
                llm_rank=llm_rank,
                topic=topic,
                days=days,
            )
            if scraper_results:
                logger.info("Scraper returned %d results for '%s'", len(scraper_results), query)
                return scraper_results
        except Exception as e:
            logger.warning("Search scraper failed: %s", e)

        # 2. Try SerpAPI
        if self.serpapi_key:
            try:
                results = await self._search_serpapi(query, max_results, topic, days, country)
                if results:
                    logger.info("SerpAPI returned %d results for '%s'", len(results), query)
                    return results
            except Exception as e:
                logger.warning("SerpAPI search failed: %s", e)

        # 3. Try Bing
        if self.bing_key:
            try:
                results = await self._search_bing(query, max_results, country)
                if results:
                    return results
            except Exception as e:
                logger.warning("Bing search failed: %s", e)

        # 4. Try Google CSE
        if self.google_key and self.google_cse_id:
            try:
                results = await self._search_google_cse(query, max_results)
                if results:
                    return results
            except Exception as e:
                logger.warning("Google CSE search failed: %s", e)

        # 5. Final fallback: DuckDuckGo
        try:
            results = await self._search_duckduckgo(query, max_results)
            if results:
                return results
        except Exception as e:
            logger.warning("DuckDuckGo search failed: %s", e)

        logger.warning("All search providers failed for query: %s", query)
        return []

    async def _search_serpapi(
        self,
        query: str,
        max_results: int,
        topic: str,
        days: Optional[int],
        country: str,
    ) -> list[dict]:
        """Search via SerpAPI."""
        params = {
            "api_key": self.serpapi_key,
            "q": query,
            "num": min(max_results, 20),
            "gl": country,
            "engine": "google",
        }

        if topic == "news":
            params["tbm"] = "nws"
            if days:
                params["as_qdr"] = f"d{days}"

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get("https://serpapi.com/search", params=params)
            resp.raise_for_status()
            data = resp.json()

        results = []
        organic = data.get("organic_results", [])
        for r in organic[:max_results]:
            results.append({
                "title": r.get("title", ""),
                "url": r.get("link", ""),
                "content": r.get("snippet", ""),
            })

        return results

    async def _search_bing(
        self,
        query: str,
        max_results: int,
        country: str,
    ) -> list[dict]:
        """Search via Bing Web Search API v7."""
        headers = {"Ocp-Apim-Subscription-Key": self.bing_key}
        params = {
            "q": query,
            "count": min(max_results, 50),
            "mkt": f"en-{country.upper()}",
        }

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://api.bing.microsoft.com/v7.0/search",
                headers=headers,
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()

        results = []
        web_pages = data.get("webPages", {}).get("value", [])
        for r in web_pages[:max_results]:
            results.append({
                "title": r.get("name", ""),
                "url": r.get("url", ""),
                "content": r.get("snippet", ""),
            })

        return results

    async def _search_google_cse(self, query: str, max_results: int) -> list[dict]:
        """Search via Google Custom Search Engine."""
        params = {
            "key": self.google_key,
            "cx": self.google_cse_id,
            "q": query,
            "num": min(max_results, 10),
        }

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://www.googleapis.com/customsearch/v1",
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()

        results = []
        items = data.get("items", [])
        for r in items[:max_results]:
            results.append({
                "title": r.get("title", ""),
                "url": r.get("link", ""),
                "content": r.get("snippet", ""),
            })

        return results

    async def _search_duckduckgo(self, query: str, max_results: int) -> list[dict]:
        """Fallback search via DuckDuckGo HTML endpoint."""
        params = {"q": query}
        headers = {
            "User-Agent": settings.crawl_user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(
                "https://html.duckduckgo.com/html/",
                params=params,
                headers=headers,
            )
            resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        results = []

        for item in soup.select(".result")[:max_results]:
            title_tag = item.select_one(".result__title a")
            snippet_tag = item.select_one(".result__snippet")

            if title_tag:
                url = title_tag.get("href", "")
                if "uddg=" in url:
                    from urllib.parse import parse_qs, urlparse
                    parsed = urlparse(url)
                    qs = parse_qs(parsed.query)
                    url = qs.get("uddg", [""])[0]

                results.append({
                    "title": title_tag.get_text(strip=True),
                    "url": url,
                    "content": snippet_tag.get_text(strip=True) if snippet_tag else "",
                })

        return results
