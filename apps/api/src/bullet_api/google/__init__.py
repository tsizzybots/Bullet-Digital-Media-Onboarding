"""Google integration seams for S1-27 sales-call transcript capture.

`meet_client` (transcript + participants), `calendar_client` (invite attendee
emails for the auto-link match), `credentials` (service-account token minting
via domain-wide delegation), and `pubsub` (verify + parse the Workspace Events
push that delivers the transcript-ready webhook).

Every external surface is a Protocol with an `Http*` production wiring and a
`Fake*` test double, mirroring the PandaDoc / GHL / R2 seams. The whole feature
is built and tested against the fakes; the live clients run only once Bullet's
Google Cloud project + service account are provisioned (env-only, no code
change - see CHANGELOG 17/06/2026 S1-27).
"""

from __future__ import annotations
