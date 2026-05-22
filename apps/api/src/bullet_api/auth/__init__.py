"""Authentication primitives.

S1-12 lands the role-based dependency framework (`CurrentUser`,
`require_role()`, the four named dependencies). S1-13 wires the login
endpoint that creates sessions; S1-14 adds the email confirmation step.
"""

from __future__ import annotations

from bullet_api.auth.confirmation import (
    generate_confirmation_token,
    send_confirmation_email,
)
from bullet_api.auth.confirmation import router as confirmation_router
from bullet_api.auth.dependencies import (
    CurrentUser,
    get_current_user,
    require_am,
    require_engineer,
    require_founder,
    require_pd,
    require_role,
)
from bullet_api.auth.login import router as login_router
from bullet_api.auth.sessions import router as logout_router

__all__ = [
    "CurrentUser",
    "confirmation_router",
    "generate_confirmation_token",
    "get_current_user",
    "login_router",
    "logout_router",
    "require_am",
    "require_engineer",
    "require_founder",
    "require_pd",
    "require_role",
    "send_confirmation_email",
]
