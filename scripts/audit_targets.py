#!/usr/bin/env python3
"""Verify + audit every URL in manifest/targets.csv.

For each row with a URL: re-fetch it fresh (plain first, escalate to headless on
JS-shell / failure, same as the pipeline), then audit whether the content really
belongs to the intended county + page type by looking for identity keywords
(county name, county seat) and page-type keywords (early voting, polling, ...).

Writes the findings BACK into targets.csv as extra columns (the pipeline ignores
unknown columns, so this is safe):
    verify_status   ok | broken | gap
    http_status
    final_url       (only when it differs from the requested url)
    audit_confidence  confident | likely | uncertain | broken | gap
    audit_reason      why we think it is / isn't the right page
    flag_for_review   yes  (when a human should eyeball it)

Also writes a human-readable summary to logs/audit-report.md.

Usage:
    python scripts/audit_targets.py                # audit all
    python scripts/audit_targets.py --county leon
    python scripts/audit_targets.py --no-headless  # faster, plain only
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import logging
import sys
import threading
from pathlib import Path

import normalize
import snapshot

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "manifest" / "targets.csv"
REPORT = ROOT / "manifest" / "audit-report.md"

log = logging.getLogger("audit")

# Plain fetches parallelize freely, but Playwright's sync API is not safe to drive
# from several threads at once, so headless renders are serialized behind a lock.
_HEADLESS_LOCK = threading.Lock()
_LOG_LOCK = threading.Lock()

# City names add a second identity signal beyond the county name. Both come from
# manifest/counties.csv — the single seed of truth. Nothing here hardcodes a county
# list, so adding or correcting a county is a data edit, not a code change.
#
# Two cities per county, because in Florida they differ: the SOE office is not always
# in the county seat (Brevard's is in Melbourne, seat Titusville; Pinellas's is in
# Largo, seat Clearwater). An SOE page naturally names the office's own city, so
# accepting either is what keeps those counties from auditing as unidentifiable.
SEED = ROOT / "manifest" / "counties.csv"


def _load_cities() -> dict[str, tuple[str, str]]:
    if not SEED.exists():
        return {}
    with SEED.open(newline="", encoding="utf-8") as fh:
        return {r["county"].strip(): (r["seat"].strip(),
                                      (r.get("office_city") or "").strip())
                for r in csv.DictReader(fh)}


CITIES = _load_cities()

# Page-type keywords. First list = strong (specific) signals, second = weak.
#
# `homepage` is the Supervisor of Elections front page, NOT a county government
# homepage — so its strong signals are election-office vocabulary, where the Texas
# version looked for commissioners court / county judge / district clerk.
TYPE_KEYWORDS = {
    "homepage": (["supervisor of elections", "voter registration", "vote by mail",
                  "early voting", "elections office"],
                 ["county", "election", "voter", "ballot"]),
    "elections": (["upcoming election", "election information", "election date",
                   "election calendar", "sample ballot", "election"],
                  ["voter", "voting", "ballot", "registration"]),
    "polling": (["polling place", "polling location", "polling site", "precinct finder",
                 "where do i vote", "where to vote", "election day location",
                 "find my polling", "polling"],
                ["location", "precinct", "election day"]),
    "early_voting": (["early voting", "early vote", "vote early"],
                     ["schedule", "hours", "location", "site"]),
    "results": (["election result", "unofficial result", "election night reporting",
                 "returns", "canvass", "election night", "results"],
                ["reporting", "precinct", "turnout"]),
}

# Signals that a page is an error / wrong page even if HTTP 200.
BAD_SIGNALS = ["page not found", "404 error", "not be found", "no longer available",
               "access denied", "forbidden", "under construction", "account suspended",
               "domain is for sale", "this site can", "temporarily unavailable"]


def audit_content(county: str, page_type: str, text: str, title: str | None,
                  final_url: str, requested_url: str) -> tuple[str, str, bool]:
    """Return (confidence, reason, flag_for_review)."""
    hay = f"{title or ''}\n{text}".lower()
    url_hay = final_url.lower()
    county_l = county.lower()
    seat_l, office_l = (s.lower() for s in CITIES.get(county, ("", "")))

    # Identity: county name, seat or SOE office city, in text/title OR in the
    # (final) domain.
    import re as _re
    county_in_text = county_l in hay
    # Normalize separators so "miami dade" matches "/FL/Miami_Dade/" and "miami-dade".
    county_squash = _re.sub(r"[\s_-]", "", county_l)
    url_squash = _re.sub(r"[\s_-]", "", url_hay)
    county_in_url = county_squash in url_squash
    seat_in_text = bool(seat_l) and seat_l in hay
    office_in_text = bool(office_l) and office_l in hay
    city_in_text = seat_in_text or office_in_text
    # A city name alone identifies a deep page but not a homepage: every SOE
    # homepage names its own city, so that would be circular.
    identity = county_in_text or county_in_url or (city_in_text and page_type != "homepage")

    strong_kw, weak_kw = TYPE_KEYWORDS.get(page_type, ([], []))
    strong_hits = [k for k in strong_kw if k in hay]
    weak_hits = [k for k in weak_kw if k in hay]

    bad_hits = [b for b in BAD_SIGNALS if b in hay]

    # Deep page that redirected to the site root often means the page is gone.
    from urllib.parse import urlparse
    req_p, fin_p = urlparse(requested_url), urlparse(final_url)
    redirected_to_root = (
        req_p.path.rstrip("/") not in ("", fin_p.path.rstrip("/"))
        and fin_p.path.rstrip("/") in ("", "/")
        and len(req_p.path.rstrip("/")) > 1
    )

    idents = []
    if county_in_text:
        idents.append(f"county name '{county}' in page")
    elif county_in_url:
        idents.append(f"'{county}' in domain")
    if seat_in_text:
        idents.append(f"seat '{CITIES[county][0]}' in page")
    elif office_in_text:
        idents.append(f"SOE office city '{CITIES[county][1]}' in page")

    parts = []
    if idents:
        parts.append("; ".join(idents))
    if strong_hits:
        parts.append(f"{page_type} keywords: {', '.join(strong_hits[:4])}")
    elif weak_hits:
        parts.append(f"weak {page_type} keywords: {', '.join(weak_hits[:3])}")
    if title:
        parts.append(f"title=\"{title[:70]}\"")
    reason = " | ".join(parts) if parts else "no identity or topic keywords found"

    flag = False
    if bad_hits:
        confidence = "uncertain"
        reason = f"POSSIBLE ERROR PAGE ({', '.join(bad_hits[:2])}); " + reason
        flag = True
    elif redirected_to_root:
        confidence = "uncertain"
        reason = f"requested deep path redirected to site root ({final_url}); " + reason
        flag = True
    elif identity and strong_hits:
        confidence = "confident"
    elif identity and (weak_hits or page_type == "homepage"):
        confidence = "likely"
    elif identity or strong_hits:
        confidence = "uncertain"
        reason = "only partial match — " + reason
        flag = True
    else:
        confidence = "uncertain"
        flag = True

    # Very thin body (rendered app frame / mostly empty) — worth a look.
    if len(text.strip()) < 200 and not bad_hits:
        reason = f"thin content ({len(text.strip())} chars); " + reason
        if confidence == "confident":
            confidence = "likely"
        else:
            flag = True

    return confidence, reason, flag


def verify_row(row: dict, allow_headless: bool) -> dict:
    url = row["url"].strip()
    county, page_type = row["county"].strip(), row["page_type"].strip()
    if not url:
        return {"verify_status": "gap", "http_status": "", "final_url": "",
                "audit_confidence": "gap", "audit_reason": row.get("notes", ""),
                "flag_for_review": ""}

    result = snapshot.fetch_plain(url)
    render_mode = "plain"
    non_html = result["ok"] and not snapshot._is_html(result["content_type"])
    cleaned_html = (normalize.clean_html(result["html"])
                    if result["html"] and not non_html else "")
    cleaned_text = normalize.extract_text(cleaned_html) if cleaned_html else ""

    escalate = allow_headless and not non_html and (
        (not result["ok"]) or (result["ok"] and normalize.looks_like_js_shell(
            cleaned_text, cleaned_html)))
    if escalate:
        with _HEADLESS_LOCK:
            h = snapshot.fetch_headless(url)
        if h["ok"]:
            result, render_mode = h, "headless"
            cleaned_html = normalize.clean_html(result["html"])
            cleaned_text = normalize.extract_text(cleaned_html)

    status = result["http_status"]
    final_url = result["final_url"]
    title = normalize.extract_title(cleaned_html) if cleaned_html else None

    # Liveness.
    if result["error"] and not result["ok"]:
        vs = "broken"
        conf, reason, flag = "broken", f"fetch failed: {result['error'][:90]}", True
    elif non_html:
        vs = "broken"
        conf, reason, flag = "uncertain", f"non-HTML content ({result['content_type']}) — out of scope", True
    elif status is not None and status >= 400:
        vs = "broken"
        conf, reason, flag = "broken", f"HTTP {status}", True
    else:
        vs = "ok"
        conf, reason, flag = audit_content(county, page_type, cleaned_text, title,
                                            final_url, url)

    out = {
        "verify_status": vs,
        "http_status": "" if status is None else str(status),
        "final_url": final_url if final_url != url else "",
        "audit_confidence": conf,
        "audit_reason": f"[{render_mode}] {reason}",
        "flag_for_review": "yes" if flag else "",
    }
    with _LOG_LOCK:
        log.info("%-11s %-12s %-7s %-10s %s", county, page_type, vs, conf,
                 ("FLAG " if flag else "") + reason[:70])
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--county", action="append", default=None)
    ap.add_argument("--no-headless", action="store_true")
    ap.add_argument("--workers", type=int, default=8,
                    help="concurrent plain fetches (headless is serialized)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s",
                        handlers=[logging.StreamHandler(sys.stdout)])

    with MANIFEST.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    base_fields = ["county", "batch", "page_type", "url", "external", "notes"]
    audit_fields = ["verify_status", "http_status", "final_url",
                    "audit_confidence", "audit_reason", "flag_for_review"]

    counties = {c.lower() for c in args.county} if args.county else None
    todo = []
    for row in rows:
        for f in audit_fields:
            row.setdefault(f, "")
        if counties and row["county"].strip().lower() not in counties:
            continue  # keep any prior audit columns untouched
        todo.append(row)

    log.info("auditing %d rows with %d workers\n", len(todo), args.workers)
    with cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(verify_row, r, not args.no_headless): r for r in todo}
        for f in cf.as_completed(futs):
            futs[f].update(f.result())

    with MANIFEST.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=base_fields + audit_fields)
        w.writeheader()
        w.writerows(rows)
    log.info("\nwrote audit columns -> %s", MANIFEST)

    _write_report(rows, audit_fields)


def _md(cell: str) -> str:
    """Escape a value for a markdown table cell."""
    return (cell or "").replace("|", "\\|").replace("\n", " ")


def _write_report(rows: list[dict], audit_fields: list[str]) -> None:
    """Write a summary grouped by batch and page type.

    Grouped rather than one flat list because at 254 counties a flat dump of every
    flagged row is unreadable, and because what a reviewer needs first is "which
    batch and which page type is weakest" — batch 1 was human-curated, batches 2
    and 3 were auto-discovered, so their error profiles differ.
    """
    from collections import Counter, defaultdict

    PAGE_TYPES = ["homepage", "elections", "polling", "early_voting", "results"]
    total_counties = len({r["county"] for r in rows})
    live = [r for r in rows if r.get("verify_status") == "ok"]
    broken = [r for r in rows if r.get("verify_status") == "broken"]
    gaps = [r for r in rows if r.get("verify_status") == "gap"]
    flagged = [r for r in rows if r.get("flag_for_review") == "yes"]
    batches = sorted({str(r.get("batch", "")).strip() for r in rows} - {""})

    L = [f"# targets.csv audit report", "",
         f"**{total_counties} counties | {len(rows)} rows | {len(live)} live | "
         f"{len(broken)} broken | {len(gaps)} gaps | {len(flagged)} flagged**", ""]

    # ---- coverage by page type, split by batch ------------------------------
    L += ["## Coverage by page type", "",
          "| page type | " + " | ".join(f"batch {b}" for b in batches) + " | total |",
          "|---" * (len(batches) + 2) + "|"]
    for pt in PAGE_TYPES:
        cells = []
        for b in batches:
            sel = [r for r in rows if r["page_type"] == pt
                   and str(r.get("batch", "")).strip() == b]
            got = sum(1 for r in sel if r["url"].strip())
            cells.append(f"{got}/{len(sel)}")
        allsel = [r for r in rows if r["page_type"] == pt]
        L.append(f"| `{pt}` | " + " | ".join(cells) + " | "
                 f"{sum(1 for r in allsel if r['url'].strip())}/{len(allsel)} |")
    L.append("")

    # ---- health by batch ----------------------------------------------------
    L += ["## Health by batch", "",
          "| batch | counties | live | broken | gaps | flagged |", "|---|---|---|---|---|---|"]
    for b in batches:
        sel = [r for r in rows if str(r.get("batch", "")).strip() == b]
        L.append(f"| {b} | {len({r['county'] for r in sel})} | "
                 f"{sum(1 for r in sel if r.get('verify_status') == 'ok')} | "
                 f"{sum(1 for r in sel if r.get('verify_status') == 'broken')} | "
                 f"{sum(1 for r in sel if r.get('verify_status') == 'gap')} | "
                 f"{sum(1 for r in sel if r.get('flag_for_review') == 'yes')} |")
    L.append("")

    # ---- why rows are gaps -------------------------------------------------
    import re as _re
    reasons = Counter(_re.sub(r"[:(].*", "", (r.get("notes") or "")
                              .replace("GAP: ", "")).strip()[:52]
                      for r in gaps)
    if reasons:
        L += ["## Why rows are gaps", "", "| reason | rows |", "|---|---|"]
        for reason, n in reasons.most_common(10):
            L.append(f"| {_md(reason) or '(unspecified)'} | {n} |")
        L.append("")

    # ---- actionable detail, grouped ----------------------------------------
    if broken:
        L += ["## Broken (needs a new URL)", "",
              "| batch | county | page_type | status | reason | url |",
              "|---|---|---|---|---|---|"]
        for r in sorted(broken, key=lambda r: (r.get("batch", ""), r["county"])):
            L.append(f"| {r.get('batch','')} | {r['county']} | {r['page_type']} | "
                     f"{r['http_status']} | {_md(r['audit_reason'])} | {r['url']} |")
        L.append("")

    flagged_only = [r for r in flagged if r not in broken]
    if flagged_only:
        L += [f"## Flagged for review ({len(flagged_only)})", "",
              "Grouped by page type — a whole type flagging together usually means "
              "one systematic discovery problem rather than many unrelated ones.", ""]
        by_type: dict[str, list[dict]] = defaultdict(list)
        for r in flagged_only:
            by_type[r["page_type"]].append(r)
        for pt in PAGE_TYPES:
            sel = by_type.get(pt)
            if not sel:
                continue
            L += [f"### `{pt}` ({len(sel)})", "",
                  "| batch | county | confidence | reason | url |",
                  "|---|---|---|---|---|"]
            for r in sorted(sel, key=lambda r: (r.get("batch", ""), r["county"])):
                L.append(f"| {r.get('batch','')} | {r['county']} | "
                         f"{r['audit_confidence']} | {_md(r['audit_reason'])} | {r['url']} |")
            L.append("")

    REPORT.write_text("\n".join(L) + "\n", encoding="utf-8")
    log.info("wrote report -> %s", REPORT)


if __name__ == "__main__":
    main()
