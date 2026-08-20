#!/usr/bin/env python3
"""Check whether each county states the key operational facts, and states them right.

Four facts a voter needs, checked per county across every captured page:

    poll hours              statutory in Florida, so any deviation is an error
    next election date      the general following the primary
    registration deadline   book closing, set centrally per election
    early voting window     bounded by statute; counties choose within the bounds

Two checks per fact:

  * **against the authoritative value** (below). This is what makes all 67 counties
    checkable. A pure county-vs-itself check is barely possible: a county can only
    contradict itself where it states the same fact on two or more pages, and on
    this corpus that is 7-20 counties depending on the fact.
  * **internally**, where a county does state a fact more than once.

Three outcomes per (county, fact): `matches`, `conflicts`, `not stated`. "Not stated"
is a finding in its own right, not missing data — a county that never tells you when
the polls close has a coverage gap regardless of consistency.

Every row carries the matched text, so no cell has to be taken on trust.

AUTHORITATIVE VALUES ARE A CONFIG, NOT A CLAIM. `EXPECTED` below is the one place
they live. Poll hours (7 a.m.-7 p.m.) are statutory and safe; the dates come from the
2026 calendar and the book-closing rule and should be **verified against the Florida
Division of Elections calendar before any published use**. Getting these wrong would
turn correct counties into false positives, so they are deliberately in one editable
block rather than scattered through the regexes.

Usage:
    python scripts/check_consistency.py
    python scripts/check_consistency.py --out manifest/fl-consistency.csv
"""

from __future__ import annotations

import argparse
import csv
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SNAPS = ROOT / "snapshots"
MANIFEST = ROOT / "manifest" / "targets.csv"
OUT = ROOT / "manifest" / "fl-consistency.csv"

# --------------------------------------------------------------------------- #
# The authoritative values. VERIFY THESE before publishing anything.
# --------------------------------------------------------------------------- #
EXPECTED = {
    # Florida statute sets polling hours statewide; a county stating anything else
    # is simply wrong, which makes this the cleanest of the four checks.
    "poll_hours": {"label": "7 a.m. - 7 p.m.", "authority": "Fla. Stat. 100.011"},
    # The general election following the 2026 primary.
    "election_date": {"label": "November 3, 2026",
                      "authority": "2026 general election date"},
    # Book closing is 29 days before an election.
    "registration_deadline": {"label": "October 5, 2026",
                              "authority": "book closing, 29 days before Nov 3"},
    # Counties choose within a statutory range, so a date inside the range is a
    # legitimate local choice rather than an error. Only dates OUTSIDE the range
    # are flagged.
    "early_voting": {"label": "between Oct 19 and Nov 1, 2026",
                     "authority": "statutory early-voting range"},
}

MONTHS = ("january|february|march|april|may|june|july|august|september|october|"
          "november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec")

# Any stated polling-hours range. Captures both ends so a wrong one is visible.
_HOURS_RE = re.compile(
    r"\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?\s*(?:to|until|-|–|—)\s*"
    r"(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?", re.I)

# A date with an explicit year.
_DATE_RE = re.compile(rf"\b({MONTHS})\.?\s+(\d{{1,2}}),?\s+(20\d\d)\b", re.I)

# Lines that are talking about registration closing.
_REG_CONTEXT = re.compile(r"book\s*clos|registration deadline|deadline to register|"
                          r"last day to register", re.I)
# Lines that are talking about early voting.
_EV_CONTEXT = re.compile(r"early voting", re.I)
# Lines that are talking about POLLS being open on election day. Without this the
# hours regex matches any time range on the page and reports the office's business
# hours ("8:30 a.m. - 5 p.m.") or an early-voting site's hours ("10 a.m. - 6 p.m.")
# as though they were statutory polling hours — which turned 64 of 67 counties into
# false positives on the first run.
_POLL_HOURS_CONTEXT = re.compile(
    r"polls?\s+(?:are\s+|will\s+be\s+)?(?:open|close)|"
    r"polling (?:place|location|site)s?[^.]{0,40}(?:open|hours)|"
    r"election day[^.]{0,60}(?:open|hours|7)", re.I)
# A bare "Hours: 10 AM - 6 PM" was also being accepted, which is how Calhoun's
# office hours became a statutory violation. Dropped: too loose to be worth its
# recall.
# Statewide 2026 election dates. Hours are only judged against the statute when the
# line is about one of these: Florida municipal and special elections can lawfully
# run different hours, and Manatee's "March 10, 2026 ... 12:00 pm - 8:00 pm" is a
# correct statement about a municipal election, not an error.
_STATEWIDE_DATES = re.compile(
    r"(august|aug\.?)\s*18,?\s*2026|(november|nov\.?)\s*3,?\s*2026", re.I)
_OTHER_DATE = re.compile(rf"\b({MONTHS})\.?\s+\d{{1,2}},?\s+20\d\d\b", re.I)
# ...but NOT when the line is about early voting, whose hours differ legitimately and
# are often described with the same word. Washington says "Polls open from 8am-5pm
# daily, Mon-Sat" on its EARLY VOTING page: correct information, and reporting it as
# a statutory violation was a false positive.
_EV_EXCLUDE = re.compile(r"early voting|early vote|vote early", re.I)
# "Polls open at 7 a.m. and close at 7 p.m." — two separate times rather than a
# range, so the range regex alone misses it.
_OPEN_CLOSE_RE = re.compile(
    r"open[^.]{0,24}?(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?[^.]{0,24}?"
    r"clos\w*[^.]{0,24}?(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?", re.I)

MONTH_NUM = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
     "dec"], start=1)}


def _month(tok: str) -> int:
    t = tok.lower()[:3]
    return MONTH_NUM.get("sep" if t == "sep" else t, 0)


def _norm_date(m: re.Match) -> tuple[int, int, int]:
    return (int(m.group(3)), _month(m.group(1)), int(m.group(2)))


FACTS = ["poll_hours", "election_date", "registration_deadline", "early_voting"]


def _fmt(s, sm, sap, e, em, eap) -> str:
    return (f"{int(s)}{':' + sm if sm and sm != '00' else ''} {sap.lower()}.m. - "
            f"{int(e)}{':' + em if em and em != '00' else ''} {eap.lower()}.m.")


def extract(text: str, page_type: str = "") -> dict[str, list[str]]:
    """Return, per fact, the distinct stated values found on this page."""
    out: dict[str, list[str]] = {f: [] for f in FACTS}

    # Hours only count when the line is about polls opening or closing AND is not
    # about early voting. The early_voting page is skipped wholesale for the same
    # reason: essentially every hours statement on it is an early-voting hour.
    if page_type != "early_voting":
        for line in text.splitlines():
            if _EV_EXCLUDE.search(line):
                continue
            if not _POLL_HOURS_CONTEXT.search(line):
                continue
            # If the line names a date, it must be a statewide one for the statutory
            # 7-7 rule to apply.
            if _OTHER_DATE.search(line) and not _STATEWIDE_DATES.search(line):
                continue
            for m in _HOURS_RE.finditer(line):
                val = _fmt(*m.groups())
                if val not in out["poll_hours"]:
                    out["poll_hours"].append(val)
            for m in _OPEN_CLOSE_RE.finditer(line):
                val = _fmt(*m.groups())
                if val not in out["poll_hours"]:
                    out["poll_hours"].append(val)

    # Dates, split by what the surrounding line is about. Line-scoped rather than
    # document-scoped: a page can carry an election date and a registration
    # deadline, and attributing both to the same fact would be nonsense.
    for line in text.splitlines():
        if not line.strip():
            continue
        dates = list(_DATE_RE.finditer(line))
        if not dates:
            continue
        reg, ev = bool(_REG_CONTEXT.search(line)), bool(_EV_CONTEXT.search(line))
        for m in dates:
            y, mo, d = _norm_date(m)
            val = f"{y:04d}-{mo:02d}-{d:02d}"
            key = ("registration_deadline" if reg
                   else "early_voting" if ev else "election_date")
            if val not in out[key]:
                out[key].append(val)
    return out


def judge(fact: str, values: list[str]) -> tuple[str, str]:
    """Return (verdict, detail) for one fact given every value a county stated."""
    if not values:
        return "not stated", ""

    if fact == "poll_hours":
        ok = [v for v in values if v.startswith("7 a.m. - 7 p.m.")]
        bad = [v for v in values if not v.startswith("7 a.m. - 7 p.m.")]
        if ok and not bad:
            return "matches", "; ".join(ok[:3])
        if ok and bad:
            # Both a correct and an incorrect statement present.
            return "conflicts", f"states {'; '.join(bad[:3])} as well as 7-7"
        return "conflicts", "; ".join(bad[:3])

    if fact == "election_date":
        # Post-primary, a county should be pointing at the general. Naming the
        # already-held primary is not an error — results pages legitimately do — so
        # failing to name the general is a COVERAGE gap, not a contradiction.
        # Reporting it as "conflicts" would overstate what the data shows.
        if "2026-11-03" in values:
            return "matches", "2026-11-03"
        return "next election not named", ("names " + ", ".join(values[:4])
                                           + " but not the Nov 3 general")

    if fact == "registration_deadline":
        if "2026-10-05" in values:
            return "matches", "2026-10-05"
        return "conflicts", "states " + ", ".join(values[:4])

    if fact == "early_voting":
        # A date inside the statutory range is a legitimate local choice.
        inside = [v for v in values if "2026-10-19" <= v <= "2026-11-01"]
        past = [v for v in values if v < "2026-09-01"]
        if inside:
            return "matches", "; ".join(inside[:4])
        if past and not inside:
            return "conflicts", ("only past-election dates: "
                                 + ", ".join(past[:4]))
        return "conflicts", "states " + ", ".join(values[:4])

    return "not stated", ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    counties = sorted({r["county"].strip()
                       for r in csv.DictReader(MANIFEST.open(encoding="utf-8"))})

    rows = []
    for county in counties:
        slug = county.lower().replace(" ", "_")
        # fact -> {value: [page types stating it]}
        seen: dict[str, dict[str, list[str]]] = {f: defaultdict(list) for f in FACTS}
        pages = 0
        for f in sorted((SNAPS / slug).glob("*/page.txt")) if (SNAPS / slug).exists() else []:
            pages += 1
            ptype = f.parent.name
            got = extract(f.read_text(encoding="utf-8", errors="ignore"), ptype)
            for fact, vals in got.items():
                for v in vals:
                    seen[fact][v].append(ptype)

        for fact in FACTS:
            values = list(seen[fact])
            verdict, detail = judge(fact, values)
            # Internal disagreement: two or more DIFFERENT values, each asserted by
            # at least one page. Only meaningful where the county is redundant.
            pages_stating = len({p for ps in seen[fact].values() for p in ps})
            # Not applied to election_date: a county naming both the primary it just
            # held and the general it is heading into is correct, not inconsistent,
            # so flagging it would make 40 of 67 look broken for no reason.
            internal = ("yes" if fact != "election_date" and len(values) > 1
                        and pages_stating >= 2 else "")
            rows.append({
                "county": county,
                "fact": fact,
                "verdict": verdict,
                "internally_inconsistent": internal,
                "expected": EXPECTED[fact]["label"],
                "authority": EXPECTED[fact]["authority"],
                "stated_values": " | ".join(values[:6]),
                "pages_stating": pages_stating,
                "detail": detail,
            })

    with Path(args.out).open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    print(f"wrote {args.out} ({len(rows)} county-fact rows)\n")
    print(f"{'fact':<24}{'matches':>9}{'conflicts':>11}{'not stated':>12}"
          f"{'internal':>10}")
    for fact in FACTS:
        sub = [r for r in rows if r["fact"] == fact]
        c = Counter(r["verdict"] for r in sub)
        ic = sum(1 for r in sub if r["internally_inconsistent"])
        print(f"{fact:<24}{c['matches']:>9}{c['conflicts']:>11}"
              f"{c['not stated']:>12}{ic:>10}")

    print("\ncounties with at least one conflict:",
          len({r["county"] for r in rows if r["verdict"] == "conflicts"}))
    print("counties stating all four facts   :",
          len([c for c in counties
               if all(r["verdict"] != "not stated"
                      for r in rows if r["county"] == c)]))
    bad_hours = [r for r in rows
                 if r["fact"] == "poll_hours" and r["verdict"] == "conflicts"]
    if bad_hours:
        print("\npoll-hours conflicts (statutory, so unambiguous):")
        for r in bad_hours[:12]:
            print(f"  {r['county']:<14}{r['detail'][:76]}")


if __name__ == "__main__":
    main()
