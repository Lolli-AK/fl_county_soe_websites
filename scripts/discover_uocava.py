#!/usr/bin/env python3
"""Discover each county's UOCAVA page and add it to the manifest.

    python scripts/discover_uocava.py                 # crawl -> draft CSV
    python scripts/discover_uocava.py --county Leon
    python scripts/discover_uocava.py --append        # draft -> targets.csv

A seventh page type, built on the same argument that added the sixth. It is a
separate script rather than a re-run of discover_pages.py so that a scoring
tweak made for UOCAVA cannot churn targets that have been stable and
hand-audited for months. This crawls for one type and appends; nothing
already in targets.csv is touched.

Why the type was added. The UOCAVA flag (uocava.py) reads false for Harris and
Travis -- the two largest counties in Texas -- and for Palm Beach and
Pinellas. Those pages were captured cleanly at HTTP 200; the counties simply
do not put military-and-overseas material on their homepage, elections
landing page, polling, early-voting or results pages. It lives on a dedicated
page that nothing in the manifest points at. Without this type, "no UOCAVA
material" means "not on the five pages we happen to capture", which is not the
question anyone is asking.

The content test IS the measurement. A candidate qualifies when
uocava.scan() fires on its non-anchor prose -- the same function, with the
same term list, that stamps the flag into meta.json. Discovery and
measurement therefore cannot drift apart: a page this script accepts is by
construction a page the flag reads true on, and widening one widens the other.
That is the opposite of how the registration crawl works, where the discovery
language (_REG_LANGUAGE) and the flag (noncitizen.py) are deliberately
different vocabularies, because there the flag is a tripwire that is expected
to read false on the very pages the crawl is looking for.

A GAP is a finding -- the county publishes nothing of its own for these
voters and leaves them to the state or to FVAP -- and not a discovery failure
to paper over.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bs4 import BeautifulSoup  # noqa: E402

import uocava  # noqa: E402
from discover_pages import (  # noqa: E402
    REG_HUB_PATTERNS, _plausible_target, fetch, is_external, is_generic_portal,
    links_of, rank_links, stabilize_url,
)

ROOT = Path(__file__).resolve().parent.parent
TARGETS = ROOT / "manifest" / "targets.csv"
DRAFT = ROOT / "manifest" / "uocava_draft.csv"
PTYPE = "uocava"

FIELDS = ["county", "batch", "page_type", "url", "external", "notes",
          "verify_status", "http_status", "final_url", "audit_confidence",
          "audit_reason", "flag_for_review"]

log = logging.getLogger("discover_uocava")

# Link scoring for this type, kept local rather than added to
# discover_pages.PATTERNS. Nothing else imports it, and leaving that table
# untouched guarantees this cannot perturb the five hand-audited types --
# discover_pages.score() also branches on `pats is PATTERNS["elections"]`,
# which an added key must not disturb.
UOCAVA_PATTERNS: list[tuple[str, int]] = [
    ("uocava", 16), ("military and overseas", 15), ("military & overseas", 15),
    ("military/overseas", 15), ("overseas voter", 14), ("military voter", 14),
    ("federal post card application", 14), ("federal postcard application", 14),
    ("fpca", 13), ("federal write-in absentee ballot", 13), ("fwab", 12),
    ("federal voting assistance", 12), ("fvap", 11),
    ("overseas citizens", 11), ("uniformed services", 11),
    ("absent military", 12), ("voting from overseas", 13),
    ("armed forces", 8), ("military", 5), ("overseas", 5),
    # The three false-positive families the flag itself excludes. They are
    # re-stated here as negative weights because scoring happens on ANCHOR
    # TEXT and URLs, which uocava.scan() never sees -- the prose test below
    # would reject these pages, but only after paying for a fetch, and on a
    # 254-county sweep the cheap rejection is the one worth having.
    ("military discharge", -20), ("dd-214", -20), ("dd 214", -20),
    ("discharge", -12), ("military id", -18),
    ("identification card", -12), ("overseas highway", -20),
    # Siblings that stack the same words without being this page.
    ("veteran", -10), ("veterans services", -14), ("armed forces day", -12),
    ("poll worker", -12), ("candidate", -10), ("result", -10),
    ("job", -10), ("employment", -10),
]

# Below this, the page is kept but flagged for review rather than trusted.
# Set at the weight of a single unambiguous term ("fpca", "overseas voter")
# so that one specific word is enough and two generic ones are not:
# "military" + "overseas" alone sums to 10 and stays under.
MIN_STRONG = 13

# Hub links worth opening at the second level. UOCAVA material almost never
# hangs off the homepage; it sits under a vote-by-mail or absentee section,
# because the ballot these voters get IS a mail ballot.
UOCAVA_HUB_PATTERNS = REG_HUB_PATTERNS + [
    ("vote by mail", 14), ("vote-by-mail", 14), ("absentee", 14),
    ("mail ballot", 13), ("ballot by mail", 13), ("military", 12),
    ("overseas", 12), ("uocava", 16), ("voter information", 8),
]


def _prose(html: str) -> str:
    """Visible text with every link removed.

    Dropping anchors is the point, not a simplification. A county CMS that
    puts "Military & Overseas Voters" in the sidebar of every page makes the
    check-register page match exactly as strongly as the real one. Only
    non-anchor text distinguishes them.
    """
    soup = BeautifulSoup(html or "", "lxml")
    for tag in soup(["script", "style", "noscript", "a", "nav", "header",
                     "footer"]):
        tag.decompose()
    return " ".join(soup.get_text(separator=" ").split())


def _page_prose(url: str, res: dict) -> str:
    """Non-anchor text, rendering once through Chromium if the plain fetch is thin.

    A CMS that builds its body client-side yields nav links and nothing else
    to httpx, and judging a county on that would invent gaps for counties that
    do publish a page.
    """
    prose = _prose(res["html"])
    if len(prose) >= 400:
        return prose
    rendered = fetch(url, require_links=True)
    if rendered["ok"]:
        deeper = _prose(rendered["html"])
        if len(deeper) > len(prose):
            return deeper
    return prose


# Per-election and per-event URL shapes, extending the shared guard with the
# news-item forms it does not cover.
#
# discover_pages.score() already docks 12 for the shapes it knows, and for the
# other six page types that is the right strength -- the comment there says to
# prefer the durable page and take the volatile one only if nothing else
# exists. For THIS type the penalty is not enough and the fallback is wrong.
# Not enough, because an announcement is written in denser UOCAVA vocabulary
# than a standing page is: Lake County's calendar entry "2026 General
# Election -- Vote-by-mail mailouts continue to military and overseas voters"
# scored 27 AFTER the deduction and beat everything else on the site. Wrong,
# because there is no sense in which a dated notice is a county's UOCAVA
# information page. It goes stale within the cycle, and when it 404s mid-series
# the diff reads as the county having removed its material for these voters --
# manufacturing exactly the event this repo exists to detect.
# Spelled out in full rather than extended from discover_pages, which carries
# an equivalent guard in the Florida repo and none at all in the Texas one.
# Importing it would fork these two files over a constant that is not the
# point; this way both copies stay identical and neither depends on which
# guards its sibling happens to have.
_EPHEMERAL = re.compile(
    r"calendar\.aspx|[?&](?:eid|aid|nid|iid)=|/calendar/?$|/event/|/events/\d"
    r"|/newsflash|/news[/_-]?(?:flash|detail|item)|/detail/\d+", re.I)


# A URL path that names these voters explicitly. Used only for pages with too
# little prose to judge -- a Quicklinks page whose entire body is anchors.
_UOCAVA_IN_PATH = re.compile(
    r"uocava|fpca|fwab"
    r"|military[._\-/]?(?:and[._\-/]?)?overseas"
    r"|overseas[._\-/]?(?:and[._\-/]?)?military"
    r"|(?:military|overseas)[._\-/]?vot", re.I)


def _named_by_its_url(url: str, home: str) -> bool:
    """Is the county's own URL evidence enough on its own?

    Deliberately narrower than the registration equivalent: it will not accept
    a bare "military" or "overseas" in a path, because /military is as likely
    to be a veterans-services page and /overseas-hwy is an address.
    """
    return bool(not is_external(url, home) and _UOCAVA_IN_PATH.search(url))


def _names_county(prose: str, county: str) -> bool:
    """Does the page say it belongs to THIS county?

    The last line of defence for an off-site host. It has to be the page's
    words rather than its domain, because the elections portals these links
    land on (harrisvotes.com, votedenton.gov) name neither the county nor its
    seat in the hostname.
    """
    esc = re.escape(county.lower())
    hay = prose.lower()
    return bool(re.search(rf"\b{esc}\b[ \t]*'?s?[ \t]+county\b", hay)
                or re.search(rf"\bcounty\s+of\s+{esc}\b", hay))


def anchors() -> dict[str, dict]:
    """Per county: batch, homepage, and the verified elections landing page."""
    out: dict[str, dict] = {}
    for r in csv.DictReader(TARGETS.open(encoding="utf-8")):
        c = out.setdefault(r["county"], {"batch": r["batch"], "home": "", "elections": ""})
        if r["page_type"] == "homepage" and r["url"]:
            c["home"] = r["url"]
        elif r["page_type"] == "elections" and r["url"]:
            c["elections"] = r["url"]
    return out


def discover_one(county: str, a: dict) -> dict:
    def row(url: str, note: str, status: str = "", http: str = "",
            final: str = "", flag: str = "") -> dict:
        return {"county": county, "batch": a["batch"], "page_type": PTYPE,
                "url": url,
                "external": str(is_external(url, a["home"])).lower() if url else "false",
                "notes": note, "verify_status": status or ("ok" if url else "gap"),
                "http_status": http, "final_url": final,
                "audit_confidence": "", "audit_reason": "", "flag_for_review": flag}

    if not a["home"]:
        return row("", "GAP: county has no verified homepage to crawl from")

    home_res = fetch(a["home"])
    if not home_res["ok"] or (home_res["status"] or 0) >= 400:
        why = home_res["error"] or f"HTTP {home_res['status']}"
        return row("", f"GAP: could not crawl — homepage blocked/unreachable ({why})")

    exclude = {a["home"].rstrip("/"), home_res["final_url"].rstrip("/")}
    links = links_of(home_res["html"], home_res["final_url"])

    # The elections landing page carries this link far more often than the
    # homepage does, so its links rank ahead of the homepage's.
    if a["elections"]:
        exclude.add(a["elections"].rstrip("/"))
        er = fetch(a["elections"])
        if er["ok"]:
            links = links_of(er["html"], er["final_url"]) + links

    # Second level. UOCAVA sits one level deeper than registration does: it is
    # normally a subsection of vote-by-mail rather than a top-level item, so
    # three hubs are opened where the registration crawl opens two.
    for _s, hub, _t in rank_links(links, UOCAVA_HUB_PATTERNS, exclude,
                                  a["home"], prefer_internal=True)[:3]:
        hr = fetch(hub)
        if hr["ok"] and "html" in hr["ctype"].lower():
            links = links_of(hr["html"], hr["final_url"]) + links

    ranked = rank_links(links, UOCAVA_PATTERNS, exclude, a["home"],
                        prefer_internal=True)[:8]
    if not ranked:
        return row("", "GAP: no distinct UOCAVA page found")

    tried: list[str] = []
    for score, url, text in ranked:
        # Verification only: this needs to exist and be HTML, not to be
        # crawlable. A UOCAVA page is often a single block of instructions
        # with almost no links, which must not be mistaken for a JS shell.
        r = fetch(url, require_links=False)
        tail = url.rstrip("/").split("/")[-1][:30] or url
        if not r["ok"] or (r["status"] or 0) >= 400:
            tried.append(f"{tail} -> {r['status'] or r['error']}")
            continue
        if "html" not in r["ctype"].lower():
            tried.append(f"{tail} -> {r['ctype'][:20]}")
            continue
        if _EPHEMERAL.search(url):
            tried.append(f"{tail} -> dated notice, not a standing page")
            continue
        if is_generic_portal(r["final_url"]):
            # fvap.gov and the state portals are where a county WITHOUT its own
            # page sends these voters. Recording that as the county's page
            # would erase exactly the distinction this type exists to measure.
            tried.append(f"{tail} -> statewide/federal portal")
            continue
        final = stabilize_url(r["final_url"]).split("#")[0]
        # A link that lands back on a page already snapshotted under another
        # type is a finding, not a target: capturing one URL twice would make
        # two page types diff identically forever.
        for other, ourl in (("homepage", a["home"]), ("elections", a["elections"])):
            if ourl and final.rstrip("/") == ourl.rstrip("/"):
                return row("", f"GAP: folded into the {other} page")
        prose = _page_prose(final, r)
        if (not _plausible_target(final, county, a["home"], a["elections"])
                and not _names_county(prose, county)):
            tried.append(f"{tail} -> another county's site")
            continue
        # The measurement is the content test. See the module docstring.
        hit = uocava.scan(prose)
        if not hit["present"] and not (
                len(prose) < 400 and _named_by_its_url(final, a["home"])):
            tried.append(f"{tail} -> no UOCAVA content in prose")
            continue
        weak = score < MIN_STRONG
        note = (f'found via "{text[:40]}" score={score}'
                + (f" terms={'+'.join(hit['terms'])}" if hit["terms"] else "")
                + (" (weak match — review)" if weak else "")
                + (f" [after {len(tried)} rejected]" if tried else ""))
        return row(final, note, "ok", str(r["status"]), r["final_url"],
                   "weak-score" if weak else "")

    detail = "; ".join(tried[:3])
    return row("", f"GAP: no reachable HTML UOCAVA page ({detail})")


def append_to_targets() -> None:
    """Merge the draft into targets.csv, replacing any prior rows for this type.

    REQUIRES A MATCHING TEST EDIT. tests/test_manifest.py hard-codes
    PAGE_TYPES and asserts that every county carries exactly those types and
    that len(targets) == county_count * len(PAGE_TYPES). Appending this type
    without adding "uocava" to that list fails three tests immediately; adding
    it to the list before appending fails the same three. Do both in one
    commit, in this order: run discovery, --append, then edit PAGE_TYPES.
    """
    if not DRAFT.exists():
        raise SystemExit(f"missing {DRAFT} — run discovery first")
    draft = {r["county"]: r for r in csv.DictReader(DRAFT.open(encoding="utf-8"))}
    existing = list(csv.DictReader(TARGETS.open(encoding="utf-8")))

    kept = [r for r in existing if r["page_type"] != PTYPE]
    order = {c: i for i, c in enumerate(dict.fromkeys(r["county"] for r in kept))}
    merged = kept + [draft[c] for c in sorted(draft, key=lambda c: order.get(c, 1 << 30))]
    # `uocava` sorts last so that adding it cannot reorder the six types that
    # existing snapshots, reports and diffs are already keyed on.
    seq = ["homepage", "elections", "voter_registration", "polling",
           "early_voting", "results", "uocava"]
    merged.sort(key=lambda r: (order.get(r["county"], 1 << 30),
                               seq.index(r["page_type"])))

    with TARGETS.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(merged)
    got = sum(1 for r in merged if r["page_type"] == PTYPE and r["url"])
    print(f"targets.csv: {len(merged)} rows, {PTYPE} {got}/{len(draft)} with a URL")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--county", action="append", default=None)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--append", action="store_true",
                    help="merge the existing draft into targets.csv and exit")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s",
                        handlers=[logging.StreamHandler(sys.stdout)])

    if args.append:
        append_to_targets()
        return

    todo = anchors()
    if args.county:
        want = {c.lower() for c in args.county}
        todo = {c: a for c, a in todo.items() if c.lower() in want}

    # Results are written as they land, not collected and written at the end,
    # and completion order is used rather than pool.map's input order. Both for
    # the same reason: one county that never returns should cost that county,
    # not the run.
    rows: list[dict] = []
    DRAFT.parent.mkdir(parents=True, exist_ok=True)
    with DRAFT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        with cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(discover_one, c, a): c
                       for c, a in sorted(todo.items())}
            for fut in cf.as_completed(futures):
                county = futures[fut]
                try:
                    r = fut.result()
                except Exception as exc:  # noqa: BLE001
                    log.warning("%-14s crawl raised %s", county, type(exc).__name__)
                    continue
                rows.append(r)
                w.writerow(r)
                fh.flush()
                log.info("%-14s %s", r["county"], r["url"] or r["notes"][:70])

    # Rewrite in county order now that everything is in; the streamed file was
    # in completion order, which is not a stable thing to diff.
    rows.sort(key=lambda r: r["county"])
    with DRAFT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    got = sum(1 for r in rows if r["url"])
    print(f"\n{got}/{len(rows)} counties have a UOCAVA page -> {DRAFT}")


if __name__ == "__main__":
    main()
