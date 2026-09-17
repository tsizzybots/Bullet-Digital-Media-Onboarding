"""S1-26l: identifier rename in classify_postcode - NO recompute owed

Revision ID: 0014_identity_key_rename
Revises: 0013_clients_identity_key
Create Date: 2026-09-17

NOTE ON THE REVISION ID LENGTH. `alembic_version.version_num` is
`character varying(32)`. This file was first written as
`0014_identity_key_rename_no_recompute` (37 chars) and `alembic upgrade head`
died on it with `StringDataRightTruncationError: value too long for type
character varying(32)` - which would have failed `render.yaml`'s
`preDeployCommand` on every staging deploy after merge. Keep revision ids at
32 characters or fewer.

THIS MIGRATION CHANGES NO SCHEMA AND NO DATA. It exists to answer, in the
place the gate looks for the answer, a question that must be answered
explicitly rather than absorbed.

WHAT MOVED. S1-26l renames two locals inside `worker/identity_key.py`'s
`classify_postcode`:

    real_candidates  -> non_ordinal_candidates
    distinct_real    -> distinct_non_ordinal

"real" there meant NON-ORDINAL, never `PostcodeConfidence.REAL`. The two
meanings collided when round 14 introduced the enum, to the point that
`real_candidates[0]` can legitimately come back AMBIGUOUS. Round 15 found the
collision, documented it in place, and deferred the rename deliberately:
`classify_postcode` is inside `_G7_KEY_FUNCTIONS`, and round 15 proved by
execution that the fingerprint moves on ANY change to that body, identifier
names included. Spending the gate's first proven movement on a cosmetic rename
teaches the next maintainer that fingerprint moves are sometimes noise, which
is how a gate decays. S1-26l changes that body for real reasons, so the move
is earned here.

WHY NO RECOMPUTE IS OWED. G7 exists because `clients.identity_key` is DERIVED
and STORED, so any change to how it derives silently splits the population:
old rows keep keys the new code can no longer produce, a genuine returning
client stops matching, and it gets a duplicate sub-account. That is a real
hazard and 0013's own warning describes it at length.

A rename is not that change. Python identifiers are not data: no local's NAME
reaches the returned value. The AST fingerprint moves because `ast.dump`
includes `ast.Name(id=...)` - it is deliberately more sensitive than the
value, so that a reviewer is forced to look. Having looked: the produced value
is unchanged for every input.

HOW THAT CLAIM IS DISCHARGED, rather than asserted:

- `tests/golden/identity_key_golden.json` pins 3,752 postcodes on BOTH value
  and confidence, plus 8 identity keys, and `TestGoldenFile` fails on any
  value diff with the message "A VALUE diff means a stored identity_key is
  orphaned and owes a migration." It is green across this change.
- `scripts/key_invariance_sweep.py`, added by S1-26l, runs the working tree
  against the merge base over a far wider generated corpus and exits non-zero
  on any value or key difference. It reports 0 of each across this change.
  That harness is CHECKED IN this time: rounds 14 and 15 both reported a
  ~400,000-input sweep whose harness was then lost with a session scratchpad,
  which is exactly why the golden file was demanded in the first place.

So: no rows are orphaned, no backfill exists to run, and this migration
deliberately does nothing.

FOR WHOEVER CHANGES THE NORMALIZER NEXT. Do not take this file as a template
for waving G7 through. It is discharging the gate by MEASUREMENT, and the
measurement is re-runnable by command. A change that alters a produced value
owes a real recompute of every non-NULL `identity_key` (S1-26h owns that
tooling), not a note.
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "0014_identity_key_rename"
down_revision: str | Sequence[str] | None = "0013_clients_identity_key"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """No-op. See the module docstring: a rename moves no stored value."""


def downgrade() -> None:
    """No-op, and therefore trivially lossless - unlike 0013's downgrade."""
