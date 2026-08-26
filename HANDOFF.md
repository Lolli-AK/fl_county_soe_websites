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

**Section 203 (86 FR 69611, Docket 211029-0221, applicable 2021-12-08).** Florida has
**14** Spanish-covered counties: Broward, Collier, DeSoto, Hardee, Hendry,
Hillsborough, Lee, Miami-Dade, Orange, Osceola, Palm Beach, Pinellas, Polk, Seminole.
The notice states counties not listed are exempt despite the statewide row. Glades is
covered for **Seminole**, an American Indian language, not Spanish.

Cross-referenced against provision: **0 covered counties have no Spanish signal.**
5 of the 14 offer **only a Google Translate widget** — DeSoto, Hardee, Palm Beach,
Pinellas, Polk.

**No DOJ position on machine translation exists.** 28 CFR Part 55 never says
"website", "internet", "online", "electronic", or "digital", and has zero hits for
machine/automated translation. No DOJ decree or settlement addresses it. The
governing standards are "clear, complete and accurate" (§ 55.19(b)), "all reasonable
steps" / "effectively informed" (§ 55.2(b)), and compliance "best measured by
results" (§ 55.16).

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

1. **Post-election takedown.** Needs snapshots in the weeks *after* Nov 3. The cron
   produced nothing Aug 7–17, so the primary has two endpoints and no curve. Flip
   `ELECTION_WINDOW=true` and confirm the workflow actually fires.
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
