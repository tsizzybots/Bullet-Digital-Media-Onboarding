"""Email confirmation flow (S1-14).

A new user's `email_confirmed` is `false` until they click a link sent
to their inbox. The token is a signed itsdangerous payload carrying the
user id; the signature key lives in env (`EMAIL_TOKEN_SECRET`) and the
default 24h max_age is enforced server-side at verification time.

`POST /auth/confirm/{token}` flips the flag and timestamps
`email_confirmed_at`. It is idempotent: a re-click on an already-confirmed
user returns 200 with a no-op status.

`send_confirmation_email(user_id, email)` builds the link, hands the
message to the injected `EmailClient`, and is the seam tests substitute
to assert that an email *would* have been sent without hitting Resend.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from bullet_api.config import get_settings
from bullet_api.db import get_session
from bullet_api.email import EmailClient, EmailMessage, get_email_client

# 24 hour token expiry per S1-14 spec.
CONFIRMATION_TOKEN_MAX_AGE = timedelta(hours=24)

# Serializer purpose / salt - rotating this invalidates every outstanding
# confirmation token without touching the secret.
_TOKEN_SALT = "bullet-email-confirmation-v1"


router = APIRouter(prefix="/auth", tags=["auth"])


def _serializer() -> URLSafeTimedSerializer:
    """Build a fresh serializer per call so settings hot-reloads pick up
    a rotated secret. itsdangerous serializers are cheap to construct."""
    return URLSafeTimedSerializer(
        secret_key=get_settings().email_token_secret,
        salt=_TOKEN_SALT,
    )


def generate_confirmation_token(user_id: uuid.UUID) -> str:
    """Return a signed time-limited token that encodes `user_id`."""
    return _serializer().dumps(str(user_id))


def _decode_confirmation_token(token: str) -> uuid.UUID:
    """Raise HTTPException(410) for expired, 400 for invalid; otherwise
    return the encoded user id as a UUID."""
    try:
        payload = _serializer().loads(
            token,
            max_age=int(CONFIRMATION_TOKEN_MAX_AGE.total_seconds()),
        )
    except SignatureExpired as exc:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Confirmation link expired. Please request a new one.",
        ) from exc
    except BadSignature as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid confirmation token.",
        ) from exc
    return uuid.UUID(payload)


async def send_confirmation_email(
    *,
    user_id: uuid.UUID,
    email: str,
    email_client: EmailClient,
) -> str:
    """Send the confirmation email and return the provider message id.

    Caller passes the already-resolved email client so tests can swap in
    `FakeEmailClient` without touching settings.
    """
    token = generate_confirmation_token(user_id)
    base_url = get_settings().email_confirmation_base_url.rstrip("/")
    link = f"{base_url}/{token}"
    html = (
        "<p>Welcome to Bullet Digital Media.</p>"
        "<p>Please confirm your email by clicking the link below "
        "(valid for 24 hours):</p>"
        f'<p><a href="{link}">{link}</a></p>'
        "<p>If you did not expect this email, you can ignore it.</p>"
    )
    return await email_client.send(
        EmailMessage(
            to=email,
            subject="Confirm your Bullet Digital Media account",
            html=html,
        )
    )


@router.post("/confirm/{token}")
async def confirm_email(
    token: str,
    db: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    """Flip `email_confirmed` and stamp `email_confirmed_at` for the user
    encoded in `token`. Idempotent for already-confirmed users."""
    user_id = _decode_confirmation_token(token)

    result = await db.execute(
        text(
            "SELECT email_confirmed FROM users WHERE id = :u"
        ),
        {"u": user_id},
    )
    row = result.first()
    if row is None:
        # Signed token refers to a user that no longer exists - treat
        # like an invalid token rather than leaking existence.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid confirmation token.",
        )
    already_confirmed: bool = row[0]
    if already_confirmed:
        return {"status": "already_confirmed"}

    await db.execute(
        text(
            "UPDATE users SET "
            "  email_confirmed = true, "
            "  email_confirmed_at = now() "
            "WHERE id = :u"
        ),
        {"u": user_id},
    )
    await db.commit()
    return {"status": "confirmed"}


@router.post("/resend-confirmation")
async def resend_confirmation(
    payload: dict[str, str],
    db: Annotated[AsyncSession, Depends(get_session)],
    email_client: Annotated[EmailClient, Depends(get_email_client)],
) -> dict[str, str]:
    """Re-send the confirmation email for an unconfirmed user.

    Body: `{"email": "..."}`. Always returns 200 with the same status
    regardless of whether the email matched a real user - this keeps the
    endpoint from being a free user-enumeration oracle.
    """
    email = (payload.get("email") or "").strip()
    if not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="email is required",
        )
    result = await db.execute(
        text(
            "SELECT id, email_confirmed FROM users WHERE email = :e"
        ),
        {"e": email},
    )
    row = result.first()
    if row is not None:
        user_id, already_confirmed = row
        if not already_confirmed:
            await send_confirmation_email(
                user_id=user_id,
                email=email,
                email_client=email_client,
            )
    # Same response for hit / miss / already-confirmed.
    return {"status": "sent_if_unconfirmed"}
