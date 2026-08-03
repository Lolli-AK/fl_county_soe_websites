"""Homepage-identity verification tests.

Florida's identity hazards are different from Texas's, and these tests pin the
behaviour that handles them.

Texas's central problem was *intra-state*: twelve counties share a name with a
different county's seat (Houston is Harris County's seat; Tyler is Smith County's),
so a loose "county name + the word county" check produced confident-looking false
positives. Florida has essentially none of that — `test_florida_has_no_name_seat_
collisions` asserts it, so the assumption is checked rather than assumed.

Florida's problems are instead:

  1. **Cross-state collisions.** Nassau (NY), Duval (TX), Monroe (NY/MI/PA), Jackson
     (MO/MS/OR), Marion (IN/OR), Polk (IA/OR), Union (NJ), Washington (OR/PA),
     Franklin (OH), Orange (CA/NY), Lee (AL/VA), Madison (AL/IL) and Jefferson
     (KY/CO/AL) all exist elsewhere. A Florida signal is mandatory.
  2. **The other four constitutional officers.** Every Florida county separately
     elects a Clerk of Court, Property Appraiser, Tax Collector and Sheriff. Their
     sites carry the county name and heavy county-government vocabulary on lookalike
     domains, so they are the most plausible wrong answer for "this county's SOE
     homepage" — more so than any tourism site.
  3. **Punctuation variants.** "St. Johns" is written "St. Johns", "St Johns" and
     "Saint Johns"; "Miami-Dade" appears as "Miami Dade".

Run:  .venv/bin/python -m pytest tests/ -q
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import discover_homepages as H  # noqa: E402

SEED = ROOT / "manifest" / "counties.csv"


def _seed() -> dict[str, tuple[str, str]]:
    with SEED.open(newline="", encoding="utf-8") as fh:
        return {r["county"].strip(): (r["seat"].strip(), r["office_city"].strip())
                for r in csv.DictReader(fh)}


def _page(body: str) -> str:
    """Pad to clear the near-empty-page floor without changing the signals."""
    filler = (" The office is open Monday through Friday and serves voters "
              "throughout the county with registration and ballot services.")
    while len(body) < 250:
        body += filler
    return body


# --------------------------------------------------------------------------- #
# The shape of Florida's collision problem
# --------------------------------------------------------------------------- #
def test_florida_has_no_name_seat_collisions():
    """Documents why this file does NOT mirror the Texas name/seat guard.

    If Florida ever gained such a collision, the wrong-county logic would need the
    same scrutiny Texas's did — so this asserts the premise instead of assuming it.
    """
    seed = _seed()
    names = set(seed)
    collisions = {seat: cty for cty, (seat, _o) in seed.items()
                  if seat in names and seat != cty}
    assert not collisions, (
        f"Florida now has name/seat collisions {collisions} — revisit the "
        f"identity check, which currently relies on there being none")


def test_every_county_is_locatable_by_city():
    """The verifier falls back to seat/office city when 'Florida' is absent, so
    every county must actually have both recorded."""
    seed = _seed()
    assert len(seed) == 67
    bad = [c for c, (s, o) in seed.items() if not s or not o]
    assert not bad, f"counties missing a city signal: {bad}"


# --------------------------------------------------------------------------- #
# Cross-state collisions must be rejected
# --------------------------------------------------------------------------- #
NASSAU_NY_PAGE = _page(
    "Nassau County, New York - Board of Elections. Welcome to Nassau County. "
    "Voter registration, early voting and polling place information for residents "
    "of Mineola, New York.")


def test_nassau_new_york_is_not_accepted_as_nassau_florida():
    conf, ev = H.verify("Nassau", "Fernandina Beach", "Yulee",
                        "https://www.nassaucountyny.gov/", NASSAU_NY_PAGE,
                        "Nassau County, New York Board of Elections")
    assert conf == "reject", ev


def test_nassau_florida_still_verifies():
    page = _page("Nassau County, FL Supervisor of Elections. Vote by mail, early "
                 "voting, voter registration and polling place lookup. Our office "
                 "is in Yulee, Florida.")
    conf, ev = H.verify("Nassau", "Fernandina Beach", "Yulee",
                        "https://www.votenassaufl.gov/", page,
                        "Nassau County Supervisor of Elections")
    assert conf == "confident", ev


DUVAL_TX_PAGE = _page(
    "Duval County, Texas. Welcome to Duval County. County clerk, sheriff and "
    "courthouse information for San Diego, Texas.")


def test_duval_texas_is_not_accepted_as_duval_florida():
    conf, ev = H.verify("Duval", "Jacksonville", "Jacksonville",
                        "https://www.co.duval.tx.us/", DUVAL_TX_PAGE,
                        "Duval County, Texas")
    assert conf == "reject", ev


# --------------------------------------------------------------------------- #
# The other four constitutional officers are NOT the Supervisor of Elections
# --------------------------------------------------------------------------- #
def test_clerk_of_court_site_is_rejected():
    """Florida's most plausible wrong answer: a sibling elected office."""
    page = _page("Leon County Clerk of the Circuit Court and Comptroller. "
                 "Tallahassee, Florida. Official records, court records, county "
                 "commission minutes, tax deeds.")
    conf, ev = H.verify("Leon", "Tallahassee", "Tallahassee",
                        "https://cvweb.leonclerk.com/", page,
                        "Leon County Clerk of Court & Comptroller")
    assert conf == "reject", ev


def test_property_appraiser_site_is_rejected():
    page = _page("Polk County Property Appraiser. Bartow, Florida. Homestead "
                 "exemption, parcel search, tangible personal property, county "
                 "commission and tax collector links.")
    conf, ev = H.verify("Polk", "Bartow", "Bartow",
                        "https://www.polkpa.org/", page,
                        "Polk County Property Appraiser")
    assert conf == "reject", ev


def test_soe_page_carrying_a_shared_officer_masthead_is_kept():
    """Several SOE sites list every constitutional officer in a shared header.

    That must not be read as "this is the Tax Collector's site" — the title also
    says Supervisor of Elections, which wins.
    """
    page = _page("Alachua County Supervisor of Elections. Gainesville, Florida. "
                 "Vote by mail, early voting, voter registration, polling place. "
                 "Other offices: Clerk of Court, Tax Collector, Property Appraiser.")
    conf, ev = H.verify("Alachua", "Gainesville", "Gainesville",
                        "https://www.votealachua.gov/", page,
                        "Alachua County Supervisor of Elections | Tax Collector")
    assert conf == "confident", ev


# --------------------------------------------------------------------------- #
# Punctuation variants in county names
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("written", ["St. Johns", "St Johns", "Saint Johns"])
def test_st_johns_verifies_however_it_is_written(written):
    page = _page(f"{written} County Supervisor of Elections. St. Augustine, "
                 f"Florida. Early voting, vote by mail, voter registration, "
                 f"polling place lookup and sample ballot.")
    conf, ev = H.verify("St. Johns", "St. Augustine", "St. Augustine",
                        "https://www.votesjc.gov/", page,
                        f"{written} County Supervisor of Elections")
    assert conf == "confident", ev


@pytest.mark.parametrize("written", ["Miami-Dade", "Miami Dade"])
def test_miami_dade_verifies_with_or_without_the_hyphen(written):
    page = _page(f"{written} County Supervisor of Elections. Miami, Florida. "
                 f"Early voting, vote by mail, voter registration, precinct and "
                 f"polling place information, sample ballot.")
    conf, ev = H.verify("Miami-Dade", "Miami", "Miami",
                        "https://www.votemiamidade.gov/", page,
                        f"{written} County Supervisor of Elections")
    assert conf == "confident", ev


# --------------------------------------------------------------------------- #
# Non-government and broken pages
# --------------------------------------------------------------------------- #
def test_tourism_site_mentioning_the_county_is_rejected():
    page = _page("Visit Monroe County Florida. Plan your visit to the Florida "
                 "Keys. Things to do, where to stay, places to eat, itineraries, "
                 "vacation rentals and our visitors bureau in Key West.")
    conf, ev = H.verify("Monroe", "Key West", "Key West",
                        "https://www.visitmonroecounty.com/", page,
                        "Visit Monroe County | Florida Keys Tourism")
    assert conf == "reject", ev


def test_commercial_site_mentioning_the_county_is_rejected():
    page = _page("Broward County Florida Process Servers. We serve legal documents "
                 "throughout Broward County, Florida. Attorney advertising.")
    conf, ev = H.verify("Broward", "Fort Lauderdale", "Fort Lauderdale",
                        "https://browardcountyprocess.com/", page,
                        "Broward County Florida Process Servers")
    assert conf == "reject", ev


def test_parked_domain_is_rejected():
    page = _page("This domain is for sale. Buy this domain. Levy County Florida.")
    conf, ev = H.verify("Levy", "Bronson", "Bronson",
                        "https://votelevy.com/", page, "Domain for sale")
    assert conf == "reject", ev


def test_error_page_is_rejected():
    page = _page("404 Not Found. The page you requested could not be found. "
                 "Gulf County Florida.")
    conf, ev = H.verify("Gulf", "Port St. Joe", "Port St. Joe",
                        "https://votegulf.gov/missing", page, "404 Not Found")
    assert conf == "reject", ev


def test_office_city_alone_is_enough_to_locate_a_county():
    """Brevard's SOE sits in Melbourne, not the seat (Titusville). A page naming
    only the office city must still verify, or those counties fail spuriously."""
    page = _page("Brevard County Supervisor of Elections. Melbourne. Early voting, "
                 "vote by mail, voter registration, polling place, sample ballot.")
    conf, ev = H.verify("Brevard", "Titusville", "Melbourne",
                        "https://www.votebrevard.gov/", page,
                        "Brevard County Supervisor of Elections")
    assert conf in ("confident", "likely"), ev


# --------------------------------------------------------------------------- #
# Real captured homepages must keep verifying (regression guard)
# --------------------------------------------------------------------------- #
def _manually_confirmed() -> set[str]:
    """Counties whose homepage was settled by hand rather than by the verifier.

    Marked in `notes` with "manually confirmed" or "corrected:". Four SOE sites
    never write "Supervisor of Elections" in body text — it lives in their logo
    image — so a text-only check cannot reach `confident` on them. Re-litigating
    those here would only make the test brittle.
    """
    targets = ROOT / "manifest" / "targets.csv"
    if not targets.exists():
        return set()
    out = set()
    with targets.open(newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["page_type"].strip() != "homepage":
                continue
            note = (r.get("notes") or "").lower()
            if "manually confirmed" in note or "corrected:" in note:
                out.add(r["county"].strip())
    return out


@pytest.mark.skipif(not (ROOT / "snapshots").exists(),
                    reason="no snapshots captured yet")
def test_captured_homepages_still_verify():
    """Every captured SOE homepage must keep passing the identity checks.

    Skips captures with nothing to identify (non-200 / errored) and counties
    confirmed by hand — see _manually_confirmed().
    """
    import json
    seed = _seed()
    exempt = _manually_confirmed()
    failures = []
    for f in sorted((ROOT / "snapshots").glob("*/homepage/page.txt")):
        slug = f.parent.parent.name
        county = next((c for c in seed if c.lower().replace(" ", "_") == slug), None)
        if county is None or county in exempt:
            continue
        meta = json.loads((f.parent / "meta.json").read_text(encoding="utf-8"))
        if meta.get("http_status") != 200 or meta.get("error"):
            continue  # blocked/errored capture, nothing to identify
        seat, office = seed[county]
        conf, ev = H.verify(county, seat, office, meta["final_url"],
                            f.read_text(encoding="utf-8"), meta.get("title"))
        if conf == "reject":
            failures.append(f"{county}: {ev[:80]}")
    assert not failures, f"previously-verified homepages now rejected: {failures}"
