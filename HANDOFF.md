# Handoff — fl-county-watch (and its Texas sibling)

Written 2026-08-25. Read this first, then the README. Most of the *reasoning* lives
in code comments; this file carries the *state*: what is established, what was
retracted, what is still open.

---

## 1. What exists

Two sibling repos under `election_websites/`:

| repo | scope | anchor page |
|---|---|---|
| `fl-county-watch` | 67 FL counties, 314 targets | Supervisor of Elections site |
| `tx-county-watch` | 254 TX counties, 756 targets | county government site |

The snapshot pipeline (`snapshot.py`, `normalize.py`) is shared. Everything else in
FL is Florida-specific.

**Analysis scripts (FL only — Texas has none of these):**

| script | what it does |
|---|---|
| `analyze_diffs.py` | classify + categorize what changed between two snapshot revisions |
| `write_digest.py` | render that as one plain-English row per county |
| `build_county_attributes.py` | join behaviour to platform, services vendor, rurality, population |
| `check_consistency.py` | do counties state the operational facts, and correctly? |
| `check_language_access.py` | Spanish provision, joined to Section 203 coverage |
| `figures_election_day.R`, `figures_consistency.R` | ggmedsl figures |

Re-run order after any new snapshot:
```
python scripts/snapshot.py
python scripts/build_county_attributes.py --rucc <RUCC.csv> --from <rev> \
    --election-from 50a50696 --election-to 5d198b83
python scripts/check_consistency.py
python scripts/check_language_access.py
Rscript scripts/figures_election_day.R
Rscript scripts/figures_consistency.R
```
`--election-from/--to` must stay pinned to the election-day window; recomputing that
flag from a later diff finds zero switchers because the counties reverted.

---

## 2. Established findings

**Election-night failover (2026-08-18).** Ten counties — Calhoun, Dixie, Gadsden,
Holmes, Jackson, Lafayette, Liberty, Wakulla, Walton, Washington — replaced their
whole site with a minimal static election-night page. All ten run WordPress.
Two failure modes for old URLs:
- 5 counties: old deep URLs returned **404** (fails loudly)
- 5 counties: old URLs returned **HTTP 200 serving the replacement page** — one
  identical body at every path. A scraper checking only status codes records the
  wrong page as real. This is the more dangerous mode.

**All ten fully reverted within two days.** Every 404 was back to 200 by 08-20. Do
not "fix" the manifest for these; the URLs self-heal.

**Sharpened 2026-08-26 — the switch was SYNCHRONIZED, and it lasted ~33 hours, not
"two days."** With the recovered 3-hourly series (`analyze_failover_timing.py`), all
ten counties left their steady state inside the **same 2.5-hour window** (08-18
01:20–03:49) and returned inside the **same 3.2-hour window** (08-19 09:40–12:51),
across all five page types — 44 targets, median shrink 90.7%. Ten independent county
decisions do not land in one 2.5-hour window. The replacement pages share a single
template, county-customized: the same "Thank you for visiting - and for voting!"
line, the same Fla. Stat. 668.6076 email notice, the same link vocabulary. **Treat
this as one vendor / shared-administrator push, not ten local choices.** This is
open thread 6 (synchronized-diff detection), answered.

The 27-vs-17 target split inside that cluster independently reproduces the two
failure modes above: 27 targets served the replacement page at HTTP 200, 17 went
404/0 bytes.

**Size, not vendor, tracks the behaviour.** Among the 21 WordPress counties, the 10
that switched span 7,974–75,305 population; the largest switcher is smaller than the
median non-switcher (86,613). 0 of 16 large metros switched; 5 of 11 rural counties did.

**Vendor clustering is a Texas story, not a Florida one.**
- TX: **170 of 254 counties (67%) run ezTask Titanium**, supplied via the Texas
  Association of Counties. CivicPlus 41 (16%). Verified against the TX corpus.
- FL: fragmented — WordPress 22, other/unknown 17, CivicPlus 15, Revize 9, DNN 2,
  Drupal 1, Granicus 1.
- **No cross-state spillover.** Clustering follows state purchasing vehicles, so it
  stops at state lines. Zero TX county sites reference VR Systems; ezTask is absent
  from FL.

**Extended to the neighbouring states, 2026-08-26** (`probe_vendor_spillover.py`,
`manifest/vendor-spillover.csv`). Probed every county in FL's land neighbours (GA,
AL) and TX's (LA, AR, NM, OK), with TX and FL re-probed on county-government domains
as controls:

- **ezTask: 61 resolved TX counties, 0 of 189 resolved counties across all six
  neighbours.** No neighbour shows any comparable concentration — the largest is
  CivicPlus at 24% of GA's 84 resolved (NM's 46% is over 13 counties, too few to
  lean on) against ezTask's 56% of TX's 108. **The neighbours look like Florida.**
- **CivicPlus is in all eight states**; Revize, Granicus and WordPress in most.

So the rule is not "vendors stop at state lines" — it is that the **mechanism**
decides. A vendor supplied through a state association is state-bounded by
construction; a national commercial vendor is not. State the mechanism, not the
border.

**The mechanism, now named from primary evidence.** Not "the Texas Association of
Counties" loosely but **TAC's County Information Resources Agency (CIRA)**:
`county.org/TAC-CIRA` appears on 116 TX county sites carrying a `cira_logo.png`
credited to TAC, 152 of the 170 ezTask counties reference TAC/county.org, and CIRA's
own service pages describe two website packages (Essential, Ultimate) both hosted on
ezTask. CIRA has run since 2001.

*Limitation to quote if this is published:* URL discovery is guessed from per-state
domain conventions; resolve rates are 25–53%. ~90% of misses are nonexistent domains
and most of the rest JS shells serving no matchable text. This does not threaten the
null — ezTask is server-rendered ASP.NET, so JS-shell misses are systematically
not-ezTask, and an association-supplied fleet would follow a consistent domain
convention and so resolve *more* readily, not less.

**Section 203 (86 FR 69611, Docket 211029-0221, applicable 2021-12-08).** Florida has
**14** Spanish-covered counties: Broward, Collier, DeSoto, Hardee, Hendry,
Hillsborough, Lee, Miami-Dade, Orange, Osceola, Palm Beach, Pinellas, Polk, Seminole.
The notice states counties not listed are exempt despite the statewide row. Glades is
covered for **Seminole**, an American Indian language, not Spanish.

Cross-referenced against provision: **0 covered counties have no Spanish signal.**
5 of the 14 offer **only a Google Translate widget** — DeSoto, Hardee, Palm Beach,
Pinellas, Polk.

**Independently confirmed 2026-08-26** against Florida's own DE Reference Guide 0004
("Minority Language Requirements in Florida", rev. 01/01/2022): the state's Table 1
lists exactly those 14 Spanish counties, plus Glades as **"American Indian"** and
statewide Spanish coverage. Note the label mismatch — the Federal Register row for
Glades says **"Seminole"**; Florida's own guide generalises it to "American Indian".

**§ 203 is the wrong denominator for Florida Spanish, and this matters.** The same
guide records two obligations the 14-county frame misses entirely:

- **§ 4(e) applies to ALL 67 counties.** Fla. Admin. Code r. 1S-2.032 (uniform
  ballots and language) and 1S-2.034 (polling-place language assistance), both
  effective 04/23/2020, are statewide.
- ***Madera v. Detzner*, No. 1:18-cv-152-MW/GRJ (N.D. Fla. Sep. 7, 2018)** put **32
  named counties** under a federal preliminary injunction for Spanish sample ballots
  and poll assistance — Alachua, Bay, Brevard, Charlotte, Citrus, Clay, Columbia,
  Duval, Escambia, Flagler, Hernando, Highlands, Indian River, Jackson, Lake, Leon,
  Levy, Manatee, Marion, Martin, Monroe, Okaloosa, Okeechobee, Pasco, Putnam,
  St. Johns, St. Lucie, Santa Rosa, Sarasota, Sumter, Taylor, Wakulla. Only two
  (Highlands, Monroe) are also § 203 counties.

So "does provision match obligation" should be run against **§ 203 (14) ∪ Madera (32)
∪ statewide 4(e) (67)**, not § 203 alone. `check_language_access.py` currently joins
only § 203 — the biggest single upgrade available to that script.

Also: the same guide lists "**Making website materials/information available in the
designated minority language**" under Table 2 *recommended best practices* — i.e.
Florida itself frames website language provision as recommended, not required. That
cuts both ways and belongs in the § 8 argument.

**No DOJ position on machine translation exists** — *still true as stated, but it was
being over-read, corrected 2026-08-26.* 28 CFR Part 55 never says "website",
"internet", "online", "electronic", or "digital", and has zero hits for
machine/automated translation. The governing standards are "clear, complete and
accurate" (§ 55.19(b)), "all reasonable steps" / "effectively informed"
(§ 55.2(b)), and compliance "best measured by results" (§ 55.16).

**But "Part 55 doesn't say website" does NOT mean § 203 doesn't reach websites.** Two
federal sources say otherwise, and neither was in the earlier read:

- **DOJ Civil Rights Division states websites are inside its review scope.** Its
  language-minority enforcement page says the Division looks at "the full range of
  information provided by covered jurisdictions to voters in English — not just the
  ballot and election pamphlets themselves, but also newspaper notices required by
  state law, **website information**, and other election information — and seeks to
  determine whether the same information is being made available to each language
  minority community." So the absence of the word from the 1976-era regulation is a
  fact about the regulation's age, not a safe harbour.
- **Federal LEP guidance addresses machine translation head-on** (a *different*
  statutory track — Title VI, not § 203 — so cite it as analogy, never as § 203 law):
  DOJ, OJP and HHS language-access plans all say machine translation should not be
  used without qualified human review.

Net: a covered county running a Translate widget is **not in a settled violation** —
no § 203 authority names widgets — but "§ 203 has no online standard, therefore
widgets are fine" is not supportable either. See §8 for the argument on both sides.

**Counties do not contradict the state; they lag it.** Zero confirmed contradictions.
All remaining flags are the *primary's* correct dates still displayed after the
primary. FL DOE calendar verified 2026-08-25: general 11/03, book closing 10/05,
mandatory early voting 10/24–31 (primary was 07/20 and 08/08–15).

---

## 3. Retracted — do not repeat these

**"48 of 67 Florida counties run one website vendor (VR Systems)."** WRONG. The
detector checked outbound links to `voterfocus.com` / `vrswebapps.com` first — those
are voter-lookup *services* counties link to, not the CMS. 60 of 67 link VR
regardless of platform. **Platform and services vendor are two different variables**
and are now detected separately.

**"normalize.py strips the platform fingerprint."** WRONG. Comparing stored artifacts
against raw fetches showed the tells survive. The large unknown bucket was the
detector missing Revize and Granicus entirely, plus a WordPress test that required an
absolute same-host asset URL. Fixing both took unknown from 28 to 17.

**"5 Section 203 counties have no Spanish signal."** WRONG on both inputs — the
covered-county list was from memory (~29 counties, roughly double the real 14) and
the detector was homepage-only. Correct answer is zero.

**"Rural counties' pages are more likely to carry an operational fact."** This is a
Texas finding and **does not replicate in Florida**: facts/page metro 0.300 vs
nonmetro 0.292; facts/4 metro 0.350 vs nonmetro 0.330. FL rural counties also publish
about as many pages as metros (4.64 vs 4.71), so the Texas premise doesn't hold here
either. Treat as state-specific until shown otherwise.

---

## 4. Measurement caveats that matter

- **"Not comparable"** in any figure = the plain/headless capture path differed
  between snapshots, so line counts aren't comparable. It is a pipeline artifact, not
  county behaviour. Cause: GitHub Actions runs from datacenter IPs that get
  challenged; local runs don't. **Pin render mode per target before November** —
  this currently costs Broward, the second-largest county.
- **Category columns never sum to "lines changed."** A line can match several
  categories; many match none. Use "lines changed" for magnitude only.
- **Consistency checking is dominated by false positives.** Three separate rounds of
  them: office hours read as poll hours, early-voting hours read as poll hours,
  municipal-election hours read as statewide. Extraction is easy; disambiguating
  which election and which voting mode a statement refers to is the hard part.
- **"Never states it" is an upper bound.** Phrasing varies enormously; at least 9 of
  54 counties marked as not stating poll hours actually do, in wording the extractor
  missed.
- **FL vs TX page-type completeness (76% vs 20%) is confounded twice**: FL `homepage`
  is a dedicated elections site while TX's is a county front page, and the metric
  counts *distinct URLs*, penalising counties that consolidate. For a fair
  comparison, exclude `homepage` and use the fact-coverage measure instead.
- **17 FL platforms remain "other/unknown"** (25%). Unresolved; likely custom
  regional builds. Hand-verification would also validate the classifier.

---

## 5. Open threads

1. ~~**Post-election takedown.**~~ **RESOLVED 2026-08-26 — the premise was false.**
   The cron fired the whole time. `ELECTION_WINDOW` is already `true`, the workflow
   has been running every ~3 hours since 08-12, and the remote holds **9 snapshots a
   day including 9 on election day itself**. Nothing was missing; the local clone had
   simply never been pulled, so `git log` showed a gap that existed only locally.
   See §7. The post-Nov-3 takedown curve now needs no setup — only that
   `ELECTION_WINDOW` stay `true` through November.
2. **Website richness as a capacity proxy**, validated against EPI/EAVS. Probably the
   most publishable idea; turns the scraper into a measurement instrument.
3. **Section 203 + machine translation.** The 5 widget-only covered counties, and
   especially **Glades** (Seminole — no machine translator offers it at all).
4. **Registered voters** instead of population as the size measure. FL publishes it
   behind a JS-injected link; add a `registered_voters` column and the scripts prefer it.
5. **SOE budgets.** Not centrally published; would need the FL Dept of Financial
   Services local-government reporting database. A project, not a pull.
6. **Synchronized-diff detection** — a vendor template push shows up as the same diff
   on the same day across many counties. Detectable, and nobody has it.

---

## 6. Environment

`.venv` needs `pip install -r requirements.txt` + `playwright install chromium`.
R figures need **ggmedsl**, a private MIT lab package — not on CRAN, won't exist on a
cloud box. `usmap` is NOT installed; maps use `tigris` + `sf`.

**Load the `medsl-dataviz` skill before writing any plotting code.**

Gotchas that cost real time: `readr` parses `"true"`/`"false"` into logicals (compare
as logical, not string); the USDA RUCC file is Latin-1; `plot_annotation()` has no
`tag` slot; long legends on side-by-side maps need `title.position = "top"` plus row
wrapping or they clip off-canvas.

---

## 7. The local clone had diverged from the remote (fixed 2026-08-26)

Worth knowing because it invalidated the single biggest "open thread" above and
would have done so again.

`origin` (github.com/Lolli-AK/fl_county_soe_websites) had **137 commits the local
clone did not**, all automated snapshot runs from 08-07 to 08-26. The local clone had
**15 commits the remote did not** — the entire analysis layer (`analyze_diffs`,
`write_digest`, `build_county_attributes`, `check_consistency`,
`check_language_access`, both R figure scripts, all figures, HANDOFF). They diverged
at `50a50696` (08-06) and never met.

Reading `git log` locally therefore showed a snapshot gap of 08-07..08-17 that did
not exist anywhere but locally. **That is where "the cron produced nothing" came
from.** The cron was fine.

Reconciled by merging `origin/main` with snapshot artifacts resolved to the remote's
08-26 capture and every local-only analysis file kept — safe because the two sides
never touched the same file outside `snapshots/` (the bot only ever writes
artifacts). One trap: `-X theirs` left 91 body files at the local 08-18 state while
all 314 `meta.json` claimed the 08-26 fetch, so stored `html_sha256`/`text_sha256`
no longer matched their bodies. Fixed by restoring `snapshots/` wholesale from
`origin/main`; verified 628/628 hashes coherent, 39/39 tests pass.

**Before trusting any local `git log` here, run `git fetch && git status`.**
`ELECTION_WINDOW` is currently `true`, so the remote gains ~9 commits a day and a
local clone goes stale within hours.

**Cadence — decided 2026-08-26.** Dropped to the daily baseline for now, raised
automatically for the general.

- `ELECTION_WINDOW` set to **`false`** (was `true` since 08-11 19:53 UTC, which is
  exactly why the primary got 3-hourly coverage). Cadence is now the daily 08:00 UTC
  baseline — effective immediately, no push needed, since it is a repo variable.
- The workflow gained an **automatic high-cadence window, 2026-10-20 → 2026-11-17**,
  evaluated in a gate step because an Actions `if:` cannot see the date. It opens
  before mandatory early voting (Oct 24–31) and closes two weeks *after* Nov 3,
  because the post-election takedown curve is the whole point and lives after
  election day. **Nothing has to be remembered in October.**
- `ELECTION_WINDOW=true` still overrides in either direction at any time.
- Gate logic was tested across ten event/date/variable combinations before commit;
  boundaries are inclusive (Oct 19 skips, Oct 20 captures, Nov 17 captures, Nov 18
  skips).

Trade-off accepted: outside the window the 3-hourly cron still starts a runner and
exits at the gate — ~8 short starts a day. That is the price of the `if:` limitation,
and it is far cheaper than either full runs or a missed general election.

---

## 8. Section 203 and machine translation: the argument on both sides

Asked for directly. Short answer: **it does not clearly lean either way, and the
honest position is that the question is open** — but the two sides are not
symmetric, and the asymmetry is the interesting part.

### The county-level bottom line first

- **None of the 14 § 203 Spanish counties is in a known violation.** Zero have no
  Spanish signal at all, and DOJ has brought no action against any of them.
- **The 5 widget-only counties** (DeSoto, Hardee, Palm Beach, Pinellas, Polk) are the
  live question — not because a rule condemns them, but because **no rule addresses
  them**, in either direction.

### For "a Translate widget can satisfy § 203"

1. **No authority says otherwise.** 28 CFR Part 55 predates the web and never says
   website, online, or digital; no § 203 case, decree or settlement turns on machine
   translation. Nothing to violate.
2. **Compliance is "best measured by results" (§ 55.16).** If a Spanish-speaking
   voter can in fact read the page, the mechanism is arguably not the test.
3. **The statute names materials, not media.** § 203's list — notices, forms,
   instructions, ballots — is about election materials. A website reproducing
   information available elsewhere in Spanish may be surplus to the obligation.
4. **Florida's own guidance treats it as optional.** DE Guide 0004 puts website
   language provision in Table 2 *recommended best practices*, not the requirement.
5. **The obligation runs to information, and a widget covers 100% of the page** —
   including material a hand-translated subset would miss.

### Against

1. **DOJ says websites are in scope.** Its Civil Rights Division reviews "website
   information" alongside ballots and notices, asking whether the same information
   reaches each language minority community. § 203's own text is medium-agnostic:
   "other materials or **information** relating to the electoral process."
2. **§ 55.19(b) requires translations be "clear, complete and accurate."** Nobody
   warrants machine output as accurate, and the county cannot inspect what the widget
   will emit tomorrow. It also asks whether the jurisdiction *consulted language
   minority group members* about the translation — a widget involves no consultation
   by construction.
3. **Every other federal language-access track has already answered it the other
   way.** DOJ, OJP and HHS all say machine translation must not be used without
   qualified human review. § 203 is a different statute, so this is analogy, not
   authority — but it makes "machine translation is presumptively fine" an outlier.
4. **§ 55.2(b) requires "all reasonable steps" so voters are "effectively
   informed."** Embedding a third-party widget is closer to no step than to all
   reasonable ones, and it **outsources a non-delegable legal duty to a vendor** with
   no election expertise, no accuracy obligation, and no duty of continuity.
5. **Ballot-specific vocabulary is exactly where machine translation fails** —
   "precinct", "provisional ballot", "vote-by-mail cure affidavit", "book closing".
   A plausible-looking wrong translation is worse than none, because it is trusted.

### Where it actually lands

The strongest anti-widget argument is **#2 plus #4**: not "machine translation is
banned" but that a county relying on a widget has **no way to demonstrate**
compliance with an affirmative, results-measured standard it is required to meet.
The strongest pro-widget argument is **#1**: absent any authority, a county cannot be
in violation of a rule that does not exist.

Those are both right, which is why this is a **research contribution rather than a
compliance finding**. The publishable claim is not "these counties are violating
§ 203" — they are not — but that **a measurable, consequential gap has opened between
what the statute demands and what the regulation describes**, and that gap is
occupied almost entirely by machine translation. That is a paper.

### Glades County — and it is a better story than "no translator supports Seminole"

The premise is right: **no machine translator supports the language.** Google
Translate covers no North American Indigenous language; Muscogee (Creek) and Mikasuki
are both absent, and Google has said publicly why (training data). Glades runs a
**GTranslate** widget (WordPress, Elementor), so its widget cannot offer the one
language it is covered for.

Three facts make Glades sharper than that, though:

1. **The covered community is Creek-speaking, not Mikasuki.** Glades' coverage comes
   from the **Brighton Reservation** of the Seminole Tribe of Florida, in the county's
   northeast. Brighton is the **Muscogee (Creek)** community — Mikasuki is the other
   Florida Seminole language (Big Cypress, Hollywood, and the separate Miccosukee
   Tribe). Fewer than ~200 Brighton residents speak Muscogee, the largest such
   population outside Oklahoma. "Seminole" in the Federal Register is a census label
   covering both; the county's actual obligation is Creek.
2. **§ 203 probably requires NO written translation here at all.** 52 U.S.C.
   § 10503(c): where the minority language "is oral or unwritten," or for American
   Indians whose "predominant language is historically unwritten," the jurisdiction
   "is only required to furnish **oral** instructions, assistance, or other
   information." **28 CFR 55.12(c)** goes further and is the decisive text: only oral
   assistance and publicity are required; **a language may be treated as unwritten
   even if a written form exists, where it is not commonly used in writing**; and
   **"it is the responsibility of the covered jurisdiction to determine whether a
   language should be considered written or unwritten."** Creek does have a Latin
   orthography, but it is not in common written use in Florida. So Glades can
   lawfully classify Seminole as unwritten and owe **oral assistance only**.
3. **Which inverts the finding.** The interesting thing about Glades is *not* that its
   widget fails to offer Seminole — under § 55.12(c) the widget was never the relevant
   instrument. It is that **Glades' site shows no language assistance of any kind**:
   across all five captured pages there is no mention of Seminole, Creek, Mikasuki,
   an interpreter, oral assistance, or a language-assistance phone number. The
   § 203 duty it does have is an *oral* one, and the natural way to publicise an oral
   service is to say on the website that it exists. **The gap is not a translation
   gap; it is a publicity gap** — and § 55.20 makes publicity part of the obligation.

That is the reportable Glades finding, and it is defensible: no claim that Glades
violates a translation rule it is exempt from, and a specific, evidenced observation
about the duty it actually has.

**Caveats before this goes anywhere.** (a) Whether Glades has *in fact* classified
Seminole as unwritten is not published anywhere we found — it should be asked, not
assumed; the § 55.12(c) argument establishes that it *may*, not that it *did*.
(b) Our evidence covers only the five captured pages, so an unlinked language page
would be invisible. (c) The claim that a DOJ consent decree requires online Spanish
(Union County, NJ, 2023, §§ 203 and 208) came from a search summary — justice.gov
blocked direct retrieval of the release and the DOJ case list itself mentions no
website. **Do not cite it until the decree text is read.**
