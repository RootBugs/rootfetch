"""Research router for Rootfetch API."""
from __future__ import annotations

import json
import logging
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import verify_api_key, log_usage
from app.database import APIKey, ResearchJob, get_db
from app.models import ResearchRequest, ResearchResponse
from app.services.researcher import create_research_job, execute_research_job

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/research", tags=["research"])


@router.post("", response_model=ResearchResponse)
async def create_research(
    request: ResearchRequest,
    http_request: Request,
    api_key: Optional[APIKey] = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Create a new research job (async). Returns a request_id for polling."""
    start_time = time.time()

    job = await create_research_job(
        query=request.query,
        depth=request.depth,
        max_results=request.max_results,
        include_images=request.include_images,
        include_sources=request.include_sources,
        topic=request.topic,
        db=db,
    )

    response_time = round(time.time() - start_time, 2)

    return ResearchResponse(
        request_id=job.request_id,
        status="pending",
        response_time=response_time,
    )


@router.get("/{request_id}", response_model=ResearchResponse)
async def get_research(
    request_id: str,
    http_request: Request,
    api_key: Optional[APIKey] = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Get research results by request_id. Runs the job if still pending."""
    result = await db.execute(
        select(ResearchJob).where(ResearchJob.request_id == request_id)
    )
    job = result.scalar_one_or_none()

    if job is None:
        raise HTTPException(status_code=404, detail="Research job not found")

    # Execute if pending
    if job.status in ("pending", "running"):
        await execute_research_job(job, db)

    response_data = {
        "request_id": job.request_id,
        "status": job.status,
        "response_time": 0.0,
    }

    if job.status == "completed" and job.result:
        result_data = json.loads(job.result)
        response_data["answer"] = result_data.get("answer")
        response_data["sources"] = result_data.get("sources")
        response_data["response_time"] = result_data.get("response_time", 0.0)

    if job.status == "failed":
        response_data["error"] = job.error

    return ResearchResponse(**response_data)
