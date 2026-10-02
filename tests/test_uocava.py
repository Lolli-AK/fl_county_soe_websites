"""What the UOCAVA flag must and must not match.

Every case here comes from the captured corpus: it is either a string the
detector matched across the 1,082 pages, or one of the three families it has
to keep excluding. Cases were found by running an over-broad net
(overseas|military|uniformed|armed forces|merchant marine|abroad|fpca|fwab|
fvap|uocava) over every unflagged page and reading what came back -- which is
how the `uniformed service members`, `merchant marine` and
`citizens outside of the U.S.` misses were caught.

Unlike the non-citizen flag, this one reads true on about a third of the
corpus, so the two halves matter about equally. A false positive corrupts a
cross-county comparison rather than raising a spurious alarm, and a false
negative makes a county look like it publishes nothing for these voters when
it does.

Run:  .venv/bin/python -m pytest tests/ -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import uocava  # noqa: E402


MATCHES = [
    # The statute. The corpus writes the act's name with an AMPERSAND, not the
    # "and" the statute itself uses; matching only "and" finds it zero times.
    ("UOCAVA ballots are counted", "uocava"),
    ("the Uniformed & Overseas Citizens Absentee Voting Act", "uocava"),
    ("the Uniformed and Overseas Citizens Absentee Voting Act", "uocava"),
    ("Uniformed Services and Overseas Citizens Absentee Voting Act", "uocava"),
    # The instruments. Both spellings of "Post Card" appear in the corpus.
    ("submit an FPCA", "uocava_form"),
    ("Federal Post Card Application", "uocava_form"),
    ("Federal Postcard Application", "uocava_form"),
    ("return the FWAB by mail", "uocava_form"),
    ("Federal Write-In Absentee Ballot", "uocava_form"),
    # Acronyms appear in Spanish copy too, so they are matched bare.
    ("o FPCA para una elección de enero", "uocava_form"),
    # The federal program and the officer who runs it on base.
    ("see FVAP for details", "fvap"),
    ("the Federal Voting Assistance Program", "fvap"),
    ("links to Federal Voting Assistance", "fvap"),
    ("visit fvap.gov", "fvap"),
    ("the voting assistance officer at each military installation", "fvap"),
    # The 2009 act.
    ("required by the MOVE Act", "move_act"),
    ("Military and Overseas Voter Empowerment Act", "move_act"),
    # The descriptive phrasing, in the joinings the corpus actually uses.
    ("Military and Overseas Voters", "military_overseas_voter"),
    ("Military & Overseas Voting", "military_overseas_voter"),
    ("Military/Overseas Voters", "military_overseas_voter"),
    ("Absent Military/Overseas", "military_overseas_voter"),
    ("overseas and military voters", "military_overseas_voter"),
    ("unless overseas or military voter deadlines apply", "military_overseas_voter"),
    ("overseas citizens", "military_overseas_voter"),
    ("canvass overseas ballots", "military_overseas_voter"),
    ("Overseas Vote Foundation", "military_overseas_voter"),
    ("uniformed service members", "military_overseas_voter"),
    ("a member of the uniformed services or merchant marine", "military_overseas_voter"),
    # The civilian half in plain language, with and without a residence verb.
    ("citizens living abroad are eligible", "citizen_abroad"),
    ("if you are studying abroad", "citizen_abroad"),
    ("a citizen residing outside the United States", "citizen_abroad"),
    ("temporarily overseas", "citizen_abroad"),
    ("members of the armed forces, their dependents, and citizens outside of the U.S.",
     "citizen_abroad"),
    ("if your ballot is submitted from outside the United States", "citizen_abroad"),
]

# The three families the detector has to keep out, all of them real strings
# from the corpus. Every one sits within a clause of a voting word, which is
# why matching is adjacency and not the proximity window noncitizen.py uses.
NON_MATCHES = [
    # 1. Military ID as an accepted photo-ID document, listed on voter-ID
    # pages. The single largest false-positive family.
    "United States military identification card containing the person's photograph",
    "U.S. passport, debit or credit card, military ID, student ID, retirement center ID",
    "photo identification card (driver's license, military ID, etc)",
    "United States Uniformed Services or Merchant Marine identification card",
    # 2. DD-214 recording. A county clerk records discharge papers; this is a
    # records service with no connection to voting.
    "DD 214 Military Discharge",
    "Military Discharge Records (DD-214)",
    "marriage licenses, birth and death records, military discharge",
    # 3. Placenames. "Overseas Highway" is US-1 through the Keys and appears
    # as a Monroe County, FL POLLING PLACE ADDRESS.
    "530 Whitehead St, Key West, FL — 103400 Overseas Highway",
    "Overseas Hwy, Marathon",
    # Florida publishes "Uniformed Ballot Structure" meaning a UNIFORM ballot
    # layout. This is why `uniformed` is always bound to `service(s)`.
    "Florida's Uniformed Ballot Structure video",
    "Uniformed Ballot Structure",
    # County history and a veterans outreach event: `military` with no
    # electoral noun attached.
    "settlers and military campaigns pushed them out",
    "if you are a hill country military veteran, a military dependent or a caregiver",
    # `move` is ordinary on registration pages; only "MOVE Act" counts.
    "If you move, you must update your address",
    "Moved recently? Update your registration.",
    # "voting assistance" without `federal` is disability/general help.
    "text to request curbside voting assistance",
    "reach out for voting assistance",
    # A residence phrase with no connection to being abroad.
    "located along the U.S. 290 corridor",
    "the office is located in the heart of the Texas hill country",
    "",
]


@pytest.mark.parametrize("text,expected_term", MATCHES)
def test_matches(text, expected_term):
    got = uocava.scan(text)
    assert got["present"], f"missed: {text!r}"
    assert expected_term in got["terms"], \
        f"{text!r} matched {got['terms']}, expected {expected_term}"


@pytest.mark.parametrize("text", NON_MATCHES)
def test_does_not_match(text):
    got = uocava.scan(text)
    assert not got["present"], f"false positive on {text!r}: {got['terms']}"


def test_shape_is_self_consistent():
    for text in [t for t, _ in MATCHES] + NON_MATCHES:
        got = uocava.scan(text)
        assert set(got) == {"present", "terms", "count"}
        assert got["present"] == bool(got["terms"])
        assert (got["count"] > 0) == got["present"]
        assert got["terms"] == sorted(got["terms"], key=[
            label for label, _ in uocava.PATTERNS].index)


def test_shape_matches_the_noncitizen_flag():
    """Both flags land in the same meta.json and are read by the same code."""
    import noncitizen
    assert set(uocava.scan("UOCAVA")) == set(noncitizen.scan("noncitizen"))


def test_acronyms_are_case_insensitive_but_move_is_not():
    # Not English words, and the corpus holds lowercase "fvap" and "fpca".
    assert uocava.scan("uocava")["present"]
    assert uocava.scan("fpca")["present"]
    # `move` is, so MOVE is case-sensitive like SAVE in noncitizen.py.
    assert not uocava.scan("please move act quickly on your registration")["present"]


def test_excerpts_explain_a_hit():
    text = "Notice: military and overseas voters should submit an FPCA."
    out = uocava.excerpts(text)
    assert out and any("uocava_form" in line for line in out)
    assert any("FPCA" in line for line in out)


def test_excerpts_are_empty_when_nothing_matched():
    assert uocava.excerpts("Bring your military ID to the polls.") == []
