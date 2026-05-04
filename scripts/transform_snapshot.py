"""Transform a raw StrikeFlow boards_get_snapshot response into the
BoardSnapshot shape consumed by progress-site/src/main.tsx.

Usage:
    python scripts/transform_snapshot.py <raw_input.json> <output.json>

The raw input is the full MCP response (with the outer {"success", "data"}
wrapper). The output matches src/types/progress.ts BoardSnapshot.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

# Lists to include in the public progress page, in display order.
INCLUDE_LISTS = [
    "Backlog",
    "Planned",
    "Sprint 1: Foundation & Signing Capture",
    "Sprint 2: Core Fan-Out",
    "Sprint 3: Financial, Comms & Kick-Off",
    "Sprint 4: Sales Intelligence, Polish & Pilot",
    "Next Up",
    "In Progress",
    "To Review",
    "Completed",
    "Tested & Done",
]

BOARD_NAME = "Bullet Digital Media"


def normalize_tags(raw):
    if not raw:
        return []
    out = []
    for t in raw:
        if isinstance(t, str):
            out.append(t)
        elif isinstance(t, dict) and t.get("name"):
            out.append(t["name"])
    return out


def normalize_notes(raw):
    if not raw:
        return []
    out = []
    for n in raw:
        if not isinstance(n, dict):
            continue
        out.append({
            "content": n.get("note_text") or n.get("content") or "",
            "author": n.get("author") or "",
            "created_at": n.get("created_at") or "",
        })
    return out


def normalize_card(card, list_name):
    return {
        "id": card["id"],
        "title": card.get("title", ""),
        "description": card.get("description") or "",
        "status": card.get("status"),
        "list_name": list_name,
        "tags": normalize_tags(card.get("tags")),
        "notes": normalize_notes(card.get("notes")),
        "created_at": card.get("created_at", ""),
        "updated_at": card.get("updated_at", ""),
    }


def main(in_path: str, out_path: str) -> None:
    with open(in_path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    data = raw.get("data", raw)
    raw_lists = data.get("lists", [])
    by_name = {lst.get("name"): lst for lst in raw_lists}

    output_lists = []
    seen = set()
    unparseable = []
    for name in INCLUDE_LISTS:
        lst = by_name.get(name)
        if not lst:
            continue
        cards = [normalize_card(c, name) for c in lst.get("cards", [])]
        for c in cards:
            if c["title"] and not c["title"][:1] == "S":
                # Title doesn't start with S — won't parse as Sprint.
                unparseable.append((name, c["title"]))
        output_lists.append({"name": name, "cards": cards})
        seen.add(name)

    # Warn for any list present on the board but not in INCLUDE_LISTS.
    for lst in raw_lists:
        if lst.get("name") not in seen:
            unparseable.append(("excluded-list", lst.get("name", "<no name>")))

    snapshot = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "board_name": BOARD_NAME,
        "lists": output_lists,
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2, ensure_ascii=False)
        f.write("\n")

    total = sum(len(lst["cards"]) for lst in output_lists)
    print(f"Snapshot written: {out_path}")
    print(f"  Generated at: {snapshot['generated_at']}")
    print(f"  Lists: {len(output_lists)}")
    print(f"  Total cards: {total}")
    for lst in output_lists:
        print(f"    {lst['name']}: {len(lst['cards'])}")
    if unparseable:
        print("\nWarnings:")
        for kind, item in unparseable:
            print(f"  [{kind}] {item}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: transform_snapshot.py <raw_input.json> <output.json>", file=sys.stderr)
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
