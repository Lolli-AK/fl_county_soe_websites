#!/usr/bin/env python3
"""Phase 1 — verify (and where needed, discover) each county's SUPERVISOR OF
ELECTIONS homepage.

This is the Florida counterpart of tx-county-watch's script of the same name, and
the job is inverted. Texas had 230 unknown homepages and had to *guess* them from
domain patterns. Florida's Department of State publishes an authoritative directory
of all 67 SOE offices with their websites, so `manifest/counties.csv` already carries
a homepage for every county. What remains is to **prove each one is right**:

  1. Probe the seeded URL from counties.csv first.
  2. VERIFY the content is really that county's Supervisor of Elections site:
     "<county> county" present AND (Florida signal OR county seat / SOE office city)
     AND Supervisor-of-Elections vocabulary, with parked / error / wrong-state /
     wrong-county / commercial rejection.
  3. Only if the seeded URL fails, fall back to probing Florida SOE domain patterns
     (vote<county>.gov, <county>votes.gov, vote<county>fl.gov, ...).

Step 3 is not decoration. A state directory is a periodic export, not a liveness
check: SOE offices have been steadily migrating .com/.net/.org sites to .gov, and a
directory row can point at a domain that has already lapsed. Treating the state file
as a lead to verify — rather than as truth — is the entire QA premise here.

Outputs:
    manifest/batch1_homepages.csv     county, seat, homepage, confidence, evidence,
                                      flag_for_review, source
    logs/batch1-homepage-probes.json  full probe record (every candidate tried)

Usage:
    python scripts/discover_homepages.py
    python scripts/discover_homepages.py --county Alachua --county Leon
    python scripts/discover_homepages.py --workers 10
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import json
import logging
import re
import socket
import sys
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
OUT_CSV = ROOT / "manifest" / "batch1_homepages.csv"
OUT_JSON = ROOT / "logs" / "batch1-homepage-probes.json"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
# A realistic, COMPLETE browser header set — not just a User-Agent. Bot protection
# fingerprints the whole request; see the README for the measured effect. The `br`
# encoding requires the `brotli` package (see requirements.txt), otherwise
# advertising it yields undecodable bodies.
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
               "image/avif,image/webp,*/*;q=0.8"),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Sec-Ch-Ua": '"Chromium";v="125", "Not.A/Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"macOS"',
    "Cache-Control": "max-age=0",
    "Connection": "keep-alive",
}
TIMEOUT = 15.0

# County/seat/homepage data is NOT hardcoded here — it lives in manifest/counties.csv
# and is read via load_seed().

# Domains that are never a county's official SOE site.
BAD_HOST_SUBSTRINGS = ("facebook", "twitter", "linkedin", "yelp", "city-data",
                       "countyoffice.org", "usa.com", "areaconnect", "zillow",
                       "realtor", "netronline", "publicrecords", "wikipedia",
                       "ballotpedia", "vote411", "rockthevote", "instagram",
                       "youtube", "nextdoor")

# Signals that a page is an actual Supervisor of Elections site (or, failing that, a
# county government site) rather than a commercial page that mentions the county.
# Florida's vocabulary is election-specific in a way Texas's county homepages were
# not, because the SOE office is a single-purpose agency.
GOV_SIGNALS = ("supervisor of elections", "vote by mail", "vote-by-mail",
               "voter registration", "early voting", "sample ballot",
               "polling place", "polling location", "precinct", "poll worker",
               "canvassing board", "elections office", "voter information lookup",
               "register to vote", "candidate qualifying", "election results",
               "provisional ballot", "county commission",
               "board of county commissioners", "courthouse", "county clerk",
               "tax collector", "property appraiser", "sheriff")

# Commercial/vendor tells that disqualify a page outright.
COMMERCIAL_SIGNALS = ("process server", "bail bond", "personal injury",
                      "attorney advertising", "real estate listings",
                      "for sale by owner", "insurance quotes", "add your business",
                      "advertise with us", "sponsored listings", "casino")

# Adjacent-but-not-government organisations that legitimately carry a county's name
# and rank well. Weighted against GOV_SIGNALS so a real SOE site that links to its
# county's tourism page isn't rejected.
NON_GOV_ORG_SIGNALS = ("visitor center", "visitors bureau", "things to do",
                       "where to stay", "places to eat", "itineraries",
                       "economic development council",
                       "economic development corporation",
                       "chamber of commerce", "convention and visitors",
                       "historical society", "genealogical society",
                       "plan your visit", "tourism", "vacation rentals")

# Non-government organisations — and OTHER county constitutional officers — that name
# themselves in the page TITLE. Florida counties elect five separate constitutional
# officers whose sites sit on near-identical domains and carry heavy county-government
# vocabulary, so "Clerk of Court" in the title is as disqualifying as "Chamber of
# Commerce": both are real county pages, neither is the Supervisor of Elections.
NON_GOV_ORG_TITLE_MARKERS = ("economic development", "chamber of commerce",
                             "visitors bureau", "visitor center", "tourism",
                             "convention and visitors", "historical society",
                             "genealogical society", "property appraiser",
                             "clerk of court", "clerk of the circuit court",
                             "tax collector")

PARKED_SIGNALS = ("domain is for sale", "buy this domain", "parked",
                  "this domain may be for sale", "godaddy", "sedo",
                  "account suspended", "coming soon", "under construction",
                  "default web site page", "index of /")
ERROR_SIGNALS = ("page not found", "404 not found", "403 forbidden",
                 "access denied", "not be found", "site can't be reached")

# States whose counties collide with Florida's. Florida shares county names far more
# widely than Texas does — Nassau (NY), Duval (TX), Monroe (NY/MI/PA), Jackson
# (MO/MS/OR), Marion (IN/OR), Polk (IA/OR), Union (NJ), Washington (OR/PA), Franklin
# (OH), Orange (CA/NY), Lee (AL/VA), Madison (AL/IL), Jefferson (KY/CO/AL) — so a
# page that says "<county> County" but never says Florida is a real hazard, not a
# hypothetical one.
WRONG_STATE_MARKERS = (
    "new york", "texas", "michigan", "pennsylvania", "missouri", "mississippi",
    "oregon", "indiana", "iowa", "new jersey", "ohio", "california", "alabama",
    "illinois", "kentucky", "colorado", "georgia", "tennessee", "arkansas",
    "north carolina", "south carolina", "virginia", "west virginia", "kansas",
    "nebraska", "minnesota", "wisconsin",
)

log = logging.getLogger("discover_homepages")

SEED = ROOT / "manifest" / "counties.csv"


def load_seed(batch: str | None = None) -> list[dict[str, str]]:
    """Seed rows from manifest/counties.csv — the single source of truth.

    Nothing in this module hardcodes a county list; adding or fixing a county is a
    manifest edit, not a code change.
    """
    with SEED.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    return [{k: (v or "").strip() for k, v in r.items()} for r in rows
            if batch is None or (r["batch"] or "").strip() == str(batch)]


_COUNTY_NAMES_CACHE: set[str] | None = None


def _other_county_names(county: str) -> set[str]:
    """Every Florida county name except this one, for cross-identification checks."""
    global _COUNTY_NAMES_CACHE
    if _COUNTY_NAMES_CACHE is None:
        with SEED.open(newline="", encoding="utf-8") as fh:
            _COUNTY_NAMES_CACHE = {r["county"].strip().lower()
                                   for r in csv.DictReader(fh)}
    return _COUNTY_NAMES_CACHE - {county.strip().lower()}


def slugs(county: str) -> tuple[str, str]:
    """('miamidade', 'miami-dade') for 'Miami-Dade'; ('stjohns', 'st-johns') for
    'St. Johns'."""
    low = county.lower()
    squashed = re.sub(r"[^a-z0-9]", "", low)
    hyphen = re.sub(r"[^a-z0-9]+", "-", low).strip("-")
    return squashed, hyphen


def candidates(county: str, seeded: str = "") -> list[str]:
    """Seeded state-directory URL first, then Florida SOE domain patterns.

    The patterns are ordered by how common they actually are across the 67 offices:
    `vote<county>.gov` and `<county>votes.gov` dominate, and nearly every office has
    moved to (or is moving to) .gov.
    """
    s, h = slugs(county)
    urls = [
        f"https://www.vote{s}.gov/",
        f"https://vote{s}.gov/",
        f"https://www.{s}votes.gov/",
        f"https://{s}votes.gov/",
        f"https://www.vote{s}fl.gov/",
        f"https://vote{s}fl.gov/",
        f"https://www.{s}votesfl.gov/",
        f"https://www.{s}elections.gov/",
        f"https://{s}elections.gov/",
        f"https://www.{s}elections.com/",
        f"https://www.{s}votes.com/",
        f"https://www.vote{s}.com/",
        f"https://www.{s}soefl.gov/",
        f"https://elections.{s}countyfl.gov/",
        f"https://soe.{s}-fl.gov/",
    ]
    if h != s:  # hyphenated hosts, e.g. miami-dade / st-johns
        urls.insert(2, f"https://www.vote{h}.gov/")
        urls.insert(3, f"https://{h}votes.gov/")
    if seeded:
        # The state directory's URL is the strongest lead; try it before any pattern.
        urls.insert(0, seeded)
    # De-duplicate while preserving priority order.
    seen: set[str] = set()
    out = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def host_resolves(url: str) -> bool:
    host = httpx.URL(url).host
    if any(b in host for b in BAD_HOST_SUBSTRINGS):
        return False
    try:
        socket.getaddrinfo(host, None)
        return True
    except socket.gaierror:
        return False


def visible_text(html: str) -> tuple[str, str | None]:
    soup = BeautifulSoup(html or "", "lxml")
    for t in soup.find_all(["script", "style", "noscript", "svg"]):
        t.decompose()
    title = None
    if soup.title and soup.title.string:
        title = " ".join(soup.title.string.split())
    text = " ".join(soup.get_text(separator=" ").split())
    return text, title


def county_phrase(county: str) -> str:
    """Regex fragment matching how a county names itself, tolerating punctuation.

    'St. Johns' is written 'St. Johns', 'St Johns' and 'Saint Johns'; 'Miami-Dade'
    appears as both 'Miami-Dade' and 'Miami Dade'. Matching the literal seed string
    alone would fail to identify the county's own site.
    """
    low = county.lower()
    low = re.sub(r"^st\.?\s+", "SAINT", low)
    frag = re.escape(low)
    frag = frag.replace("SAINT", r"(?:st\.?|saint)\s+")
    frag = frag.replace(r"\-", "[- ]")
    return frag


def verify(county: str, seat: str, office_city: str, url: str, text: str,
           title: str | None) -> tuple[str, str]:
    """Return (confidence, evidence). confidence: confident|likely|reject.

    Identity is established by the literal phrase "<county> county", NOT by the
    county name and the word "county" appearing separately, and the page must also
    place itself in Florida (or in the county seat / SOE office city). Florida shares
    county names with other states more than Texas does — Nassau, Duval, Monroe,
    Jackson, Marion, Polk, Union, Washington, Franklin, Orange, Lee, Madison and
    Jefferson all exist elsewhere — so the state check is doing real work here.
    """
    hay = f"{title or ''} {text}".lower()
    seat_l = seat.lower()
    office_l = (office_city or "").lower()
    host = httpx.URL(url).host

    # Parked/placeholder detection needs corroboration: real SOE sites say "Coming
    # soon" about an upcoming election page. A genuinely parked domain has the phrase
    # in its TITLE, or almost no content and no election vocabulary.
    parked_hits = [sig for sig in PARKED_SIGNALS if sig in hay[:4000]]
    if parked_hits:
        gov_early = [g for g in GOV_SIGNALS if g in hay]
        in_title = any(sig in (title or "").lower() for sig in PARKED_SIGNALS)
        if in_title or (len(text.strip()) < 800 and not gov_early):
            return "reject", f"parked/placeholder domain ({parked_hits[0]})"
    if any(sig in hay[:2000] for sig in ERROR_SIGNALS):
        return "reject", "error page"
    if len(text.strip()) < 120:
        return "reject", f"near-empty page ({len(text.strip())} chars)"

    esc = county_phrase(county)
    has_county_name = bool(re.search(rf"\b{esc}\b\s*'?s?\s+county\b", hay)) or \
        bool(re.search(rf"\bcounty\s+of\s+{esc}\b", hay))
    has_word_county = "county" in hay
    has_state = ("florida" in hay) or bool(re.search(r"\bfl\b", hay))
    has_seat = bool(re.search(rf"\b{re.escape(seat_l)}\b", hay))
    has_office_city = bool(office_l and re.search(rf"\b{re.escape(office_l)}\b", hay))

    # Does the TITLE identify a DIFFERENT county? Same-line matching only
    # ([ \t]+ not \s+): across newlines a nav list would join unrelated words into a
    # phantom county name.
    title_l = (title or "").lower()
    others_in_title = [
        o for o in _other_county_names(county)
        if o not in county.lower()                  # not a fragment of our own name
        and re.search(rf"\b{re.escape(o)}\b[ \t]+county\b", title_l)
    ]

    # .gov cannot be registered commercially, so the TLD is itself strong evidence.
    official_tld = host.endswith(".gov") or host.endswith(".fl.us")

    gov_hits = [g for g in GOV_SIGNALS if g in hay]
    soe_hit = "supervisor of elections" in hay
    commercial_hits = [c for c in COMMERCIAL_SIGNALS if c in hay]

    ev = []
    if has_county_name:
        ev.append(f"'{county} County' in page")
    if soe_hit:
        ev.append("'Supervisor of Elections' in page")
    if has_seat:
        ev.append(f"seat '{seat}' in page")
    if has_office_city and office_l != seat_l:
        ev.append(f"SOE office city '{office_city}' in page")
    if has_state:
        ev.append("Florida/FL present")
    if official_tld:
        ev.append(f"official TLD (.{host.rsplit('.', 1)[-1]})")
    if gov_hits:
        ev.append(f"election signals: {', '.join(gov_hits[:3])}")
    evidence = "; ".join(ev) if ev else "no signals"

    # Commercial page that merely mentions the county. Only reject when the
    # government signals are ALSO weak — a real SOE site can carry "advertise with
    # us" in a vendor footer.
    if commercial_hits and len(gov_hits) < 2:
        return "reject", f"commercial site ({commercial_hits[0]}) — {evidence}"
    # Tourism bureau / EDC / chamber, or a DIFFERENT county constitutional officer.
    # Naming itself in the TITLE is disqualifying on its own; otherwise require a
    # cluster of the vocabulary.
    nongov_hits = [n for n in NON_GOV_ORG_SIGNALS if n in hay]
    title_org = [n for n in NON_GOV_ORG_TITLE_MARKERS if n in title_l]
    # ...unless the title ALSO says Supervisor of Elections: several SOE sites carry a
    # shared county masthead listing every constitutional officer.
    if title_org and "supervisor of elections" in title_l:
        title_org = []
    if title_org or (len(nongov_hits) >= 3 and len(nongov_hits) > len(gov_hits)):
        why = (title_org or nongov_hits)[:2]
        return "reject", (f"not the Supervisor of Elections — looks like another "
                          f"office or a tourism/EDC site ({', '.join(why)}) "
                          f"— {evidence}")
    if not (has_county_name and has_word_county):
        return "reject", f"page does not identify as '{county} County' — {evidence}"
    if others_in_title:
        return "reject", (f"title identifies another county "
                          f"({', '.join(sorted(others_in_title)[:2])}) — {evidence}")
    # Must be locatable in Florida. Guards the Nassau NY / Duval TX / Monroe MI class
    # of collision, which is the single most likely wrong answer for Florida.
    if not (has_state or has_seat or has_office_city):
        return "reject", (f"no Florida/seat signal (possible wrong-state county) "
                          f"— {evidence}")
    # Explicit wrong-state tell: another state named in the TITLE and Florida absent.
    wrong_state = [w for w in WRONG_STATE_MARKERS if w in title_l]
    if wrong_state and "florida" not in title_l and not (has_seat or has_office_city):
        return "reject", f"title places this in {wrong_state[0]} — {evidence}"

    # Confidence. A .gov TLD plus the SOE phrase is conclusive. Otherwise require a
    # location signal plus real election vocabulary; with neither, keep the county but
    # flag it rather than silently dropping a real site.
    if official_tld and soe_hit:
        return "confident", evidence
    if soe_hit and (has_seat or has_office_city or has_state) and len(gov_hits) >= 3:
        return "confident", evidence
    if official_tld or len(gov_hits) >= 2:
        return "likely", evidence
    if gov_hits:
        return "likely", f"thin election vocabulary — verify manually; {evidence}"
    return "likely", f"no election vocabulary on page — verify manually; {evidence}"


def probe(url: str) -> dict:
    """Fetch a candidate, trying HTTP/2 then HTTP/1.1.

    Neither version works everywhere and you cannot tell which from the URL, so both
    are tried — this is what took tx-county-watch from 15 broken pages to 0.
    """
    last = None
    for http2 in (True, False):
        try:
            with httpx.Client(headers=HEADERS, follow_redirects=True,
                              timeout=TIMEOUT, verify=True, http2=http2) as c:
                r = c.get(url)
            text, title = visible_text(r.text)
            last = {"url": url, "ok": True, "status": r.status_code,
                    "final_url": str(r.url), "title": title, "text": text,
                    "error": None}
            if r.status_code < 400:
                return last
        except Exception as exc:  # noqa: BLE001
            last = {"url": url, "ok": False, "status": None, "final_url": url,
                    "title": None, "text": "", "error": f"{type(exc).__name__}"}
    return last


def resolve_county(row: dict[str, str]) -> dict:
    county, seat = row["county"], row["seat"]
    office_city, seeded = row.get("office_city", ""), row.get("homepage", "")
    attempts: list[dict] = []
    best = None
    for i, url in enumerate(candidates(county, seeded)):
        from_seed = bool(seeded) and i == 0
        if not host_resolves(url):
            attempts.append({"url": url, "result": "dns_fail", "seeded": from_seed})
            continue
        p = probe(url)
        if not p["ok"]:
            attempts.append({"url": url, "result": f"fetch_fail:{p['error']}",
                             "seeded": from_seed})
            continue
        if p["status"] >= 400:
            attempts.append({"url": url, "result": f"http_{p['status']}",
                             "seeded": from_seed})
            continue
        conf, ev = verify(county, seat, office_city, p["final_url"], p["text"],
                          p["title"])
        attempts.append({"url": url, "result": conf, "status": p["status"],
                         "final_url": p["final_url"], "title": p["title"],
                         "evidence": ev, "seeded": from_seed})
        src = "state directory" if from_seed else "pattern probe"
        if conf == "confident":
            best = {"homepage": p["final_url"], "confidence": "confident",
                    "evidence": ev, "title": p["title"], "source": src}
            break
        if conf == "likely" and best is None:
            best = {"homepage": p["final_url"], "confidence": "likely",
                    "evidence": ev, "title": p["title"], "source": src}
    if best is None:
        best = {"homepage": "", "confidence": "unresolved", "title": None,
                "source": "",
                "evidence": "state directory URL failed and no pattern verified "
                            "— needs a targeted web search"}
    out = {"county": county, "seat": seat, **best, "attempts": attempts}
    log.info("%-14s %-10s %-16s %s", county, best["confidence"],
             best.get("source", ""), best["homepage"] or best["evidence"][:60])
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--county", action="append", default=None)
    ap.add_argument("--batch", default=None,
                    help="restrict to a batch from manifest/counties.csv")
    ap.add_argument("--out", default=None, help="output CSV path")
    ap.add_argument("--workers", type=int, default=10)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s",
                        handlers=[logging.StreamHandler(sys.stdout)])

    global OUT_CSV, OUT_JSON
    if args.out:
        OUT_CSV = Path(args.out)
        OUT_JSON = OUT_CSV.with_name(OUT_CSV.stem + "-probes.json")
    elif args.batch:
        OUT_CSV = ROOT / "manifest" / f"batch{args.batch}_homepages.csv"
        OUT_JSON = ROOT / "logs" / f"batch{args.batch}-homepage-probes.json"

    seed = load_seed(args.batch)
    if args.county:
        want = {c.lower() for c in args.county}
        seed = [r for r in seed if r["county"].lower() in want]
    if not seed:
        sys.exit("no counties selected — check --batch / --county")

    results: list[dict] = []
    with cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = [pool.submit(resolve_county, r) for r in seed]
        for f in cf.as_completed(futs):
            results.append(f.result())

    order = {r["county"]: i for i, r in enumerate(seed)}
    results.sort(key=lambda r: order[r["county"]])

    OUT_JSON.parent.mkdir(exist_ok=True)
    OUT_JSON.write_text(json.dumps(results, indent=2, ensure_ascii=False),
                        encoding="utf-8")
    with OUT_CSV.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["county", "seat", "homepage", "confidence",
                                           "evidence", "flag_for_review", "source"])
        w.writeheader()
        for r in results:
            w.writerow({"county": r["county"], "seat": r["seat"],
                        "homepage": r["homepage"], "confidence": r["confidence"],
                        "evidence": r["evidence"], "source": r.get("source", ""),
                        "flag_for_review":
                            "yes" if r["confidence"] != "confident" else ""})

    from collections import Counter
    log.info("\nconfidence: %s", dict(Counter(r["confidence"] for r in results)))
    log.info("source:     %s", dict(Counter(r.get("source", "") for r in results)))
    log.info("wrote %s and %s", OUT_CSV, OUT_JSON)


if __name__ == "__main__":
    main()
