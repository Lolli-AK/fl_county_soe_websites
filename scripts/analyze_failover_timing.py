#!/usr/bin/env python3
"""Reconstruct, from the commit series, when a page left its steady state and came back.

`analyze_diffs.py` compares two revisions and says WHAT changed. That is the right
tool when you have two endpoints. Once the scheduler is running at election-window
cadence there are ~9 commits a day, and the interesting question stops being "what
differs between before and after" and becomes "WHEN did it change, and for how
long" — which two endpoints cannot answer and which no amount of re-diffing them
will recover.

So this walks the whole commit series per target and finds EPISODES: a maximal run
of consecutive snapshots in which the page was far from its own steady state,
bounded on both sides by snapshots in which it was not.

Why `page.txt` and not `page.html`: the README's determinism work established that
residual churn lives in markup and that `page.txt` never differed across two
identical runs. A text change is therefore a content change. The git blob sha gives
exact content identity for free, and the blob SIZE gives magnitude — both without
reading a single blob.

**The reason this script exists rather than a per-county eyeball.** An episode in
one county is that county publishing something. The same episode in many counties,
starting and ending inside the same pair of consecutive snapshots, is not
independent local decisions — it is one actor pushing one template to all of them.
That is a different finding, it is invisible county-by-county, and detecting it is
the point.

Timing is reported as BOUNDS, never as a point. A snapshot series can only say "the
change happened between the last normal capture and the first changed one". With
3-hourly commits that bound is a few hours wide; the columns are named
`*_after` / `*_by` so an interval cannot be misread as a measurement.

### Why the episode test is a size band and not "returned to the same bytes"

The first version defined an episode as a run of states differing from the baseline
blob, ending when the baseline blob came back exactly. That is the cleaner
definition and it is too brittle to use: an election page that swaps out and back
generally returns with a new date, a new turnout figure or a new results link, so it
never returns byte-identical, and the run then never terminates — the detector
silently reports zero episodes on data that plainly contains them. (Calhoun happens
to return byte-identical; most do not.)

The band test asks the question that actually matches the phenomenon: was the page
a small fraction of its usual size for a while, and then its usual size again. A
county replacing its whole site with a one-screen election-night page moves ~90%; a
county editing a paragraph does not move 40%. `returned_to_exact_prior_state`
records the strict condition as an observation instead of relying on it as a gate.

Usage:
    python scripts/analyze_failover_timing.py
    python scripts/analyze_failover_timing.py --since 2026-08-16 --until 2026-08-23
    python scripts/analyze_failover_timing.py --band 0.4 --min-cluster 3
"""
from __future__ import annotations

import argparse
import csv
import statistics
import subprocess
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EPISODES_OUT = ROOT / "manifest" / "fl-failover-episodes.csv"
CLUSTERS_OUT = ROOT / "manifest" / "fl-failover-clusters.csv"

PAGE_TYPES = ("homepage", "elections", "polling", "early_voting", "results")


def slug(county: str) -> str:
    """Snapshot directory name for a county.

    Must stay identical to snapshot.py's rule (`lower()`, spaces to underscores) or
    every lookup silently misses and the run reports no episodes at all — which is
    exactly what happened the first time this was written. "St. Johns" is
    `st._johns`, "Miami-Dade" is `miami-dade`.
    """
    return county.strip().lower().replace(" ", "_")


def _git(*args: str) -> str:
    return subprocess.run(("git", *args), cwd=ROOT, capture_output=True,
                          text=True, check=True).stdout


def snapshot_commits(since: str | None, until: str | None) -> list[tuple[str, datetime]]:
    """Commits that touched snapshots/, oldest first.

    Restricted to commits touching snapshots/ so analysis-only commits (a figure, a
    README edit) don't enter the series as points: they carry no new capture, and
    counting them would stretch every reported bound.
    """
    cmd = ["log", "--format=%H\t%aI"]
    if since:
        cmd.append(f"--since={since}")
    if until:
        cmd.append(f"--until={until}")
    cmd += ["--", "snapshots"]
    out = []
    for line in _git(*cmd).splitlines():
        if not line.strip():
            continue
        sha, when = line.split("\t")
        out.append((sha, datetime.fromisoformat(when)))
    # git log is newest-first; sort explicitly so index arithmetic below means
    # "chronological neighbour".
    return sorted(out, key=lambda r: r[1])


def state_matrix(commits, targets):
    """(county, page_type) -> per-commit (blob sha, byte size), None where absent.

    One `git cat-file --batch-check` for the whole grid. Per-cell calls would be
    tens of thousands of processes; this is one, and `--batch-check` gives size
    without ever materializing a blob.
    """
    specs = [f"{sha}:snapshots/{slug(c)}/{pt}/page.txt"
             for sha, _ in commits for c, pt in targets]
    proc = subprocess.run(("git", "cat-file", "--batch-check"), cwd=ROOT,
                          input="\n".join(specs), capture_output=True, text=True)
    lines = proc.stdout.splitlines()
    if len(lines) != len(specs):
        raise SystemExit(f"cat-file returned {len(lines)} lines for {len(specs)} specs")

    matrix = {t: [] for t in targets}
    i = 0
    for _sha, _when in commits:
        for t in targets:
            parts = lines[i].split()
            i += 1
            matrix[t].append((parts[0], int(parts[2]))
                             if len(parts) == 3 and parts[1] == "blob" else None)
    return matrix


def classify_episodes(episodes, commits, band: float) -> None:
    """Tag each episode `county_change` or `capture_failure`, in place.

    A size-band detector cannot tell "the county replaced its site" from "we failed
    to fetch the site" — both collapse the byte count. Left unclassified this script
    would report a blocked run as county behaviour, which is the single most
    misleading thing it could do. So every episode is checked against the
    `meta.json` captured alongside it.

    **The `error` field alone is not sufficient**, contrary to the README's "filter
    on `error`, not on `http_status`" rule. Measured here: on 2026-08-21 three
    counties returned an Akamai/Cloudflare **403 Forbidden** whose body normalized
    to the 28-character text "403 Forbidden", with `http_status: 403` and
    `error: null` — the fetch did not fail, so nothing set `error`, and the page was
    stored as though it were content. The README's rule was written for a challenge
    that clears after a 403; it does not cover a 403 that IS the final response.

    **A 404 is not a capture failure.** The first version of this test treated any
    non-200 as junk, which mislabelled the whole 10-county election-night cluster:
    17 of its 44 episodes are `404 / 0 bytes` because the county's deep URLs stopped
    resolving while the replacement page was up. That is the *finding* — one of the
    two failure modes the project documented — not a fetch problem. Status has to be
    read for WHOSE failure it is:

      * `error` set (timeout, reset)     -> capture_failure  (our side)
      * 403 / 429 / 5xx                  -> capture_failure  (blocked or server down)
      * 200 with a body under 200 bytes  -> capture_failure  (empty success)
      * 404 / 410                        -> page_removed     (the county's side)
      * 200 with a plausible body        -> county_change

    The size floor (200 bytes) applies only to 200s. A real minimal election-night
    page runs 800-1,000 characters; a stored error body runs 0-30. Nothing observed
    falls between, so the floor separates them without needing to be tuned.
    """
    specs, index = [], []
    for ep in episodes:
        sha = commits[ep["start_idx"]][0]
        specs.append(f"{sha}:snapshots/{slug(ep['county'])}/{ep['page_type']}/meta.json")
        index.append(ep)
    if not specs:
        return
    proc = subprocess.run(("git", "cat-file", "--batch"), cwd=ROOT,
                          input="\n".join(specs), capture_output=True, text=True)
    # --batch emits "<sha> blob <size>\n<payload>\n" per spec; walk it by declared
    # length rather than by line, because JSON payloads contain newlines.
    out, pos = [], 0
    text = proc.stdout
    for _ in specs:
        nl = text.find("\n", pos)
        if nl < 0:
            break
        header = text[pos:nl].split()
        if len(header) == 3 and header[1] == "blob":
            size = int(header[2])
            out.append(text[nl + 1:nl + 1 + size])
            pos = nl + 1 + size + 1
        else:
            out.append(None)
            pos = nl + 1

    import json
    for ep, payload in zip(index, out):
        status = err = None
        if payload:
            try:
                meta = json.loads(payload)
                status, err = meta.get("http_status"), meta.get("error")
            except json.JSONDecodeError:
                pass
        failures, notes = [], []
        if err:
            failures.append(f"error={err[:60]}")
        if status in (403, 429) or (status is not None and status >= 500):
            failures.append(f"http_status={status}")
        if status == 200 and ep["min_bytes"] < 200:
            failures.append(f"empty 200 body={ep['min_bytes']}B")
        removed = status in (404, 410)
        if removed:
            notes.append(f"http_status={status}; body={ep['min_bytes']}B")

        if failures:
            ep["episode_class"] = "capture_failure"
            ep["episode_evidence"] = "; ".join(failures)
        elif removed:
            ep["episode_class"] = "page_removed"
            ep["episode_evidence"] = "; ".join(notes)
        else:
            ep["episode_class"] = "county_change"
            ep["episode_evidence"] = "200, no error, plausible body"


def find_episodes(series, band: float) -> list[dict]:
    """Maximal runs where size is outside +/- `band` of the target's median size.

    Median over the whole window is the steady-state estimator: it is unmoved by an
    episode that occupies a minority of snapshots, which is precisely the shape being
    looked for. It does assume the page is in its normal state most of the time — a
    page that spends more than half the window swapped out would have the swapped
    state voted in as "steady" and the normal state flagged instead. At 3-hourly
    cadence over a week that is not a live risk; over a two-day window it would be,
    which is why the default window is the full history.

    A run needs an in-band observation before AND after it. An unterminated trailing
    run is a page that changed and has not come back — a different phenomenon, and
    one we cannot yet classify, so it is dropped rather than reported as an episode.
    """
    seen = [(i, blob, size) for i, cell in enumerate(series) if cell
            for blob, size in (cell,)]
    if len(seen) < 3:
        return []
    steady = statistics.median(s for _, _, s in seen)
    if steady <= 0:
        return []

    def in_band(size: int) -> bool:
        return abs(size - steady) <= band * steady

    episodes, run = [], []
    prior_in_band = None          # last in-band observation before the current run
    for idx, blob, size in seen:
        if in_band(size):
            if run:
                episodes.append({
                    "start_idx": run[0][0], "end_idx": run[-1][0],
                    "n_commits": len(run),
                    "states": {b for _, b, _ in run},
                    "steady_bytes": int(steady),
                    "episode_bytes": run[0][2],
                    "min_bytes": min(s for _, _, s in run),
                    "prior_blob": prior_in_band[1] if prior_in_band else None,
                    "restored_blob": blob,
                })
                run = []
            prior_in_band = (idx, blob, size)
        elif prior_in_band is not None:
            # Only start a run once a steady state has been observed, so a target
            # whose window opens mid-episode isn't reported with a bogus onset.
            run.append((idx, blob, size))
    return episodes


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", help="only commits after this date")
    ap.add_argument("--until", help="only commits before this date")
    ap.add_argument("--band", type=float, default=0.4,
                    help="fractional size deviation from median that counts as "
                         "out of steady state (default 0.4 = +/-40%%)")
    ap.add_argument("--min-cluster", type=int, default=3,
                    help="counties sharing one onset/offset before it's a cluster")
    args = ap.parse_args()

    commits = snapshot_commits(args.since, args.until)
    if len(commits) < 3:
        raise SystemExit(f"only {len(commits)} snapshot commits in range — "
                         "an episode needs a before, a during and an after")

    rows = list(csv.DictReader((ROOT / "manifest" / "targets.csv")
                              .open(encoding="utf-8")))
    targets = sorted({(r["county"].strip(), r["page_type"].strip()) for r in rows
                      if r["page_type"].strip() in PAGE_TYPES})

    print(f"{len(commits)} snapshot commits, "
          f"{commits[0][1]:%Y-%m-%d %H:%M} .. {commits[-1][1]:%Y-%m-%d %H:%M}")
    print(f"{len(targets)} manifest targets, band +/-{args.band:.0%}\n")

    matrix = state_matrix(commits, targets)
    captured = sum(1 for s in matrix.values() if any(s))
    print(f"{captured} targets have captures in this window "
          f"({len(targets) - captured} gaps)\n")

    found = []
    for (county, page_type), series in matrix.items():
        for ep in find_episodes(series, args.band):
            ep["county"], ep["page_type"] = county, page_type
            found.append(ep)
    classify_episodes(found, commits, args.band)

    episode_rows = []
    for ep in sorted(found, key=lambda e: (e["start_idx"], e["county"])):
        si, ei = ep["start_idx"], ep["end_idx"]
        last_normal, first_changed = commits[si - 1][1], commits[si][1]
        last_changed = commits[ei][1]
        first_restored = commits[ei + 1][1] if ei + 1 < len(commits) else None
        steady, ep_b = ep["steady_bytes"], ep["episode_bytes"]
        episode_rows.append({
            "county": ep["county"], "page_type": ep["page_type"],
            "steady_bytes": steady, "episode_bytes": ep_b,
            "min_bytes": ep["min_bytes"],
            "shrink_pct": round(100 * (1 - ep["min_bytes"] / steady), 1),
            "snapshots_in_episode": ep["n_commits"],
            "distinct_states_in_episode": len(ep["states"]),
            "changed_after": last_normal.isoformat(),
            "changed_by": first_changed.isoformat(),
            "restored_after": last_changed.isoformat(),
            "restored_by": first_restored.isoformat() if first_restored else "",
            "min_duration_hours": round(
                (last_changed - first_changed).total_seconds() / 3600, 1),
            "max_duration_hours": round(
                ((first_restored or last_changed) - last_normal).total_seconds() / 3600, 1),
            "episode_class": ep["episode_class"],
            "episode_evidence": ep["episode_evidence"],
            "returned_to_exact_prior_state":
                str(ep["prior_blob"] is not None
                    and ep["prior_blob"] == ep["restored_blob"]).lower(),
            "onset_idx": si, "offset_idx": ei,
        })

    _write(EPISODES_OUT, episode_rows, drop_idx=True)
    print(f"wrote {EPISODES_OUT} ({len(episode_rows)} episodes)\n")

    # --- synchronization -----------------------------------------------------
    # Same onset snapshot AND same offset snapshot = the same push. Keyed on commit
    # index, not wall-clock, so the grouping is exact.
    clusters = defaultdict(list)
    for r in episode_rows:
        clusters[(r["onset_idx"], r["offset_idx"])].append(r)

    cluster_rows = []
    for cid, (_key, members) in enumerate(
            sorted(clusters.items(),
                   key=lambda kv: -len({m["county"] for m in kv[1]})), 1):
        counties = sorted({m["county"] for m in members})
        if len(counties) < args.min_cluster:
            continue
        cluster_rows.append({
            "cluster": cid, "n_counties": len(counties), "n_targets": len(members),
            "changed_after": members[0]["changed_after"],
            "changed_by": members[0]["changed_by"],
            "restored_after": members[0]["restored_after"],
            "restored_by": members[0]["restored_by"],
            "onset_window_hours": _hours(members[0]["changed_after"],
                                         members[0]["changed_by"]),
            "offset_window_hours": _hours(members[0]["restored_after"],
                                          members[0]["restored_by"]),
            # page_removed counts as county behaviour: a deep URL 404ing while a
            # replacement page is up is the phenomenon, not a fetch problem.
            "cluster_class": ("capture_failure"
                              if any(m["episode_class"] == "capture_failure"
                                     for m in members)
                              else "county_change"),
            "targets_200": sum(1 for m in members
                               if m["episode_class"] == "county_change"),
            "targets_404": sum(1 for m in members
                               if m["episode_class"] == "page_removed"),
            "page_types": ",".join(sorted({m["page_type"] for m in members})),
            "median_shrink_pct": statistics.median(m["shrink_pct"] for m in members),
            "counties": ",".join(counties),
        })

    _write(CLUSTERS_OUT, cluster_rows)
    print(f"wrote {CLUSTERS_OUT} ({len(cluster_rows)} clusters of "
          f">= {args.min_cluster} counties)\n")

    for c in cluster_rows:
        tag = ("REAL county change" if c["cluster_class"] == "county_change"
               else "CAPTURE FAILURE — not county behaviour")
        print(f"  cluster {c['cluster']} [{tag}]: {c['n_counties']} counties, "
              f"{c['n_targets']} targets ({c['page_types']})")
        print(f"    changed  between {_fmt(c['changed_after'])} and "
              f"{_fmt(c['changed_by'])}   ({c['onset_window_hours']}h window)")
        if c["restored_by"]:
            print(f"    restored between {_fmt(c['restored_after'])} and "
                  f"{_fmt(c['restored_by'])}   ({c['offset_window_hours']}h window)")
        print(f"    median shrink {c['median_shrink_pct']}%   "
              f"({c['targets_200']} targets served a replacement, "
              f"{c['targets_404']} went 404)")
        print(f"    {c['counties']}\n")

    if not cluster_rows:
        print("  no synchronized episode reached the cluster threshold.")

    # Episodes that are NOT part of any cluster are ordinary single-county activity;
    # naming the count keeps the cluster figure honest about its denominator.
    clustered = {c for r in cluster_rows for c in r["counties"].split(",")}
    solo = [r for r in episode_rows if r["county"] not in clustered]
    print(f"{len(solo)} episodes in counties outside any cluster "
          f"(ordinary single-county changes)")


def _hours(a: str, b: str) -> float:
    if not a or not b:
        return ""
    return round((datetime.fromisoformat(b)
                  - datetime.fromisoformat(a)).total_seconds() / 3600, 1)


def _fmt(iso: str) -> str:
    return f"{datetime.fromisoformat(iso):%m-%d %H:%M}" if iso else "-"


def _write(path: Path, rows: list[dict], drop_idx: bool = False) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = [k for k in rows[0] if not (drop_idx and k.endswith("_idx"))]
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    main()
