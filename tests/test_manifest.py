"""Manifest integrity tests.

These guard the invariants that everything else depends on. Deliberately free of
hardcoded county names or counts beyond the one number the project is scoped to
(67 Florida counties) — everything else is derived from manifest/counties.csv, so
adding or correcting a county is a data edit and these tests still hold.

Run:  .venv/bin/python -m pytest tests/ -q
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
COUNTIES = ROOT / "manifest" / "counties.csv"
TARGETS = ROOT / "manifest" / "targets.csv"
SNAPSHOTS = ROOT / "snapshots"

# Florida has exactly 67 counties. This is the project's scope, not a magic number.
FLORIDA_COUNTY_COUNT = 67
PAGE_TYPES = ["homepage", "elections", "polling", "early_voting", "results"]
# Batch records where the SOE homepage came from:
#   1 = the Florida DOS Supervisor of Elections directory, verified as published
#   2 = the directory URL was stale; the live domain was resolved and verified
BATCHES = {"1", "2"}


def _rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


@pytest.fixture(scope="module")
def counties() -> list[dict]:
    assert COUNTIES.exists(), f"missing seed file: {COUNTIES}"
    return _rows(COUNTIES)


@pytest.fixture(scope="module")
def targets() -> list[dict]:
    assert TARGETS.exists(), f"missing manifest: {TARGETS}"
    return _rows(TARGETS)


# --------------------------------------------------------------------------- #
# The seed: 67 counties, partitioned by homepage provenance
# --------------------------------------------------------------------------- #
def test_seed_has_67_unique_counties(counties):
    names = [r["county"].strip() for r in counties]
    assert len(names) == FLORIDA_COUNTY_COUNT, \
        f"expected {FLORIDA_COUNTY_COUNT} rows, got {len(names)}"
    dupes = {n for n in names if names.count(n) > 1}
    assert not dupes, f"duplicate counties in seed: {sorted(dupes)}"
    assert len(set(names)) == FLORIDA_COUNTY_COUNT


def test_batches_partition_the_counties(counties):
    """The batch labels must cover every county exactly once."""
    by_batch: dict[str, set[str]] = {b: set() for b in BATCHES}
    for r in counties:
        batch = r["batch"].strip()
        assert batch in BATCHES, f"{r['county']}: unexpected batch {batch!r}"
        by_batch[batch].add(r["county"].strip())

    for a in sorted(BATCHES):
        for b in sorted(BATCHES):
            if a < b:
                overlap = by_batch[a] & by_batch[b]
                assert not overlap, f"batches {a} and {b} overlap: {sorted(overlap)}"

    union = set().union(*by_batch.values())
    assert len(union) == FLORIDA_COUNTY_COUNT
    assert sum(len(v) for v in by_batch.values()) == FLORIDA_COUNTY_COUNT


def test_every_county_has_a_seat_and_office_city(counties):
    """Both city columns are identity signals for verification, so both must exist.

    They are separate columns because in Florida they genuinely differ — the SOE
    office is not always in the county seat.
    """
    missing_seat = [r["county"] for r in counties if not r["seat"].strip()]
    assert not missing_seat, f"counties with no seat: {missing_seat}"
    missing_city = [r["county"] for r in counties if not r["office_city"].strip()]
    assert not missing_city, f"counties with no SOE office city: {missing_city}"


def test_every_county_has_a_verified_homepage(counties):
    """Unlike Texas, every Florida homepage is known up front (state directory)."""
    for r in counties:
        assert r["homepage"].strip().startswith("http"), \
            f"{r['county']}: missing or malformed SOE homepage"


def test_seat_and_office_city_differ_for_some_counties(counties):
    """Guards against someone 'simplifying' office_city away as a duplicate column.

    If this ever becomes zero, the two columns really are redundant and the verifier
    can be simplified. Today it is not: several SOE offices sit outside the seat.
    """
    differing = [r["county"] for r in counties
                 if r["seat"].strip().lower() != r["office_city"].strip().lower()]
    assert differing, "office_city adds nothing — collapse it into seat"


# --------------------------------------------------------------------------- #
# The manifest the pipeline actually reads
# --------------------------------------------------------------------------- #
def test_targets_covers_seed_counties_five_ways(counties, targets):
    """Every seeded county gets exactly one row per page type."""
    seeded = {r["county"].strip() for r in counties}
    seen: dict[str, list[str]] = {}
    for r in targets:
        seen.setdefault(r["county"].strip(), []).append(r["page_type"].strip())

    unknown = sorted(set(seen) - seeded)
    assert not unknown, f"targets.csv has counties absent from the seed: {unknown}"

    for county, types in seen.items():
        assert sorted(types) == sorted(PAGE_TYPES), \
            f"{county}: expected one row per page type, got {sorted(types)}"

    expected_rows = len(seen) * len(PAGE_TYPES)
    assert len(targets) == expected_rows, \
        f"expected {expected_rows} rows for {len(seen)} counties, got {len(targets)}"


def test_targets_covers_all_67_counties(counties, targets):
    """Definition of done: the manifest spans every Florida county."""
    seeded = {r["county"].strip() for r in counties}
    present = {r["county"].strip() for r in targets}
    absent = sorted(seeded - present)
    assert not absent, (
        f"{len(absent)} seeded counties missing from targets.csv "
        f"(run Phase 1 discovery + merge): {absent[:8]}...")
    assert len(present) == FLORIDA_COUNTY_COUNT
    assert len(targets) == FLORIDA_COUNTY_COUNT * len(PAGE_TYPES)


def test_targets_batch_labels_match_seed(counties, targets):
    seed_batch = {r["county"].strip(): r["batch"].strip() for r in counties}
    bad = [(r["county"], r["batch"], seed_batch[r["county"].strip()])
           for r in targets
           if r["county"].strip() in seed_batch
           and r["batch"].strip() != seed_batch[r["county"].strip()]]
    assert not bad, f"batch mismatch vs seed (county, targets, seed): {bad[:5]}"


def test_homepage_rows_match_the_seed(counties, targets):
    """The homepage row and the seed must not drift apart.

    They are edited in different places (the seed by hand, the manifest by merge),
    so a silent divergence would mean the pipeline snapshots a different URL from
    the one the seed claims is authoritative.
    """
    seed_home = {r["county"].strip(): r["homepage"].strip().rstrip("/")
                 for r in counties}
    bad = [(r["county"], r["url"], seed_home[r["county"].strip()])
           for r in targets
           if r["page_type"].strip() == "homepage"
           and r["county"].strip() in seed_home
           and r["url"].strip().rstrip("/") != seed_home[r["county"].strip()]]
    assert not bad, f"homepage differs from seed (county, targets, seed): {bad[:5]}"


def test_every_county_has_a_homepage_url(targets):
    """A homepage gap would mean a county is entirely uncaptured."""
    missing = [r["county"] for r in targets
               if r["page_type"].strip() == "homepage" and not r["url"].strip()]
    assert not missing, f"counties with no homepage URL: {missing}"


def test_gap_rows_carry_a_reason(targets):
    """An empty url is a recorded gap and must explain itself in notes."""
    silent = [f"{r['county']}/{r['page_type']}" for r in targets
              if not r["url"].strip() and not r["notes"].strip()]
    assert not silent, f"gap rows with no explanation: {silent[:10]}"


def test_external_flag_is_boolean(targets):
    bad = [f"{r['county']}/{r['page_type']}={r['external']!r}" for r in targets
           if r["external"].strip().lower() not in ("true", "false", "")]
    assert not bad, f"non-boolean external values: {bad[:10]}"


def test_urls_are_http(targets):
    bad = [f"{r['county']}/{r['page_type']}={r['url']}" for r in targets
           if r["url"].strip() and not r["url"].strip().startswith("http")]
    assert not bad, f"malformed URLs: {bad[:10]}"


def test_no_statewide_portal_captured_as_a_county_page(targets):
    """A statewide portal is identical for all 67 counties, so capturing it would
    both add no per-county signal and misreport a county as having a page.

    This is the one rule most likely to be broken by a hand-edit to the manifest,
    since those URLs look perfectly reasonable in isolation.
    """
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import discover_pages as D  # noqa: E402

    offenders = [f"{r['county']}/{r['page_type']}={r['url']}" for r in targets
                 if r["url"].strip() and D.is_generic_portal(r["url"].strip())]
    assert not offenders, f"statewide/national portals in the manifest: {offenders[:10]}"


# --------------------------------------------------------------------------- #
# Artifacts on disk agree with the manifest
# --------------------------------------------------------------------------- #
def _slug(county: str) -> str:
    return county.lower().replace(" ", "_")


@pytest.mark.skipif(not SNAPSHOTS.exists(), reason="no snapshots captured yet")
def test_every_captured_target_has_three_artifacts(targets):
    missing = []
    for r in targets:
        if not r["url"].strip():
            continue
        d = SNAPSHOTS / _slug(r["county"]) / r["page_type"].strip()
        if not d.exists():
            continue  # not yet fetched; covered by the run itself
        for name in ("page.html", "page.txt", "meta.json"):
            if not (d / name).exists():
                missing.append(str(d / name))
    assert not missing, f"captured pages missing artifacts: {missing[:10]}"


@pytest.mark.skipif(not SNAPSHOTS.exists(), reason="no snapshots captured yet")
def test_no_artifacts_for_gap_rows(targets):
    """A gap must not leave a stale directory behind from an earlier manifest."""
    stale = []
    for r in targets:
        if r["url"].strip():
            continue
        d = SNAPSHOTS / _slug(r["county"]) / r["page_type"].strip()
        if d.exists():
            stale.append(str(d))
    assert not stale, f"gap rows with leftover artifact dirs: {stale[:10]}"


@pytest.mark.skipif(not SNAPSHOTS.exists(), reason="no snapshots captured yet")
def test_meta_json_is_wellformed_and_matches_its_row(targets):
    by_key = {(r["county"].strip(), r["page_type"].strip()): r for r in targets}
    problems = []
    for f in SNAPSHOTS.glob("*/*/meta.json"):
        meta = json.loads(f.read_text(encoding="utf-8"))
        for field in ("county", "page_type", "requested_url", "http_status",
                      "render_mode", "external", "fetched_at",
                      "html_sha256", "text_sha256", "byte_size"):
            if field not in meta:
                problems.append(f"{f}: missing field {field}")
        row = by_key.get((meta.get("county", ""), meta.get("page_type", "")))
        if row and row["url"].strip() and meta.get("requested_url") != row["url"].strip():
            problems.append(f"{f}: requested_url does not match manifest")
        if meta.get("render_mode") not in ("plain", "headless"):
            problems.append(f"{f}: bad render_mode {meta.get('render_mode')!r}")
    assert not problems, f"meta.json problems: {problems[:10]}"


@pytest.mark.skipif(not SNAPSHOTS.exists(), reason="no snapshots captured yet")
def test_artifacts_contain_no_fetch_timestamp(targets):
    """The timestamp belongs only in meta.json, or every run would diff."""
    offenders = []
    for f in list(SNAPSHOTS.glob("*/*/page.txt"))[:400]:
        meta = f.parent / "meta.json"
        if not meta.exists():
            continue
        stamp = json.loads(meta.read_text(encoding="utf-8")).get("fetched_at", "")
        if stamp and stamp in f.read_text(encoding="utf-8"):
            offenders.append(str(f))
    assert not offenders, f"fetch timestamp leaked into artifacts: {offenders[:5]}"
