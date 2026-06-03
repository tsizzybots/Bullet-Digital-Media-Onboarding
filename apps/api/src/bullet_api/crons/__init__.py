"""Scheduled (cron) jobs for bullet-api.

Each module here exposes a ``main()`` entry point a Render cron service runs
via ``uv run python -m bullet_api.crons.<name>``. This package is distinct
from ``bullet_api.scripts``, which holds one-off operator tools (e.g. the
team seeder) rather than recurring jobs.
"""
