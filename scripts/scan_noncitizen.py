#!/usr/bin/env python3
"""Report the non-citizen-voting flag, over the working tree or all history.

    python scripts/scan_noncitizen.py                 # current snapshots
    python scripts/scan_noncitizen.py --history       # every snapshot commit
    python scripts/scan_noncitizen.py --history --csv manifest/noncitizen-panel.csv

snapshot.py stamps the flag into meta.json going forward. This script exists
for the part that cannot: every snapshot commit already on disk predates the
flag, and the language this watches for is exactly the kind that appears once
and is quietly removed. Reading it out of history turns a tripwire that starts
today into a panel that starts the day the repo did.

That recomputation is only sound because the flag is a pure function of
page.txt (see noncitizen.py), which is committed for every run. Nothing is
inferred from meta.json, so a historical row and a live row are produced by
the same code path.

History mode is keyed on blob hashes, not paths. An unchanged page keeps the
same blob across runs, and most pages do not change most days, so the ~44,000
(commit, page) pairs in the Florida history collapse to a few thousand
distinct blobs. Scanning per blob and reusing the result is what makes a full
sweep take seconds instead of an hour.
"""

from __future__ import annotations

import argparse
import csv
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import noncitizen

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOTS = ROOT / "snapshots"
# manifest/targets.csv calls it `county`; the state-level sibling calls it
# `state`. The path shape is the same either way, so the panel labels the
# first path segment `unit` and stays readable in both.
FIELDS = ["commit", "commit_date", "unit", "page_type", "present", "terms", "count"]


def _git(*args: str) -> str:
    return subprocess.run(("git", *args), cwd=ROOT, capture_output=True,
                          text=True, check=True).stdout


def snapshot_commits() -> list[tuple[str, str]]:
    """(sha, date) for every commit touching snapshots/, oldest first."""
    out = _git("log", "--reverse", "--format=%H\t%ad", "--date=short", "--", "snapshots")
    return [tuple(line.split("\t")) for line in out.splitlines() if line.strip()]


def page_blobs(sha: str) -> list[tuple[str, str]]:
    """(path, blob_sha) for every page.txt in one commit's tree."""
    out = _git("ls-tree", "-r", sha, "--", "snapshots")
    rows = []
    for line in out.splitlines():
        meta, _, path = line.partition("\t")
        if path.endswith("/page.txt"):
            rows.append((path, meta.split()[2]))
    return rows


def read_blobs(shas: list[str]) -> dict[str, str]:
    """Bulk-read blobs through one `git cat-file --batch` process.

    One subprocess per blob is the obvious implementation and is roughly two
    orders of magnitude slower on a history this size.
    """
    if not shas:
        return {}
    proc = subprocess.run(
        ("git", "cat-file", "--batch"), cwd=ROOT, input="\n".join(shas).encode(),
        capture_output=True, check=True,
    )
    out, pos, blobs = proc.stdout, 0, {}
    for sha in shas:
        nl = out.index(b"\n", pos)
        header = out[pos:nl].decode()
        pos = nl + 1
        size = int(header.split()[2])
        blobs[sha] = out[pos:pos + size].decode("utf-8", errors="replace")
        pos += size + 1
    return blobs


def unit_and_type(path: str) -> tuple[str, str]:
    parts = Path(path).parts
    return parts[1], parts[2]


def scan_worktree() -> list[dict]:
    rows = []
    for f in sorted(SNAPSHOTS.rglob("page.txt")):
        unit, page_type = unit_and_type(str(f.relative_to(ROOT)))
        r = noncitizen.scan(f.read_text(encoding="utf-8", errors="replace"))
        rows.append(dict(commit="(worktree)", commit_date="", unit=unit,
                         page_type=page_type, present=r["present"],
                         terms=";".join(r["terms"]), count=r["count"]))
    return rows


def scan_history() -> list[dict]:
    commits = snapshot_commits()
    print(f"scanning {len(commits)} commits ...", file=sys.stderr)

    trees = {sha: page_blobs(sha) for sha, _ in commits}
    wanted = sorted({blob for rows in trees.values() for _, blob in rows})
    print(f"  {sum(len(v) for v in trees.values()):,} page-commit pairs, "
          f"{len(wanted):,} distinct blobs", file=sys.stderr)

    cache = {sha: noncitizen.scan(text) for sha, text in read_blobs(wanted).items()}

    rows = []
    for sha, date in commits:
        for path, blob in trees[sha]:
            r = cache[blob]
            unit, page_type = unit_and_type(path)
            rows.append(dict(commit=sha[:9], commit_date=date, unit=unit,
                             page_type=page_type, present=r["present"],
                             terms=";".join(r["terms"]), count=r["count"]))
    return rows


def report(rows: list[dict]) -> None:
    flagged = [r for r in rows if r["present"]]
    units = {(r["unit"], r["page_type"]) for r in flagged}
    print(f"\n{len(flagged):,} flagged of {len(rows):,} page-observations, "
          f"{len(units)} distinct pages")
    for unit, page_type in sorted(units):
        hits = [r for r in flagged if (r["unit"], r["page_type"]) == (unit, page_type)]
        terms = sorted({t for r in hits for t in r["terms"].split(";") if t})
        first, last = hits[0], hits[-1]
        span = first["commit_date"] or "(worktree)"
        if last["commit_date"] and last["commit_date"] != first["commit_date"]:
            span += f" → {last['commit_date']}"
        print(f"  {unit}/{page_type}: {span}  [{', '.join(terms)}]")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--history", action="store_true",
                    help="scan every snapshot commit instead of the working tree")
    ap.add_argument("--csv", type=Path, help="write the panel here")
    args = ap.parse_args()

    rows = scan_history() if args.history else scan_worktree()
    report(rows)

    if args.csv:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        with args.csv.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(rows)
        print(f"\nwrote {args.csv} ({len(rows):,} rows)")


if __name__ == "__main__":
    main()
