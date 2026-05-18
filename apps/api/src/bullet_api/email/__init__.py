"""Outbound email primitives.

S1-14 lands the Resend integration and the confirmation-email flow. The
abstraction is a thin `EmailClient` protocol so tests can substitute a
`FakeEmailClient` that captures sent messages, and the production
`ResendEmailClient` can be swapped for another provider later without
touching the call sites.
"""

from __future__ import annotations

from bullet_api.email.client import (
    EmailClient,
    EmailMessage,
    FakeEmailClient,
    ResendEmailClient,
    get_email_client,
)

__all__ = [
    "EmailClient",
    "EmailMessage",
    "FakeEmailClient",
    "ResendEmailClient",
    "get_email_client",
]
