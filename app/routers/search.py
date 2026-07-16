"""Search router for Rootfetch API."""
from __future__ import annotations

import logging
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import verify_api_key, log_usage
from app.database import APIKey, get_db
from app.models import SearchRequest, SearchResponse, SearchResult
from app.services.extractor import extract_urls_batch
from app.services.indexer import SearchIndex
from app.services.search_provider import SearchProvider
from app.services.synthesizer import generate_answer, generate_followup_questions

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/search", tags=["search"])


@router.post("", response_model=SearchResponse)
async def search(
    request: SearchRequest,
    http_request: Request,
    api_key: Optional[APIKey] = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Execute a web search with optional content extraction and answer.

    Uses the free multi-engine scraper (Bing, DuckDuckGo, Mojeek) with
    TF-IDF semantic reranking — zero paid APIs required. Results are
    cached with TTL for instant repeat queries.

    When deep_search=True (default), uses the multi-engine scraper which
    cross-references 3+ engines and ranks by combined relevance.
    Results are semantically reranked using TF-IDF cosine similarity.
    """
    start_time = time.time()
    provider = SearchProvider()
    index = SearchIndex(db)

    # Cache check first
    cached = await index.get_cached(
        request.query,
        topic=request.topic,
        max_results=request.max_results,
    )
    if cached:
        search_results = cached
    else:
        # Search — with scraper or classic
        if request.deep_search:
            search_results = await provider.search(
                query=request.query,
                max_results=request.max_results,
                topic=request.topic,
                days=request.days,
                enrich=request.enrich or request.include_raw_content,
                llm_rank=request.llm_rank,
            )
        else:
            search_results = await provider.search(
                query=request.query,
                max_results=request.max_results,
                topic=request.topic,
                days=request.days,
            )

        if not search_results:
            # Fallback to indexed content
            indexed = await index.search(request.query, request.max_results)
            search_results = [
                {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "content": r.get("content", ""),
                }
                for r in indexed
            ]

        # Extraction for raw content
        if request.include_raw_content and not request.enrich:
            urls = [r["url"] for r in search_results if r.get("url") and not r.get("raw_content")]
            if urls:
                extracted, failed = await extract_urls_batch(urls)
                content_map = {r["url"]: r["content"] for r in extracted}
                for r in search_results:
                    if r.get("url") in content_map and not r.get("raw_content"):
                        r["raw_content"] = content_map[r["url"]]

        # Cache results with TTL
        if search_results:
            await index.cache_search_results(
                request.query, search_results,
                topic=request.topic,
            )

    # Apply domain filters
    if request.include_domains:
        search_results = [
            r for r in search_results
            if any(d in r.get("url", "") for d in request.include_domains)
        ]
    if request.exclude_domains:
        search_results = [
            r for r in search_results
            if not any(d in r.get("url", "") for d in request.exclude_domains)
        ]

    # Generate answer (local, no LLM needed)
    answer = None
    if request.include_answer:
        answer = generate_answer(request.query, search_results)

    # Generate follow-up questions (local, no LLM needed)
    follow_up = None
    if request.include_followup:
        follow_up = generate_followup_questions(request.query, answer, search_results)

    # Fetch images if requested
    images = None
    if request.include_images:
        images = await _fetch_images(request.query)

    response_time = round(time.time() - start_time, 2)

    # Log usage
    await log_usage("/search", api_key, db=db, ip_address=http_request.client.host if http_request.client else None)

    return SearchResponse(
        query=request.query,
        answer=answer,
        follow_up_questions=follow_up,
        images=images,
        results=[
            SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                content=r.get("content", ""),
                raw_content=r.get("raw_content") if request.include_raw_content else None,
                score=r.get("score", 0.0),
                published_date=r.get("published_date"),
                engine=r.get("engine"),
            )
            for r in search_results
        ],
        response_time=response_time,
    )


async def _fetch_images(query: str) -> Optional[list[str]]:
    """Fetch image results by scraping Bing Images directly (free, no API)."""
    try:
        from app.services.search_scraper import scrape_bing_images
        images = await scrape_bing_images(query, max_results=8)
        if images:
            return [img["url"] for img in images]
        return None
    except Exception as e:
        logger.debug("Image fetch failed: %s", e)
        return None
