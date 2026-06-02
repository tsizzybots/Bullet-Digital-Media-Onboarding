"""Email client abstraction over Resend.

`EmailClient` is a small Protocol so handlers depend on the interface
rather than on Resend specifically. `ResendEmailClient` is the
production wiring; `FakeEmailClient` is used by tests to capture
outgoing messages and assert on them without an API call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import httpx

from bullet_api.config import get_settings


@dataclass(frozen=True)
class EmailMessage:
    """The minimal shape of an outbound transactional email."""

    to: str
    subject: str
    html: str
    from_email: str | None = None  # falls back to settings.email_from


class EmailClient(Protocol):
    async def send(self, message: EmailMessage) -> str:
        """Send the email and return the provider message id."""
        ...


class ResendEmailClient:
    """Production client - posts to Resend's `/emails` REST endpoint.

    The Resend Python SDK is synchronous as of the current release; the
    REST API is tiny (two fields plus auth), so we just speak it
    directly with httpx and keep the dependency surface small.
    """

    def __init__(
        self,
        api_key: str,
        default_from: str,
        base_url: str = "https://api.resend.com",
        timeout: float = 10.0,
    ) -> None:
        self._api_key = api_key
        self._default_from = default_from
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def send(self, message: EmailMessage) -> str:
        if not self._api_key:
            # Fail loudly rather than silently dropping mail when an env
            # var is missing - a Render env group misconfiguration should
            # be surfaced in the logs / 500 response, not papered over.
            raise RuntimeError(
                "RESEND_API_KEY is empty; cannot send email. Set it on the Render env group."
            )
        payload = {
            "from": message.from_email or self._default_from,
            "to": [message.to],
            "subject": message.subject,
            "html": message.html,
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{self._base_url}/emails",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=payload,
            )
        response.raise_for_status()
        body = response.json()
        # Resend returns `{"id": "..."}`.
        return body["id"]


@dataclass
class FakeEmailClient:
    """Test double. Records sent messages on `sent` for assertions."""

    sent: list[EmailMessage] = field(default_factory=list)

    async def send(self, message: EmailMessage) -> str:
        self.sent.append(message)
        return f"fake-{len(self.sent)}"


def get_email_client() -> EmailClient:
    """FastAPI dependency. Tests override this with a `FakeEmailClient`
    instance so they can read `client.sent` and assert against it."""
    settings = get_settings()
    return ResendEmailClient(
        api_key=settings.resend_api_key,
        default_from=settings.email_from,
    )
