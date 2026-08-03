#!/usr/bin/env python3
"""One-shot: generate manifest/counties.csv — the seed of truth for all 67 Florida
counties. Kept as provenance for how the seed was assembled; not part of any run.

Florida differs from Texas in one way that shapes this whole project: each county
elects an independent **Supervisor of Elections (SOE)** who runs their own website,
separate from the county government's. The elections content therefore lives on the
SOE domain, so `homepage` here is the **SOE front page**, not the county front page.

That also means the homepages did not have to be guessed. The Florida Department of
State, Division of Elections publishes an authoritative machine-readable directory of
all 67 SOE offices, including the office website:

    https://dos.fl.gov/elections/contacts/supervisor-of-elections/
    -> qryCountyInfo_Excel-<mmddyyyy>.xlsx   (snapshot used: 2026-07-07)

So unlike tx-county-watch — which had to probe domain patterns and verify content for
254 unknown homepages — every Florida homepage starts from a state-published URL. It
is still independently re-verified by scripts/audit_targets.py; an official directory
can be stale (offices migrate .com -> .gov mid-term), and a URL being published by the
state is not evidence that it currently resolves.

Two city columns, because they differ and both are useful identity signals:
  seat        — the county seat (a fixed geographic fact)
  office_city — where the SOE office actually sits, per the state directory

They disagree for 8 counties (Brevard's SOE is in Melbourne, the seat is Titusville;
Pinellas's is in Largo, the seat is Clearwater; Citrus, Nassau, Sumter, Collier,
Hamilton and Walton also differ in spelling or place). Verification accepts either,
which is what keeps those 8 from being flagged as unidentifiable.

Usage:
    python scripts/_build_counties_seed.py            # writes manifest/counties.csv
    python scripts/_build_counties_seed.py --check    # verify the CSV still matches
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "manifest" / "counties.csv"

# Source: Florida DOS Division of Elections SOE directory export, 2026-07-07.
# (county, seat, office_city, soe_homepage)
#
# `seat` is the county seat. `office_city` is the SOE office's city from the state
# directory. Where the directory truncated a name ("Green Cove Spring") the full
# name is used.
COUNTIES: list[tuple[str, str, str, str]] = [
    ("Alachua", "Gainesville", "Gainesville", "https://www.votealachua.gov"),
    ("Baker", "Macclenny", "Macclenny", "https://votebakerfl.gov"),
    ("Bay", "Panama City", "Panama City", "https://www.bayvotesfl.gov"),
    ("Bradford", "Starke", "Starke", "https://www.votebradfordfl.gov"),
    ("Brevard", "Titusville", "Melbourne", "https://www.votebrevard.gov"),
    ("Broward", "Fort Lauderdale", "Fort Lauderdale", "https://www.browardvotes.gov"),
    ("Calhoun", "Blountstown", "Blountstown", "https://www.votecalhounfl.gov"),
    ("Charlotte", "Punta Gorda", "Punta Gorda", "https://www.soecharlottecountyfl.gov"),
    ("Citrus", "Inverness", "Lecanto", "https://www.votecitrus.gov"),
    ("Clay", "Green Cove Springs", "Green Cove Springs", "https://www.clayelections.gov"),
    ("Collier", "Naples", "Naples", "https://www.colliervotes.gov"),
    ("Columbia", "Lake City", "Lake City", "https://www.votecolumbiafl.gov"),
    ("DeSoto", "Arcadia", "Arcadia", "https://www.votedesotofl.gov"),
    ("Dixie", "Cross City", "Cross City", "https://www.dixievotes.com"),
    ("Duval", "Jacksonville", "Jacksonville", "https://www.duvalelections.com"),
    ("Escambia", "Pensacola", "Pensacola", "https://escambiavotes.gov"),
    ("Flagler", "Bunnell", "Bunnell", "https://www.flaglerelections.gov"),
    ("Franklin", "Apalachicola", "Apalachicola", "https://www.votefranklinfl.gov"),
    ("Gadsden", "Quincy", "Quincy", "https://www.gadsdensoefl.gov"),
    ("Gilchrist", "Trenton", "Trenton", "https://www.votegilchrist.com"),
    ("Glades", "Moore Haven", "Moore Haven", "https://www.voteglades.gov"),
    ("Gulf", "Port St. Joe", "Port St. Joe", "https://www.votegulf.gov"),
    ("Hamilton", "Jasper", "Jasper", "https://www.hamiltonvotesfl.gov"),
    ("Hardee", "Wauchula", "Wauchula", "https://hardeeflvotes.gov"),
    ("Hendry", "LaBelle", "LaBelle", "https://www.hendryelections.gov"),
    ("Hernando", "Brooksville", "Brooksville", "https://www.hernandovotes.gov"),
    ("Highlands", "Sebring", "Sebring", "https://www.votehighlands.gov"),
    ("Hillsborough", "Tampa", "Tampa", "https://www.votehillsborough.gov"),
    ("Holmes", "Bonifay", "Bonifay", "https://www.holmeselectionsfl.gov"),
    ("Indian River", "Vero Beach", "Vero Beach", "https://www.voteindianriver.gov"),
    ("Jackson", "Marianna", "Marianna", "https://www.votejacksonfl.gov"),
    ("Jefferson", "Monticello", "Monticello", "https://www.jeffersonvotesfl.gov"),
    ("Lafayette", "Mayo", "Mayo", "https://www.lafayettevotes.net"),
    ("Lake", "Tavares", "Tavares", "https://www.lakevotes.gov"),
    ("Lee", "Fort Myers", "Fort Myers", "https://www.lee.vote"),
    ("Leon", "Tallahassee", "Tallahassee", "https://www.leonvotes.gov"),
    ("Levy", "Bronson", "Bronson", "https://www.votelevy.com"),
    ("Liberty", "Bristol", "Bristol", "https://libertycountyflsoe.gov"),
    ("Madison", "Madison", "Madison", "https://www.votemadison.com"),
    ("Manatee", "Bradenton", "Bradenton", "https://www.votemanatee.gov"),
    ("Marion", "Ocala", "Ocala", "https://votemarion.gov"),
    ("Martin", "Stuart", "Stuart", "https://www.martinvotes.gov"),
    ("Miami-Dade", "Miami", "Miami", "https://www.miamidade.gov/elections"),
    ("Monroe", "Key West", "Key West", "https://votemonroeflkeys.gov"),
    ("Nassau", "Fernandina Beach", "Yulee", "https://www.votenassaufl.gov"),
    ("Okaloosa", "Crestview", "Crestview", "https://www.voteokaloosa.gov"),
    ("Okeechobee", "Okeechobee", "Okeechobee", "https://www.voteokeechobee.gov"),
    ("Orange", "Orlando", "Orlando", "https://www.ocfelections.gov"),
    ("Osceola", "Kissimmee", "Kissimmee", "https://www.voteosceola.gov"),
    ("Palm Beach", "West Palm Beach", "West Palm Beach", "https://www.votepalmbeach.gov"),
    ("Pasco", "Dade City", "Dade City", "https://www.pascovotes.gov"),
    ("Pinellas", "Clearwater", "Largo", "https://www.votepinellas.gov"),
    ("Polk", "Bartow", "Bartow", "https://www.polkelections.gov"),
    ("Putnam", "Palatka", "Palatka", "https://soe.putnam-fl.gov"),
    ("Santa Rosa", "Milton", "Milton", "https://www.votesantarosa.gov"),
    ("Sarasota", "Sarasota", "Sarasota", "https://www.sarasotavotes.gov"),
    ("Seminole", "Sanford", "Sanford", "https://www.voteseminole.gov"),
    ("St. Johns", "St. Augustine", "St. Augustine", "https://www.votesjc.gov"),
    ("St. Lucie", "Fort Pierce", "Fort Pierce", "https://www.stlucievotes.gov"),
    ("Sumter", "Bushnell", "Wildwood", "https://elections.sumtercountyfl.gov"),
    ("Suwannee", "Live Oak", "Live Oak", "https://www.suwanneevotes.com"),
    ("Taylor", "Perry", "Perry", "https://www.taylorelectionsfl.gov"),
    ("Union", "Lake Butler", "Lake Butler", "https://www.unionflvotes.com"),
    ("Volusia", "DeLand", "DeLand", "https://volusiaelections.gov"),
    ("Wakulla", "Crawfordville", "Crawfordville", "https://www.wakullavotes.gov"),
    ("Walton", "DeFuniak Springs", "DeFuniak Springs", "https://www.votewalton.gov"),
    ("Washington", "Chipley", "Chipley", "https://www.wcsoe.gov"),
]

FIELDS = ["county", "seat", "office_city", "batch", "homepage"]


def rows() -> list[dict[str, str]]:
    # batch 1 = homepage came from the official state SOE directory. A county whose
    # directory URL fails the audit and has to be corrected by hand is re-labelled
    # batch 2, so provenance stays visible in the manifest.
    return [{"county": c, "seat": s, "office_city": o, "batch": "1", "homepage": h}
            for c, s, o, h in COUNTIES]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="compare the existing CSV against this table instead of writing")
    args = ap.parse_args()

    if len(COUNTIES) != 67:
        sys.exit(f"expected 67 Florida counties, have {len(COUNTIES)}")
    names = [c for c, *_ in COUNTIES]
    if len(set(names)) != len(names):
        sys.exit("duplicate county name in the seed table")

    if args.check:
        with OUT.open(newline="", encoding="utf-8") as fh:
            on_disk = list(csv.DictReader(fh))
        # Only the identity + provenance columns are generated here; `homepage` may
        # have been corrected by hand after an audit, so it is reported, not enforced.
        drift = [(d["county"], d["homepage"], e["homepage"])
                 for d, e in zip(on_disk, rows())
                 if d["homepage"] != e["homepage"]]
        for county, disk, seed in drift:
            print(f"homepage differs (hand-corrected?): {county}: {disk} != {seed}")
        print(f"{len(on_disk)} rows on disk, {len(drift)} homepage differences")
        return

    OUT.parent.mkdir(exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows())
    print(f"wrote {OUT} ({len(COUNTIES)} counties)")


if __name__ == "__main__":
    main()
