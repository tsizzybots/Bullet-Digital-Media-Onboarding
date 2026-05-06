# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "psycopg[binary]>=3.2,<4.0",
# ]
# ///
"""Verify the local Postgres dev DB exposes the `vector` extension.

Satisfies the S1-02 test contract: a local script can `CREATE EXTENSION vector;`
against the docker compose Postgres instance.

Run with uv (recommended; no project-level deps required):

    uv run scripts/verify_pgvector.py

Or with a regular Python env that has psycopg installed:

    pip install 'psycopg[binary]'
    python scripts/verify_pgvector.py

The script:
  1. Reads DATABASE_URL from the environment (loads .env via stdlib if present).
  2. Connects to Postgres.
  3. Runs CREATE EXTENSION IF NOT EXISTS vector; then asserts pg_extension
     contains a 'vector' row.
  4. Exits 0 on success, 1 on any failure with the full exception printed.

Idempotent: re-runs against the same DB succeed. Compatible with the Alembic
migration in S1-05, which uses the same IF NOT EXISTS form.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

import psycopg


def load_env_file(path: Path) -> None:
    """Tiny .env loader: KEY=VALUE per line, comments / blanks ignored.

    Does not override variables already set in the process environment.
    """
    if not path.is_file():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    load_env_file(repo_root / ".env")

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print(
            "ERROR: DATABASE_URL is not set. Copy .env.example to .env or "
            "export DATABASE_URL before running this script.",
            file=sys.stderr,
        )
        return 1

    parsed = urlsplit(database_url)
    target = (
        f"{parsed.hostname or '?'}:{parsed.port or '?'}"
        f"{parsed.path or ''}"
    )

    try:
        with psycopg.connect(database_url, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
                cur.execute(
                    "SELECT extname FROM pg_extension WHERE extname = 'vector';"
                )
                row = cur.fetchone()
                if row is None:
                    print(
                        "ERROR: pg_extension has no 'vector' row after "
                        "CREATE EXTENSION; pgvector is not available on this "
                        "Postgres instance.",
                        file=sys.stderr,
                    )
                    return 1
    except Exception as exc:  # surface the full driver / SQL error
        print(f"ERROR: pgvector verification failed: {exc}", file=sys.stderr)
        return 1

    print(f"OK: pgvector extension available on {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
