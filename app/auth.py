"""API key authentication for Rootfetch."""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, APIKey, UsageLog


async def verify_api_key(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None, alias="X-Api-Key"),
    db: AsyncSession = Depends(get_db),
) -> Optional[APIKey]:
    """Verify API key from Authorization header or X-Api-Key header.

    Returns None for keyless access (no key provided).
    Raises HTTPException for invalid keys.
    Logs usage for valid keys.
    """
    # Extract key from headers
    api_key_str: Optional[str] = None
    if authorization and authorization.startswith("Bearer "):
        api_key_str = authorization[7:]
    elif x_api_key:
        api_key_str = x_api_key

    # Keyless access
    if not api_key_str:
        return None

    # Look up key in database
    result = await db.execute(select(APIKey).where(APIKey.key == api_key_str))
    api_key = result.scalar_one_or_none()

    if api_key is None:
        raise HTTPException(
            status_code=401,
            detail={
                "error": "Invalid API key",
                "detail": "The provided API key is not valid. Please check your API key and try again.",
                "next_actions": [
                    "Ensure you are using the correct API key",
                    "Generate a new API key from your dashboard",
                ],
            },
        )

    if not api_key.is_active:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "API key is inactive",
                "detail": "The provided API key is inactive. Please reactivate it from your dashboard.",
                "next_actions": ["Reactivate your API key from the dashboard"],
            },
        )

    if api_key.credits <= 0:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "Insufficient credits",
                "detail": "You have run out of credits. Please purchase more to continue using the API.",
                "retry_after_seconds": None,
                "next_actions": ["Purchase more credits from your dashboard"],
            },
        )

    return api_key


async def log_usage(
    endpoint: str,
    api_key: Optional[APIKey],
    credits: int = 1,
    success: bool = True,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Log API usage and deduct credits."""
    ip_address = None  # Would come from request in production

    usage = UsageLog(
        api_key_id=api_key.id if api_key else None,
        endpoint=endpoint,
        credits_used=credits,
        ip_address=ip_address,
        success=success,
    )
    db.add(usage)

    if api_key and success:
        api_key.credits -= credits

    await db.commit()
