"""Extract router for Rootfetch API."""
from __future__ import annotations

import logging
import time
from typing import Optional

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import verify_api_key, log_usage
from app.database import APIKey, get_db
from app.models import ExtractRequest, ExtractResponse, ExtractResult, FailedResult
from app.services.extractor import extract_urls_batch

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/extract", tags=["extract"])


@router.post("", response_model=ExtractResponse)
async def extract(
    request: ExtractRequest,
    http_request: Request,
    api_key: Optional[APIKey] = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Extract content from one or more URLs."""
    start_time = time.time()

    results, failed = await extract_urls_batch(
        urls=request.urls,
        extract_format=request.extract_format or "markdown",
        query=request.query,
    )

    response_time = round(time.time() - start_time, 2)

    # Log usage
    await log_usage("/extract", api_key, credits=len(request.urls), db=db, ip_address=http_request.client.host if http_request.client else None)

    return ExtractResponse(
        results=[
            ExtractResult(
                url=r["url"],
                title=r.get("title", ""),
                content=r.get("content", ""),
                raw_content=r.get("content"),
                usage_tokens=len(r.get("content", "")) // 4,  # Approximate token count
            )
            for r in results
        ],
        failed_results=[
            FailedResult(url=f["url"], error=f["error"])
            for f in failed
        ],
        response_time=response_time,
    )
