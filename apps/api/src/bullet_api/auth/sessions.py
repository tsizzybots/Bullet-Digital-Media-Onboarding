"""POST /auth/logout - invalidate the current session cookie.

Reads the raw session token from the HttpOnly ``session`` cookie, computes
its sha256 hash, deletes the matching row from ``sessions``, then clears
the cookie from the browser. The ``get_current_user`` dependency guards the
endpoint so unauthenticated callers (no cookie or expired session) receive
401 rather than a silent no-op.
"""

from __future__ import annotations

import hashlib
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from bullet_api.auth.dependencies import (
    SESSION_COOKIE_NAME,
    CurrentUser,
    get_current_user,
)
from bullet_api.db import get_session
from bullet_api.schemas import StatusResponse

router = APIRouter(prefix="/auth", tags=["auth"])


def _hash_token(token: str) -> str:
    """sha256 hex - matches what login.py stores and dependencies.py reads."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@router.post("/logout", response_model=StatusResponse)
async def logout(
    response: Response,
    _user: Annotated[CurrentUser, Depends(get_current_user)],
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
    db: Annotated[AsyncSession, Depends(get_session)] = None,  # type: ignore[assignment]
) -> StatusResponse:
    """Invalidate the caller's current session.

    Status codes:
    - 200 ``{"status": "ok"}``: session deleted and cookie cleared.
    - 401: no session cookie present, or the session has already expired.

    ``_user`` being resolved by ``get_current_user`` guarantees the session
    is valid before we touch the DB. ``session_token`` carries the same raw
    value ``get_current_user`` read; FastAPI resolves both independently from
    the same cookie header.
    """
    if session_token:
        token_hash = _hash_token(session_token)
        await db.execute(
            text("DELETE FROM sessions WHERE token_hash = :h"),
            {"h": token_hash},
        )
        await db.commit()

    response.delete_cookie(key=SESSION_COOKIE_NAME)
    return StatusResponse(status="ok")
