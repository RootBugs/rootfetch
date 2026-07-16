"""Configuration management for Rootfetch."""
from __future__ import annotations

from typing import Optional

from pydantic import ConfigDict
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = ConfigDict(extra="ignore", env_file=".env", env_file_encoding="utf-8")

    api_host: str = "0.0.0.0"
    api_port: int = 8000
    secret_key: str = "change-me-to-a-random-secret"
    debug: bool = True

    database_url: str = "sqlite+aiosqlite:///./rootfetch.db"
    default_api_keys: str = "test-key:1000,demo-key:500"

    serpapi_api_key: Optional[str] = None
    bing_api_key: Optional[str] = None
    google_api_key: Optional[str] = None
    google_cse_id: Optional[str] = None
    openai_api_key: Optional[str] = None
    proxy_url: Optional[str] = None

    keyless_rate_limit: str = "20/minute"
    keyed_rate_limit: str = "100/minute"
    premium_rate_limit: str = "1000/minute"

    crawl_user_agent: str = "Rootfetch/1.0 (Research Bot; +https://github.com/user/rootfetch)"
    crawl_delay: float = 1.0
    crawl_max_pages: int = 1000


settings = Settings()
