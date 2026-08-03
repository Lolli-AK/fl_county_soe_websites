#!/usr/bin/env python3
"""fl-county-watch — snapshot pipeline.

Drives fetch -> normalize -> write 3 artifacts (page.html, page.txt, meta.json)
for every row in manifest/targets.csv, then makes ONE git commit per run.

Fetch strategy is plain-first, escalate-on-empty:
  1. plain HTTP via httpx (realistic UA, follow redirects)
  2. if the cleaned page looks like an empty JS shell -> escalate to headless
  3. headless render via Playwright/Chromium, wait for network idle, grab the DOM

Determinism is sacred: page.html / page.txt must be byte-identical across runs
when the site has not changed. The volatile timestamp lives ONLY in meta.json.

Usage:
    python scripts/snapshot.py                 # all targets, then git commit
    python scripts/snapshot.py --no-commit     # write artifacts, skip commit
    python scripts/snapshot.py --county harris  # one county (repeatable)
    python scripts/snapshot.py --county harris --page-type results
    python scripts/snapshot.py --no-headless   # never escalate (debug/offline)
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import hashlib
import json
import logging
import random
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

import normalize

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "manifest" / "targets.csv"
SNAPSHOTS = ROOT / "snapshots"
LOGS = ROOT / "logs"
CONFIG_PATH = ROOT / "config.json"
# Written incrementally during a run so --resume can skip finished targets.
CHECKPOINT = ROOT / "logs" / "checkpoint.json"


def _load_config() -> dict:
    """Load fetch knobs from config.json; fall back to built-in defaults."""
    defaults = {
        "js_shell_min_chars": 500,
        "plain_timeout_seconds": 30,
        "headless_timeout_ms": 45000,
        "plain_retries": 2,
        "hydration_settle_ms": 2000,
        "hydration_max_wait_ms": 6000,
        "interstitial_max_wait_ms": 45000,
        "workers": 8,
        "request_delay_ms": 250,
        "request_jitter_ms": 250,
    }
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8")).get("fetch", {})
        defaults.update({k: v for k, v in cfg.items() if k in defaults})
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return defaults


CONFIG = _load_config()

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
# A realistic, COMPLETE browser header set — not just a User-Agent. Bot protection
# fingerprints the whole request: with only UA+Accept, Imperva served Aransas
# County a 212-byte challenge stub and Cloudflare 403'd Delta County, while the
# full set below returns their real pages (33 KB and 104 KB respectively). The
# `br` encoding requires the `brotli` package (see requirements.txt), otherwise
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

# HTTP/2 is enabled deliberately, not incidentally. Several county sites behind
# Akamai/Granicus answer 403 to HTTP/1.1 and 200 to HTTP/2 — Galveston, Brazoria,
# Henderson, Johnson, Nueces and Reeves were all recorded as "blocked" purely
# because httpx defaults to 1.1. Every real browser negotiates h2, so this is
# about being a standards-normal client, not about evading anything.
PLAIN_TIMEOUT = float(CONFIG["plain_timeout_seconds"])
HEADLESS_TIMEOUT_MS = int(CONFIG["headless_timeout_ms"])
PLAIN_RETRIES = int(CONFIG["plain_retries"])  # total plain attempts on transient errors
HYDRATION_SETTLE_MS = int(CONFIG["hydration_settle_ms"])
# Upper bound on the post-networkidle wait (see _wait_for_dom_quiescence).
HYDRATION_MAX_WAIT_MS = int(CONFIG["hydration_max_wait_ms"])

log = logging.getLogger("snapshot")

# Playwright's sync API is not safe to drive from several threads at once, and
# parallel renders would also skew hydration timing. Serialize them.
_HEADLESS_LOCK = threading.Lock()

REQUEST_DELAY_MS = int(CONFIG["request_delay_ms"])
REQUEST_JITTER_MS = int(CONFIG["request_jitter_ms"])


def _throttle() -> None:
    """Politeness pause before each fetch, with jitter.

    At 254 counties a run makes well over a thousand requests against small
    county servers; jitter avoids a synchronized stampede from the worker pool.
    """
    delay = REQUEST_DELAY_MS + random.uniform(0, REQUEST_JITTER_MS)
    if delay > 0:
        time.sleep(delay / 1000.0)

# Bot-check interstitials ("Just a moment…", Cloudflare's challenge page). These
# resolve themselves after a second or two in a real browser, but if we capture
# while one is still up we store the interstitial instead of the county's page —
# and the next run stores the real page, producing an enormous phantom diff.
_INTERSTITIAL_MARKERS = (
    "just a moment", "checking your browser", "verifying you are human",
    "cf-browser-verification", "challenge-platform", "cf_chl",
    "enable javascript and cookies to continue", "ddos protection by",
    "attention required! | cloudflare",
    # Seen on GitHub-runner IPs (Cherokee County) and Delta's Cloudflare check.
    "security check", "verifying your browser", "performing security verification",
    "performing a brief security check", "security service to protect",
)
# Bot challenges take noticeably longer to clear from a datacenter IP than from a
# residential one: the same Cloudflare check that passes in ~2s locally was still
# mid-verification after 20s on a GitHub Actions runner. Configurable so it can be
# raised further if runs keep coming back blocked.
INTERSTITIAL_MAX_WAIT_MS = int(CONFIG["interstitial_max_wait_ms"])


def _looks_like_interstitial(html: str) -> bool:
    head = (html or "")[:6000].lower()
    return any(m in head for m in _INTERSTITIAL_MARKERS)


def _wait_for_dom_quiescence(page, settle_ms: int,
                             max_ms: int = HYDRATION_MAX_WAIT_MS) -> None:
    """Wait for the DOM to stop changing, within a bounded window.

    Client-side scripts keep mutating pages after `networkidle`: Kerr recomputes
    an election calendar's year, Cochran interpolates {{YEAR}} placeholders, Delta
    loads its content sections late. Sampling until two consecutive snapshots
    match handles those far better than a fixed sleep.

    The window is deliberately CAPPED rather than open-ended. Some pages never
    truly settle — Tarrant's homepage renders formatted news dates, then a late
    (~10s) AJAX pass replaces them one by one with raw `Date.toString()` values,
    so a longer wait lands mid-rewrite and is *less* reproducible than a shorter
    one. Capturing the early-stable state is the more deterministic choice.
    """
    prev = None
    waited = 0
    step = 500
    while waited < max_ms:
        try:
            cur = hashlib.sha256(page.content().encode("utf-8")).hexdigest()
        except Exception:  # noqa: BLE001 - mid-navigation, sample again
            cur = None
        # Require BOTH a minimum elapsed wait and two matching samples. Quiescence
        # alone can exit too early: Tarrant's news-date rewrite fires a couple of
        # seconds in, after the DOM has already looked stable for one interval.
        if cur is not None and cur == prev and waited >= settle_ms:
            return
        prev = cur
        page.wait_for_timeout(step)
        waited += step
    log.debug("DOM still changing after %dms: %s", max_ms, page.url)


def _wait_out_interstitial(page) -> None:
    """Poll until a bot-check interstitial clears (or we give up)."""
    waited = 0
    step = 1000
    while waited < INTERSTITIAL_MAX_WAIT_MS:
        try:
            if not _looks_like_interstitial(page.content()):
                return
        except Exception:  # noqa: BLE001 - mid-navigation; just keep waiting
            pass
        page.wait_for_timeout(step)
        waited += step
    log.warning("interstitial did not clear after %dms: %s",
                INTERSTITIAL_MAX_WAIT_MS, page.url)


# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #
def _fetch_once(url: str, http2: bool) -> dict:
    """One plain HTTP attempt over the given protocol version."""
    _throttle()
    with httpx.Client(headers=HEADERS, follow_redirects=True,
                      timeout=PLAIN_TIMEOUT, verify=True, http2=http2) as client:
        resp = client.get(url)
    chain = [str(r.url) for r in resp.history] + [str(resp.url)]
    return {
        "ok": True,
        "html": resp.text,
        "final_url": str(resp.url),
        "redirect_chain": chain,
        "http_status": resp.status_code,
        "content_type": resp.headers.get("content-type"),
        "error": None,
    }


def fetch_plain(url: str) -> dict:
    """Plain HTTP fetch, trying BOTH protocol versions before giving up.

    Neither version works everywhere, and the split is not predictable from the
    URL:

      * HTTP/2 only — Akamai/Granicus fronted sites answer 403 to HTTP/1.1.
        Galveston, Brazoria, Henderson, Johnson, Nueces and Reeves were all
        recorded as "blocked" purely because httpx defaults to 1.1.
      * HTTP/1.1 only — Wichita County and Delta's results page do the opposite,
        403-ing an h2 request and serving 200 over 1.1.

    So we try h2 first (what a modern browser negotiates) and fall back to 1.1 on a
    4xx. 5xx is retried on the same protocol, since that is a transient server
    fault rather than a protocol mismatch. Returns a result dict; never raises for
    HTTP status.
    """
    last_exc = None
    last_result = None
    for http2 in (True, False):
        for attempt in range(1, PLAIN_RETRIES + 1):
            try:
                result = _fetch_once(url, http2=http2)
            except Exception as exc:  # noqa: BLE001 - record & maybe retry
                last_exc = exc
                log.warning("plain fetch attempt %d/%d (h2=%s) failed for %s: %s",
                            attempt, PLAIN_RETRIES, http2, url, exc)
                continue
            status = result["http_status"]
            # 5xx is transient by definition — retry the same protocol rather than
            # recording a server hiccup as the page's content (Childress returns
            # intermittent 500s).
            if status >= 500 and attempt < PLAIN_RETRIES:
                log.warning("plain fetch got HTTP %d for %s (attempt %d/%d), retrying",
                            status, url, attempt, PLAIN_RETRIES)
                continue
            last_result = result
            if status < 400:
                return result
            break  # 4xx on this protocol — fall through and try the other one
        if last_result is not None and (last_result["http_status"] or 0) < 400:
            return last_result
    if last_result is not None:
        return last_result  # best 4xx/5xx we saw, recorded honestly
    return {
        "ok": False,
        "html": "",
        "final_url": url,
        "redirect_chain": [url],
        "http_status": None,
        "content_type": None,
        "error": f"plain_fetch_error: {type(last_exc).__name__}: {last_exc}",
    }


def fetch_headless(url: str) -> dict:
    """Headless render via Playwright/Chromium. Waits for network idle."""
    _throttle()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        return {
            "ok": False, "html": "", "final_url": url, "redirect_chain": [url],
            "http_status": None, "content_type": None,
            "error": f"playwright_not_installed: {exc}",
        }
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                # Pin the viewport: some CMS widgets emit responsive class names
                # (CivicPlus renders `widget ... wide` vs `... narrow`), so an
                # unpinned window size makes the captured DOM flap between runs.
                context = browser.new_context(user_agent=USER_AGENT,
                                              reduced_motion="reduce",
                                              viewport={"width": 1280, "height": 900})
                # Neutralize animation timers BEFORE any page script runs. JS
                # carousels/sliders/clocks advance via setInterval and continue
                # mutating the DOM after "networkidle", so the captured DOM would
                # otherwise reflect a random animation frame (non-deterministic
                # `class="active"`, transforms, etc.). Content loading almost
                # never depends on setInterval, so this is safe and keeps the
                # headless HTML stable across runs.
                # Also pin Math.random to a constant: JS banners/galleries shuffle
                # their slide order on load (Lubbock) and some widgets mint random
                # element ids from it, both of which would otherwise churn.
                context.add_init_script(
                    "window.setInterval = function(){ return 0; };"
                    "Math.random = function(){ return 0.42; };"
                )
                page = context.new_page()
                resp = page.goto(url, wait_until="domcontentloaded",
                                 timeout=HEADLESS_TIMEOUT_MS)
                # Give client-side rendering a chance to settle.
                try:
                    page.wait_for_load_state("networkidle",
                                             timeout=HEADLESS_TIMEOUT_MS)
                except Exception:  # noqa: BLE001 - networkidle can time out; DOM may still be fine
                    log.debug("networkidle wait timed out for %s", url)
                # Let client-side hydration finish. Without this, capture can land
                # mid-render — Cochran's homepage was snapshotted with its literal
                # {{YEAR}}/{{COUNTY}} template placeholders still un-interpolated,
                # which diffs against a fully-rendered capture.
                # Cloudflare-style challenge still up? Wait it out first.
                if _looks_like_interstitial(page.content()):
                    _wait_out_interstitial(page)
                # Then wait for client-side rendering to actually finish.
                _wait_for_dom_quiescence(page, HYDRATION_SETTLE_MS)
                html = page.content()
                final_url = page.url
                status = resp.status if resp else None
                ctype = None
                if resp:
                    ctype = resp.headers.get("content-type")
                return {
                    "ok": True, "html": html, "final_url": final_url,
                    "redirect_chain": [url, final_url] if final_url != url else [url],
                    "http_status": status, "content_type": ctype, "error": None,
                }
            finally:
                browser.close()
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False, "html": "", "final_url": url, "redirect_chain": [url],
            "http_status": None, "content_type": None,
            "error": f"headless_error: {type(exc).__name__}: {exc}",
        }


# --------------------------------------------------------------------------- #
# Per-target processing
# --------------------------------------------------------------------------- #
def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_html(content_type: str | None) -> bool:
    """True if the response looks like HTML/XML we should parse.

    Non-HTML bodies (PDF, images, octet-stream) are out of scope and, worse,
    contain volatile binary bytes that would diff every run and parse to garbage.
    A missing content-type is treated as HTML (best effort)."""
    if not content_type:
        return True
    ct = content_type.lower()
    if any(t in ct for t in ("html", "xml", "text/plain")):
        return True
    return False


def process_target(row: dict, fetched_at: str, allow_headless: bool) -> dict:
    """Fetch + normalize one target; write artifacts; return summary for logging."""
    county = row["county"].strip()
    page_type = row["page_type"].strip()
    url = row["url"].strip()
    external = str(row.get("external", "")).strip().lower() in ("true", "1", "yes")

    out_dir = SNAPSHOTS / county.lower().replace(" ", "_") / page_type
    out_dir.mkdir(parents=True, exist_ok=True)

    result = fetch_plain(url)
    render_mode = "plain"

    # Guard: don't parse non-HTML bodies (PDFs etc. are out of scope and volatile).
    non_html = result["ok"] and not _is_html(result["content_type"])
    if non_html and result["error"] is None:
        result["error"] = f"non_html_content: {result['content_type']}"

    cleaned_html = (normalize.clean_html(result["html"])
                    if result["html"] and not non_html else "")
    cleaned_text = normalize.extract_text(cleaned_html) if cleaned_html else ""

    # Escalate to headless when the plain path is inadequate:
    #  - plain fetch only got a JS shell (empty / "enable JavaScript"), OR
    #  - plain fetch failed outright (some county sites, e.g. Webb, reset non-
    #    browser clients via bot protection but serve a real browser fine).
    should_escalate = allow_headless and not non_html and (
        (not result["ok"])
        or (result["ok"] and normalize.looks_like_js_shell(
            cleaned_text, cleaned_html, min_chars=CONFIG["js_shell_min_chars"]))
        # A bot-check interstitial can be wordy enough to pass the JS-shell test,
        # so detect it explicitly — only the headless path can wait it out.
        or _looks_like_interstitial(result["html"])
    )

    if should_escalate:
        log.info("escalating to headless: %s/%s (%s)", county, page_type, url)
        with _HEADLESS_LOCK:
            h_result = fetch_headless(url)
        if h_result["ok"]:
            result = h_result
            render_mode = "headless"
            cleaned_html = normalize.clean_html(result["html"])
            cleaned_text = normalize.extract_text(cleaned_html)
        else:
            # Headless failed; keep the plain result but note it.
            log.warning("headless failed for %s/%s: %s",
                        county, page_type, h_result["error"])
            if result["error"] is None:
                result["error"] = h_result["error"]

    title = normalize.extract_title(cleaned_html) if cleaned_html else None
    byte_size = len(cleaned_html.encode("utf-8"))

    # If what we captured is still a bot challenge, say so explicitly. Otherwise
    # the artifact looks like a tiny legitimate page and an analyst could read it
    # as "the county took this page down". This happens far more from datacenter
    # IPs (GitHub Actions runners) than from a residential connection.
    if cleaned_text and _looks_like_interstitial(cleaned_html):
        marker = "bot_challenge_not_cleared: captured security-check page, not content"
        result["error"] = f"{marker}; {result['error']}" if result["error"] else marker

    meta = {
        "county": county,
        "page_type": page_type,
        "requested_url": url,
        "final_url": result["final_url"],
        "redirect_chain": result["redirect_chain"],
        "http_status": result["http_status"],
        "content_type": result["content_type"],
        "render_mode": render_mode,
        "external": external,
        "fetched_at": fetched_at,
        "html_sha256": _sha256(cleaned_html),
        "text_sha256": _sha256(cleaned_text),
        "byte_size": byte_size,
        "title": title,
        "error": result["error"],
    }

    (out_dir / "page.html").write_text(cleaned_html, encoding="utf-8")
    (out_dir / "page.txt").write_text(cleaned_text, encoding="utf-8")
    (out_dir / "meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    status = "ERROR" if result["error"] else f"{result['http_status']}"
    log.info("%-12s %-12s %-8s %s [%s bytes] %s",
             county, page_type, render_mode, status, byte_size,
             "(external)" if external else "")
    return {"county": county, "page_type": page_type, "render_mode": render_mode,
            "status": status, "error": result["error"]}


# --------------------------------------------------------------------------- #
# git datastore
# --------------------------------------------------------------------------- #
def git_commit(fetched_at: str, n_targets: int) -> None:
    def run(*args):
        return subprocess.run(["git", *args], cwd=ROOT, check=True,
                              capture_output=True, text=True)

    run("add", "-A")
    status = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                            capture_output=True, text=True).stdout.strip()
    if not status:
        log.info("git: nothing to commit (zero diff) — determinism holding")
        return
    msg = f"snapshot run {fetched_at} ({n_targets} targets)"
    run("commit", "-m", msg)
    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                          capture_output=True, text=True).stdout.strip()
    log.info("git: committed %s — %s", head, msg)


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def load_targets(counties: list[str] | None, page_type: str | None,
                 batch: str | None = None) -> list[dict]:
    if not MANIFEST.exists():
        sys.exit(f"manifest not found: {MANIFEST} (run Phase 1 / discover.py first)")
    rows = []
    with MANIFEST.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if not row.get("url", "").strip():
                continue  # gap row: page type doesn't exist for this county
            if counties and row["county"].strip().lower() not in counties:
                continue
            if page_type and row["page_type"].strip().lower() != page_type.lower():
                continue
            if batch and str(row.get("batch", "")).strip() != str(batch):
                continue
            rows.append(row)
    return rows


def _target_key(row: dict) -> str:
    return f"{row['county'].strip()}/{row['page_type'].strip()}"


def prune_stale_artifacts() -> int:
    """Delete artifact directories that the manifest no longer claims.

    When a row becomes a gap (or a county/page type is removed), its old
    page.html/page.txt/meta.json would otherwise linger and be mistaken for a
    current capture. The invariant this maintains — a gap has no directory — is
    what makes a county's tree readable at a glance, and it's asserted by
    tests/test_manifest.py.
    """
    import shutil

    if not SNAPSHOTS.exists() or not MANIFEST.exists():
        return 0
    wanted: set[tuple[str, str]] = set()
    with MANIFEST.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row.get("url", "").strip():
                slug = row["county"].strip().lower().replace(" ", "_")
                wanted.add((slug, row["page_type"].strip()))

    removed = 0
    for page_dir in sorted(SNAPSHOTS.glob("*/*")):
        if not page_dir.is_dir():
            continue
        if (page_dir.parent.name, page_dir.name) not in wanted:
            shutil.rmtree(page_dir)
            log.info("pruned stale artifacts: %s", page_dir.relative_to(ROOT))
            removed += 1
    # Drop county directories left empty by the above.
    for county_dir in sorted(SNAPSHOTS.glob("*")):
        if county_dir.is_dir() and not any(county_dir.iterdir()):
            county_dir.rmdir()
    return removed


def load_checkpoint(path: Path) -> set[str]:
    """Target keys already completed by an interrupted run of this manifest."""
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return set(data.get("done", []))
    except (json.JSONDecodeError, OSError):
        log.warning("unreadable checkpoint %s — starting fresh", path)
        return set()


def main() -> None:
    ap = argparse.ArgumentParser(description="Snapshot FL county election pages.")
    ap.add_argument("--county", action="append", default=None,
                    help="limit to county (repeatable, case-insensitive)")
    ap.add_argument("--page-type", default=None,
                    help="limit to one page_type (homepage/elections/polling/...)")
    ap.add_argument("--batch", default=None,
                    help="limit to a batch label from the manifest (1/2/3)")
    ap.add_argument("--no-headless", action="store_true",
                    help="never escalate to headless (offline/debug)")
    ap.add_argument("--no-commit", action="store_true",
                    help="write artifacts but do not git commit")
    ap.add_argument("--workers", type=int, default=CONFIG["workers"],
                    help="concurrent PLAIN fetches; headless is always serialized")
    ap.add_argument("--resume", action="store_true",
                    help="skip targets already completed per the checkpoint file")
    args = ap.parse_args()

    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    LOGS.mkdir(exist_ok=True)
    log_path = LOGS / f"run-{fetched_at.replace(':', '').replace('-', '')}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout),
                  logging.FileHandler(log_path, encoding="utf-8")],
    )

    counties = [c.lower() for c in args.county] if args.county else None
    # Only prune on a full run: a filtered run legitimately leaves other counties'
    # artifacts in place.
    if not (counties or args.page_type or args.batch):
        n = prune_stale_artifacts()
        if n:
            log.info("pruned %d artifact dirs no longer in the manifest", n)
    targets = load_targets(counties, args.page_type, args.batch)

    done: set[str] = load_checkpoint(CHECKPOINT) if args.resume else set()
    if done:
        before = len(targets)
        targets = [r for r in targets if _target_key(r) not in done]
        log.info("--resume: skipping %d already-completed targets",
                 before - len(targets))

    log.info("run %s — %d targets, workers=%d, headless=%s",
             fetched_at, len(targets), args.workers, not args.no_headless)

    # Concurrency is applied to the PLAIN path only; headless renders are
    # serialized inside process_target via _HEADLESS_LOCK. Running Chromium
    # renders in parallel would contend for CPU and shift hydration timing, which
    # is exactly the non-determinism this project spent so long eliminating.
    completed: list[str] = list(done)
    lock = threading.Lock()

    def run_one(row: dict) -> None:
        try:
            process_target(row, fetched_at, allow_headless=not args.no_headless)
            with lock:
                completed.append(_target_key(row))
                # Persist progress as we go so an interrupted run can resume.
                CHECKPOINT.write_text(json.dumps(
                    {"fetched_at": fetched_at, "done": completed}, indent=0),
                    encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 - one bad target must not kill the run
            log.exception("unhandled error for %s/%s: %s",
                          row.get("county"), row.get("page_type"), exc)

    if args.workers <= 1:
        for row in targets:
            run_one(row)
    else:
        with cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
            list(pool.map(run_one, targets))

    if not args.no_commit:
        git_commit(fetched_at, len(targets))
    else:
        log.info("--no-commit: skipping git commit")

    # A clean finish invalidates the checkpoint; a crash leaves it for --resume.
    CHECKPOINT.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
