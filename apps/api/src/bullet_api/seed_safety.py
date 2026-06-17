"""Fail-closed guards for the standalone seed scripts (S1-31 PR review).

The seed scripts create a known-credential founder login and bulk fixture
data. Run against a remote database they would plant an admin backdoor, so
they must refuse to touch anything that is not a local/test database.

The load-bearing guard is the **host allowlist**: `assert_local_seed_db()`
parses the resolved `DATABASE_URL` host(s) and aborts unless EVERY one is
local. This closes the actual exploit unconditionally - no flag can override
it - so a mis-pointed `DATABASE_URL` (Neon staging/prod) can never be seeded,
while local dev keeps working with no extra ceremony. Multi-host DSNs
(`postgresql://u@localhost,prod.example/db`, which asyncpg accepts and will
fail over between) are split and checked per host, so a remote second host
cannot ride in behind a local first host.

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


def _seed_db_hosts() -> list[str]:
    """Every host the resolved DATABASE_URL could connect to, lower-cased.

    asyncpg accepts comma-separated multi-host DSNs
    (`postgresql://u@localhost,prod.example/db`) and connects to whichever
    host answers, but SQLAlchemy's `make_url` captures the whole comma list as
    a single `.host` string. We split it ourselves and treat EVERY entry as a
    connection target. A host-less DSN (the unix-socket form, `.host is None`)
    yields an empty list - local by construction, since a socket cannot address
    a remote server.
    """
    raw_host = make_url(get_async_database_url()).host
    if not raw_host:
        return []
    return [part.strip().lower() for part in raw_host.split(",") if part.strip()]


def assert_local_seed_db() -> None:
    """Abort (exit code 2) unless EVERY resolved DATABASE_URL host is local.

    Unconditional - there is deliberately no override flag, because the whole
    point is that no seed script can ever reach a remote database. A multi-host
    DSN must have all of its hosts in the allowlist; a single remote host is
    enough to refuse, because asyncpg could connect to it.
    """
    remote = [host for host in _seed_db_hosts() if host not in _LOCAL_HOSTS]
    if remote:
        print(
            f"Refusing to seed: DATABASE_URL host(s) {remote!r} not local "
            f"({', '.join(sorted(_LOCAL_HOSTS))}). Seed scripts create a "
            "known-credential founder login and must only run on a local/test DB.",
            file=sys.stderr,
        )
        raise SystemExit(2)


def dev_seed_password() -> str:
    """Dev/test seed password. Overridable via `DEV_SEED_PASSWORD`; the default
    is local-only (the host guard makes it unreachable off localhost)."""
    return os.environ.get("DEV_SEED_PASSWORD", _DEFAULT_DEV_PASSWORD)
