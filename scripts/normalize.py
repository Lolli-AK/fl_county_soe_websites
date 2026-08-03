"""Shared, deterministic HTML normalization for fl-county-watch.

The whole point of this module is that the SAME input HTML always produces the
SAME `page.html` / `page.txt` bytes, run after run, so that an unchanged site
yields a zero git diff. Any volatility that survives here will destroy the diffs.

Parse with BeautifulSoup + lxml. Strip scripts/styles/volatile tokens, collapse
whitespace, and pretty-print line-oriented output.
"""

from __future__ import annotations

import re
from bs4 import BeautifulSoup, Comment, NavigableString

# Tags removed wholesale — no content value, pure noise for diffing.
_DROP_TAGS = ("script", "style", "noscript", "svg", "template", "iframe")

# ASP.NET WebForms volatile hidden inputs. These change every single request on
# many county sites and are huge; left in, they alone guarantee a diff each run.
_VOLATILE_INPUT_NAMES = {
    "__viewstate",
    "__viewstategenerator",
    "__viewstateencrypted",
    "__eventvalidation",
    "__eventtarget",
    "__eventargument",
    "__previouspage",
    "__scrollpositionx",
    "__scrollpositiony",
    "__requestverificationtoken",
}

# Attributes carrying per-request tokens — drop them off EVERY element.
#
# `style` is included deliberately: inline styles are purely presentational and
# are the dominant source of non-determinism on headless-rendered pages — JS
# carousels/sliders continuously rewrite `left`/`z-index`/`display` as they
# animate, so the captured DOM reflects a random animation frame. Dropping
# `style` keeps page.html a stable structural artifact. Visible text (page.txt)
# is unaffected. `class` animation churn is handled separately below.
_VOLATILE_ATTRS = {"nonce", "integrity", "crossorigin", "style"}

# Substrings that mark an attribute name as carrying a per-request/session token.
_VOLATILE_ATTR_SUBSTRINGS = ("csrf", "token", "nonce", "session", "viewstate")

# <meta> names carrying a build/publish counter rather than content. Wix emits
# <meta http-equiv="X-Wix-Published-Version" content="6061"> and bumps it on every
# publish, so two captures minutes apart can disagree.
_VOLATILE_META_SUBSTRINGS = ("published-version", "build-version", "buildid",
                             "build-id", "revision", "x-wix-",
                             # Dublin Core date emitted as the page GENERATION time,
                             # not a content date: Volusia's homepage carries
                             # <meta name="DC.Date" content="2026-08-03T11:54:31-04:00">
                             # which changes on every request.
                             "dc.date", "dcterms.modified", "generation-time")

# Markers that a "plain" fetch only got a JS shell and should be escalated.
_JS_SHELL_MARKERS = (
    "please enable javascript",
    "enable javascript to run this app",
    "you need to enable javascript",
    "this application requires javascript",
    "javascript is required",
    "javascript is disabled",
)

_WS_RUN = re.compile(r"[ \t\f\v]+")
_BLANK_LINES = re.compile(r"\n\s*\n\s*\n+")

# Java `Date.toString()` rendering, e.g. "Tue Jul 28 08:49:00 CDT 2026".
# Tarrant's homepage first shows news dates in display form ("July 28, 2026"),
# then a late AJAX pass rewrites them into this raw form one at a time, so a
# capture can catch any mixture of the two. Both encode the SAME date, so we
# canonicalize the raw form to the display form the site actually shows users.
# This preserves the date; only the (never-displayed) time of day is dropped.
_JAVA_DATE_RE = re.compile(
    r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) "
    r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) "
    r"(\d{2}) \d{2}:\d{2}:\d{2} [A-Z]{2,5} (\d{4})\b")
_MONTHS = {"Jan": "January", "Feb": "February", "Mar": "March", "Apr": "April",
           "May": "May", "Jun": "June", "Jul": "July", "Aug": "August",
           "Sep": "September", "Oct": "October", "Nov": "November",
           "Dec": "December"}


def _canon_java_dates(text: str) -> str:
    def sub(m: re.Match) -> str:
        return f"{_MONTHS[m.group(1)]} {int(m.group(2))}, {m.group(3)}"
    return _JAVA_DATE_RE.sub(sub, text)

# Cloudflare email obfuscation re-encodes the address with a fresh random key on
# every request, so the token after `email-protection#` and the `data-cfemail`
# attribute both change each fetch. Neutralize them to a stable placeholder.
_CF_EMAIL_HREF = re.compile(r"(/cdn-cgi/l/email-protection#)[0-9a-fA-F]+")

# Akamai "Access Denied" / edge error pages embed a per-request Reference # like
# "18.4f78ce17.1784308058.1113f7d6" (also in an errors.edgesuite.net URL). It
# changes every request; scrub it so a blocked site captures deterministically.
_AKAMAI_REF = re.compile(r"\b\d+\.[0-9a-f]{6,}\.\d+\.[0-9a-f]{6,}\b")

# Machine-generated opaque identifiers that are regenerated on every render.
# Modern county CMSs are full of them and none of them are content:
#   CivicPlus widgets:  id="widgetFAQ(194)a6fb6078-ff97-47b6-9809-b9598800536b"
#   Drupal views:       class="js-view-dom-id-5be9f617...cfc7" (64 hex)
#   SharePoint viewer:  hid=14C02BA2-508D-E000-...
_GUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
# 10+ hex chars is enough to catch short random widget ids (Travis renders
# data-search-id="tc-search-6a68ccbc3ce9d") while staying long enough that real
# words/slugs in attribute values aren't touched.
_LONGHEX_RE = re.compile(r"\b[0-9a-f]{10,}\b")
# Epoch-style timestamps used as cache-busters / session stamps in URLs
# (SharePoint's WOPI viewer sends hfto=1785260699277.8).
_EPOCHISH_RE = re.compile(r"^\d{10,}(\.\d+)?$")

# Random base62 token appended after a "--" separator. Fort Bend renders
# data-target-id="meetings-and-events-view--pPh8W-F6LrM" with a fresh token each
# request; the mixed case means the hex rule above cannot catch it.
_DASHDASH_TOKEN_RE = re.compile(r"--[A-Za-z0-9_-]{8,}(?=$|[\s\"'])")

# Short random hex ids that carry a known generator prefix. Cameron's Elementor
# markup emits id="style-9bc41b9" (7 hex chars — under the generic hex threshold),
# regenerated on every request.
# The (?=…[a-f]) lookahead requires at least one hex letter, so genuine decimal
# ids (WordPress post-123456) are left alone and still diff if they change.
_PREFIXED_SHORTHEX_RE = re.compile(
    r"\b(style|elementor|post|block|widget)-(?=[0-9a-f]*[a-f])[0-9a-f]{6,}\b")
# Same idea but with NO separator, so the \b before the hex never matches:
# La Salle County renders class="pbckid6a6a382fdf001", fresh on every request.
_GLUED_TOKEN_RE = re.compile(r"\b(pbckid|comp-|uid|guid|ctl)([0-9a-f]{10,})\b")
# Random base62 suffix after a SINGLE dash, e.g. Jim Wells' FAQ plugin emits
# id="ewd-ufaq-post-308-MeFxb5jB1A" and id="ewd-ufaq-post-306-hyPkOkqghR".
# Requiring BOTH cases is what distinguishes these from real content: slugs are
# lowercase ("-election-results") and years are digits ("-2026"). A digit is NOT
# required — half of these tokens contain none, and demanding one canonicalized
# only some of them, which produced churn rather than removing it.
_MIXEDCASE_SUFFIX_RE = re.compile(
    r"-(?=[A-Za-z0-9]*[A-Z])(?=[A-Za-z0-9]*[a-z])[A-Za-z0-9]{8,}\b")
# WordPress child-theme build counter: a body class like
# "wp-child-theme-<name>-redesign_102" whose trailing number bumps on each deploy.
# (First seen on a Texas county site; it is a WordPress convention rather than
# anything state-specific, and several Florida SOE sites run WordPress too.)
_THEME_BUILD_RE = re.compile(r"\b(wp-(?:child-)?theme-[a-z0-9-]+)_\d+\b")

# Attributes whose values are identifier-ish and safe to canonicalize.
_ID_ATTRS = ("id", "class", "for", "aria-controls", "aria-labelledby",
             "aria-describedby", "headers", "name", "value", "href", "action")

# Always-random per-render attributes worth dropping outright.
_DROP_ATTRS_EXACT = ("data-drupal-selector", "data-style-uid",
                     # Laravel Livewire mints a fresh component id and embeds a
                     # full state snapshot on every render (Gregg County).
                     "wire:id", "wire:snapshot", "wire:effects", "wire:initial-data",
                     # WP Rocket stamps which optimizations its cache applied, and
                     # the list ends in wpr_mobile or wpr_desktop depending on which
                     # cache variant answered. Clay County's homepage alternates
                     # between the two, which also flips <img> lazy-loading on and
                     # off (handled separately by restoring data-lazy-src).
                     "data-wpr-features")

# Cache-busting / per-session query parameters on asset and form URLs. WordPress
# stamps stylesheets with ?ver=<unix time>; SharePoint's Office viewer packs a
# dozen per-session tokens into the form action.
_CACHEBUST_PARAMS = ("ver", "v", "rev", "cachebust", "cache", "ts", "t", "_",
                     "cdv", "cb", "build", "version", "vsn", "rnd", "r")

# Server-clock timestamps embedded in attribute values. Elementor ships
# data-elementor-settings='{… "schedule_server_datetime":"2026-07-28 12:31:37" …}'
# which changes on every render. Only match values carrying a seconds-precision
# time, so genuine event dates (which rarely include HH:MM:SS) survive.
_DATETIME_RE = re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(\.\d+)?Z?")
# US-format server clock, e.g. Howard County pre-fills a date-picker input with
# value="7/22/2026 12:27:59 PM". Seconds precision marks it as a clock, not content.
_US_DATETIME_RE = re.compile(
    r"\b\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}:\d{2}\s*(?:AM|PM|am|pm)?\b")

# CivicPlus widgets pick a size class by measuring their container at runtime, so
# the same widget renders `wide` on one capture and `narrow` on the next.
_WIDGET_SIZE_CLASSES = {"wide", "narrow"}
# ...and the same measurement is applied to plain LAYOUT containers, not just
# elements whose class mentions "widget": Lee County renders
# class="row outer wide" / "row nest first last wide" on one capture and drops
# `wide` on the next. So the size-class strip is keyed off these layout classes too.
_LAYOUT_CLASSES = {"row", "outer", "nest", "col", "column", "container",
                   "grid", "cell"}

# Elementor responsive-visibility classes. These say "hide at this breakpoint", are
# purely presentational, and flap between captures — Okeechobee's homepage rendered
# `elementor-hidden-tablet elementor-hidden-mobile elementor-hidden-desktop` on one
# request and none of them on the next. Consistent with dropping `style` outright.
_RESPONSIVE_HIDDEN_PREFIXES = ("elementor-hidden-", "e-hidden-")

# Play/pause state that a slideshow toggles on its OWN container (not on
# <html>/<body>), so it must be stripped element-wide: Orange County's EventON
# slider carries evo_slideshow_pause only while paused.
_PLAYBACK_STATE_CLASSES = {"evo_slideshow_pause", "paused", "is-paused",
                           "playing", "is-playing", "evo_slideshow_play"}

# Accessibility-overlay vendors that inject a whole toolbar into the DOM
# asynchronously, so it is present or absent depending on when capture happened.
# Their content is vendor chrome, never county information.
# Live weather widgets on county homepages ("Fair" -> "Partly Cloudy"). Genuine
# real-world data, but it changes hourly and is unrelated to election content, so
# leaving it in would put permanent noise in every homepage diff.
_WEATHER_HINTS = ("weathercontainer", "weather-container", "weatherwidget",
                  "weather-widget", "weatherblock", "weather-block",
                  "weathericon", "current-weather", "weather-current")

# Self-describing cache-buster elements: Gregg County emits empty
# <span class="nocache" data-nocache="..."> markers whose presence varies per
# render. The name says it all — never content.
_NOCACHE_HINTS = ("nocache", "no-cache")

_INJECTED_WIDGET_HINTS = ("audioeye", "accessibe", "userway", "usablenet",
                          "recite-me", "recitememe", "equalweb",
                          # JS-injected document viewer overlay (Delta County)
                          "docbox")

# Classes that JavaScript adds to <html>/<body> to advertise load state. They flip
# mid-hydration, so a capture can land on either side of them.
_JS_STATE_CLASSES = {
    "js", "no-js", "wf-active", "wf-loading", "wf-inactive", "loaded", "ready",
    "fontawesome-i2svg-active", "fontawesome-i2svg-complete", "fontawesome-i2svg",
    "is-ready", "dom-loaded", "page-loaded",
    # Slideshow play/pause state toggles as the widget runs (Orange County's
    # EventON slider emits evo_slideshow_pause only while paused).
    "evo_slideshow_pause", "paused", "is-paused", "playing", "is-playing",
}


# --- Florida additions ----------------------------------------------------- #
# Anti-forgery token nested INSIDE a JSON attribute value. The attribute-name rules
# above cannot see it: DotNetNuke's 2sxc module renders
#   data-edit-context='{"jsApi":{…,"rvtHeader":"RequestVerificationToken",
#                       "rvt":"y7X-…87N7Hg2",…}}'
# so the volatile part is a JSON *value* under an innocuous attribute name. Fresh on
# every request, and it appears on every content block, so a single homepage produced
# dozens of diff lines (Brevard and Leon, 5 pages each).
_JSON_TOKEN_RE = re.compile(
    r'("(?:rvt|__RequestVerificationToken|antiForgeryToken|csrfToken|'
    r'requestVerificationToken|nonce|sessionId)"\s*:\s*")[^"]*(")')

# Random NUMERIC suffix on a dynamic-widget id. The hex/base62 rules above all
# require a letter, so a purely decimal token slips through: Orange County's results
# page renders id="ocsoe-cpt-ajax-268481" and its homepage id="blog-slider-555748",
# both regenerated per request. Scoped to ids whose prefix names a dynamic widget, so
# genuine decimal ids (WordPress post-123456, DocumentCenter/View/1783) keep diffing
# normally — those are stable content references, and losing them would cost real
# signal.
_RANDOM_NUM_SUFFIX_RE = re.compile(
    r"\b([a-z][a-z0-9-]*(?:ajax|wrapper|instance|uid|rnd|rand|slider|carousel"
    r"|slideshow|gallery|modal|accordion|tabs?|popup|lightbox))-\d{4,}\b", re.I)

# A class that, after canonicalization, is nothing but a prefix plus a random
# placeholder — e.g. "stk-container--RANDOM", "stk--RANDOM", "style-HEX". Matched
# after _canon_ids has run, so the placeholders are already in place.
# The prefix must allow hyphens: real cases are "stk-block-columns--RANDOM" and
# "stk-container--RANDOM", not just single-word prefixes.
_RANDOM_ONLY_CLASS_RE = re.compile(
    r"^[A-Za-z][A-Za-z0-9_-]*-{1,2}(RANDOM|HEX|GUID)$")

# Stackable (WordPress) block ids. Orange County's pages carry
# class="stk-block stk-b2y6ajz" data-block-id="b2y6ajz" — and some of these are
# regenerated per request while others are persisted with the saved post. The two are
# distinguishable: the stable ones are hex-looking ("b885ea1", "3e4da80"), the
# volatile ones are base36 and contain letters outside a-f ("b2y6ajz", "signnvf",
# "ykblxme"). Requiring a non-hex letter therefore canonicalizes exactly the volatile
# ones and leaves the stable ids diffing normally.
_STACKABLE_ID_RE = re.compile(r"\bstk-(?=[a-z0-9]*[g-z])[a-z0-9]{6,10}\b")
# ...and the bare token as it appears in data-block-id / data-v attributes.
_STACKABLE_TOKEN_RE = re.compile(r"^(?=[a-z0-9]*[g-z])[a-z0-9]{6,10}$")

# An UNRENDERED server-side template expression that leaked into the markup. This is
# a CivicPlus bug: Palm Beach's homepage emits
#   <ol aria-hidden="False.ToString().ToLowerInvariant()" class="tabbedWidgetNarrow">
# i.e. the C# was never evaluated, so the literal expression is in the attribute. Its
# operand still flips True/False between captures, because it is driven by the same
# runtime container measurement behind the `wide`/`narrow` class churn — so the value
# is both meaningless and volatile. Replace the whole value rather than the operand:
# the leading token varies too ("aria-hidden.ToString()…" on one capture).
_UNRENDERED_EXPR_RE = re.compile(r"^[^\"']*\.ToString\(\)[.A-Za-z()]*$")


def _canon_ids(value: str) -> str:
    """Replace regenerated-per-render opaque ids/timestamps with placeholders."""
    value = _JSON_TOKEN_RE.sub(r"\1TOKEN\2", value)
    value = _GUID_RE.sub("GUID", value)
    value = _LONGHEX_RE.sub("HEX", value)
    value = _DASHDASH_TOKEN_RE.sub("--RANDOM", value)
    value = _GLUED_TOKEN_RE.sub(r"\1HEX", value)
    value = _MIXEDCASE_SUFFIX_RE.sub("-RANDOM", value)
    value = _THEME_BUILD_RE.sub(r"\1_BUILD", value)
    value = _PREFIXED_SHORTHEX_RE.sub(r"\1-HEX", value)
    value = _RANDOM_NUM_SUFFIX_RE.sub(r"\1-RANDOM", value)
    value = _STACKABLE_ID_RE.sub("stk-RANDOM", value)
    value = _DATETIME_RE.sub("TIMESTAMP", value)
    return _US_DATETIME_RE.sub("TIMESTAMP", value)


def _strip_cachebust(url: str) -> str:
    """Drop cache-busting / per-session query params from a URL.

    SharePoint's Office viewer form action carries ~20 per-session tokens, so if
    more than half the params look volatile the query is dropped wholesale.
    """
    if "?" not in url:
        return url
    from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
    parts = urlsplit(url)
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    if not pairs:
        return url
    kept = [(k, v) for k, v in pairs
            if k.lower() not in _CACHEBUST_PARAMS
            and not _GUID_RE.search(v) and not _LONGHEX_RE.search(v)
            and not _EPOCHISH_RE.match(v.strip())]
    if len(kept) < len(pairs) / 2:
        kept = []  # mostly session junk (SharePoint/WOPI) — drop the query entirely
    return urlunsplit((parts.scheme, parts.netloc, parts.path,
                       urlencode(kept), ""))


def _attr_is_volatile(name: str) -> bool:
    lname = name.lower()
    if lname in _VOLATILE_ATTRS:
        return True
    return any(sub in lname for sub in _VOLATILE_ATTR_SUBSTRINGS)


# <link rel="..."> resource hints reference build-hashed JS/CSS assets and vary
# per load/build; they are never content. Drop these rels (keep stylesheet /
# canonical / icon / alternate, which can be meaningful).
# `stylesheet` is included: <style> is already stripped, so a stylesheet <link>
# contributes no content — only churn. WordPress/Elementor emits per-post CSS files
# conditionally (Midland's votemidlandtx.gov drops several between renders) and
# stamps them with ?ver= cache-busters.
_DROP_LINK_RELS = {"preload", "modulepreload", "prefetch", "preconnect",
                   "dns-prefetch", "prerender", "stylesheet"}

# id attribute prefixes whose suffix is randomized per page load (widgets that
# mint a unique id each time). Normalize the suffix to keep structure stable.
_VOLATILE_ID_PREFIXES = ("gt-wrapper-", "ivs-gallery-")

# Class hints for rotating IMAGE hero carousels. Some county homepages (e.g.
# Lubbock) randomize both the subset and order of banner photos server-side, so
# no client-side stub helps. We replace the content of an image slideshow with a
# stable placeholder. Scoped to containers that actually hold an <img> so that
# text-only rotating alert/countdown banners are preserved.
_SLIDESHOW_CLASS_HINTS = ("slideshow", "carousel", "swiper", "slick-slider",
                          "glide__track", "splide__track")
_SLIDESHOW_PLACEHOLDER = "[rotating image slideshow removed for determinism]"


def _input_is_volatile(tag) -> bool:
    """A hidden input carrying a per-request token (ASP.NET, CSRF, anti-bot)."""
    for key in ("name", "id"):
        val = tag.get(key)
        if not val:
            continue
        lval = val.strip().lower()
        if lval in _VOLATILE_INPUT_NAMES:
            return True
        if any(sub in lval for sub in _VOLATILE_ATTR_SUBSTRINGS):
            return True
    return False


def _meta_is_volatile(tag) -> bool:
    """A <meta> carrying a per-request token or a build/publish counter."""
    subs = _VOLATILE_ATTR_SUBSTRINGS + _VOLATILE_META_SUBSTRINGS
    for key in ("name", "property", "http-equiv", "itemprop"):
        val = tag.get(key)
        if val and any(sub in val.strip().lower() for sub in subs):
            return True
    return False


def _strip_tree(soup: BeautifulSoup) -> None:
    """Mutate the parse tree in place: remove noise tags, comments, volatile bits."""
    # HTML comments (includes conditional comments, cache stamps, build ids).
    for comment in soup.find_all(string=lambda t: isinstance(t, Comment)):
        comment.extract()

    # Noise tags.
    for tag in soup.find_all(_DROP_TAGS):
        tag.decompose()

    # Rotating IMAGE hero carousels: server-randomized subset/order → replace
    # content with a stable placeholder. Outer container is hit first (document
    # order), so nested slideshow markup is cleared with it (guard skips the now
    # decomposed inner nodes). Image-ness is detected via an <img> OR a CSS
    # background-image (Lubbock's banner is background-url only, no <img>), which
    # keeps text-only rotating alert/countdown banners untouched.
    for tag in soup.find_all(True):
        if getattr(tag, "decomposed", False):
            continue
        classes = " ".join(tag.get("class") or []).lower()
        if not any(h in classes for h in _SLIDESHOW_CLASS_HINTS):
            continue
        has_image = tag.find("img") is not None or tag.find(
            lambda t: "url(" in (t.get("style") or "").lower()) is not None
        if has_image:
            tag.clear()
            tag.append(_SLIDESHOW_PLACEHOLDER)

    # Asynchronously-injected accessibility-overlay widgets (AudioEye et al.):
    # present or absent depending on capture timing, and never county content.
    for tag in soup.find_all(True):
        if getattr(tag, "decomposed", False):
            continue
        ident = " ".join(filter(None, [
            str(tag.get("id") or ""), " ".join(tag.get("class") or []),
            str(tag.get("href") or ""), str(tag.get("src") or ""),
        ])).lower()
        if ident and any(h in ident for h in _INJECTED_WIDGET_HINTS):
            tag.decompose()
            continue
        if tag.has_attr("data-nocache") or any(
                c.lower() in _NOCACHE_HINTS for c in (tag.get("class") or [])):
            tag.decompose()
            continue
        # Live weather readout — replace with a placeholder rather than deleting,
        # so a layout change is still visible while the hourly value is not.
        if ident and any(h in ident for h in _WEATHER_HINTS):
            tag.clear()
            tag.append("[live weather removed for determinism]")

    # JS load-state classes on <html>/<body> flip mid-hydration.
    for tag in soup.find_all(["html", "body"]):
        classes = tag.get("class")
        if classes:
            kept = [c for c in classes if c.lower() not in _JS_STATE_CLASSES]
            if kept:
                tag["class"] = kept
            else:
                del tag["class"]

    # Resource-hint <link>s (build-hashed asset preloads) — pure noise.
    for tag in soup.find_all("link"):
        rels = {r.lower() for r in (tag.get("rel") or [])}
        if rels & _DROP_LINK_RELS:
            tag.decompose()

    # <meta> carrying per-request tokens (Rails csrf-token, etc.).
    for tag in soup.find_all("meta"):
        if _meta_is_volatile(tag):
            tag.decompose()

    # Volatile hidden inputs (ASP.NET viewstate, CSRF/authenticity tokens, ...).
    for tag in soup.find_all("input"):
        if _input_is_volatile(tag):
            tag.decompose()
            continue
        # Any remaining hidden input: its `value` is form/session state, never
        # visible content, and is a common source of per-request churn (anti-bot
        # tokens like name="ht"). Drop the value, keep the element for structure.
        if (tag.get("type") or "").strip().lower() == "hidden" and tag.has_attr("value"):
            del tag["value"]

    # Per-request token attributes on every remaining element.
    for tag in soup.find_all(True):
        for attr in list(tag.attrs.keys()):
            if _attr_is_volatile(attr):
                del tag[attr]
        # Cloudflare email obfuscation: drop the per-request encoded token attr
        # and stabilize the obfuscated href.
        if tag.has_attr("data-cfemail"):
            del tag["data-cfemail"]
        href = tag.get("href")
        if href and "/cdn-cgi/l/email-protection#" in href:
            tag["href"] = _CF_EMAIL_HREF.sub(r"\1REDACTED", href)
        # Randomized per-load widget ids (GTranslate, IVS gallery, ...).
        tid = tag.get("id")
        if tid:
            for prefix in _VOLATILE_ID_PREFIXES:
                if tid.startswith(prefix):
                    tag["id"] = prefix + "X"
                    break

        # Always-random framework attributes.
        for attr in _DROP_ATTRS_EXACT:
            if tag.has_attr(attr):
                del tag[attr]

        # Unrendered server-side template expressions (see _UNRENDERED_EXPR_RE).
        for attr, val in list(tag.attrs.items()):
            if isinstance(val, str) and _UNRENDERED_EXPR_RE.match(val):
                tag[attr] = "UNRENDERED_EXPR"

        # Stackable's per-request block token, as a bare attribute value
        # (data-block-id="b2y6ajz"). The class form is handled by _canon_ids.
        if tag.has_attr("data-block-id"):
            v = str(tag["data-block-id"])
            if _STACKABLE_TOKEN_RE.match(v):
                tag["data-block-id"] = "RANDOM"

        # WP Rocket lazy-loading rewrites <img src> to an inline SVG placeholder and
        # moves the real URL to data-lazy-src — but only when its cache has the
        # optimized variant, so the same page serves both forms (Clay's homepage).
        # Restore the real URL so the two forms normalize to one artifact.
        for real, placeholder in (("data-lazy-src", "src"),
                                  ("data-lazy-srcset", "srcset")):
            if tag.has_attr(real):
                tag[placeholder] = tag[real]
                del tag[real]

        # CivicPlus widget size classes are measured at runtime and flap. Applies to
        # elements naming a widget OR a layout container (see _LAYOUT_CLASSES).
        classes = tag.get("class")
        if classes and any("widget" in c.lower() or c.lower() in _LAYOUT_CLASSES
                           for c in classes):
            kept = [c for c in classes if c.lower() not in _WIDGET_SIZE_CLASSES]
            if kept != classes:
                tag["class"] = kept
        # Elementor responsive-visibility classes, on any element.
        classes = tag.get("class")
        if classes:
            kept = [c for c in classes
                    if not c.lower().startswith(_RESPONSIVE_HIDDEN_PREFIXES)]
            if kept != classes:
                tag["class"] = kept
        # Slideshow playback state, on any element.
        classes = tag.get("class")
        if classes:
            kept = [c for c in classes if c.lower() not in _PLAYBACK_STATE_CLASSES]
            if kept != classes:
                tag["class"] = kept

        # Canonicalize GUID/hex/timestamp values and strip cache-bust query
        # params. data-* attributes are included because frameworks stash random
        # ids and server clocks in them (data-search-id, data-elementor-settings).
        for attr in (*_ID_ATTRS, *[a for a in tag.attrs if a.startswith("data-")]):
            if not tag.has_attr(attr):
                continue
            val = tag[attr]
            if isinstance(val, list):  # e.g. class="a b c"
                canon = [_canon_ids(v) for v in val]
                if attr == "class":
                    # A class whose only distinguishing part was a per-render random
                    # token carries no information once canonicalized — and worse, it
                    # is often emitted inconsistently. Orange County's Stackable
                    # blocks render `stk-block-columns--<hash>` and
                    # `stk-container--<hash>` on one capture and omit them entirely on
                    # the next, so canonicalizing the value is not enough; the
                    # presence/absence still diffs. Dropping them removes the class of
                    # churn rather than one instance of it.
                    canon = [c for c in canon if not _RANDOM_ONLY_CLASS_RE.match(c)]
                tag[attr] = canon
                continue
            # Order matters: strip volatile query params FIRST, while their values
            # are still recognizable. Canonicalizing first would turn an epoch
            # stamp like hfto=1785260699277.8 into "HEX.8", which no longer looks
            # like a timestamp and would survive as churn.
            new = val
            if attr in ("href", "action", "src") and "?" in new:
                new = _strip_cachebust(new)
            new = _canon_ids(new)
            if new != val:
                tag[attr] = new
        # <link>/<img>/<script> asset URLs also carry ?ver= stamps.
        for attr in ("src", "srcset"):
            if tag.has_attr(attr) and "?" in str(tag[attr]):
                tag[attr] = _strip_cachebust(_canon_ids(str(tag[attr])))


def _collapse_navstrings(soup: BeautifulSoup) -> None:
    """Collapse intra-line whitespace runs inside every text node.

    We deliberately do NOT touch newlines here; the pretty-printer re-lays-out
    structure. This keeps meaningful token spacing stable across runs.
    """
    for node in soup.find_all(string=True):
        if isinstance(node, NavigableString):
            collapsed = _WS_RUN.sub(" ", str(node))
            if collapsed != node:
                node.replace_with(collapsed)


def clean_html(raw_html: str) -> str:
    """Return normalized, pretty-printed, line-oriented HTML."""
    soup = BeautifulSoup(raw_html or "", "lxml")
    _strip_tree(soup)
    _collapse_navstrings(soup)

    # Pretty-print for a stable, line-oriented artifact. BeautifulSoup's
    # prettify is deterministic given a deterministic tree.
    pretty = soup.prettify()

    # Trim trailing whitespace per line + collapse blank-line runs so indentation
    # churn never shows up as a diff.
    lines = [line.rstrip() for line in pretty.splitlines()]
    out = "\n".join(lines).strip() + "\n"
    # Scrub per-request edge/CDN reference ids that survive as text content.
    out = _AKAMAI_REF.sub("EDGE_REF_REDACTED", out)
    # Canonicalize raw Java date renderings to the site's display form.
    out = _canon_java_dates(out)
    return out


def extract_text(cleaned_html: str) -> str:
    """Visible text only, from ALREADY-cleaned HTML. Lowest-noise diff artifact."""
    soup = BeautifulSoup(cleaned_html or "", "lxml")
    text = soup.get_text(separator="\n")
    # Normalize each line, drop empties, collapse blank runs.
    lines = [_WS_RUN.sub(" ", ln).strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    out = "\n".join(lines)
    out = _BLANK_LINES.sub("\n\n", out).strip()
    return out + "\n" if out else ""


def extract_title(cleaned_html: str) -> str | None:
    soup = BeautifulSoup(cleaned_html or "", "lxml")
    if soup.title and soup.title.string:
        return _WS_RUN.sub(" ", soup.title.string).strip() or None
    return None


def looks_like_js_shell(cleaned_text: str, cleaned_html: str, min_chars: int = 500) -> bool:
    """Heuristic: did a plain fetch only return an empty JS shell?

    True if visible text is tiny, OR an explicit "enable JavaScript" marker is
    present. Callers use this to decide whether to escalate to headless.
    """
    lowered_text = cleaned_text.lower()
    if any(marker in lowered_text for marker in _JS_SHELL_MARKERS):
        return True
    if any(marker in cleaned_html.lower() for marker in _JS_SHELL_MARKERS):
        return True
    if len(cleaned_text.strip()) < min_chars:
        return True
    return False
