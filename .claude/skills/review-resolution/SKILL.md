---
name: review-resolution
description: The mandatory procedure for resolving a code-review round on this
  project. Use BEFORE writing any fix for a review finding - it exists because
  rounds 9-12 of S1-26b/c each fixed the reviewer's example and shipped its
  mirror. A review comment is one INSTANCE of a defect class; this skill turns
  it into the class before any code changes.
---

# Review resolution

Round 11 of S1-26b/c adopted this as process; round 12 found the file this
skill lives in had never been created - the process existed only as a CHANGELOG
bullet, which is exactly the docstring-not-enforcement failure the project
keeps re-learning. This file is the enforcement surface: follow it literally.

## The pattern this exists to break

Rounds 9-12 of S1-26b/c ran the same loop four times on one guard (the ordinal
postcode rule): skip-all -> keep-first -> keep-last-with-a-denylist -> still
wrong. Each fix was correct on the reviewer's example and wrong on its mirror,
because each was derived FROM the example rather than from a specification.
The reviewer's round-12 summary: "the guard was written against the one example
in the review rather than against the class."

## The procedure, per finding

1. **Reproduce by execution first.** Run the finding's inputs against the head
   code and confirm the claimed output byte-for-byte. Never plan a fix from
   reading the review alone.
2. **Name the class, not the instance.** Before any fix: what is the defect a
   CLASS of? Enumerate in writing:
   - every CALL SITE of the thing being changed (both DB and GHL legs, every
     caller, concurrent callers);
   - every INPUT SHAPE the rule must handle, not just the shapes in the review;
   - the SYMMETRIC / positional MIRROR of the reported case (prefix vs suffix,
     leading vs trailing, first vs last, ours vs theirs, left leg vs right leg);
   - the FAILURE DIRECTION of any residual: MERGE-direction residuals are
     never deferrable; SPLIT-direction residuals may be deferred only with a
     logged reason and an owning ticket.
3. **Write the specification before the code.** If the fix is to a rule or
   normalizer, state what must hold for ALL inputs (an acceptance matrix:
   every reviewer example from every round, plus every previously pinned row),
   write the matrix as tests, and confirm the new rows FAIL on head before
   implementing. A positional patch (first/last/skip) is not a specification.
4. **Take the reviewer's stated fix as written.** If the review names a
   mechanism, implement that mechanism. Substituting a weaker equivalent has
   caused repeat findings twice (round 11 P1.1 implemented a leading-anchor
   regex where the review said "classify on digit content"; see also the
   memory rule feedback_review_supersedes_older_verbal). Deviate only with a
   written reason in the reply, stated as a deviation.
5. **Pin every new guard.** Test + mutation-manifest entry, chosen so no
   sibling guard can mask the kill (verify the sole-kill property by hand at
   authoring time - the round-11 floor-params masking). A guard without a
   manifest entry is not finished.
6. **Sweep for collateral staleness.** Every docstring, comment, table and
   CHANGELOG claim the fix touches or contradicts - the round-11 sweep found a
   FIFTH stale site in the module header. Treat every "never / cannot /
   guarantees" as falsifiable.
7. **Prove the unchanged surface.** For a rule change, run a side-by-side
   sweep (old code vs new) over a generated corpus and account for EVERY
   difference by an intended rule; zero unexplained diffs.

## Answering the review

- Reply per finding: what was wrong, the class it belongs to, the fix, the
  evidence (test names, sweep numbers), and any deviation with its reason.
- Fix all P2/P3s in the same round unless explicitly deferred with a ticket -
  deferred "minor" items have become the next round's blockers repeatedly.
- Lifecycle: CHANGELOG entry with a Verified bullet BEFORE hand-off, the PR
  body updated (not a comment), and the CI-green Verified bullet appended per
  step 15 when it lands.
