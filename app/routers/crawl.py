"""Crawl router for Rootfetch API."""
from __future__ import annotations

import logging
import time
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import verify_api_key, log_usage
from app.database import APIKey, get_db
from app.models import CrawlRequest, CrawlResponse, CrawlResult
from app.services.crawler import WebCrawler

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/crawl", tags=["crawl"])


@router.post("", response_model=CrawlResponse)
async def crawl(
    request: CrawlRequest,
    api_key: Optional[APIKey] = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Crawl a website starting from a URL."""
    start_time = time.time()

    crawler = WebCrawler(
        start_url=request.url,
        max_depth=request.max_depth,
        max_pages=request.max_pages,
        include_paths=request.include_paths,
        exclude_paths=request.exclude_paths,
        include_domains=request.include_domains,
        exclude_domains=request.exclude_domains,
        extract_format=request.extract_format or "markdown",
    )

    results = await crawler.crawl()

    response_time = round(time.time() - start_time, 2)

    # Log usage
    await log_usage("/crawl", api_key, credits=len(results), db=db)

    return CrawlResponse(
        status="completed",
        total_pages=len(results),
        results=[
            CrawlResult(
                url=r["url"],
                title=r.get("title", ""),
                content=r.get("content", ""),
                depth=r.get("depth", 0),
            )
            for r in results
        ],
        response_time=response_time,
    )
