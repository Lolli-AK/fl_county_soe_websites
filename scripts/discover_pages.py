#!/usr/bin/env python3
"""Phase 1 — discover the four election page types per county.

Runs a TWO-LEVEL crawl from each county's Supervisor of Elections homepage:

    SOE homepage  --score-->  elections / voter-info landing
    that landing (+ homepage)  --score-->  polling / early_voting / results

Candidates are keyword-scored, PDF links are skipped (out of scope), and each pick
is confirmed with a real fetch before being written.

**How this differs from the Texas version.** There, discovery started at a general
county government homepage and its hardest problem was *finding the elections
function at all* — buried under "Departments", or handed off to a separate portal
on another domain. Florida starts on the elections portal, because the Supervisor of
Elections is a standalone elected office with its own site. Two consequences:

  - The "walk the Departments nav" and "promote a dedicated elections portal"
    escalations are gone; they solved a problem that does not exist here.
  - Scoring is retuned for a single-purpose agency. On a county homepage the word
    "election" is a strong signal. On an SOE site *every* link says "election", so
    the generic weights that worked for Texas are near-useless and the specific
    phrases ("upcoming elections", "early voting sites", "precinct finder") carry
    the discrimination instead.

A page type whose best candidate is just the homepage or the elections landing again
is recorded as a GAP ("folded into ...") rather than duplicating the same URL across
rows — a missing distinct page is expected data, not an error.

Input:  manifest/batch<N>_homepages.csv   (from discover_homepages.py)
Output: manifest/batch<N>_targets_draft.csv  county,batch,page_type,url,external,notes

Usage:
    python scripts/discover_pages.py
    python scripts/discover_pages.py --county Leon --workers 6
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import logging
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
IN_CSV = ROOT / "manifest" / "batch1_homepages.csv"
OUT_CSV = ROOT / "manifest" / "batch1_targets_draft.csv"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
# A realistic, COMPLETE browser header set — not just a User-Agent. Bot protection
# fingerprints the whole request; see the README for the measured effect. The `br`
# encoding requires the `brotli` package (see requirements.txt).
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

# Scoring patterns. (keyword, weight) — negative weights push candidates away.
#
# Tuned for Supervisor of Elections sites, where bare "election" is noise because it
# appears in every nav item. The discriminating tokens are the specific ones.
PATTERNS: dict[str, list[tuple[str, int]]] = {
    "elections": [
        ("upcoming election", 13), ("election information", 12),
        ("upcoming elections", 13), ("election dates", 11),
        ("voter information", 10), ("election calendar", 10),
        ("next election", 10), ("elections", 5), ("election", 4),
        ("voter", 4), ("voting", 4), ("ballot", 2),
        # "Voter Information *Card*" is a mailed document, not a landing page, but
        # it out-scores the real nav item on the phrase "voter information" alone.
        ("information card", -14), ("id card", -10),
        # Everything below is a *sibling* page on an SOE site, not the landing page.
        ("result", -8), ("early voting", -8), ("polling", -8),
        ("candidate", -6), ("archive", -8), ("past election", -10),
        ("history", -5), ("financial", -8), ("campaign", -6),
        ("poll worker", -8), ("employment", -8),
        # A registration form/application is not the elections landing page.
        ("application", -9), ("registration application", -6), ("form", -5),
        # Sibling topics on an SOE site. Each of these is a legitimate page that
        # scores well on generic election vocabulary and would otherwise beat the
        # actual landing page; Leon's "Voter ID" and "Check Your Info" both did.
        ("voter id", -12), ("check your info", -12), ("registration", -6),
        ("absentee", -10), ("vote by mail", -10), ("vote-by-mail", -10),
        ("sample ballot", -8), ("address change", -10), ("name change", -10),
        ("party change", -10), ("felon", -10), ("restoration", -10),
        ("military", -10), ("overseas", -10), ("student", -8),
        ("petition", -10), ("district map", -8), ("accessibility", -8),
        ("public record", -10), ("statistics", -8), ("data", -6),
        ("contact", -8), ("about", -6), ("faq", -6), ("news", -6),
    ],
    "polling": [
        ("polling place", 14), ("polling location", 14), ("precinct finder", 13),
        ("find my precinct", 13), ("precinct lookup", 12), ("where to vote", 12),
        ("election day polling", 14), ("election day location", 13),
        ("my polling place", 14), ("polling site", 13), ("precinct map", 8),
        ("where do i vote", 12), ("find my polling", 14),
        ("polling", 8), ("precinct", 5),
        ("early", -10), ("result", -8), ("drop box", -4), ("worker", -8),
    ],
    "early_voting": [
        ("early voting location", 15), ("early voting site", 15),
        ("early voting schedule", 14), ("early voting dates", 13),
        ("early voting hours", 13), ("early voting", 12), ("early vote", 11),
        ("vote early", 11),
        ("result", -8), ("election day", -5), ("vote by mail", -6),
        ("worker", -8),
    ],
    "results": [
        ("election result", 14), ("unofficial result", 14),
        ("election night reporting", 13), ("election returns", 12),
        ("current results", 13), ("results", 8), ("returns", 6),
        ("canvass", 5), ("election night", 11),
        ("search result", -14), ("polling", -8), ("early", -6),
        ("past election", -4),
    ],
}

# Link text/urls that are navigation chrome, never a target page.
SKIP_LINK_HINTS = ("javascript:", "mailto:", "tel:", "#", "/search", "search.results",
                   "/login", "/rss", "facebook.com", "twitter.com", "youtube.com",
                   "instagram.com", "linkedin.com", "x.com/", "t.co/", "nextdoor.com",
                   "civicplus.com", "governmentjobs.com", "/myaccount", "/sitemap",
                   "/privacy", "/copyright", "/accessibility", "quicklinks.aspx")

# "Hub" pages that hold the current election's details. Some SOE sites have no
# standing "Polling Places" page — the election-day sites and early-voting schedule
# live inside a per-election page such as "November 3, 2026 General Election". We
# crawl these as a third level so those targets are actually found.
HUB_PATTERNS = [
    ("current election", 12), ("upcoming election", 11), ("election information", 9),
    ("general election", 8), ("primary election", 7), ("municipal election", 6),
    ("election day information", 10), ("next election", 8), ("2026", 5),
    ("archive", -8), ("past", -8), ("result", -4), ("financial", -10),
]

# Generic statewide/national portals. These are real sites, but they are STATE-level
# and identical for every county — capturing them 67 times would add no per-county
# signal and would misrepresent a county as having a page it does not have. They are
# rejected as candidates outright; if a page type has nothing but these, it is
# recorded as a gap instead.
#
# (County-specific external domains like a county's own Clarity results sub-path are
# NOT in here and remain valid picks.)
GENERIC_PORTAL_HINTS = (
    # Statewide (Florida Department of State / Division of Elections and friends).
    # registertovoteflorida.gov and the DOS voter lookup are linked by nearly every
    # SOE site; floridaelectionwatch.gov is the STATE's results portal, so it must
    # not be captured as a county's `results` page.
    "dos.fl.gov", "dos.myflorida.com", "floridados.gov", "election.dos.state.fl.us",
    "registertovoteflorida.gov", "elections.myflorida.com",
    "dos.elections.myflorida.com", "floridaelectionwatch.gov",
    "voterfocus.com/ws/wsauth", "myflorida.com", "flgov.com", "flsenate.gov",
    "myfloridalegal.com", "fsase.org", "flrules.org", "leg.state.fl.us",
    # National third-party voter-info sites. Especially important to exclude because
    # several have "vote" in the domain and would otherwise be promoted as a county's
    # elections portal (vote411.org is the League of Women Voters, not a county).
    "vote411.org", "lwv.org", "vote.org", "ballotpedia.org", "rockthevote",
    "usa.gov", "eac.gov", "votesmart.org", "nass.org", "turbovote",
    "headcount.org", "when-we-all-vote", "voteamerica", "usvotefoundation.org",
    "overseasvotefoundation.org", "fairelectionscenter.org", "fvap.gov",
    "canivote.org", "nvrhotline.org",
    # Voting-system VENDOR marketing sites. SOE pages routinely link "how to use this
    # machine" material, and those pages are stuffed with election vocabulary, so
    # they outscore the real county page.
    "essvote.com", "hartintercivic.com", "dominionvoting.com", "clearballot.com",
    "unisynvoting.com", "verifiedvoting.org", "esands.com", "vrsystems.com",
    # Translation proxies. Never a valid target in themselves, AND they launder every
    # other entry in this list: Google Translate rewrites a host's dots to dashes
    # ("www-vote411-org.translate.goog"), which slips past a plain substring check.
    "translate.goog", "translate.google.com",
)

log = logging.getLogger("discover_pages")

# Set from --batch so emitted rows carry the right label.
BATCH_LABEL = "1"


def is_generic_portal(url: str) -> bool:
    """True if the URL is a statewide/national/vendor portal, not a county page.

    Also catches translation-proxy laundering by testing the de-dashed host.
    """
    low = url.lower()
    if any(g in low for g in GENERIC_PORTAL_HINTS):
        return True
    host = urlparse(low).netloc
    dedashed = host.replace("-", ".")
    return any(g in dedashed for g in GENERIC_PORTAL_HINTS)


def _fetch_plain(url: str) -> dict:
    """Plain fetch, trying HTTP/2 then HTTP/1.1 (neither works everywhere)."""
    last = None
    for http2 in (True, False):
        try:
            with httpx.Client(headers=HEADERS, follow_redirects=True,
                              timeout=TIMEOUT, http2=http2) as c:
                r = c.get(url)
            last = {"ok": True, "status": r.status_code, "html": r.text,
                    "final_url": str(r.url),
                    "ctype": r.headers.get("content-type", ""), "error": None}
            if r.status_code < 400:
                return last
        except Exception as exc:  # noqa: BLE001
            last = {"ok": False, "status": None, "html": "", "final_url": url,
                    "ctype": "", "error": type(exc).__name__}
    return last


# Discovery has to clear the same two hurdles the snapshot pipeline does: some sites
# 403 non-browser clients, and others build their whole nav menu in JavaScript, so a
# plain fetch yields a page with no links to score. Escalate to Chromium in both
# cases, otherwise those counties silently look like they have no election pages.
_ANCHOR_RE = re.compile(r"<a\s[^>]*href=", re.I)


def fetch(url: str, allow_headless: bool = True) -> dict:
    r = _fetch_plain(url)
    needs_headless = (
        not r["ok"]
        or (r["status"] or 0) >= 400
        or ("html" in r["ctype"].lower() and len(_ANCHOR_RE.findall(r["html"])) < 5)
    )
    if allow_headless and needs_headless:
        try:
            import snapshot  # local module; imports playwright lazily
            h = snapshot.fetch_headless(url)
            status = h.get("http_status")
            if h["ok"] and (status or 200) < 400 and h["html"]:
                return {"ok": True, "status": status or 200, "html": h["html"],
                        "final_url": h["final_url"],
                        "ctype": h.get("content_type") or "text/html",
                        "error": None, "headless": True}
        except Exception:  # noqa: BLE001 - keep the plain result on any failure
            pass
    return r


def links_of(html: str, base: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html or "", "lxml")
    out = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        low = href.lower()
        if not href or any(h in low for h in SKIP_LINK_HINTS):
            continue
        if low.endswith(".pdf"):  # PDFs are out of scope
            continue
        absu = urljoin(base, href)
        if not absu.startswith("http"):
            continue
        if any(g in absu.lower() for g in GENERIC_PORTAL_HINTS):
            continue
        text = " ".join(a.get_text(separator=" ").split())
        out.append((text, absu))
    return out


# Link labels that are unambiguously the elections landing page on an SOE site. An
# exact label match beats a longer, noisier link that happens to stack keywords.
EXACT_ELECTION_LABELS = {
    "elections", "election", "upcoming elections", "election information",
    "elections information", "voter information", "election dates",
    "elections & voting", "voting & elections", "elections and voting",
    "voting and elections", "voters", "voter info", "election calendar",
    "upcoming election", "current elections", "elections home",
}


# URL shapes that carry a per-election / per-event identifier. These go stale every
# cycle — a CivicPlus "Calendar.aspx?EID=359" entry is *this* election's early-voting
# notice, not the county's standing early-voting page — so a snapshot of one would
# start 404ing mid-series. Same reasoning as normalizing Clarity deep links to the
# stable county index: prefer the durable page, and take the volatile one only if
# nothing else exists.
_EPHEMERAL_URL_RE = re.compile(
    r"(calendar\.aspx|[?&](eid|aid|nid|iid)=|/calendar/?$|/event/|/events/\d)", re.I)


def score(text: str, url: str, pats: list[tuple[str, int]]) -> int:
    # Weight anchor text higher than the URL path; URLs often contain generic words.
    t, u = text.lower(), url.lower()
    s = 0
    for kw, w in pats:
        if kw in t:
            s += w
        elif kw in u.replace("-", " ").replace("_", " ").replace("/", " "):
            s += max(1, w // 2) if w > 0 else w
    if pats is PATTERNS["elections"] and t.strip() in EXACT_ELECTION_LABELS:
        s += 10
    if s > 0 and _EPHEMERAL_URL_RE.search(url):
        s -= 12
    return s


def rank_links(links: list[tuple[str, str]], pats: list[tuple[str, int]],
               exclude: set[str], home: str | None = None,
               prefer_internal: bool = False) -> list[tuple[int, str, str]]:
    """Score and rank links. Dedups by URL keeping the best score."""
    best: dict[str, tuple[int, str, str]] = {}
    for text, url in links:
        if url.rstrip("/") in exclude:
            continue
        s = score(text, url, pats)
        if s <= 0:
            continue
        # Tie-break toward pages on the SOE's own site. Weighted harder than in the
        # Texas version: there, a county legitimately ran elections on a separate
        # domain, so external could not be penalised much. Here we already start on
        # the elections domain, so an off-site candidate is usually a vendor app or
        # the county government site rather than the page we want.
        if prefer_internal and home and is_external(url, home):
            s -= 4
        if s <= 0:
            continue
        prev = best.get(url.rstrip("/"))
        if prev is None or s > prev[0]:
            best[url.rstrip("/")] = (s, url, text)
    return sorted(best.values(), key=lambda x: (-x[0], len(x[1])))


def best_link(links: list[tuple[str, str]], ptype: str, exclude: set[str],
              home: str | None = None) -> tuple[str, int, str] | None:
    # `results` is the one type that legitimately lives off-site (Clarity ENR and
    # similar per-county vendor portals), so it is not pushed toward the SOE domain.
    prefer_internal = ptype in ("elections", "polling", "early_voting")
    ranked = rank_links(links, PATTERNS[ptype], exclude, home, prefer_internal)
    if not ranked:
        return None
    s, url, text = ranked[0]
    return url, s, text


def is_external(url: str, home: str) -> bool:
    def reg(h: str) -> str:
        parts = urlparse(h).netloc.lower().split(".")
        # Florida government hosts use *.fl.us (three labels) as well as *.gov.
        return ".".join(parts[-3:]) if h.endswith(".fl.us") else ".".join(parts[-2:])
    return reg(url) != reg(home)


# Minimum score to accept a pick without flagging it as weak.
MIN_STRONG = {"elections": 10, "polling": 12, "early_voting": 12, "results": 10}


def _try_candidates(links: list[tuple[str, str]], exclude: set[str], home: str
                    ) -> tuple[str, str, list[str]]:
    """Walk the ranked elections candidates until one actually validates.

    Trying several instead of only the best one matters because SOE sites carry
    stale links, and stopping at the first failure loses the whole county.
    """
    tried: list[str] = []
    for s, url, text in rank_links(links, PATTERNS["elections"], exclude,
                                   home, prefer_internal=True)[:4]:
        r = fetch(url)
        if not r["ok"] or (r["status"] or 0) >= 400 or "html" not in r["ctype"].lower():
            tried.append(f"{url.rstrip('/').split('/')[-1] or url} -> "
                         f"{r['status'] or r['error']}")
            continue
        if is_generic_portal(r["final_url"]):
            tried.append(f"{url.rstrip('/').split('/')[-1]} -> statewide portal")
            continue
        weak = "" if s >= MIN_STRONG["elections"] else " (weak match — review)"
        return r["final_url"], f'found via link "{text[:40]}" score={s}{weak}', tried
    return "", "", tried


def _find_elections(home: str, home_links: list[tuple[str, str]], exclude: set[str]
                    ) -> tuple[str, str, list[tuple[str, str]]]:
    """Locate the elections / voter-info landing page.

    Two strategies, not Texas's three: the "Departments nav" walk is dropped because
    an SOE site has no departments to walk through.
    """
    all_tried: list[str] = []

    # 1. Straight from the homepage links.
    url, note, tried = _try_candidates(home_links, exclude, home)
    all_tried += tried
    if url:
        return url, note, home_links

    # 2. Force a headless render — several SOE sites build their nav entirely in JS,
    #    so the plain HTML has links but not the elections one.
    try:
        import snapshot
        h = snapshot.fetch_headless(home)
        if h["ok"] and h["html"]:
            rendered = links_of(h["html"], h["final_url"])
            if rendered:
                url, note, tried = _try_candidates(rendered, exclude, home)
                all_tried += tried
                if url:
                    return url, note + " [via headless render]", rendered + home_links
                home_links = rendered + home_links
    except Exception:  # noqa: BLE001
        pass

    detail = f" (tried: {'; '.join(all_tried[:3])})" if all_tried else ""
    return "", f"GAP: no distinct elections page found{detail}", home_links


# Clarity ENR deep links embed a per-election id (…/FL/Leon/124476/web.307579/…)
# that goes stale every cycle. The county index page lists all elections and is
# stable, so prefer it.
_CLARITY_RE = re.compile(
    r"^(https?://results\.enr\.clarityelections\.com/[A-Z]{2}/[^/]+/)")


def unlaunder_translate_proxy(url: str) -> str:
    """Recover the real URL behind a Google Translate proxy host.

    SOE sites sometimes link their own pages through Translate. The underlying site
    is the genuine target, so rewrite rather than discard — the proxy adds a language
    layer and its own churn.
    """
    p = urlparse(url)
    if not p.netloc.endswith(".translate.goog"):
        return url
    host = p.netloc[: -len(".translate.goog")].replace("-", ".")
    query = "&".join(kv for kv in p.query.split("&")
                     if kv and not kv.startswith("_x_tr_"))
    return urlunparse(("https", host, p.path, p.params, query, ""))


def stabilize_url(url: str) -> str:
    url = unlaunder_translate_proxy(url)
    m = _CLARITY_RE.match(url)
    return m.group(1) if m else url


def _plausible_target(url: str, county: str, home: str, elections_url: str) -> bool:
    """Is this URL plausibly THIS county's page?

    Accept anything on the SOE's own site. Off-site URLs are accepted only when they
    name the county, which keeps legitimate per-county vendor portals
    (results.enr.clarityelections.com/FL/Leon/) while rejecting unrelated third-party
    pages that merely scored well on election vocabulary.
    """
    if not is_external(url, home):
        return True
    if elections_url and not is_external(url, elections_url):
        return True
    squash = re.sub(r"[^a-z0-9]", "", county.lower())
    return squash in re.sub(r"[^a-z0-9]", "", url.lower())


def discover_county(county: str, seat: str, home: str) -> list[dict]:
    rows: list[dict] = []

    def row(ptype, url, note):
        rows.append({"county": county, "batch": BATCH_LABEL, "page_type": ptype,
                     "url": url,
                     "external": str(is_external(url, home)).lower() if url else "false",
                     "notes": note})

    home_res = fetch(home)
    if not home_res["ok"] or (home_res["status"] or 0) >= 400:
        # Hard-blocked or genuinely down. The homepage row still carries the verified
        # URL so the pipeline captures the block state as a stable, diffable artifact
        # — if the block ever lifts, that shows up as a real change. The four election
        # pages can't be crawled, so they're gaps.
        why = home_res["error"] or f"HTTP {home_res['status']}"
        for pt in ("elections", "polling", "early_voting", "results"):
            row(pt, "", f"GAP: could not crawl — homepage blocked/unreachable "
                        f"({why}); needs manual URL discovery")
        log.info("%-14s homepage unreachable (%s)", county, why)
        return rows

    home_links = links_of(home_res["html"], home_res["final_url"])
    exclude = {home_res["final_url"].rstrip("/"), home.rstrip("/")}

    # --- level 1: elections landing -------------------------------------------
    elections_url, elections_note, home_links = _find_elections(
        home, home_links, exclude)
    row("elections", elections_url, elections_note)

    # --- level 2 + 3: polling / early_voting / results -------------------------
    # Level 2 = links on the elections landing page. Level 3 = links on the
    # per-election hub pages found there, which is where some SOE sites publish
    # polling & early-voting detail.
    deep_links = list(home_links)
    hub_urls: list[str] = []
    if elections_url:
        exclude.add(elections_url.rstrip("/"))
        er = fetch(elections_url)
        if er["ok"]:
            elections_links = links_of(er["html"], er["final_url"])
            deep_links = elections_links + home_links
            # Hub pages must live on the SOE's own site. Without this, an external
            # widget or a municipality's page can become a polling/EV target.
            for _s, hurl, _t in rank_links(elections_links, HUB_PATTERNS, exclude,
                                           home, prefer_internal=True)[:4]:
                if is_external(hurl, home) and is_external(hurl, elections_url):
                    continue
                hub_urls.append(hurl)
                if len(hub_urls) == 2:
                    break
    # Only keep hubs that are real, reachable HTML — some "Current Elections" links
    # point straight at a PDF, which is out of scope and must never become a target.
    verified_hubs: list[str] = []
    for hurl in hub_urls:
        hr = fetch(hurl)
        if hr["ok"] and (hr["status"] or 0) < 400 and "html" in hr["ctype"].lower():
            verified_hubs.append(hr["final_url"])
            deep_links = links_of(hr["html"], hr["final_url"]) + deep_links
    hub_urls = verified_hubs

    for ptype in ("polling", "early_voting", "results"):
        pick = best_link(deep_links, ptype, exclude, home)
        chosen_url, note = "", ""
        if pick and not _plausible_target(pick[0], county, home, elections_url):
            pick = None
            note = ("GAP: no county-specific page found (best candidate was an "
                    "unrelated third-party site)")
        if pick:
            url, s, text = pick
            if elections_url and url.rstrip("/") == elections_url.rstrip("/"):
                note = "GAP: folded into elections page"
            elif url.rstrip("/") in exclude:
                note = "GAP: folded into SOE homepage"
            else:
                r = fetch(url)
                if not r["ok"] or (r["status"] or 0) >= 400:
                    note = f"GAP: candidate unreachable ({url})"
                elif "html" not in r["ctype"].lower():
                    note = (f"GAP: candidate is non-HTML ({r['ctype'][:25]}) "
                            f"— out of scope")
                elif is_generic_portal(r["final_url"]):
                    note = ("GAP: candidate redirects to a statewide portal "
                            "(not county-specific)")
                else:
                    chosen_url = stabilize_url(r["final_url"])
                    weak = "" if s >= MIN_STRONG[ptype] else " (weak match — review)"
                    stab = (" [normalized to stable Clarity index]"
                            if chosen_url != r["final_url"] else "")
                    # Nothing durable existed, so a per-election URL was the only
                    # option. Say so — it will need refreshing next cycle.
                    eph = (" [per-election URL — refresh each cycle]"
                           if _EPHEMERAL_URL_RE.search(chosen_url) else "")
                    note = f'found via "{text[:40]}" score={s}{weak}{stab}{eph}'
        # Fall back to the per-election hub page: for some counties that IS where
        # polling/early-voting content lives, and it is the highest-value page to
        # diff around an election. Better to capture it than to record nothing.
        if not chosen_url and ptype in ("polling", "early_voting") and hub_urls:
            hub = hub_urls[0]
            if hub.rstrip("/") != (elections_url or "").rstrip("/"):
                chosen_url = hub
                note = (f"no standing {ptype} page; using per-election hub page "
                        f"(shared with other types) — review")
        if not chosen_url and not note:
            note = "GAP: no distinct page found" + (
                " (likely folded into elections page)" if elections_url else "")
        row(ptype, chosen_url, note)

    got = sum(1 for r in rows if r["url"])
    log.info("%-14s %d/4 pages", county, got)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--county", action="append", default=None)
    ap.add_argument("--batch", default="1",
                    help="which batch's discovered homepages to crawl (default 1)")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s",
                        handlers=[logging.StreamHandler(sys.stdout)])
    global BATCH_LABEL
    BATCH_LABEL = str(args.batch)

    global IN_CSV, OUT_CSV
    IN_CSV = ROOT / "manifest" / f"batch{args.batch}_homepages.csv"
    OUT_CSV = ROOT / "manifest" / f"batch{args.batch}_targets_draft.csv"
    if not IN_CSV.exists():
        sys.exit(f"missing {IN_CSV} — run discover_homepages.py --batch {args.batch} "
                 f"first")
    homes = list(csv.DictReader(IN_CSV.open(encoding="utf-8")))
    if args.county:
        want = {c.lower() for c in args.county}
        homes = [h for h in homes if h["county"].lower() in want]
    homes = [h for h in homes if h["homepage"].strip()]

    all_rows: list[dict] = []
    with cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(discover_county, h["county"], h["seat"],
                            h["homepage"]): h["county"] for h in homes}
        for f in cf.as_completed(futs):
            all_rows.extend(f.result())

    order = {h["county"]: i for i, h in enumerate(homes)}
    ptorder = {"elections": 0, "polling": 1, "early_voting": 2, "results": 3}
    all_rows.sort(key=lambda r: (order[r["county"]], ptorder[r["page_type"]]))

    with OUT_CSV.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["county", "batch", "page_type", "url",
                                           "external", "notes"])
        w.writeheader()
        w.writerows(all_rows)
    found = sum(1 for r in all_rows if r["url"])
    log.info("\nwrote %s — %d rows, %d with URLs, %d gaps",
             OUT_CSV, len(all_rows), found, len(all_rows) - found)


if __name__ == "__main__":
    main()
