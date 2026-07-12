"""Sliding-window rate limiter for Rootfetch."""
from __future__ import annotations

import time
from collections import defaultdict
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings


def parse_rate_limit(rate_str: str) -> tuple[int, float]:
    """Parse a rate limit string like '100/minute' into (max_requests, window_seconds)."""
    parts = rate_str.split("/")
    max_requests = int(parts[0])
    unit = parts[1] if len(parts) > 1 else "minute"
    window_map = {
        "second": 1,
        "minute": 60,
        "hour": 3600,
        "day": 86400,
    }
    window_seconds = window_map.get(unit, 60)
    return max_requests, window_seconds


class RateLimiter:
    """In-memory sliding window rate limiter."""

    def __init__(self) -> None:
        self._windows: dict[str, list[float]] = defaultdict(list)

    def check(self, key: str, max_requests: int, window_seconds: float) -> tuple[bool, Optional[int]]:
        """Check if a request is allowed. Returns (allowed, retry_after_seconds)."""
        now = time.time()
        cutoff = now - window_seconds

        # Prune old entries
        timestamps = self._windows[key]
        self._windows[key] = [t for t in timestamps if t > cutoff]

        if len(self._windows[key]) >= max_requests:
            oldest = self._windows[key][0]
            retry_after = int(window_seconds - (now - oldest))
            return False, max(retry_after, 1)

        self._windows[key].append(now)
        return True, None

    def cleanup(self) -> None:
        """Periodically clean up stale entries."""
        now = time.time()
        for key in list(self._windows.keys()):
            self._windows[key] = [t for t in self._windows[key] if t > now - 3600]
            if not self._windows[key]:
                del self._windows[key]


rate_limiter = RateLimiter()


def get_rate_limit_for_key(api_key: Optional[str]) -> tuple[int, float]:
    """Determine rate limit based on whether a key is present and premium."""
    if api_key is None:
        return parse_rate_limit(settings.keyless_rate_limit)
    # Premium check would go here based on key metadata
    return parse_rate_limit(settings.keyed_rate_limit)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Middleware that applies rate limiting with Tavily-compatible error envelopes."""

    def __init__(self, app: FastAPI) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):
        # Skip rate limiting for health and docs
        if request.url.path in ("/health", "/docs", "/openapi.json", "/redoc"):
            return await call_next(request)

        # Extract API key
        api_key: Optional[str] = None
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            api_key = auth_header[7:]
        if not api_key:
            api_key = request.headers.get("x-api-key")

        max_req, window = get_rate_limit_for_key(api_key)
        client_ip = request.client.host if request.client else "unknown"
        rate_key = f"{api_key or client_ip}:{request.url.path}"

        allowed, retry_after = rate_limiter.check(rate_key, max_req, window)
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "detail": f"Rate limit exceeded. {max_req} requests per {int(window)} seconds.",
                    "error": "Rate limit exceeded",
                    "retry_after_seconds": retry_after,
                    "next_actions": [
                        "Reduce request rate",
                        "Upgrade to a premium plan for higher limits",
                        "Wait and retry",
                    ],
                },
            )

        return await call_next(request)
