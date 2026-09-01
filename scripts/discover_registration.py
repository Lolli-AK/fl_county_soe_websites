#!/usr/bin/env python3
"""Discover each county's voter-registration page and add it to the manifest.

    python scripts/discover_registration.py                 # crawl -> draft CSV
    python scripts/discover_registration.py --county Leon
    python scripts/discover_registration.py --append        # draft -> targets.csv

A sixth page type, added after the other five were already established and
snapshotting. It is a separate script rather than a re-run of
discover_pages.py for one reason: re-running full discovery would re-pick all
five existing types, and a scoring tweak made for registration would silently
churn targets that have been stable and hand-audited for months. This crawls
for one type and appends; nothing already in targets.csv is touched.

Why the type was added. The non-citizen-voting flag (noncitizen.py) watches
for language that lives on eligibility and registration pages. Of the five
original page types, none is where a county would write "you must provide
proof of citizenship" -- so the flag could have sat at false forever without
that meaning anything. This is the page type that makes it answerable.

Discovery starts from the homepage and the already-verified elections landing
page, since registration is normally one click from either. The statewide
portal (registertovoteflorida.gov) is rejected like any other generic portal:
in Florida it *is* the registration mechanism for many counties, so a GAP here
is a finding -- the county publishes no registration page of its own -- and
not a discovery failure to paper over.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from discover_pages import (  # noqa: E402
    MIN_STRONG, PATTERNS, REG_HUB_PATTERNS, fetch, is_external,
    is_generic_portal, links_of, rank_links, stabilize_url,
)

ROOT = Path(__file__).resolve().parent.parent
TARGETS = ROOT / "manifest" / "targets.csv"
DRAFT = ROOT / "manifest" / "voter_registration_draft.csv"
PTYPE = "voter_registration"

FIELDS = ["county", "batch", "page_type", "url", "external", "notes",
          "verify_status", "http_status", "final_url", "audit_confidence",
          "audit_reason", "flag_for_review"]

log = logging.getLogger("discover_registration")


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

    # The elections landing page usually carries the registration link even when
    # the homepage does not, so its links rank ahead of the homepage's.
    if a["elections"]:
        exclude.add(a["elections"].rstrip("/"))
        er = fetch(a["elections"])
        if er["ok"]:
            links = links_of(er["html"], er["final_url"]) + links

    # Second level. On the common county-government platforms the homepage
    # carries only section headers, so registration is invisible from the top:
    # Hillsborough, Pinellas and Polk each score zero on every homepage link and
    # then resolve immediately once "Voters" or "Voter Services" is opened.
    for _s, hub, _t in rank_links(links, REG_HUB_PATTERNS, exclude,
                                  a["home"], prefer_internal=True)[:2]:
        hr = fetch(hub)
        if hr["ok"] and "html" in hr["ctype"].lower():
            links = links_of(hr["html"], hr["final_url"]) + links

    # Walk the ranked candidates rather than trusting the top one. The best-
    # scoring link is very often the registration *application PDF* served from
    # a document viewer with no .pdf in the href, so it survives the extension
    # filter in links_of and only fails on content-type. Stopping there would
    # lose the county; the HTML page is usually the next candidate down.
    ranked = rank_links(links, PATTERNS[PTYPE], exclude, a["home"],
                        prefer_internal=True)[:5]
    if not ranked:
        return row("", "GAP: no distinct voter-registration page found")

    tried: list[str] = []
    for score, url, text in ranked:
        r = fetch(url)
        tail = url.rstrip("/").split("/")[-1][:30] or url
        if not r["ok"] or (r["status"] or 0) >= 400:
            tried.append(f"{tail} -> {r['status'] or r['error']}")
            continue
        if "html" not in r["ctype"].lower():
            tried.append(f"{tail} -> {r['ctype'][:20]}")
            continue
        if is_generic_portal(r["final_url"]):
            tried.append(f"{tail} -> statewide portal")
            continue
        final = stabilize_url(r["final_url"])
        weak = score < MIN_STRONG[PTYPE]
        note = (f'found via "{text[:40]}" score={score}'
                + (" (weak match — review)" if weak else "")
                + (f" [after {len(tried)} rejected]" if tried else ""))
        return row(final, note, "ok", str(r["status"]), r["final_url"],
                   "weak-score" if weak else "")

    detail = "; ".join(tried[:3])
    return row("", f"GAP: no reachable HTML registration page ({detail})")


def append_to_targets() -> None:
    """Merge the draft into targets.csv, replacing any prior rows for this type."""
    if not DRAFT.exists():
        raise SystemExit(f"missing {DRAFT} — run discovery first")
    draft = {r["county"]: r for r in csv.DictReader(DRAFT.open(encoding="utf-8"))}
    existing = list(csv.DictReader(TARGETS.open(encoding="utf-8")))

    kept = [r for r in existing if r["page_type"] != PTYPE]
    order = {c: i for i, c in enumerate(dict.fromkeys(r["county"] for r in kept))}
    merged = kept + [draft[c] for c in sorted(draft, key=lambda c: order.get(c, 1 << 30))]
    merged.sort(key=lambda r: (order.get(r["county"], 1 << 30),
                               ["homepage", "elections", "voter_registration",
                                "polling", "early_voting",
                                "results"].index(r["page_type"])))

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
    # not the run. A 254-county sweep once sat blocked on its last six with
    # everything else finished and nothing on disk to show for it.
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
    print(f"\n{got}/{len(rows)} counties have a registration page -> {DRAFT}")


if __name__ == "__main__":
    main()
