"""Map router for Rootfetch API."""
from __future__ import annotations

import logging
import time
from typing import Optional

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import verify_api_key, log_usage
from app.database import APIKey, get_db
from app.models import MapRequest, MapResponse
from app.services.crawler import WebCrawler

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/map", tags=["map"])


@router.post("", response_model=MapResponse)
async def map_site(
    request: MapRequest,
    http_request: Request,
    api_key: Optional[APIKey] = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Discover URLs from a starting point without extracting content."""
    start_time = time.time()

    crawler = WebCrawler(
        start_url=request.url,
        max_depth=2,  # Map is shallow by design
        include_paths=request.include_paths,
        exclude_paths=request.exclude_paths,
        include_domains=request.include_domains,
        exclude_domains=request.exclude_domains,
    )

    urls = await crawler.map_site(search=request.search)
    urls = urls[: request.limit]

    response_time = round(time.time() - start_time, 2)

    # Log usage
    await log_usage("/map", api_key, db=db, ip_address=http_request.client.host if http_request.client else None)

    return MapResponse(
        urls=urls,
        total=len(urls),
        response_time=response_time,
    )
