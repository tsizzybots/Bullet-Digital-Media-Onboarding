"""Fail-closed guards for the standalone seed scripts (S1-31 PR review).

The seed scripts create a known-credential founder login and bulk fixture
data. Run against a remote database they would plant an admin backdoor, so
they must refuse to touch anything that is not a local/test database.

The load-bearing guard is the **host allowlist**: `assert_local_seed_db()`
parses the resolved `DATABASE_URL` host and aborts on anything that is not
local. This closes the actual exploit unconditionally - no flag can override
it - so a mis-pointed `DATABASE_URL` (Neon staging/prod) can never be seeded,
while local dev keeps working with no extra ceremony.

The dev password is sourced through `dev_seed_password()` (overridable via
`DEV_SEED_PASSWORD`); the committed default is retained only for local DX and
is unreachable off localhost because of the host guard above.
"""

from __future__ import annotations

import os
import sys

from sqlalchemy.engine import make_url

from bullet_api.config import get_async_database_url

# Hosts a seed script is willing to write to. Covers bare localhost, the
# docker-compose service names, and the docker host alias.
_LOCAL_HOSTS = frozenset(
    {"localhost", "127.0.0.1", "::1", "db", "postgres", "host.docker.internal"}
)

_DEFAULT_DEV_PASSWORD = "DevPassw0rd!seed"


def assert_local_seed_db() -> None:
    """Abort (exit 2) unless the resolved DATABASE_URL host is local.

    Unconditional - there is deliberately no override flag, because the whole
    point is that no seed script can ever reach a remote database.
    """
    host = make_url(get_async_database_url()).host or "localhost"
    if host not in _LOCAL_HOSTS:
        sys.exit(
            f"Refusing to seed: DATABASE_URL host {host!r} is not local "
            f"({', '.join(sorted(_LOCAL_HOSTS))}). Seed scripts create a "
            "known-credential founder login and must only run on a local/test DB."
        )


def dev_seed_password() -> str:
    """Dev/test seed password. Overridable via `DEV_SEED_PASSWORD`; the default
    is local-only (the host guard makes it unreachable off localhost)."""
    return os.environ.get("DEV_SEED_PASSWORD", _DEFAULT_DEV_PASSWORD)
