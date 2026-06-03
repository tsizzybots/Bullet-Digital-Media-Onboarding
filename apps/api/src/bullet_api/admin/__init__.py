"""Admin / operator endpoints (founder + engineer only).

S1-24 lands the PandaDoc manual replay route (`POST
/admin/pandadoc/replay/{document_id}`). Future manual-ops endpoints
(re-trigger a fan-out step, force a status recheck) join this router. The
existing `/admin/ping` smoke endpoint stays in `main.py`.
"""

from __future__ import annotations

from bullet_api.admin.pandadoc import router as admin_router

__all__ = ["admin_router"]
