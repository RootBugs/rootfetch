"""Multi-engine web search scraper for Rootfetch.

Scrapes Bing, DuckDuckGo, Mojeek, and Bing News directly using curl_cffi
(browser TLS impersonation) to bypass anti-bot protections. Results are
merged, deduplicated, cross-ranked, and optionally enriched with full page
content.

Engines:
  - Startpage (general) — Google results via privacy proxy (best coverage)
  - Bing (general)      — reliable, high-quality results
  - DuckDuckGo (general) — privacy-first, good for diverse perspectives
  - Mojeek (general)    — independent search index, privacy-focused
  - Bing News           — news-specific search with date filtering

Note: Google is intentionally excluded — its JS-only rendering cannot be
      scraped without a headless browser.
Note: Yahoo is excluded — returns HTTP 500 to all automated requests.
Note: Brave is excluded — curl_cffi has a known write-error (curl:23) with
      Brave's response format.
"""
from __future__ import annotations

import asyncio
import json as _json
import logging
import random
import re as _re
import time
from typing import Optional
from urllib.parse import urlparse, parse_qs, quote, unquote

from bs4 import BeautifulSoup
from curl_cffi.requests import AsyncSession as CurlAsyncSession

from app.config import settings
from app.services.extractor import fetch_url, extract_content
from app.services.synthesizer import local_rerank

logger = logging.getLogger(__name__)

# ── Browser profile pool (matched to curl_cffi impersonation targets) ──────

BROWSER_PROFILES = [
    "chrome124",
    "chrome123",
    "chrome131",
    "safari17_0",
    "firefox135",
]


async def smart_fetch(
    url: str,
    timeout: int = 15,
    max_retries: int = 2,
    allow_redirects: bool = True,
    method: str = "GET",
    data: Optional[dict] = None,
) -> Optional[str]:
    """Fetch URL using curl_cffi with browser TLS impersonation.

    This bypasses Cloudflare, Turnstile, and most anti-bot protections
    by mimicking a real browser TLS handshake at the network level.
    """
    last_error = None
    for attempt in range(max_retries + 1):
        browser = random.choice(BROWSER_PROFILES)
        try:
            async with CurlAsyncSession(impersonate=browser, timeout=timeout) as session:
                if method.upper() == "POST" and data:
                    resp = await session.post(url, data=data, allow_redirects=allow_redirects)
                else:
                    resp = await session.get(url, allow_redirects=allow_redirects)
                resp.raise_for_status()
                return resp.text
        except Exception as e:
            last_error = e
            logger.debug("Fetch attempt %d/%d [%s] failed for %s: %s", attempt + 1, max_retries + 1, browser, url, e)
            if attempt < max_retries:
                await asyncio.sleep(1.0 + random.random())
    logger.warning("All fetch attempts failed for %s: %s", url, last_error)
    return None


# ── Query Expander ────────────────────────────────────────────────────────

class QueryExpander:
    """Generate multiple search query variants from a single user query."""

    @staticmethod
    def expand(query: str) -> list[str]:
        """Generate search variants. Returns the original + 2-3 alternatives."""
        query = query.strip()
        variants = [query]

        # Always keep the original as a solid variant
        variants.append(query)

        # For longer queries, add a keyword-focused variant
        words = query.split()
        key_terms = [w for w in words if len(w) > 3 and w.lower() not in (
            "what", "how", "why", "the", "and", "for", "are", "that", "this", "with",
            "does", "can", "get", "was", "its", "has", "not",
        )]
        if key_terms and len(key_terms) < len(words):
            variants.append(" ".join(key_terms[:5]))

        # Deduplicate while preserving order
        seen = set()
        unique = []
        for v in variants:
            if v.lower() not in seen:
                seen.add(v.lower())
                unique.append(v)

        return unique


# ── Engine Scrapers ────────────────────────────────────────────────────────

async def scrape_bing(query: str, max_results: int = 10) -> list[dict]:
    """Scrape Bing search results. Most reliable engine."""
    url = f"https://www.bing.com/search?q={quote(query)}&setlang=en&count={min(max_results + 5, 30)}"
    html = await smart_fetch(url)
    if not html:
        return []

    try:
        soup = BeautifulSoup(html, "html.parser")
        results = []

        for item in soup.select("li.b_algo")[:max_results]:
            title_tag = item.select_one("h2 a")
            if not title_tag:
                continue

            href = title_tag.get("href", "")
            # Bing wraps URLs in /ck/a redirect. Extract real URL from cite text.
            if "bing.com/ck/a" in href or href.startswith("/ck/a"):
                cite = item.select_one(".b_attribution cite")
                if cite:
                    # Strip soft hyphens (\\u00ad) and other formatting chars
                    clean = cite.get_text().replace("\u00ad", "").replace("\u200b", "").strip()
                    if clean.startswith("http"):
                        href = clean

            if not href.startswith("http"):
                continue

            title = title_tag.get_text(strip=True)
            snippet_tag = item.select_one(".b_caption p, .b_lineclamp2")
            snippet = snippet_tag.get_text(strip=True) if snippet_tag else ""

            results.append({
                "title": title,
                "url": href,
                "content": snippet or title,
                "engine": "bing",
            })

        return results
    except Exception as e:
        logger.error("Bing parse error: %s", e)
        return []


async def scrape_duckduckgo(query: str, max_results: int = 10) -> list[dict]:
    """Scrape DuckDuckGo HTML search results."""
    html = await smart_fetch(
        "https://html.duckduckgo.com/html/",
        method="POST",
        data={"q": query},
    )
    if not html:
        return []

    try:
        soup = BeautifulSoup(html, "html.parser")
        results = []

        for item in soup.select(".result")[:max_results]:
            title_tag = item.select_one(".result__title a")
            snippet_tag = item.select_one(".result__snippet, .snippet")

            if not title_tag:
                continue

            href = title_tag.get("href", "")
            # Extract real URL from DDG redirect wrapper
            if "uddg=" in href:
                parsed = urlparse(href)
                qs = parse_qs(parsed.query)
                href = qs.get("uddg", [""])[0]

            if not href.startswith("http"):
                continue

            title = title_tag.get_text(strip=True)
            snippet = snippet_tag.get_text(strip=True) if snippet_tag else ""

            results.append({
                "title": title,
                "url": href,
                "content": snippet or title,
                "engine": "duckduckgo",
            })

        return results
    except Exception as e:
        logger.error("DuckDuckGo parse error: %s", e)
        return []


# ── Result Merger ─────────────────────────────────────────────────────────

class ResultMerger:
    """Merge, deduplicate, and cross-rank results from multiple engines."""

    @staticmethod
    def merge(results_by_engine: dict[str, list[dict]]) -> list[dict]:
        """Merge results from all engines, dedup by URL, rank by cross-engine signals."""
        url_sources: dict[str, dict] = {}
        url_engine_count: dict[str, int] = {}
        url_positions: dict[str, list[int]] = {}

        for engine, engine_results in results_by_engine.items():
            for pos, result in enumerate(engine_results):
                url = result.get("url", "")
                if not url:
                    continue

                # Normalize URL for dedup
                parsed = urlparse(url)
                norm_path = parsed.path.rstrip("/") or "/"
                norm_url = f"{parsed.scheme}://{parsed.netloc.lower()}{norm_path}"

                if norm_url not in url_sources:
                    url_sources[norm_url] = dict(result)  # copy
                    url_engine_count[norm_url] = set()  # Track unique engines
                    url_positions[norm_url] = []

                # Count unique engines only
                url_engine_count[norm_url].add(engine)
                url_positions[norm_url].append(pos)

                # Keep the best title/snippet (prefer longer)
                existing = url_sources[norm_url]
                if len(result.get("title", "")) > len(existing.get("title", "")):
                    existing["title"] = result["title"]
                if result.get("content") and len(result.get("content", "")) > len(existing.get("content", "")):
                    existing["content"] = result["content"]
                # Track which engines found it
                if "engines" not in existing:
                    existing["engines"] = set()
                existing["engines"].add(engine)

        # Calculate cross-engine score
        scored = []
        active_engine_count = len(results_by_engine)
        max_engines = max(active_engine_count, 1)
        for norm_url, result in url_sources.items():
            engine_count = len(url_engine_count.get(norm_url, set()))
            positions = url_positions.get(norm_url, [10])
            avg_position = sum(positions) / max(len(positions), 1)

            # Score formula (0.0 - 1.0):
            # - Cross-engine bonus: +0.5 for being found by multiple engines
            # - Position bonus: +0.3 for early positions
            # - Base: 0.2
            cross_engine_score = min(engine_count / max_engines, 1.0) * 0.5
            position_score = max(0, 1.0 - (avg_position / 20.0)) * 0.3
            total_score = 0.2 + cross_engine_score + position_score

            result["score"] = round(total_score, 3)
            result["cross_engine_count"] = engine_count
            result["engines"] = list(result.get("engines", []))  # Convert set to list
            result["url"] = norm_url
            scored.append(result)

        # Sort by score descending
        scored.sort(key=lambda r: r.get("score", 0), reverse=True)
        return scored


# ── Content Enricher ──────────────────────────────────────────────────────

async def enrich_results(
    results: list[dict],
    max_enrich: int = 5,
) -> list[dict]:
    """Fetch full page content for top results using the extractor."""
    enriched = 0
    for r in results:
        if enriched >= max_enrich:
            break
        url = r.get("url", "")
        if not url or r.get("raw_content"):
            continue
        try:
            html = await fetch_url(url)
            if html:
                full_content = extract_content(html, url=url, extract_format="markdown")
                if full_content and len(full_content) > len(r.get("content", "")):
                    r["raw_content"] = full_content
                    r["content_length"] = len(full_content)
                    enriched += 1
        except Exception as e:
            logger.debug("Enrichment failed for %s: %s", url, e)
    return results


# ── Mojeek Scraper ─────────────────────────────────────────────────────────

async def scrape_mojeek(query: str, max_results: int = 10) -> list[dict]:
    """Scrape Mojeek search results. Independent search index, privacy-first."""
    url = f"https://www.mojeek.com/search?q={quote(query)}"
    html = await smart_fetch(url)
    if not html:
        return []

    try:
        soup = BeautifulSoup(html, "html.parser")
        results = []

        # Mojeek results: <h2 class="title"> with <a>, snippet in <p class="s">
        for item in soup.select("div.results-item, div.result, li.result")[:max_results]:
            title_tag = item.select_one("h2 a, h2.title a, .title a")
            if not title_tag:
                continue

            href = title_tag.get("href", "")
            if not href.startswith("http"):
                continue

            title = title_tag.get_text(strip=True)
            snippet_tag = item.select_one(".s, .snippet, p.s, .teaser")
            snippet = snippet_tag.get_text(strip=True) if snippet_tag else ""

            results.append({
                "title": title,
                "url": href,
                "content": snippet or title,
                "engine": "mojeek",
            })

        return results
    except Exception as e:
        logger.error("Mojeek parse error: %s", e)
        return []


# ── Startpage Scraper (Google via proxy) ────────────────────────────────────

async def scrape_startpage(query: str, max_results: int = 10) -> list[dict]:
    """Scrape Startpage search results — effectively Google via a privacy proxy.

    Startpage returns Google's search results without JavaScript requirements,
    making this the closest we can get to Google-quality results without a
    headless browser.
    """
    url = f"https://www.startpage.com/search?query={quote(query)}"
    html = await smart_fetch(url)
    if not html:
        return []

    try:
        soup = BeautifulSoup(html, "html.parser")
        results = []

        for item in soup.select("div.result")[:max_results]:
            # Title link: <a class="result-title result-link ...">
            title_tag = item.select_one("a.result-title, a[class*=result-title]")
            if not title_tag:
                continue

            href = title_tag.get("href", "")
            if not href.startswith("http"):
                continue

            title = title_tag.get_text(strip=True)
            if not title:
                continue

            # Snippet: <p class="description ...">
            snippet_tag = item.select_one("p.description, [class*=description]")
            snippet = snippet_tag.get_text(strip=True) if snippet_tag else ""

            # Published date (often prepended to snippet)
            published_date = ""
            if snippet:
                date_match = _re.match(
                    r"(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4})",
                    snippet,
                )
                if date_match:
                    published_date = date_match.group(1)
                    snippet = snippet[len(date_match.group(0)):].lstrip(".").strip()

            results.append({
                "title": title,
                "url": href,
                "content": snippet or title,
                "engine": "startpage",
                "published_date": published_date,
            })

        return results
    except Exception as e:
        logger.error("Startpage parse error: %s", e)
        return []


# ── Bing News Scraper ──────────────────────────────────────────────────────

async def scrape_bing_news(
    query: str,
    max_results: int = 10,
    days: Optional[int] = None,
) -> list[dict]:
    """Scrape Bing News search results with optional date filtering.

    Bing News HTML is flat: results are direct <a> tags with external URLs,
    dates are in sibling div.publisher-part elements with relative time
    strings like "6h" (6 hours) or "1d" (1 day).
    """
    url = f"https://www.bing.com/news/search?q={quote(query)}&setlang=en"
    if days:
        url += f"&qft=interval%3d%22{days}%22"
    html = await smart_fetch(url)
    if not html:
        return []

    try:
        soup = BeautifulSoup(html, "html.parser")
        results = []
        seen_urls: set[str] = set()

        # Bing News renders results as direct external links.
        # Collect all external links and their associated dates.
        external_links: list[tuple[str, str, str]] = []  # (url, title, date)

        for a in soup.find_all("a", href=True):
            href = a["href"]
            # Skip Bing/Microsoft links, javascript, anchors, ads
            if any(skip in href for skip in [
                "bing.com", "microsoft.com", "javascript:", "#", ".js",
            ]):
                continue
            if not href.startswith("http"):
                continue

            title = a.get_text(strip=True)
            # Skip very short text and empty titles
            if not title or len(title) < 10:
                continue

            if href in seen_urls:
                continue
            seen_urls.add(href)

            # Find the closest publisher-part element for the date
            date_text = ""
            # Check previous siblings first, then parent, then next siblings
            for direction in ["previous_sibling", "previous", "next_sibling", "next"]:
                neighbor = getattr(a, direction, None)
                if neighbor:
                    cls = " ".join(neighbor.get("class", [])) if hasattr(neighbor, "get") else ""
                    if "pub" in cls.lower() or "date" in cls.lower() or "time" in cls.lower():
                        date_text = neighbor.get_text(strip=True)
                        break

            if not date_text:
                # Try finding date in parent's children (siblings of the link)
                parent = a.parent
                if parent:
                    # Look for any child element containing relative time text
                    for child in parent.find_all(True):
                        child_text = child.get_text(strip=True).replace("\u200e", "").replace("\u200f", "")
                        if _re.match(r"^\d+[hdwms]$", child_text):
                            date_text = child_text
                            break

            if not date_text:
                # Fallback: search nearby for ns_sc_tm class
                parent = a.parent
                if parent:
                    tm_elem = parent.select_one(".ns_sc_tm, .source.set_top, [class*=biglogo]")
                    if tm_elem:
                        date_text = tm_elem.get_text(strip=True).replace("\u200e", "").replace("\u200f", "")

            # Clean and parse date
            published_date = ""
            if date_text:
                date_text = date_text.replace("\u200e", "").replace("\u200f", "").strip()
                match = _re.match(r"(\d+)([hdwms])", date_text)
                if match:
                    num = int(match.group(1))
                    unit = match.group(2)
                    unit_map = {"h": "hours", "d": "days", "w": "weeks", "m": "months", "s": "seconds"}
                    published_date = f"{num} {unit_map.get(unit, unit)} ago"
                else:
                    published_date = date_text

            external_links.append((href, title, published_date))

        for href, title, published_date in external_links[:max_results]:
            results.append({
                "title": title,
                "url": href,
                "content": title,
                "engine": "bing_news",
                "published_date": published_date,
            })

        return results
    except Exception as e:
        logger.error("Bing News parse error: %s", e)
        return []


# ── LLM Reranker ─────────────────────────────────────────────────────────

async def llm_rerank(query: str, results: list[dict]) -> list[dict]:
    """Use LLM to rerank results by relevance to the query."""
    if not settings.openai_api_key or not results:
        return results

    try:
        from openai import AsyncOpenAI

        items = []
        for i, r in enumerate(results[:10]):
            items.append(
                f"[{i+1}] {r['title']}\n"
                f"    URL: {r['url']}\n"
                f"    Snippet: {r.get('content', '')[:200]}"
            )

        context = "\n\n".join(items)
        client = AsyncOpenAI(api_key=settings.openai_api_key)
        resp = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "You are a search result re-ranker. Given a query and results "
                    "(title, URL, snippet), re-order them by relevance. "
                    "Return ONLY comma-separated item numbers in preferred order, "
                    "e.g. '3,1,5,2,4'. No other text.",
                },
                {
                    "role": "user",
                    "content": f"Query: {query}\n\nResults:\n{context}\n\nRanked order:",
                },
            ],
            max_tokens=100,
            temperature=0.1,
        )

        ranked_text = resp.choices[0].message.content.strip()
        indices = []
        for part in ranked_text.split(","):
            part = part.strip()
            try:
                idx = int(part) - 1
                if 0 <= idx < len(results):
                    indices.append(idx)
            except ValueError:
                continue

        if not indices:
            return results

        reranked = [results[i] for i in indices if i < len(results)]
        for i in range(len(results)):
            if i not in indices:
                reranked.append(results[i])

        return reranked[: len(results)]
    except Exception as e:
        logger.warning("LLM reranking failed: %s", e)
        return results


# ── Orchestrator ──────────────────────────────────────────────────────────

async def deep_search(
    query: str,
    max_results: int = 10,
    enrich: bool = False,
    llm_rank: bool = False,
    topic: str = "general",
    days: Optional[int] = None,
) -> list[dict]:
    """Full multi-engine search pipeline. Zero paid APIs required.

    1. Expand query into 1-3 variants
    2. Scrape 3+ engines in parallel for each variant
    3. Merge, deduplicate, and cross-rank results
    4. TF-IDF semantic reranking (free, no LLM needed)
    5. Enrich top results with full page content (optional)

    When topic='news', uses Bing News instead of general engines.
    """
    start = time.time()
    logger.info("Deep search: '%s' (topic=%s, days=%s)", query, topic, days)

    # Step 1: Expand
    expanded = QueryExpander.expand(query)

    # Step 2: Scrape engines for each variant
    all_engine_results: dict[str, list[dict]] = {}

    if topic == "news":
        # News mode: use Bing News
        engines = {"bing_news": scrape_bing_news}
    else:
        # General mode: use all available engines
        engines = {
            "startpage": scrape_startpage,  # ⬅ Google results via proxy
            "bing": scrape_bing,
            "duckduckgo": scrape_duckduckgo,
            "mojeek": scrape_mojeek,
        }

    for variant in expanded:
        tasks = []
        engine_names = []
        for name, func in engines.items():
            if name == "bing_news":
                tasks.append(func(variant, max_results=max(10, max_results), days=days))
            else:
                tasks.append(func(variant, max_results=max(10, max_results)))
            engine_names.append(name)

        outcomes = await asyncio.gather(*tasks, return_exceptions=True)

        for name, outcome in zip(engine_names, outcomes):
            if isinstance(outcome, Exception):
                logger.debug("Engine %s failed: %s", name, outcome)
                continue
            if name not in all_engine_results:
                all_engine_results[name] = []
            all_engine_results[name].extend(outcome)

    logger.debug("Engines returned: %s", {k: len(v) for k, v in all_engine_results.items()})

    # Step 3: Merge and cross-rank
    merged = ResultMerger.merge(all_engine_results)
    logger.debug("Merged to %d unique results", len(merged))

    # Trim to max_results (keep extras)
    merged = merged[: max_results + 5]

    # Step 4: TF-IDF semantic reranking (free, no API key needed)
    if merged:
        merged = await local_rerank(query, merged)

    # Step 5: Enrich with full content
    if enrich:
        merged = await enrich_results(merged, max_enrich=min(5, max_results))

    merged = merged[:max_results]
    elapsed = round(time.time() - start, 2)
    logger.info("Deep search completed in %ss, %d results", elapsed, len(merged))

    return merged


# ── Image Search ──────────────────────────────────────────────────────────

async def scrape_bing_images(query: str, max_results: int = 8) -> list[dict]:
    """Scrape Bing Images search results (free, no API)."""
    url = f"https://www.bing.com/images/search?q={quote(query)}&count={max_results + 5}"
    html = await smart_fetch(url)
    if not html:
        return []

    try:
        soup = BeautifulSoup(html, "html.parser")
        results = []
        seen_urls: set[str] = set()

        # Bing Images uses a masonry grid with <img> tags inside `a.iusc` links
        for item in soup.select("a.iusc"):
            if len(results) >= max_results:
                break

            # Bing stores metadata in the `m` attribute as JSON
            m_attr = item.get("m", "")
            if not m_attr:
                # Fallback: direct img src
                img = item.select_one("img")
                if img and img.get("src"):
                    src = img["src"]
                    if src.startswith("http") and src not in seen_urls:
                        seen_urls.add(src)
                        alt = img.get("alt", "") or ""
                        results.append({
                            "url": src,
                            "alt": alt,
                            "title": alt,
                            "engine": "bing_images",
                        })
                continue

            try:
                data = _json.loads(m_attr)
            except (_json.JSONDecodeError, TypeError):
                continue

            # Bing stores the full-size image URL as "murl" (media URL)
            img_url = data.get("murl") or data.get("imgurl", "")
            if not img_url or img_url in seen_urls:
                continue
            seen_urls.add(img_url)

            # Get title from the alt text of the thumbnail
            img_tag = item.select_one("img")
            title = img_tag.get("alt", "") if img_tag else ""

            results.append({
                "url": img_url,
                "alt": title,
                "title": title,
                "engine": "bing_images",
                "source": data.get("purl", ""),
            })

        return results
    except Exception as e:
        logger.error("Bing Images parse error: %s", e)
        return []
