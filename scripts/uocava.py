#!/usr/bin/env python3
"""Flag whether a snapshotted page carries UOCAVA voting information.

UOCAVA is the Uniformed and Overseas Citizens Absentee Voting Act: the federal
law covering military voters, their families, and civilians living abroad. One
flag per page, written into meta.json by snapshot.py, so the day a county adds
or drops this material shows up as a diff like any other change.

This is a MEASUREMENT, not a tripwire, and that is the one thing to hold onto
when editing it. Its sibling noncitizen.py watches for language that appears
zero times in 1,082 captured pages, so there a single true is the whole signal
and the term set is kept deliberately narrow. UOCAVA is the opposite: roughly a
third of the corpus mentions it, and the question being asked is comparative --
which counties publish this material, how much, and which of the two audiences
(uniformed or civilian-abroad) they name. So the labels are designed to be
counted and cross-tabulated, and each one has to mean the same thing in Harris
County as in Monroe County. A label that quietly widens breaks the comparison
rather than raising a false alarm.

Matching is ADJACENCY, not proximity, and that is load-bearing. The three
false-positive families below all sit within a clause of a voting word, so the
60-character proximity window that noncitizen.py uses would swallow every one
of them:

    "United States military identification card"   an accepted photo ID,
                                                   listed on voter-ID pages
    "DD-214 Military Discharge Records"            a county clerk RECORDING
                                                   service, not voting at all
    "Overseas Highway" / "Overseas Hwy"            US-1 through the Keys; it
                                                   is a Monroe County, FL
                                                   POLLING PLACE ADDRESS

Requiring `overseas` and `military` to sit directly against a voting word
excludes all three without an exclusion list, which is why there isn't one.
`military` in particular is never matched on proximity: it is the single most
common word in this space that has nothing to do with UOCAVA.

Spelling slack is spent where real pages actually vary:

  * The act's own name is written with an AMPERSAND in this corpus --
    "Uniformed & Overseas Citizens Absentee Voting Act" -- not the "and" the
    statute uses. Matching only `and` finds it zero times.
  * "Post Card" is the official spelling of the FPCA; pages write "Postcard"
    about as often.
  * Pages carry Spanish copy ("o FPCA para una elección"), so the acronyms are
    matched bare rather than anchored to English context around them.

The acronyms are matched case-insensitively, unlike SAVE in noncitizen.py.
UOCAVA, FPCA, FWAB and FVAP are not English words, so there is no ordinary
sentence for them to collide with, and the corpus already contains lowercase
"fvap" (in fvap.gov URLs) and lowercase "fpca". MOVE is the exception and is
handled like SAVE: "move" is ordinary on registration pages ("if you move,
update your address"), so it is case-sensitive and must be followed by "Act".

Matching runs on page.txt (normalized visible text), never on raw HTML, so the
result is deterministic and can be recomputed for any past commit -- see
scripts/scan_uocava.py, which backfills the whole git history.
"""

from __future__ import annotations

import re

# Nouns that make an adjacent `overseas` or `military` electoral. `citizens`
# is here because "overseas citizens" is the statutory phrase for the civilian
# half of UOCAVA; it is NOT enough on its own (see noncitizen.py, which
# excludes bare citizenship language for the opposite reason).
_VOTER = r"(?:voters?|vote|voting|citizens?|electors?|ballots?|registrants?)"

# The uniformed half, in the words pages use for it. `merchant marine` belongs
# here because UOCAVA covers it by name, and both states' pages say so
# ("member of the uniformed services or merchant marine").
#
# `uniformed` is ALWAYS bound to `service`/`services` and must stay that way.
# Florida publishes "Florida's Uniformed Ballot Structure" -- meaning a UNIFORM
# ballot layout, nothing military -- six times in this corpus. A bare
# `uniformed` picks it up.
_MIL = (r"(?:military|uniformed\s+services?|armed\s+forces"
        r"|merchant\s+marine)")

# What sits between "military" and "overseas" when a page names both:
# "and", "or", "&", a slash, or a comma. Real examples in the corpus include
# "military and overseas voters" and the heading "Absent Military/Overseas".
_JOIN = r"(?:\s*(?:and|or|&|/|,)\s*)"

# (label, pattern). Labels are what land in meta.json, so they are stable
# identifiers -- renaming one rewrites history's meaning, not just its text.
PATTERNS: list[tuple[str, re.Pattern]] = [
    # The statute, by acronym or by name. Strongest single signal: a page
    # naming the act is almost always a page built for these voters.
    ("uocava", re.compile(
        r"\bUOCAVA\b"
        r"|\buniformed\s+(?:services\s+)?(?:and|&)\s+overseas\s+citizens"
        r"\s+absentee\s+voting\s+act\b", re.I)),
    # The instruments: the registration/ballot-request form (FPCA) and the
    # backup ballot (FWAB). A page naming either is giving operational
    # instructions, not just acknowledging the category exists -- which is why
    # this is recorded apart from `uocava`.
    ("uocava_form", re.compile(
        r"\bFPCAs?\b|\bFWABs?\b"
        r"|\bfederal\s+post\s?card\s+application\b"
        r"|\bfederal\s+write[\s\-‐‑‒–—]?in\s+absentee\s+ballot\b", re.I)),
    # The federal program counties point voters to, and the officer who runs
    # it on base. "voting assistance" alone is NOT enough: the corpus has
    # "curbside voting assistance" and "reach out for voting assistance",
    # which are disability and general-help services.
    ("fvap", re.compile(
        r"\bFVAP\b|\bfvap\.gov\b"
        # `program` is optional: one page links the agency as bare "Federal
        # Voting Assistance". The `federal` qualifier is what does the work --
        # without it this fires on "curbside voting assistance".
        r"|\bfederal\s+voting\s+assistance(?:\s+program)?\b"
        r"|\bvoting\s+assistance\s+officers?\b", re.I)),
    # The 2009 act that set the 45-day ballot-transmission deadline. MOVE is
    # case-sensitive and must be followed by "Act", for the same reason SAVE
    # is case-sensitive in noncitizen.py.
    ("move_act", re.compile(
        r"\bMOVE\s+Act\b"
        r"|(?i:\bmilitary\s+(?:and|&)\s+overseas\s+voter\s+empowerment\b)")),
    # The descriptive phrasing, for pages that serve these voters without
    # naming the statute or a form. Adjacency only -- see the module docstring
    # for the three false-positive families that a proximity window picks up.
    ("military_overseas_voter", re.compile(
        # "military and overseas voters", "Absent Military/Overseas",
        # "overseas and military voters". The trailing noun is optional here
        # because the pairing is already specific enough on its own.
        rf"\b{_MIL}{_JOIN}overseas(?:\s+{_VOTER})?\b"
        rf"|\boverseas{_JOIN}{_MIL}(?:\s+{_VOTER})?\b"
        # "overseas voters", "overseas citizens", "overseas ballots".
        # Excludes "Overseas Highway", a Monroe County polling-place address.
        rf"|\boverseas\s+{_VOTER}\b"
        # "military voters", "military ballots", "uniformed service members".
        # Excludes "military ID" and "Military Discharge Records", which is why
        # `citizens` is dropped from the noun set here but kept above.
        rf"|\b{_MIL}\s+(?:voters?|vote|voting|electors?|ballots?"
        rf"|(?:service\s+|family\s+)?members?)\b"
        # The same relation with the noun in front: "member of the uniformed
        # services or merchant marine", "Members of the military reserves on
        # active duty", "spouse or dependent of a member of the military".
        rf"|\b(?:members?|spouses?|dependents?)\s+of\s+(?:the|an?)\s+{_MIL}\b"
        # "Absent Military", "absentee uniformed services voter".
        rf"|\babsent(?:ee)?\s+{_MIL}\b", re.I)),
    # The civilian half stated in plain language rather than as a category:
    # students, expatriates, anyone temporarily out of the country. Requires a
    # residence verb, so "located along the U.S. 290 corridor" does not match.
    ("citizen_abroad", re.compile(
        r"\b(?:liv(?:e|es|ing)|resid(?:e|es|ing)|stud(?:y|ies|ying)"
        r"|stationed|serving|temporarily|permanently)\s+"
        r"(?:abroad|overseas|outside\s+(?:of\s+)?the\s+"
        r"(?:U\.?\s?S\.?A?\b|United\s+States))"
        # ...and the same idea with the verb left out: "members of the armed
        # forces, their dependents, and citizens outside of the U.S." The gap
        # stops at a sentence boundary so this cannot bridge two sentences.
        r"|\b(?:citizens?|voters?|electors?|members?|ballots?)\b[^.\n]{0,30}?"
        r"\boutside\s+(?:of\s+)?the\s+(?:U\.?\s?S\.?A?\b|United\s+States)"
        r"|\bcitizens?\s+abroad\b", re.I)),
]


def scan(text: str) -> dict:
    """Binary flag plus the evidence behind it.

    Returns `present`, the sorted `terms` that matched, and a total `count`.
    Shape matches noncitizen.scan() exactly, so the two flags can be read,
    panelled and diffed by the same code.

    `count` is a raw occurrence total, not a page score. A county that repeats
    "military and overseas voters" in a nav menu on every page will out-count
    one with a single dedicated paragraph, so compare `present` and `terms`
    across counties and treat `count` as a within-page tiebreaker.
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
