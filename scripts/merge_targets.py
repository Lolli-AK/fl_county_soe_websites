#!/usr/bin/env python3
"""Merge Phase 1 discovery output into manifest/targets.csv.

Combines:
  manifest/batch<N>_homepages.csv       -> the homepage row per county
  manifest/batch<N>_targets_draft.csv   -> the 4 election page rows per county

into one unified manifest of 67 counties x 5 page types = 335 rows.

The `batch` column is taken from **manifest/counties.csv**, not from the discovery
run, because in Florida batch means "where this county's homepage came from"
(1 = the state SOE directory, 2 = the directory URL was stale and the live domain
was resolved instead) — and that is a property of the seed, not of the crawl. The
Texas equivalent merged one discovery batch at a time and took the label from the
run; here there is a single discovery pass over all 67 counties, so the label has to
come from the seed or it would be uniformly wrong.

Rows for counties not in this run are preserved exactly as-is, including their audit
columns. Merged rows are written with empty audit columns for audit_targets.py to
populate.

Idempotent: re-running replaces those counties' rows rather than duplicating them.

Usage:
    python scripts/merge_targets.py
    python scripts/merge_targets.py --batch 1
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGETS = ROOT / "manifest" / "targets.csv"
SEED = ROOT / "manifest" / "counties.csv"

BASE = ["county", "batch", "page_type", "url", "external", "notes"]
AUDIT = ["verify_status", "http_status", "final_url", "audit_confidence",
         "audit_reason", "flag_for_review"]
PAGE_ORDER = ["homepage", "elections", "polling", "early_voting", "results"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", default="1",
                    help="which discovery run's files to merge (default 1)")
    args = ap.parse_args()
    run = str(args.batch)

    homepages = ROOT / "manifest" / f"batch{run}_homepages.csv"
    draft = ROOT / "manifest" / f"batch{run}_targets_draft.csv"
    for f in (homepages, draft, SEED):
        if not f.exists():
            raise SystemExit(f"missing {f} — run Phase 1 discovery first")

    # Authoritative batch label per county, from the seed.
    seed_batch = {r["county"].strip(): (r["batch"] or "1").strip()
                  for r in csv.DictReader(SEED.open(encoding="utf-8"))}

    existing = (list(csv.DictReader(TARGETS.open(encoding="utf-8")))
                if TARGETS.exists() else [])

    homes = {r["county"]: r for r in csv.DictReader(homepages.open(encoding="utf-8"))}
    drafts: dict[tuple[str, str], dict] = {}
    for r in csv.DictReader(draft.open(encoding="utf-8")):
        drafts[(r["county"], r["page_type"])] = r

    # Preserve rows for any county this run did not touch.
    keep = [r for r in existing if r["county"] not in homes]

    merged: list[dict] = []
    for county, h in homes.items():
        batch = seed_batch.get(county, "1")
        for ptype in PAGE_ORDER:
            if ptype == "homepage":
                note = h.get("evidence", "")
                if h.get("confidence") != "confident":
                    note = f"REVIEW ({h.get('confidence')}): {note}"
                src = h.get("source") or "state directory"
                row = {"county": county, "batch": batch, "page_type": "homepage",
                       "url": h.get("homepage", "").strip(), "external": "false",
                       "notes": f"SOE homepage via {src} — {note}"[:300]}
            else:
                d = drafts.get((county, ptype))
                if d is None:
                    row = {"county": county, "batch": batch, "page_type": ptype,
                           "url": "", "external": "false",
                           "notes": "GAP: not discovered (homepage unreachable "
                                    "during discovery)"}
                else:
                    row = {"county": county, "batch": batch, "page_type": ptype,
                           "url": d["url"].strip(), "external": d["external"],
                           "notes": d["notes"][:300]}
            row.update({f: "" for f in AUDIT})
            merged.append(row)

    rows = keep + merged
    for r in rows:
        for f in BASE + AUDIT:
            r.setdefault(f, "")

    with TARGETS.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=BASE + AUDIT, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    counties = {r["county"] for r in rows}
    urls = sum(1 for r in rows if r["url"])
    print(f"wrote {TARGETS}")
    print(f"  counties: {len(counties)}  rows: {len(rows)}  "
          f"with URLs: {urls}  gaps: {len(rows) - urls}")
    print(f"  kept: {len(keep)} rows | merged: {len(merged)} rows")


if __name__ == "__main__":
    main()
