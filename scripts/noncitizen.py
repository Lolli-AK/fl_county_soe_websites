#!/usr/bin/env python3
"""Flag whether a snapshotted page references non-citizen voting.

One boolean per page, written into meta.json by snapshot.py, so the day a
county or state adds this language shows up as a diff like any other change.

Why this is a tripwire and not a measurement. Across the 1,070 pages captured
by the county repos as of 2026-09, the phrase appears exactly zero times in
any spelling. The flag is expected to read false almost everywhere; its value
is that a single true is worth reading, which is only true if the term set
stays narrow.

That is why matching is a curated variant list rather than an edit-distance
score. "Fuzzy" over full page text at this specificity produces hits nobody
can explain six months later, and the interesting variants are not typos --
they are different vocabulary for the same policy ("documentary proof of
citizenship", "SAVE database"). Spelling slack is handled where it actually
occurs: the hyphen in non-citizen, which real pages write as a hyphen, an
en dash, a non-breaking hyphen, a space, or nothing at all.

Two families are deliberately EXCLUDED, because both appear on ordinary
eligibility pages and would fire on nearly every voter-registration page --
the page type most recently added to the manifests:

    "you must be a U.S. citizen"     eligibility boilerplate
    "citizenship" alone              appears in unrelated ID lists

The cost of that choice is that a page saying only "only citizens may vote"
reads as false. That is the intended reading: it is a statement of who may
vote, not a reference to non-citizen voting as a subject.

One real near-miss is worth knowing about. Several Florida sample-ballot pages
carry the historical ballot measures "Citizenship Requirement to Vote" and
"Property Rights for Aliens Ineligible for Citizenship". Neither matches, and
neither should: they are archival lists of what has appeared on a ballot, not
the county saying anything about non-citizen voting now. If the flag is ever
widened, those two are what a careless widening picks up first.

Matching runs on page.txt (normalized visible text), never on raw HTML, so
the result is deterministic and can be recomputed for any past commit --
see scripts/scan_noncitizen.py, which backfills the whole git history.
"""

from __future__ import annotations

import re

# Every way a real page writes the hyphen in "non-citizen": ASCII hyphen,
# non-breaking hyphen, figure dash, en dash, em dash, horizontal bar, a plain
# space, or nothing.
_H = r"[\s\-‐‑‒–—―]?"

# How much text may sit between the citizenship word and the voting word for
# the two proximity families. 60 characters is about one clause; wider starts
# joining unrelated sentences in nav-heavy text.
_NEAR = 60

_VOTE = r"(?:vot(?:e|es|er|ers|ing)|ballot|registration|register|registrant|roll)"

# (label, pattern). Labels are what land in meta.json, so they are stable
# identifiers -- renaming one rewrites history's meaning, not just its text.
PATTERNS: list[tuple[str, re.Pattern]] = [
    # The bare term in any spelling. Broadest of the set and the one most
    # likely to fire first.
    ("noncitizen", re.compile(rf"\bnon{_H}citizens?\b", re.I)),
    # The term in an explicitly electoral clause, either order. Recorded
    # separately from `noncitizen` because it is the stronger claim: the page
    # is talking about non-citizens *and voting*, not immigration generally.
    ("noncitizen_voting", re.compile(
        rf"\bnon{_H}citizens?\b.{{0,{_NEAR}}}?{_VOTE}"
        rf"|{_VOTE}.{{0,{_NEAR}}}?\bnon{_H}citizens?\b", re.I | re.S)),
    # "Illegal alien voting" and kin. The qualifier is REQUIRED, and bare
    # "alien" near a voting word is not enough, because "Alien Registration
    # Card" is a green card -- an ID document listed by name on the very
    # registration pages this watches. Proximity alone flagged those.
    ("alien_voting", re.compile(
        rf"\b(?:illegal|undocumented|unauthorized)\s+aliens?\b.{{0,{_NEAR}}}?{_VOTE}"
        rf"|{_VOTE}.{{0,{_NEAR}}}?\b(?:illegal|undocumented|unauthorized)\s+aliens?\b"
        rf"|\baliens?\s+(?:who\s+)?(?:vote|votes|voted|voting|register|registered)\b",
        re.I | re.S)),
    # Documentary-proof-of-citizenship requirements: the policy, not the
    # eligibility statement.
    ("proof_of_citizenship", re.compile(
        r"\b(?:documentary\s+)?proof\s+of\s+(?:u\.?\s?s\.?\s+|united\s+states\s+)?citizenship"
        r"|\bcitizenship\s+document(?:s|ation)?\b", re.I)),
    # List-maintenance and verification programs. "citizenship status" is
    # deliberately NOT here: the others name a program or an action, status
    # only describes a field, and it reads the same on an eligibility page as
    # on a purge notice.
    ("citizenship_verification", re.compile(
        r"\bcitizenship\s+(?:verification|check|audit|review|screening)\b"
        r"|\bverif(?:y|ies|ied|ying|ication\s+of)\s+(?:u\.?\s?s\.?\s+)?citizenship\b", re.I)),
    # SAVE is the federal database states actually use for these checks. The
    # acronym stays case-sensitive because "save" is an ordinary word ("save
    # time by voting early"); everything around it does not, or the program's
    # own title-case name would be missed.
    ("save_program", re.compile(
        r"(?i:\bsystematic\s+alien\s+verification\b)"
        r"|\bSAVE\s+(?i:program|database|system)\b"
        r"|(?i:\b(?:program|database|system)\s+known\s+as\s+)SAVE\b")),
]


def scan(text: str) -> dict:
    """Binary flag plus the evidence behind it.

    Returns `present`, the sorted `terms` that matched, and a total `count`.
    The boolean is what the request asked for; the other two are what makes a
    true auditable, and a true nobody can check is not much better than no
    flag at all.
    """
    terms: list[str] = []
    count = 0
    for label, rx in PATTERNS:
        n = len(rx.findall(text or ""))
        if n:
            terms.append(label)
            count += n
    return {"present": bool(terms), "terms": terms, "count": count}


def excerpts(text: str, window: int = 90, limit: int = 3) -> list[str]:
    """Surrounding text for each match, for the review scripts.

    Not written into meta.json: an excerpt is a slice of page content, and
    page content already lives in page.txt one directory up. Duplicating it
    into the metadata would put the same words in two artifacts that then
    diff independently.
    """
    out: list[str] = []
    for label, rx in PATTERNS:
        for m in rx.finditer(text or ""):
            start = max(0, m.start() - window)
            snippet = " ".join(text[start:m.end() + window].split())
            out.append(f"[{label}] …{snippet}…")
            if len(out) >= limit:
                return out
    return out
