"""Pydantic schemas for Rootfetch API."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


# --- Search ---

class SearchRequest(BaseModel):
    query: str = Field(..., description="Search query")
    search_depth: str = Field(default="basic", description="Search depth: basic or advanced")
    topic: str = Field(default="general", description="Topic: general or news")
    days: Optional[int] = Field(default=None, description="Days back for news")
    max_results: int = Field(default=5, ge=1, le=20, description="Max results to return")
    include_images: bool = Field(default=False, description="Include image results")
    include_answer: bool = Field(default=True, description="Generate local extractive answer (free, no LLM)")
    include_followup: bool = Field(default=True, description="Generate follow-up questions (free, no LLM)")
    include_raw_content: bool = Field(default=False, description="Include full page content")
    include_domains: Optional[list[str]] = Field(default=None, description="Allowed domains")
    exclude_domains: Optional[list[str]] = Field(default=None, description="Excluded domains")
    deep_search: bool = Field(default=True, description="Use multi-engine scraper (beats Tavily)")
    enrich: bool = Field(default=False, description="Extract full page content for top results")
    llm_rank: bool = Field(default=False, description="Rerank results using LLM")


class SearchResult(BaseModel):
    title: str = ""
    url: str = ""
    content: str = ""
    raw_content: Optional[str] = None
    score: float = 0.0
    published_date: Optional[str] = None
    engine: Optional[str] = None


class SearchResponse(BaseModel):
    query: str
    follow_up_questions: Optional[list[str]] = None
    answer: Optional[str] = None
    images: Optional[list[str]] = None
    results: list[SearchResult] = []
    response_time: float = 0.0


# --- Extract ---

class ExtractRequest(BaseModel):
    urls: list[str] = Field(..., description="URLs to extract (max 20)", max_length=20)
    depth: str = Field(default="basic", description="Extraction depth: basic or advanced")
    include_images: bool = Field(default=False, description="Include images in extraction")
    extract_format: Optional[str] = Field(default="markdown", description="Output format: markdown or text")
    query: Optional[str] = Field(default=None, description="Guide extraction relevance")
    chunks: bool = Field(default=False, description="Split content into chunks")


class ExtractResult(BaseModel):
    url: str
    title: str = ""
    content: str = ""
    raw_content: Optional[str] = None
    images: Optional[list[str]] = None
    usage_tokens: Optional[int] = None


class FailedResult(BaseModel):
    url: str
    error: str


class ExtractResponse(BaseModel):
    results: list[ExtractResult] = []
    failed_results: list[FailedResult] = []
    response_time: float = 0.0


# --- Crawl ---

class CrawlRequest(BaseModel):
    url: str = Field(..., description="Starting URL for crawl")
    max_depth: int = Field(default=2, ge=1, le=10, description="Max crawl depth")
    max_pages: int = Field(default=50, ge=1, le=1000, description="Max pages to crawl")
    include_paths: Optional[list[str]] = Field(default=None, description="Regex patterns for paths to include")
    exclude_paths: Optional[list[str]] = Field(default=None, description="Regex patterns for paths to exclude")
    include_domains: Optional[list[str]] = Field(default=None, description="Domains to allow")
    exclude_domains: Optional[list[str]] = Field(default=None, description="Domains to exclude")
    extract_format: Optional[str] = Field(default="markdown", description="Output format: markdown or text")


class CrawlResult(BaseModel):
    url: str
    title: str = ""
    content: str = ""
    depth: int = 0


class CrawlResponse(BaseModel):
    status: str = "completed"
    total_pages: int = 0
    results: list[CrawlResult] = []
    response_time: float = 0.0


# --- Map ---

class MapRequest(BaseModel):
    url: str = Field(..., description="URL to discover links from")
    search: Optional[str] = Field(default=None, description="Filter links by search term")
    include_paths: Optional[list[str]] = Field(default=None, description="Regex for paths to include")
    exclude_paths: Optional[list[str]] = Field(default=None, description="Regex for paths to exclude")
    include_domains: Optional[list[str]] = Field(default=None, description="Domains to include")
    exclude_domains: Optional[list[str]] = Field(default=None, description="Domains to exclude")
    limit: int = Field(default=100, ge=1, le=1000, description="Max URLs to return")


class MapResponse(BaseModel):
    urls: list[str] = []
    total: int = 0
    response_time: float = 0.0


# --- Research ---

class ResearchRequest(BaseModel):
    query: str = Field(..., description="Research query/topic")
    depth: str = Field(default="basic", description="Research depth: basic, advanced, or comprehensive")
    include_images: bool = Field(default=False, description="Include images")
    include_sources: bool = Field(default=True, description="Include sources in answer")
    max_results: int = Field(default=5, ge=1, le=20, description="Max sources per search")
    topic: str = Field(default="general", description="Topic: general or news")


class ResearchResponse(BaseModel):
    request_id: str = ""
    status: str = "pending"
    results: Optional[Any] = None
    answer: Optional[str] = None
    sources: Optional[list[dict[str, Any]]] = None
    response_time: float = 0.0


# --- Errors (Tavily-compatible) ---

class RootfetchError(BaseModel):
    detail: str = "An error occurred"
    error: str = "An error occurred"
    retry_after_seconds: Optional[int] = None
    next_actions: Optional[list[str]] = None


class ErrorResponse(BaseModel):
    detail: str = "Not found"


# --- Health ---

class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "1.0.0"
    service: str = "rootfetch"
