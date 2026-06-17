"""Sales-call transcript linking + read API (S1-27).

`linking` holds the one shared helper that attaches a parked
`sales_call_transcripts` row to a client (used by all three link paths: the
capture worker's immediate email match, the signing-time backfill, and the
manual assign endpoint). `router` exposes the unlinked-list + manual-attach
endpoints the S1-27a dashboard consumes.
"""

from __future__ import annotations

from bullet_api.transcripts.router import transcripts_router

__all__ = ["transcripts_router"]
