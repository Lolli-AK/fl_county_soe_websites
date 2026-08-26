#!/usr/bin/env python3
"""Does county-website vendor clustering cross state lines? Probe the neighbours.

The Texas corpus found 170 of 254 counties (67%) on one platform, ezTask Titanium,
and the Florida corpus found no such concentration. The proposed mechanism is that
clustering follows a STATE purchasing vehicle, which would mean it stops at the
state line. That is a testable claim, and it has never been tested against the
states that actually border these two.

So: probe every county in Florida's land neighbours (GA, AL) and Texas's (LA, AR,
NM, OK), fingerprint the CMS, and compare the vendor mix.

**Texas and Florida are included as controls, not as new data.** If the probe
recovers Texas's known ~67% ezTask share and Florida's known fragmentation using
nothing but live fetches and pattern-guessed URLs, then a null result in the
neighbours is a finding. If it fails to recover them, the method is broken and the
neighbour numbers mean nothing. Reporting the controls alongside the results is the
only way a reader can tell those two cases apart.

### What this measures, and what it does not

The unit is the **county government front page**, matching what `tx-county-watch`
measured — not the election page. The vendor question is about who builds and hosts
the county's web presence, which is a county-wide procurement, and in most of these
states elections live inside the county site rather than on a separate domain the
way Florida's Supervisors of Elections do. Florida's own row is therefore NOT
comparable to its `fl-county-watch` platform column, which fingerprints SOE sites;
it is re-probed here on county-government domains so that all eight states are
measured the same way.

### URL discovery is guessed, and the miss rate is reported

There is no authoritative national registry of county-government URLs. Counties are
seeded from the Census 2020 county file (authoritative for the county LIST) and the
URL is guessed from per-state domain conventions, first hit wins. `tx-county-watch`
went through this and needed 230 guesses plus content verification, so the same
guard is used here: a fetched page counts only if the county's own name appears in
it, which rejects parked domains, squatters and the wrong county.

Counties whose URL is never found are reported as `not_found` and kept in the
denominator. They are NOT dropped — a vendor share computed only over counties
whose domain happened to match a guess would be biased toward whichever counties
follow conventions, and those are plausibly the ones on a shared vendor platform.
Every share is therefore reported twice: over all counties, and over resolved ones.

Usage:
    python scripts/probe_vendor_spillover.py                       # all 8 states
    python scripts/probe_vendor_spillover.py --state GA --state AL
    python scripts/probe_vendor_spillover.py --workers 12 --limit 20
"""
from __future__ import annotations

import argparse
import csv
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "manifest" / "vendor-spillover.csv"
COUNTY_FILE = Path(__file__).resolve().parent / "_data" / "national_county2020.txt"

# Florida's land neighbours, Texas's land neighbours, plus both anchors as controls.
STATES = {
    "TX": "anchor", "FL": "anchor",
    "GA": "FL neighbour", "AL": "FL neighbour",
    "LA": "TX neighbour", "AR": "TX neighbour",
    "NM": "TX neighbour", "OK": "TX neighbour",
}

# Domain conventions, most-likely first. Texas's `co.<slug>.tx.us` and Georgia's
# `<slug>countyga.gov` are the dominant local forms; the bare `<slug>county.gov`
# fallback catches counties that moved to a plain .gov.
PATTERNS = {
    "TX": ["co.{s}.tx.us", "{s}county.texas.gov", "{s}countytx.gov", "{s}county.gov"],
    "FL": ["{s}countyfl.gov", "{s}county.gov", "{s}fl.gov", "co.{s}.fl.us"],
    "GA": ["{s}countyga.gov", "{s}county.gov", "{s}countyga.com", "{s}cougov.org"],
    "AL": ["{s}countyal.gov", "{s}county.gov", "co.{s}.al.us", "{s}countyalabama.gov"],
    "LA": ["{s}parishla.gov", "{s}parish.org", "{s}pgov.org", "{s}parish.gov"],
    "AR": ["{s}countyar.gov", "co.{s}.ar.us", "{s}county.org", "{s}countyarkansas.gov"],
    "NM": ["{s}countynm.gov", "co.{s}.nm.us", "{s}county.com", "{s}countynm.com"],
    "OK": ["{s}countyok.gov", "co.{s}.ok.us", "{s}county.org", "{s}countyoklahoma.gov"],
}

# Vendor tells, checked in this order. Each names the BUILDER of the site, so a
# match is unambiguous — unlike an outbound link to an election-services provider,
# which the Florida work established measures something else entirely.
TELLS = [
    ("eztask", "ezTask Titanium"),
    ("civicplus", "CivicPlus"), ("civicengage", "CivicPlus"),
    ("revize", "Revize"),
    ("granicus", "Granicus"), ("govdelivery", "Granicus"),
    ("govoffice", "GovOffice"),
    ("municipalimpact", "Municipal Impact"),
    ("streamline", "Streamline"),
    ("squarespace", "Squarespace"),
    ("wixstatic", "Wix"), ("wix.com", "Wix"),
    ("/desktopmodules/", "DotNetNuke"),
    ("joomla", "Joomla"),
]

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Upgrade-Insecure-Requests": "1",
}

_print_lock = threading.Lock()


def slug(county_name: str) -> str:
    """'St. Landry Parish' -> 'stlandry'. Matches how these domains are actually formed."""
    base = re.sub(r"\s+(County|Parish|Municipality|Borough)$", "", county_name.strip(),
                  flags=re.I)
    return re.sub(r"[^a-z0-9]", "", base.lower())


def identity_tokens(county_name: str) -> list[str]:
    """Strings that should appear on the real county's own front page."""
    base = re.sub(r"\s+(County|Parish)$", "", county_name.strip(), flags=re.I)
    low = base.lower()
    out = {low}
    # "St. Landry" is written "St Landry" and "Saint Landry" about equally often.
    if low.startswith("st. "):
        out |= {low[4:], "st " + low[4:], "saint " + low[4:]}
    if low.startswith("de "):
        out.add(low.replace("de ", "de", 1))
    return sorted(out)


def detect_platform(html: str) -> str:
    low = html.lower()
    for needle, name in TELLS:
        if needle in low:
            return name
    m = re.search(r'<meta[^>]+name=["\']generator["\'][^>]+content=["\']([^"\']{0,60})',
                  html, re.I)
    gen = (m.group(1).lower() if m else "")
    if "wordpress" in gen:
        return "WordPress"
    if re.search(r'["\'(]\s*(?:https?://[^"\')]{0,80})?/?wp-(?:content|includes|json)/',
                 low):
        return "WordPress"
    if "drupal" in gen or "/sites/default/files" in low:
        return "Drupal"
    # CivicPlus's numeric section routing (/168/Election-Day-Voting) is distinctive
    # enough to stand in when the vendor string itself has been stripped.
    if re.search(r'href="/\d{3}/[A-Za-z]', html):
        return "CivicPlus"
    return "other/unknown"


def probe_one(client: httpx.Client, state: str, county: str, delay: float) -> dict:
    tokens = identity_tokens(county)
    tried = []
    # https only. Every one of these conventions serves https, and trying http as a
    # fallback after a clean 404 just doubles the request count against dead hosts,
    # which is where nearly all of this run's wall-clock goes.
    for pat in PATTERNS[state]:
        host = pat.format(s=slug(county))
        url = f"https://{host}"
        time.sleep(delay + random.uniform(0, delay))
        try:
            r = client.get(url, headers=HEADERS, follow_redirects=True, timeout=12.0)
        except Exception as exc:
            tried.append(f"{host}:{type(exc).__name__}")
            continue
        if r.status_code >= 400 or "html" not in r.headers.get("content-type", ""):
            tried.append(f"{host}:{r.status_code}")
            continue
        html = r.text
        low = html.lower()
        if not any(t in low for t in tokens):
            # Reached something, but it isn't this county — parked domain, a
            # squatter, or a neighbouring county's site on a shared host.
            tried.append(f"{host}:wrong_county")
            continue
        return {"state": state, "county": county, "status": "resolved",
                "url": url, "final_url": str(r.url), "http_status": r.status_code,
                "platform": detect_platform(html), "bytes": len(html),
                "attempts": ";".join(tried) or "first_try"}
    return {"state": state, "county": county, "status": "not_found", "url": "",
            "final_url": "", "http_status": "", "platform": "not_found",
            "bytes": "", "attempts": ";".join(tried)}


def load_counties(states: list[str], limit: int | None) -> list[tuple[str, str]]:
    if not COUNTY_FILE.exists():
        raise SystemExit(
            f"missing {COUNTY_FILE}\n"
            "Fetch the Census county list first:\n"
            "  mkdir -p scripts/_data && curl -s -o scripts/_data/national_county2020.txt \\\n"
            "    https://www2.census.gov/geo/docs/reference/codes2020/national_county2020.txt")
    rows = []
    with COUNTY_FILE.open(encoding="utf-8") as fh:
        for rec in csv.DictReader(fh, delimiter="|"):
            if rec["STATE"] in states:
                rows.append((rec["STATE"], rec["COUNTYNAME"]))
    rows.sort()
    if limit:
        # Deterministic per-state sample so a --limit run is reproducible.
        keep, seen = [], {}
        for st, c in rows:
            seen[st] = seen.get(st, 0) + 1
            if seen[st] <= limit:
                keep.append((st, c))
        rows = keep
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", action="append", choices=sorted(STATES),
                    help="repeatable; default all eight")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, help="first N counties per state (testing)")
    ap.add_argument("--delay", type=float, default=0.15,
                    help="base politeness pause per request, seconds")
    args = ap.parse_args()

    states = args.state or sorted(STATES)
    counties = load_counties(states, args.limit)
    print(f"probing {len(counties)} counties across {len(states)} states "
          f"({', '.join(states)}) with {args.workers} workers\n")

    results = []
    done = [0]
    with httpx.Client(http2=True, verify=False) as client:
        def task(item):
            st, c = item
            r = probe_one(client, st, c, args.delay)
            with _print_lock:
                done[0] += 1
                if done[0] % 25 == 0 or r["status"] == "resolved":
                    print(f"  [{done[0]:>4}/{len(counties)}] {st} {c:<28} "
                          f"{r['platform']}")
            return r

        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            results = list(pool.map(task, counties))

    with OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(results[0]))
        w.writeheader()
        w.writerows(results)
    print(f"\nwrote {OUT} ({len(results)} rows)")
    summarize(results)


def summarize(results: list[dict]) -> None:
    from collections import Counter, defaultdict
    by_state = defaultdict(list)
    for r in results:
        by_state[r["state"]].append(r)

    print("\n" + "=" * 78)
    print("VENDOR MIX BY STATE  (share of RESOLVED counties; resolve rate in header)")
    print("=" * 78)
    for st in sorted(by_state, key=lambda s: (STATES[s] != "anchor", s)):
        rows = by_state[st]
        res = [r for r in rows if r["status"] == "resolved"]
        print(f"\n{st}  [{STATES[st]}]  {len(res)}/{len(rows)} resolved "
              f"({len(res) / len(rows):.0%})")
        if not res:
            continue
        for plat, n in Counter(r["platform"] for r in res).most_common():
            print(f"    {plat:<22}{n:>4}  {n / len(res):>5.0%} of resolved "
                  f"{n / len(rows):>5.0%} of all")

    print("\n" + "=" * 78)
    print("SPILLOVER TEST — does a state's signature vendor appear across the line?")
    print("=" * 78)
    for vendor in ("ezTask Titanium", "CivicPlus", "Revize", "Granicus", "WordPress"):
        hits = []
        for st in sorted(by_state):
            n = sum(1 for r in by_state[st] if r["platform"] == vendor)
            res = sum(1 for r in by_state[st] if r["status"] == "resolved")
            if n:
                hits.append(f"{st} {n}/{res}")
        print(f"  {vendor:<18}{', '.join(hits) if hits else 'not detected anywhere'}")


if __name__ == "__main__":
    main()
