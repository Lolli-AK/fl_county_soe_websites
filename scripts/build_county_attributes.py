#!/usr/bin/env python3
"""Join what each county DID on election day to what each county IS.

Produces `manifest/fl-county-attributes.csv`, one row per county, combining:

  from this repo      status on election day, website vendor, pages changed,
                      lines added/removed, per-category counts
  from USDA ERS       rural-urban continuum code (RUCC 2023) and 2020 population

Why vendor is derived here rather than looked up: it is the covariate that most
plausibly explains the election-night behaviour, and it is already sitting in the
captured HTML — voterfocus.com / vrswebapps.com / enr.electionsfl.org are VR Systems
tells, a `/123/Page-Name` URL shape is CivicPlus, `wp-content` is WordPress. No
external source needed, and it is verifiable against the snapshot.

On the size measure: **registered voters would be better than population** — it is the
office's actual workload rather than its resident count — but Florida publishes its
by-county registration report behind a JS-injected download link, so it is not a clean
programmatic pull. Population 2020 comes free in the same USDA file as the rurality
code and correlates with registration at roughly 0.99, so it stands in. If you download
the state's registration file by hand, add a `registered_voters` column to the output
CSV and the plotting script will use it in preference to population.

Usage:
    python scripts/build_county_attributes.py --rucc /path/to/Ruralurbancontinuumcodes2023.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path

import analyze_diffs as A

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "manifest" / "fl-county-attributes.csv"

# RUCC 2023: 1-3 are metro, 4-9 nonmetro, 9 = most rural.
RUCC_LABEL = {
    1: "Metro, 1M+", 2: "Metro, 250k-1M", 3: "Metro, <250k",
    4: "Nonmetro, urban 20k+, adjacent", 5: "Nonmetro, urban 20k+, nonadjacent",
    6: "Nonmetro, urban 5-20k, adjacent", 7: "Nonmetro, urban 5-20k, nonadjacent",
    8: "Nonmetro, rural, adjacent", 9: "Nonmetro, rural, nonadjacent",
}


def detect_vendor(county: str) -> str:
    """Identify the SOE site's platform from the captured homepage HTML.

    Order matters: a VR Systems county can also run WordPress for its CMS, and the
    VR tell is the more meaningful one here because VR is what supplies the
    election-night failover page.
    """
    slug = county.lower().replace(" ", "_")
    p = ROOT / "snapshots" / slug / "homepage" / "page.html"
    if not p.exists():
        return "unknown"
    html = p.read_text(encoding="utf-8", errors="ignore")
    low = html.lower()
    if any(t in low for t in ("voterfocus", "vrswebapps", "enr.electionsfl.org")):
        return "VR Systems"
    if "civicplus" in low or re.search(r'href="/\d{3}/[A-Z]', html):
        return "CivicPlus"
    if "wp-content" in low or "wp-json" in low:
        return "WordPress"
    return "other"


def load_rucc(path: Path) -> dict[str, dict]:
    """FL counties only, keyed by bare county name (no ' County' suffix)."""
    wide: dict[str, dict] = {}
    # The USDA file is Latin-1, not UTF-8 — it carries "Doña Ana County, NM", whose
    # ñ is a bare 0xF1 byte that makes a UTF-8 read raise part-way through.
    with path.open(newline="", encoding="latin-1") as fh:
        for r in csv.DictReader(fh):
            if r["State"].strip() != "FL":
                continue
            name = re.sub(r"\s+County$", "", r["County_Name"].strip())
            rec = wide.setdefault(name, {"fips": r["FIPS"].strip()})
            attr, val = r["Attribute"].strip(), r["Value"].strip()
            if attr == "RUCC_2023":
                rec["rucc"] = int(float(val))
            elif attr == "Population_2020":
                rec["population_2020"] = int(float(val.replace(",", "")))
    return wide


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rucc", required=True, help="USDA ERS RUCC 2023 CSV")
    ap.add_argument("--from", dest="rev_from", default="HEAD")
    ap.add_argument("--to", dest="rev_to", default=None)
    args = ap.parse_args()

    a = A.analyze(args.rev_from, args.rev_to)
    captured, counties = A.load_manifest()
    rucc = load_rucc(Path(args.rucc))

    by_class: dict[str, dict[str, list[str]]] = {"lite": {}, "empty": {},
                                                 "render_flip": {}}
    for o in a["outages"]:
        by_class[o["class"]].setdefault(o["county"], []).append(o["page_type"])

    labels = [c for c, _ in A.CATEGORY_PATTERNS]
    rows = []
    unmatched = []
    for county in sorted(counties):
        geo = rucc.get(county)
        if geo is None:
            unmatched.append(county)
            geo = {}
        st = a["per_county_stats"].get(county)
        cats = a["per_county_cat"].get(county, Counter())

        if county in by_class["empty"]:
            status = "Election-night page, old links 404"
        elif county in by_class["lite"]:
            status = "Election-night page, old links serve it"
        elif county in by_class["render_flip"]:
            status = "Not comparable"
        elif st:
            status = "Edited"
        else:
            status = "No change"

        row = {
            "county": county,
            "fips": geo.get("fips", ""),
            "status": status,
            # A single boolean is what the size question is actually about, and it
            # collapses the two URL-handling variants that are the same behaviour.
            "went_election_night": str(county in by_class["empty"]
                                       or county in by_class["lite"]).lower(),
            "vendor": detect_vendor(county),
            "rucc": geo.get("rucc", ""),
            "rucc_label": RUCC_LABEL.get(geo.get("rucc", 0), ""),
            "metro": ("metro" if (geo.get("rucc") or 9) <= 3 else "nonmetro"),
            "population_2020": geo.get("population_2020", ""),
            "pages_captured": captured.get(county, 0),
            "pages_changed": st["files"] if st else 0,
            "lines_added": st["added"] if st else 0,
            "lines_removed": st["removed"] if st else 0,
            "lines_changed": (st["added"] + st["removed"]) if st else 0,
        }
        for l in labels:
            row[l] = cats.get(l, 0)
        rows.append(row)

    if unmatched:
        raise SystemExit(f"no RUCC match for: {unmatched} — check name normalization")

    with OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    print(f"wrote {OUT} ({len(rows)} counties)")
    print("\nstatus x vendor:")
    cross: Counter = Counter((r["status"], r["vendor"]) for r in rows)
    for (s, v), n in sorted(cross.items()):
        print(f"  {s:<40}{v:<14}{n}")
    print("\nwent election-night, by metro status:")
    for m in ("metro", "nonmetro"):
        tot = [r for r in rows if r["metro"] == m]
        yes = [r for r in tot if r["went_election_night"] == "true"]
        print(f"  {m:<10}{len(yes)} of {len(tot)}")
    vr = [r for r in rows if r["vendor"] == "VR Systems"]
    vy = [r for r in vr if r["went_election_night"] == "true"]
    print(f"\nwithin VR Systems counties: {len(vy)} of {len(vr)} went election-night")
    pops = sorted(int(r["population_2020"]) for r in vr if r["population_2020"])
    yp = sorted(int(r["population_2020"]) for r in vy if r["population_2020"])
    if yp:
        print(f"  switchers' population range : {min(yp):,} - {max(yp):,}")
        print(f"  all VR counties' range      : {min(pops):,} - {max(pops):,}")


if __name__ == "__main__":
    main()
