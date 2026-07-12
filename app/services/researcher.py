"""Multi-step research service for Rootfetch."""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import ResearchJob
from app.services.extractor import extract_urls_batch
from app.services.search_provider import SearchProvider
from app.services.synthesizer import generate_answer, generate_followup_questions

logger = logging.getLogger(__name__)


async def perform_research(
    query: str,
    depth: str = "basic",
    max_results: int = 5,
    include_images: bool = False,
    include_sources: bool = True,
    topic: str = "general",
    db: Optional[AsyncSession] = None,
) -> dict[str, Any]:
    """Execute a multi-step research pipeline.

    1. Generate sub-queries based on depth
    2. Search for each sub-query
    3. Extract content from top results
    4. Synthesize findings into a report (LLM if available, else template)
    """
    start_time = time.time()
    provider = SearchProvider()

    # Step 1: Generate search queries
    queries = await _generate_search_queries(query, depth)

    # Step 2: Search for each query
    all_results: list[dict] = []
    for q in queries:
        search_results = await provider.search(
            q,
            max_results=max(max_results // len(queries), 2),
            topic=topic,
        )
        all_results.extend(search_results)

    # Deduplicate by URL
    seen = set()
    unique_results = []
    for r in all_results:
        if r.get("url") not in seen:
            seen.add(r.get("url"))
            unique_results.append(r)

    top_results = unique_results[:max_results]

    # Step 3: Extract full content from top results
    urls_to_extract = [r["url"] for r in top_results if r.get("url")]
    extracted, failed = await extract_urls_batch(urls_to_extract)
    logger.info("Extracted %d pages, %d failed", len(extracted), len(failed))

    # Merge extracted content back into results
    content_map = {r["url"]: r["content"] for r in extracted}
    for r in top_results:
        if r.get("url") in content_map:
            r["raw_content"] = content_map[r["url"]]

    # Step 4: Synthesize report
    answer = await _synthesize_report(query, top_results)

    sources = top_results if include_sources else []

    response_time = round(time.time() - start_time, 2)

    return {
        "answer": answer,
        "sources": sources,
        "response_time": response_time,
    }


async def _generate_search_queries(query: str, depth: str) -> list[str]:
    """Break a research query into sub-queries based on depth."""
    if depth == "basic":
        return [query]

    # For advanced/comprehensive, generate multiple search angles
    if depth == "advanced":
        return [
            query,
            f"{query} overview",
            f"{query} examples",
        ]

    # Comprehensive
    return [
        query,
        f"{query} overview background",
        f"{query} key concepts",
        f"{query} examples applications",
        f"{query} recent developments",
    ]


def _truncate(text: str, max_words: int = 500) -> str:
    """Truncate text to a maximum number of words."""
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]) + "..."


async def _synthesize_report(query: str, sources: list[dict]) -> Optional[str]:
    """Generate a synthesized report using the local NLP engine."""
    # Use the local synthesizer (free, no LLM needed, better than template)
    answer = generate_answer(query, sources, max_sentences=12)
    if answer:
        return answer

    # Fallback: basic template-based report
    return await _synthesize_without_llm(query, sources)


async def _synthesize_without_llm(query: str, sources: list[dict]) -> str:
    """Generate a basic synthesized report without an LLM."""
    parts = [f"# Research Results: {query}\n"]

    for i, src in enumerate(sources[:10]):
        title = src.get("title", "Untitled")
        url = src.get("url", "")
        content = (src.get("raw_content") or src.get("content", ""))[:500]
        parts.append(f"## {i+1}. {title}")
        parts.append(f"**URL:** {url}")
        parts.append("")
        parts.append(content)
        parts.append("---")

    return "\n".join(parts)


# --- Research Job Management ---

async def create_research_job(
    query: str,
    depth: str = "basic",
    max_results: int = 5,
    include_images: bool = False,
    include_sources: bool = True,
    topic: str = "general",
    db: AsyncSession = None,
) -> ResearchJob:
    """Create a new research job and return it."""
    request_id = str(uuid.uuid4())
    job = ResearchJob(
        request_id=request_id,
        query=query,
        status="pending",
        depth=depth,
        options=json.dumps({
            "max_results": max_results,
            "include_images": include_images,
            "include_sources": include_sources,
            "topic": topic,
        }),
    )
    db.add(job)
    await db.commit()
    return job


async def execute_research_job(job: ResearchJob, db: AsyncSession) -> None:
    """Execute a research job and store results."""
    options = json.loads(job.options or "{}")
    try:
        job.status = "running"
        await db.commit()

        result = await perform_research(
            query=job.query,
            depth=job.depth,
            max_results=options.get("max_results", 5),
            include_images=options.get("include_images", False),
            include_sources=options.get("include_sources", True),
            topic=options.get("topic", "general"),
        )

        job.result = json.dumps(result)
        job.status = "completed"
    except Exception as e:
        job.status = "failed"
        job.error = str(e)
        logger.error("Research job %s failed: %s", job.request_id, e)
    finally:
        await db.commit()
