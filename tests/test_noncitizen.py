"""What the non-citizen-voting flag must and must not match.

Every case here is one the detector got wrong at some point, or one it would
plausibly get wrong under a small edit. The false-positive half matters more
than the true-positive half: this flag is a tripwire that reads false almost
everywhere, so a spurious true is not noise, it is the whole signal being
wrong.

Run:  .venv/bin/python -m pytest tests/ -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import noncitizen  # noqa: E402


MATCHES = [
    # The bare term, in the spellings real pages use.
    ("noncitizens may not vote", "noncitizen"),
    ("non-citizen voting is a felony", "noncitizen"),
    ("NON CITIZEN VOTING", "noncitizen"),
    ("non‑citizens are not eligible to register", "noncitizen"),
    ("non–citizen registration", "noncitizen"),
    # The electoral clause, recorded separately from the bare term.
    ("noncitizens who vote may be prosecuted", "noncitizen_voting"),
    ("voters found to be non-citizens", "noncitizen_voting"),
    # Alien vocabulary, but only qualified or explicitly electoral.
    ("illegal aliens voting in November", "alien_voting"),
    ("aliens who vote commit a felony", "alien_voting"),
    ("an undocumented alien registered to vote", "alien_voting"),
    # The policy, not the eligibility statement.
    ("documentary proof of citizenship is required", "proof_of_citizenship"),
    ("bring your citizenship documents", "proof_of_citizenship"),
    ("citizenship verification", "citizenship_verification"),
    ("the county verifies citizenship annually", "citizenship_verification"),
    # The federal database, including its real title-case name.
    ("Systematic Alien Verification for Entitlements", "save_program"),
    ("checked against the SAVE Program", "save_program"),
    ("the SAVE database", "save_program"),
]

# Each of these fired at some point, or would under an obvious loosening.
NON_MATCHES = [
    # Eligibility boilerplate. Excluded by design -- it is on nearly every
    # registration page, which is the page type this flag most needs to read.
    "You must be a U.S. citizen to register to vote.",
    "To register you must be a citizen of the United States and 18 years old.",
    "Proof of residency and citizenship status: see the eligibility list.",
    # "Alien Registration Card" is a green card, listed by name as an accepted
    # ID document on registration pages. Bare `alien` near a voting word
    # flagged all of them until the qualifier was made mandatory.
    "Bring your Alien Registration Card to register to vote",
    "Permanent Resident Card (Alien Registration Receipt Card)",
    "Alien Registration Number (A-Number)",
    # `save` is an ordinary word; only the acronym is case-sensitive.
    "Save time by voting early!",
    "Save this page to your bookmarks",
    # Placenames and unrelated uses.
    "Alien Street polling place",
    "",
]


@pytest.mark.parametrize("text,expected_term", MATCHES)
def test_matches(text, expected_term):
    got = noncitizen.scan(text)
    assert got["present"], f"missed: {text!r}"
    assert expected_term in got["terms"], \
        f"{text!r} matched {got['terms']}, expected {expected_term}"


@pytest.mark.parametrize("text", NON_MATCHES)
def test_does_not_match(text):
    got = noncitizen.scan(text)
    assert not got["present"], f"false positive on {text!r}: {got['terms']}"


def test_shape_is_self_consistent():
    for text in [t for t, _ in MATCHES] + NON_MATCHES:
        got = noncitizen.scan(text)
        assert set(got) == {"present", "terms", "count"}
        assert got["present"] == bool(got["terms"])
        assert (got["count"] > 0) == got["present"]
        assert got["terms"] == sorted(got["terms"], key=[
            label for label, _ in noncitizen.PATTERNS].index)


def test_excerpts_explain_a_hit():
    text = "Notice: proof of citizenship is now required for new registrants."
    out = noncitizen.excerpts(text)
    assert out and "proof_of_citizenship" in out[0]
    assert "proof of citizenship" in out[0]


def test_excerpts_are_empty_when_nothing_matched():
    assert noncitizen.excerpts("You must be a U.S. citizen.") == []
