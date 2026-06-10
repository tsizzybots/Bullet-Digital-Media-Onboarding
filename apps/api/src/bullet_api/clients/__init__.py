"""Client read API (dashboard-facing).

S1-31 adds the `/clients` list view that the dashboard polls. The package
mirrors the layout of `auth` / `admin`: the router lives in `router.py` and
is re-exported here so `main.py` can `from bullet_api.clients import
clients_router`.
"""

from __future__ import annotations

from bullet_api.clients.router import clients_router

__all__ = ["clients_router"]
