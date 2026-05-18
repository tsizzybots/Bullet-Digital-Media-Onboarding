"""FastAPI dependencies that resolve the current user and gate by role.

The cookie `session` carries an opaque token; only its sha256 hash is
stored in the `sessions` table (per PRD §4.9 / S1-15). Lookup joins
sessions -> users with an `expires_at > now()` predicate so expired
cookies fail closed.

Role gates are spelled as `Depends(require_founder)` rather than a custom
permission system: keeps the OpenAPI document accurate (each operation
shows its security requirement) and keeps the failure modes consistent
(401 when unauthenticated, 403 when the role does not match).
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from bullet_api.db import get_session

# Cookie name used by login (S1-13) and read by every dependency below.
# Matched by the dashboard's fetch credentials policy.
SESSION_COOKIE_NAME = "session"


@dataclass(frozen=True)
class CurrentUser:
    """Read-only projection of the authenticated user.

    Carries only the fields any handler should need; full row reads go
    through the model layer once that lands. `role` is the raw enum label
    so `require_role()` can string-compare directly.
    """

    id: uuid.UUID
    email: str
    full_name: str
    role: str


def _hash_session_token(token: str) -> str:
    """sha256 in hex - matches what sessions.token_hash stores."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def get_current_user(
    session_token: Annotated[
        str | None, Cookie(alias=SESSION_COOKIE_NAME)
    ] = None,
    db: Annotated[AsyncSession, Depends(get_session)] = None,  # type: ignore[assignment]
) -> CurrentUser:
    """FastAPI dependency. 401 if no cookie or no matching live session."""
    if not session_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    result = await db.execute(
        text(
            "SELECT users.id, users.email, users.full_name, users.role "
            "FROM sessions "
            "JOIN users ON users.id = sessions.user_id "
            "WHERE sessions.token_hash = :h "
            "  AND sessions.expires_at > now()"
        ),
        {"h": _hash_session_token(session_token)},
    )
    row = result.first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired or invalid",
        )
    user_id, email, full_name, role = row
    return CurrentUser(id=user_id, email=email, full_name=full_name, role=role)


def require_role(*allowed_roles: str) -> Callable[..., CurrentUser]:
    """Build a FastAPI dependency that 403s unless `user.role` is in
    `allowed_roles`. Returns the user so handlers can still `Depends`
    on it for downstream use."""

    async def _checker(
        user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> CurrentUser:
        if user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient role",
            )
        return user

    # Naming the inner function helps when reading FastAPI debug output;
    # the wrapper's __name__ surfaces in the dependency graph.
    _checker.__name__ = f"require_role_{'_'.join(allowed_roles)}"
    return _checker


# Per PRD §4.8 / S1-12 plan - the four named role gates. Each is a
# dependency callable; consumers spell `Depends(require_founder)` etc.
require_founder = require_role("founder")
require_pd = require_role("performance_director")
require_am = require_role("account_manager")
require_engineer = require_role("izzyagents_engineer")
