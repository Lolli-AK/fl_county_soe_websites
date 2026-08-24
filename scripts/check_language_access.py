#!/usr/bin/env python3
"""Measure Spanish-language provision on each county's captured election pages.

Replaces an earlier homepage-only regex that asked "does the word 'espanol' or a
Translate widget appear anywhere". That conflated three very different things, and
under-counted besides, because it never looked past the homepage.

Four levels, strongest first. The distinction that matters is between a county
that PUBLISHES Spanish content and one that merely embeds a machine translator:

    spanish_content   real Spanish prose on a captured page (a cluster of Spanish
                      function words, not one stray loanword)
    spanish_link      a link or toggle to a Spanish version (/es/, ?lang=es,
                      hreflang="es", an "Espanol" anchor) or a Spanish document
    translate_widget  a Google-Translate-style machine translator and nothing else
    none              no Spanish signal of any kind

Scope note: this reads only the five captured pages per county, so a Spanish
section that exists but is not linked from those pages is invisible here. The
measure is "Spanish reachable from the pages we capture", not "the county has no
Spanish anywhere". That is a floor, not a census.

Usage:
    python scripts/check_language_access.py
"""
from __future__ import annotations

import csv
import glob
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "manifest" / "fl-language-access.csv"

# Spanish function/domain words. Requiring several DISTINCT hits is what separates
# real Spanish prose from an English page that happens to say "en Espanol" once.
_ES_WORDS = [r"\bde\s+la\b", r"\bde\s+los\b", r"\bpara\b", r"\bcomo\b", r"\bsobre\b",
             r"\bpuede\b", r"\bdebe\b", r"\bsu\s+voto\b", r"\bvotante", r"\bvotaci[oó]n",
             r"\belecci[oó]n", r"\belecciones\b", r"\binscripci[oó]n",
             r"\bboleta\b", r"\bcondado\b", r"\bpapeleta\b", r"\bencuesta\b",
             r"\bregistrarse\b", r"\bcorreo\b", r"\banticipada\b"]
_ES_RE = [re.compile(w, re.I) for w in _ES_WORDS]

_ES_LINK = re.compile(
    r'hreflang=["\']es|href=["\'][^"\']*(?:/es/|[?&]lang=es\b|[?&]language=es\b'
    r'|espanol|español|spanish)', re.I)
# Anchor text mentioning Spanish ANYWHERE, not an exact ">Spanish<" match. Alachua
# links "Spanish 2026 Notice of General Election (PDF)" whose href is an opaque
# /DocumentCenter/View/595, so neither the href pattern nor an exact-label match saw
# it, and a county publishing a Spanish legal notice was scored as having nothing.
_ES_LABEL = re.compile(r">\s*(espa[nñ]ol|spanish)\s*<", re.I)
_ES_ANCHOR = re.compile(r"<a\b[^>]*>([^<]{0,120}(?:espa[nñ]ol|spanish)[^<]{0,120})</a>",
                        re.I)
# A Spanish-language RESOURCE named in prose (a hotline, a notice, a guide). Requires
# an election/service word alongside, because a bare "Spanish" is usually just one
# entry in a Google Translate language menu — which is a machine translator, not
# provision, and must not be upgraded to it.
_ES_RESOURCE = re.compile(
    r"(?:espa[nñ]ol|spanish)[^.\n]{0,60}"
    r"(?:hotline|l[ií]nea|notice|aviso|ballot|boleta|guide|gu[ií]a|form|formulario"
    r"|sample|pdf|version|versi[oó]n|assistance|asistencia)"
    r"|(?:hotline|l[ií]nea|notice|aviso|ballot|boleta|guide|gu[ií]a|form|formulario"
    r"|sample|version|versi[oó]n|assistance|asistencia)[^.\n]{0,60}"
    r"(?:espa[nñ]ol|spanish)", re.I)
_TRANSLATE = re.compile(r"translate\.goog|google_?translate|goog-te|gtranslate|"
                        r"translate\.google", re.I)


def classify(county: str) -> dict:
    slug = county.lower().replace(" ", "_")
    text_hits, link_hits, widget = set(), 0, False
    pages_with_es = set()
    for f in sorted(glob.glob(str(ROOT / "snapshots" / slug / "*" / "page.txt"))):
        ptype = Path(f).parent.name
        txt = Path(f).read_text(encoding="utf-8", errors="ignore")
        html = Path(f).with_name("page.html").read_text(encoding="utf-8",
                                                        errors="ignore")
        hits = {w.pattern for w in _ES_RE if w.search(txt)}
        if len(hits) >= 4:          # a cluster, not a stray word
            text_hits |= hits
            pages_with_es.add(ptype)
        # Three independent ways a Spanish link can appear. The bare-label case
        # (">Espanol<") is a real language toggle and was the single biggest
        # contributor; dropping it in favour of the stricter resource test alone
        # moved 11 counties into "none" incorrectly, so all three are OR-ed.
        anchor_es = any(_ES_RESOURCE.search(m.group(1)) or "/es/" in m.group(0)
                        for m in _ES_ANCHOR.finditer(html))
        if (_ES_LINK.search(html) or _ES_LABEL.search(html) or anchor_es
                or _ES_RESOURCE.search(txt)):
            link_hits += 1
        if _TRANSLATE.search(html):
            widget = True

    if text_hits:
        level = "spanish_content"
    elif link_hits:
        level = "spanish_link"
    elif widget:
        level = "translate_widget"
    else:
        level = "none"
    return {"county": county, "level": level,
            "distinct_spanish_words": len(text_hits),
            "pages_with_spanish": ",".join(sorted(pages_with_es)),
            "spanish_links": link_hits,
            "translate_widget": str(widget).lower()}


def main() -> None:
    counties = sorted({r["county"].strip() for r in
                       csv.DictReader((ROOT / "manifest" / "targets.csv")
                                      .open(encoding="utf-8"))})
    rows = [classify(c) for c in counties]
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {OUT} ({len(rows)} counties)\n")
    for k, v in Counter(r["level"] for r in rows).most_common():
        print(f"  {k:<18}{v}")
    print("\ncounties with NO Spanish signal of any kind:")
    print(" ", ", ".join(r["county"] for r in rows if r["level"] == "none"))
    print("\ncounties with ONLY a machine-translation widget:")
    print(" ", ", ".join(r["county"] for r in rows
                         if r["level"] == "translate_widget"))


if __name__ == "__main__":
    main()
