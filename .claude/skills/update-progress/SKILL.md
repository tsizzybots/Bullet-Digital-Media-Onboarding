---
name: update-progress
description: |
  Regenerate the Bullet Digital Media client-facing progress dashboard
  from the live StrikeFlow board, verify the static-site build, commit,
  and gate on user approval before pushing to deploy on Render.
triggers:
  - update-progress
  - update progress
  - update bullet progress
  - refresh progress page
  - refresh progress snapshot
user_invocable: true
---

# /update-progress

> Fetch live StrikeFlow board data → write `progress-site/src/data/board-snapshot.json` → verify the Vite build → commit → ask before push (Render auto-deploys on push).

## Board details

- **Board ID**: `c01081f2-c27c-4a8c-b7c5-0b2857254cd9`
- **Board name**: Bullet Digital Media
- **Repo**: `tsizzybots/Bullet-Digital-Media-Onboarding`
- **Render service**: `Bullet-Digital-Media-Progress` (slug `bullet-digital-media-progress`, static site in IzzyAgents Render workspace)
- **GitHub account**: `tsizzybots` (run `gh auth switch --user tsizzybots` before push)

## Execution steps

Run sequentially. Stop on any failure and report to the user.

### Step 1 — Fetch live board data

Call `mcp__strikeflow__boards_get_snapshot` with:
- `board_id`: `c01081f2-c27c-4a8c-b7c5-0b2857254cd9`
- `full_notes`: `true`
- `max_cards_per_list`: `500`

The response is large; if it exceeds the inline token limit it will be saved to a file under `~/.claude/projects/.../tool-results/`. Capture that file path.

### Step 2 — Transform into snapshot shape

Run the transform script:

```bash
python3 scripts/transform_snapshot.py <raw_input_path> progress-site/src/data/board-snapshot.json
```

The script:
- Includes only the 10 in-scope lists in this fixed display order: Backlog, Planned, Sprint 1, Sprint 2, Sprint 3, Sprint 4, Next Up, In Progress, To Review, Completed, Tested & Done.
- Excludes any other list and prints a warning naming it.
- Normalizes tags (object-or-string) and notes (`note_text` → `content`) defensively.
- Writes the output with 2-space indentation and a trailing newline.
- Prints a per-list breakdown and warns about any card title that doesn't start with `S` (will not parse to a sprint).

Confirm the breakdown looks sane and review any warnings. If a list shows up under "excluded-list" that should be public, add it to `INCLUDE_LISTS` in `scripts/transform_snapshot.py`.

### Step 3 — Verify the build

```bash
cd progress-site && npm install && npx vite build
```

If the build fails, **stop and report the error**. Do not proceed to commit.

### Step 4 — Commit

Stage only the snapshot file (and the transform script if it was edited):

```bash
git add progress-site/src/data/board-snapshot.json
git commit -m "chore: update progress dashboard snapshot - DD/MM/YYYY"
```

Use UK date format (e.g. `04/05/2026`) per project formatting rules. Use a plain ASCII hyphen, not an em dash.

### Step 5 — Push (ask first)

Per the global rule "never auto-commit/push", **ask the user**:

> "Snapshot updated and committed. Push to `tsizzybots/Bullet-Digital-Media-Onboarding` so Render redeploys `bullet-digital-media-progress`?"

If yes:
```bash
gh auth switch --user tsizzybots
git push
```

Then inform the user the static site will redeploy automatically on Render.

If no, stop and report success.

## Notes

- Card sprint membership is parsed from the **title prefix** (`S1-…`, `S2-…`, etc), not from list membership. A card moving from `Sprint 1: …` into `In Progress` keeps its Sprint 1 grouping while updating its status to `in_progress`. New cards appear automatically as long as their title starts with `S{N}-`.
- Routine snapshot refreshes do **not** require a `docs/CHANGELOG.md` entry — they are data updates, not scope/decision changes. Log the changelog only on structural changes (new sprint added, new list, etc).

## Key file references

| File | Purpose |
|------|---------|
| `progress-site/src/data/board-snapshot.json` | Output — the snapshot file regenerated each run |
| `progress-site/src/types/progress.ts` | Type definitions and parse logic |
| `progress-site/src/App.tsx` | Top-level UI, "Last updated" reads `generated_at` |
| `progress-site/src/main.tsx` | Static site entry — imports the snapshot |
| `scripts/transform_snapshot.py` | StrikeFlow → BoardSnapshot transform |
| `render.yaml` | Render config — note that `bullet-digital-media-progress` deploys via its own per-service GitHub connection, NOT via this file |
