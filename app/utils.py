"""Utility functions for Rootfetch."""
from __future__ import annotations

import hashlib
import re
from urllib.parse import urlparse, urlunparse


def url_hash(url: str) -> str:
    """Return SHA-256 hash of a normalized URL."""
    return hashlib.sha256(normalize_url(url).encode()).hexdigest()


def normalize_url(url: str) -> str:
    """Normalize a URL for consistent comparison."""
    parsed = urlparse(url)
    # Lowercase scheme and netloc
    normalized = parsed._replace(scheme=parsed.scheme.lower(), netloc=parsed.netloc.lower())
    # Remove trailing slash from path
    path = normalized.path.rstrip("/") or "/"
    normalized = normalized._replace(path=path)
    # Sort query params
    if normalized.query:
        params = sorted(normalized.query.split("&"))
        normalized = normalized._replace(query="&".join(params))
    return urlunparse(normalized)


def extract_domain(url: str) -> str:
    """Extract domain from URL."""
    parsed = urlparse(url)
    return parsed.netloc.lower()


def is_same_domain(url1: str, url2: str) -> bool:
    """Check if two URLs share the same domain."""
    return extract_domain(url1) == extract_domain(url2)


def matches_regex_list(value: str, patterns: list[str]) -> bool:
    """Check if a string matches any regex in a list."""
    for pattern in patterns:
        if re.search(pattern, value):
            return True
    return False


def compute_search_depth_credits(depth: str) -> int:
    """Compute estimated search credits based on depth."""
    depth_map = {"basic": 1, "advanced": 5, "comprehensive": 10}
    return depth_map.get(depth, 1)
