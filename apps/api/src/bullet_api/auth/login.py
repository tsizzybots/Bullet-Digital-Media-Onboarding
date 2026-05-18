"""POST /auth/login - argon2id verification + session cookie issuance.

Verifies the submitted password against `users.password_hash` (argon2id),
records failed attempts against the caller's IP for brute-force lockout,
and on success writes a row to `sessions` and sets the HttpOnly Secure
SameSite=Lax `session` cookie.

`email_confirmed=false` blocks login with 403 - the same contract S1-14
will rely on when seeded users have not yet completed the email click
flow.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHash, VerificationError, VerifyMismatchError
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    Response,
    status,
)
from pydantic import BaseModel, EmailStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from bullet_api.auth.brute_force import BruteForceTracker, get_tracker
from bullet_api.auth.dependencies import SESSION_COOKIE_NAME
from bullet_api.db import get_session

# Session lifetime per PRD §6.1 / S1-15 plan.
SESSION_LIFETIME = timedelta(days=7)

# argon2id verifier shared across requests. argon2-cffi's PasswordHasher
# stores no per-request state; only the algorithm parameters.
_hasher = PasswordHasher()


router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


def _client_ip(request: Request) -> str:
    """Return the best client IP we can recover.

    Render's front-proxy attaches `X-Forwarded-For: <client>, <proxy...>`;
    the first entry is the original caller. Fall back to the raw peer
    address (i.e. localhost in tests) when the header is missing.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client is not None:
        return request.client.host
    return "unknown"


def _hash_token(token: str) -> str:
    """sha256 hex - the same digest used by `get_current_user`'s lookup."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@router.post("/login")
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(get_session)],
    tracker: Annotated[BruteForceTracker, Depends(get_tracker)],
) -> dict[str, str]:
    """Verify credentials and issue a session cookie.

    Status codes:
    - 200 with `Set-Cookie: session=...`: successful login.
    - 401 `Invalid credentials`: unknown email, or password mismatch.
    - 403 `Email not confirmed...`: credentials valid but
      `email_confirmed=false` (S1-14 confirmation flow has not completed).
    - 429 `Too many failed login attempts...`: caller IP has accumulated
      `tracker.max_attempts` failures inside `tracker.window`.
    """
    ip = _client_ip(request)

    # Lockout check before any DB work so a locked-out attacker can't
    # probe for valid emails by timing the response.
    if tracker.is_locked(ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed login attempts. Try again later.",
        )

    result = await db.execute(
        text(
            "SELECT id, password_hash, email_confirmed "
            "FROM users WHERE email = :e"
        ),
        {"e": body.email},
    )
    row = result.first()

    if row is None:
        tracker.record_failure(ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    user_id, password_hash, email_confirmed = row

    try:
        _hasher.verify(password_hash, body.password)
    except (VerifyMismatchError, VerificationError, InvalidHash):
        tracker.record_failure(ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        ) from None

    if not email_confirmed:
        # Don't count this against the brute-force counter - credentials
        # were valid; the lockout exists to slow password-guessing.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Email not confirmed. Please complete the confirmation "
                "link sent to your inbox."
            ),
        )

    tracker.clear(ip)

    raw_token = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + SESSION_LIFETIME
    await db.execute(
        text(
            "INSERT INTO sessions "
            "(user_id, token_hash, expires_at, ip, user_agent) "
            "VALUES (:u, :h, :e, cast(:ip AS inet), :ua)"
        ),
        {
            "u": user_id,
            "h": _hash_token(raw_token),
            "e": expires_at,
            "ip": ip,
            "ua": request.headers.get("user-agent"),
        },
    )
    await db.execute(
        text("UPDATE users SET last_login_at = now() WHERE id = :u"),
        {"u": user_id},
    )
    await db.commit()

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=raw_token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=int(SESSION_LIFETIME.total_seconds()),
    )
    return {"status": "ok"}
