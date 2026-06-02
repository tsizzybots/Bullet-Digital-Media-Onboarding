"""Dump the FastAPI OpenAPI document to packages/shared/openapi.json.

Part of the S1-17 codegen pipeline. Importing the ASGI app and calling
`app.openapi()` is pure schema generation - no server is started and no
database connection is opened - so this runs in any context (CI, local,
pre-commit) without docker or a live Postgres.

Requires `DATABASE_URL` to be *present* in the environment (any value):
importing `bullet_api.main` constructs `Settings`, whose `database_url`
field is required. The value is never used to connect here.

Output is written sorted and pretty-printed so re-running on unchanged
code produces a byte-identical file - that determinism is what the CI
drift check (`git diff --exit-code`) relies on.

Run via `make codegen`, or directly from apps/api:
    uv run python scripts/dump_openapi.py
"""

from __future__ import annotations

import json
from pathlib import Path

from bullet_api.main import app

# scripts/ -> apps/api -> apps -> <repo root>
REPO_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_PATH = REPO_ROOT / "packages" / "shared" / "openapi.json"


def main() -> None:
    spec = app.openapi()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(spec, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {OUTPUT_PATH.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
