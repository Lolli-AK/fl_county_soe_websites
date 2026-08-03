#!/usr/bin/env python3
"""Apply the human QA pass to manifest/targets.csv.

Automated discovery gets most rows right, but it optimises for "a page whose text
matches this page type", which is not the same as "the best standing page of this
type". Every correction below was made by opening the site, and each records *why*
in `notes` so the judgement is auditable rather than an unexplained URL change.

Three kinds of correction:

  REPLACE — discovery picked a real page of the wrong kind. The classic failure was
    a news item or per-election announcement outscoring the standing page: Palm
    Beach's polling row pointed at a CivicAlerts "polling place change" bulletin,
    Highlands's at a stale 2024 candidate list, Bay's results row at a canvassing
    board *schedule*. Also here: Holmes-style confusions where vote-by-mail stood in
    for early voting.

  GAP — discovery picked something because it had to, but the county genuinely does
    not publish a distinct page of that type. Recording a gap is the honest answer;
    a wrong URL is worse than a documented absence, because it silently enters the
    diff series as if it were the real thing.

  CONFIRM — the row was flagged `likely` only because a keyword was missing (four
    SOE homepages never write the literal phrase "Supervisor of Elections" in body
    text; it is in their logo image). Verified by hand and recorded, so the flag
    does not get re-raised forever.

IMPORTANT: run this AFTER scripts/merge_targets.py — a merge rewrites those counties'
rows from the discovery draft and would drop these corrections. It is idempotent, so
re-running is safe.

Usage:
    python scripts/_apply_qa_corrections.py
    python scripts/_apply_qa_corrections.py --check   # report drift, write nothing
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGETS = ROOT / "manifest" / "targets.csv"

# (county, page_type) -> (url, note). An empty url records a gap.
REPLACE: dict[tuple[str, str], tuple[str, str]] = {
    ("Bay", "results"): (
        "https://www.bayvotesfl.gov/elections/election-results/",
        "corrected: discovery picked a canvassing-board schedule announcement; "
        "this is the standing election-results page"),
    ("Gulf", "elections"): (
        "https://votegulf.gov/elections/",
        "corrected: discovery picked a 'dates to remember' widget; this is the "
        "Elections section landing page"),
    ("Hamilton", "elections"): (
        "https://www.hamiltonvotesfl.gov/elections/",
        "corrected: discovery picked the voting-system page; this is the Elections "
        "section landing page"),
    ("Jackson", "early_voting"): (
        "https://votejacksonfl.gov/early-voting/",
        "corrected: discovery fell back to the election-information hub; this is the "
        "standing early-voting page"),
    ("Okaloosa", "elections"): (
        "https://www.voteokaloosa.gov/elections/",
        "corrected: discovery picked the 'vote on election day' page; this is the "
        "Elections section landing page"),
    ("Citrus", "polling"): (
        "https://www.votecitrus.gov/229/Precinct-Locations",
        "corrected: discovery fell back to a per-election hub; this is the standing "
        "precinct/polling locations page"),
    ("Highlands", "polling"): (
        "https://www.votehighlands.gov/where-do-i-vote",
        "corrected: discovery picked a stale 2024 candidate list; this is the "
        "standing polling-place page"),
    ("Hillsborough", "early_voting"): (
        "https://www.votehillsborough.gov/171/Early-Voting",
        "corrected: discovery fell back to the 2026 primary hub; this is the "
        "standing early-voting page"),
    ("Polk", "elections"): (
        "https://www.polkelections.gov/101/Election-Info",
        "corrected: discovery picked the voter-services page; this is the election "
        "information landing page"),
    ("Leon", "elections"): (
        "https://www.leonvotes.gov/Voting/Dates-Deadlines",
        "corrected: leonvotes.gov has no /Elections landing page; 'Dates & "
        "Deadlines' is its upcoming-elections page (found by targeted search)"),
    ("Palm Beach", "polling"): (
        "https://www.votepalmbeach.gov/168/Election-Day-Voting",
        "corrected: discovery picked a CivicAlerts polling-place-change bulletin; "
        "this is the standing election-day voting page (found via sitemap)"),
    ("St. Johns", "early_voting"): (
        "https://www.votesjc.gov/ways-to-vote",
        "corrected: discovery fell back to a 2026 news page; votesjc.gov has no "
        "dedicated early-voting URL, so early voting is covered on 'Ways to Vote'"),
    ("Miami-Dade", "polling"): (
        "https://www.votemiamidade.gov/elections/data/"
        "current-precincts-districts-municipalities.page",
        "corrected: discovery picked the 'reprecincting' explainer; this is the "
        "current precinct/polling data page"),
    # Fills a gap discovery left empty.
    ("Miami-Dade", "early_voting"): (
        "https://www.votemiamidade.gov/elections/voters/early-voting.page",
        "found by targeted path probe: not linked from the pages crawled, but this "
        "is the standing early-voting page"),
}

# Rows where the automated pick was wrong AND no correct page exists.
GAPS: dict[tuple[str, str], str] = {
    ("Baker", "elections"):
        "GAP: no distinct elections landing page — votebakerfl.gov publishes "
        "election info on the SOE homepage (checked nav, /elections/, "
        "/upcoming-elections/, /election-information/ all 404)",
    ("Lafayette", "elections"):
        "GAP: no distinct elections landing page — lafayettevotes.net has only "
        "per-topic pages (results, notices, offices up for election); confirmed "
        "against its sitemap",
    ("Holmes", "early_voting"):
        "GAP: no early-voting page published — discovery had substituted the "
        "vote-by-mail page, which is a different voting method; the site's "
        "Voter Information section has no early-voting entry (sitemap confirmed)",
    ("Wakulla", "early_voting"):
        "GAP: no standing early-voting page — discovery had substituted the 2026 "
        "candidate list; sitemap has no early-voting URL",
    ("Sumter", "results"):
        "GAP: no election-results page on the SOE site — only canvassing schedules; "
        "/202/Election-Results redirects off-site to a community development "
        "district, so it is not a valid target",
    ("Escambia", "elections"):
        "GAP: folded into SOE homepage — escambiavotes.gov's 'Elections' nav link "
        "points back at the homepage, and /elections/ serves the results page; "
        "capturing the homepage URL twice would double-count one page",
}

# Rows kept as-is, with the note clarified so a reviewer knows they were checked.
CLARIFY: dict[tuple[str, str], str] = {
    ("Santa Rosa", "results"):
        "verified: 'Historical Results' is the only results page votesantarosa.gov "
        "publishes (current-cycle returns appear here after certification)",
    ("Monroe", "polling"):
        "verified: 'Precinct Details & Demographics' is the standing polling page; "
        "there is no /Where-to-Vote index (404)",
    ("Hardee", "early_voting"):
        "verified: no standing early-voting page; the per-election hub is the only "
        "place early voting is published — refresh each cycle",
    # Correct picks that only scored weakly because the anchor text was terse.
    ("Columbia", "polling"):
        "verified: 'Precincts and Maps' is Columbia's standing polling page "
        "(scored weakly only because the link text is terse)",
    ("Highlands", "results"):
        "verified: 'Elections Results' is the standing results page",
    ("Osceola", "early_voting"):
        "verified: 'Vote Early' under How to Vote is the standing early-voting page",
}

# Homepages flagged `likely` purely because the literal phrase "Supervisor of
# Elections" appears only in the site's logo image, not in body text.
CONFIRM_HOMEPAGES = ["Duval", "Marion", "Pinellas", "Polk"]

FIELDS = ["county", "batch", "page_type", "url", "external", "notes",
          "verify_status", "http_status", "final_url", "audit_confidence",
          "audit_reason", "flag_for_review"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="report which corrections are not currently applied")
    args = ap.parse_args()

    rows = list(csv.DictReader(TARGETS.open(encoding="utf-8")))
    by_key = {(r["county"].strip(), r["page_type"].strip()): r for r in rows}

    missing = [k for k in list(REPLACE) + list(GAPS) + list(CLARIFY)
               if k not in by_key]
    if missing:
        raise SystemExit(f"manifest has no row for: {missing}")

    applied = 0
    for key, (url, note) in REPLACE.items():
        r = by_key[key]
        if r["url"].strip() != url or r["notes"].strip() != note:
            if args.check:
                print(f"REPLACE pending: {key[0]}/{key[1]}")
            else:
                r["url"], r["notes"] = url, note
                # A corrected URL invalidates the stored audit verdict.
                for f in ("verify_status", "http_status", "final_url",
                          "audit_confidence", "audit_reason", "flag_for_review"):
                    r[f] = ""
            applied += 1

    for key, note in GAPS.items():
        r = by_key[key]
        if r["url"].strip() or r["notes"].strip() != note:
            if args.check:
                print(f"GAP pending: {key[0]}/{key[1]}")
            else:
                r["url"], r["external"], r["notes"] = "", "false", note
                r["verify_status"], r["audit_confidence"] = "gap", "gap"
                for f in ("http_status", "final_url", "audit_reason",
                          "flag_for_review"):
                    r[f] = ""
            applied += 1

    for key, note in CLARIFY.items():
        r = by_key[key]
        if r["notes"].strip() != note:
            if args.check:
                print(f"CLARIFY pending: {key[0]}/{key[1]}")
            else:
                r["notes"] = note
            applied += 1

    for county in CONFIRM_HOMEPAGES:
        r = by_key.get((county, "homepage"))
        if r is None:
            raise SystemExit(f"no homepage row for {county}")
        note = (f"SOE homepage from the Florida DOS SOE directory; manually "
                f"confirmed as {county} County's Supervisor of Elections site "
                f"(flagged only because the phrase 'Supervisor of Elections' "
                f"appears in the site logo rather than in body text)")
        if r["notes"].strip() != note:
            if args.check:
                print(f"CONFIRM pending: {county}/homepage")
            else:
                r["notes"] = note
                r["flag_for_review"] = ""
            applied += 1

    if args.check:
        print(f"{applied} correction(s) not yet applied")
        return

    with TARGETS.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    urls = sum(1 for r in rows if r["url"].strip())
    print(f"applied {applied} correction(s) to {TARGETS}")
    print(f"  rows: {len(rows)}  with URLs: {urls}  gaps: {len(rows) - urls}")


if __name__ == "__main__":
    main()
