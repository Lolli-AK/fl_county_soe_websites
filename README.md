# fl-county-watch

Snapshots a fixed set of **Florida county election web pages** on a schedule and
stores each run as a **git commit**, so their content can be **diffed over time** —
specifically to see how pages change **before and after an election**.

This is the ["git scraping"](https://simonwillison.net/2020/Oct/9/git-scraping/)
pattern: one commit per run, `git diff` / `git log` are the history UI.

Design priorities (in order):

1. **Lightweight, text-based, diff-friendly artifacts.** No PDFs, screenshots, or WARC.
2. **Deterministic output** — re-running against an unchanged site produces a **zero diff**.
3. **Git is the datastore.** History lives in commits, not dated folders.

Scope: **all 67 Florida counties** — the complete set.

This is the Florida sibling of `tx-county-watch`. The snapshot pipeline
(`snapshot.py` and the bulk of `normalize.py`) is shared almost verbatim; discovery, verification
and the manifest are Florida-specific for the reason in the next section.

---

## The one thing that makes Florida different

Florida elects an independent **Supervisor of Elections (SOE)** in each county, who
runs their own website separate from the county government's. Texas has no
equivalent — there, election pages live on the county's own site, under the county
clerk or an elections administrator.

So in this project **`homepage` means the Supervisor of Elections front page**, not
the county government front page. Three consequences worth knowing before reading
anything else:

| | tx-county-watch | fl-county-watch |
|---|---|---|
| what `homepage` is | county government front page | **SOE front page** |
| how homepages were found | probing domain patterns, 230 unknown | **published by the state**, then verified |
| `external` flag | common — counties hand off to separate election portals | **rare** — we already start on the elections domain |
| per-county completeness | mostly 2/5 (rural counties fold everything into one page) | **mostly 5/5** (a single-purpose agency publishes all of it) |

The trade-off of anchoring on the SOE site: you lose the county-government homepage's
alert banners. What you gain is that all five captured pages belong to the office that
actually runs the election, which is what a before/after-election diff is about.

**Nothing hardcodes a county list or count.** `manifest/counties.csv`
(`county, seat, office_city, batch, homepage`) is the seed of truth and
`manifest/targets.csv` is what the pipeline iterates; the count is always
`len(manifest)`. Adding, removing or correcting a county is a **manifest edit, not a
code change** — see [Editing the manifest](#editing-the-manifest).

---

## What it captures

Five target page types per county:

| type | what |
|---|---|
| `homepage` | Supervisor of Elections front page (alert banners, election countdowns) |
| `elections` | elections / voter-info landing page ("Upcoming Elections", "Election Dates") |
| `polling` | polling places / precinct finder |
| `early_voting` | early voting sites & schedule |
| `results` | election results / returns |

Not every county publishes a distinct page for every type. **A missing target is
expected data, not an error** — it's recorded as a gap in the manifest, with the
reason in `notes`.

## Coverage

**67 counties · 335 manifest rows · 314 pages captured · 21 recorded gaps.**

| page type | captured | why the rest are gaps |
|---|---|---|
| `homepage` | **67 / 67** | — every county has one |
| `elections` | **64 / 67** | Baker, Lafayette and Escambia publish election info on the SOE homepage itself, with no distinct landing page |
| `polling` | **65 / 67** | folded into the elections page, or published only as a per-election PDF |
| `early_voting` | **61 / 67** | a few counties publish early voting only inside a per-election page, or not as HTML |
| `results` | **57 / 67** | smaller counties post returns as PDFs, or link the statewide portal (rejected — see below) |

Per-county completeness — the inverse of Texas's shape:

| pages captured | counties |
|---|---|
| 5 / 5 | **50** |
| 4 / 5 | 13 |
| 3 / 5 | 4 |

50 of 67 counties are complete because an SOE office exists only to run elections, so
it publishes polling, early voting and results as standing pages. In Texas the
equivalent number was 50 **of 254**, because there a rural county's elections content
is a subsection of a general county website.

The 21 gaps, by recorded reason:

| reason | rows |
|---|---|
| no distinct page found (folded into another page) | 8 |
| no county-specific page found (best candidate was an unrelated third-party site) | 6 |
| established by hand during the QA pass — nav, conventional paths and sitemap all checked | 6 |
| candidate is non-HTML (PDF-only) | 1 |

**Every gap row carries its reason in `notes`** — a gap is recorded data, not a
failure, and a test enforces that no gap row is silent.

## What it stores (per captured page)

Three text artifacts per page, under `snapshots/<county>/<page_type>/`:

| file | what |
|---|---|
| **`page.html`** | cleaned, normalized HTML — the structural-diff artifact |
| **`page.txt`** | visible text only — the primary, lowest-noise human-readable diff |
| **`meta.json`** | metadata sidecar: requested/final URL, redirect chain, HTTP status, content type, render mode, `external` flag, `fetched_at`, `html_sha256`, `text_sha256`, byte size, title, error |

`meta.json` is what catches "page moved / went down / changed vendor" — changes that
leave no trace in the body.

> The fetch timestamp lives **only** in `meta.json` (`fetched_at`), never in
> `page.html`/`page.txt` — otherwise every run would diff. `meta.json` therefore
> updates every run by design; the stable `html_sha256` / `text_sha256` fields let
> you tell a real content change from a mere re-fetch. A test enforces this.

### How the data is laid out on disk

One directory per county, one subdirectory per page type, three files in each.
**A gap creates no directory** — so a county's tree shows at a glance what it
publishes.

```
fl-county-watch/
├── manifest/
│   ├── counties.csv                 ← seed of truth: 67 counties (county, seat,
│   │                                  office_city, batch, homepage)
│   ├── targets.csv                  ← what the pipeline reads: 335 rows
│   │                                  (county, batch, page_type, url, external,
│   │                                   notes + 6 audit columns)
│   └── fl-county-election-pages.xlsx ← generated review workbook
└── snapshots/                       ← overwritten in place every run; history is in git
    │
    ├── leon/
    │   ├── homepage/
    │   │   ├── page.html            ← normalized HTML   (structural diff)
    │   │   ├── page.txt             ← visible text      (content diff)
    │   │   └── meta.json            ← status/URL/hashes (metadata diff)
    │   ├── elections/               ← page.html · page.txt · meta.json
    │   ├── polling/
    │   ├── early_voting/
    │   └── results/
    │
    ├── baker/
    │   ├── homepage/
    │   ├── polling/
    │   ├── early_voting/
    │   ├── results/
    │   └── (no elections/)          ← GAP: Baker publishes election info on its
    │                                  homepage, so there is no distinct page
    │
    └── … 65 more counties
```

Because each page type is its own directory, `git log -p -- 'snapshots/*/early_voting/page.txt'`
gives you every early-voting change across all 67 counties in one stream.

---

## Install

```bash
cd fl-county-watch
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
```

Requires Python 3.11+ and `git`.

## Run

```bash
# Snapshot every target in the manifest, then make one git commit:
.venv/bin/python scripts/snapshot.py

# Useful flags:
.venv/bin/python scripts/snapshot.py --no-commit            # write artifacts, don't commit
.venv/bin/python scripts/snapshot.py --workers 12           # more concurrent PLAIN fetches
.venv/bin/python scripts/snapshot.py --resume               # continue an interrupted run
.venv/bin/python scripts/snapshot.py --county leon          # one county (repeatable)
.venv/bin/python scripts/snapshot.py --county leon --page-type results
.venv/bin/python scripts/snapshot.py --no-headless          # never escalate (offline/debug)
```

Each run overwrites the files under `snapshots/` and makes **one commit**. Logs go to
`logs/run-<timestamp>.log` (git-ignored).

---

## How it works

### Fetch strategy — plain-first, escalate-on-empty

1. Fetch with a plain HTTP client (`httpx`), realistic User-Agent, follow redirects.
2. Detect an **empty JS shell**: after cleaning, if visible text < ~500 chars, or an
   "enable JavaScript" marker is present → escalate.
3. Also escalate if the plain fetch **failed outright** — some sites reset
   non-browser clients via bot protection but serve a real browser fine.
4. **Escalate** to a headless Chromium render (Playwright) that waits for network
   idle, then capture the rendered DOM.
5. The path used is recorded in `meta.json.render_mode` (`plain` | `headless`). A page
   flipping plain↔headless between runs is itself a meaningful change.

### Normalization (why diffs stay clean)

`scripts/normalize.py` applies the **same deterministic transform every run**, and is
inherited from `tx-county-watch` — where every rule in it was added because it was
caught diffing an unchanged site. It removes scripts/styles/comments, strips ASP.NET
WebForms and CSRF hidden-input churn, drops per-request token attributes and inline
`style` attributes, neutralizes Cloudflare email obfuscation, canonicalizes
per-render GUIDs and hex ids, strips cache-busting query params, removes
asynchronously injected accessibility overlays, and collapses whitespace.

That inherited work transfers because the volatility is a property of the **web
platform and CMS vendors** (WordPress, CivicPlus, Drupal, .NET), not of a state.
Florida SOE sites run the same small set of vendors Texas counties do — several are
CivicPlus (`votecitrus.gov`, `votehillsborough.gov`, `votepalmbeach.gov`) and several
are WordPress (`votegulf.gov`, `holmeselectionsfl.gov`) — so the rules land on the
same markup. See the `tx-county-watch` README for the full list and the story behind
each rule.

The headless path additionally neutralizes `setInterval`, pins `Math.random`, pins the
viewport to 1280x900, and waits `hydration_settle_ms` after network idle so capture
can't land mid-render.

`page.txt` is `get_text()` with blank-line runs collapsed.

#### Ten rules added for Florida

Inheriting Texas's rules got most of the way, but a back-to-back full run still
produced 18 differing artifacts across 7 counties, and two further passes surfaced
more. Every one was chased down; all ten were **markup churn, not content**
(`page.txt` never differed in any pair), and each was fixed the same way Texas's were
— by finding what regenerates per request and stripping it:

| what churned | where | fix |
|---|---|---|
| **DotNetNuke anti-forgery token nested inside a JSON attribute** — `data-edit-context='{…"rvt":"y7X-…"}'`, fresh per request and repeated on every content block | Brevard, Leon (all 5 pages each) | null the token inside the JSON value; the existing rules only matched volatile *attribute names*, and this one hides under an innocuous name |
| **Random numeric widget id** — `id="ocsoe-cpt-ajax-268481"`, `id="blog-slider-555748"` | Orange | canonicalize a 4+ digit suffix, but only when the prefix names a dynamic widget — so `post-123456` and `DocumentCenter/View/1783` keep diffing, since those are stable content references |
| **Stackable block hash classes emitted inconsistently** — `stk-container--<hash>` present on one capture, absent on the next | Orange | drop classes that are nothing but a prefix plus a random token; canonicalizing the *value* wasn't enough, because presence/absence still diffed |
| **WP Rocket mobile/desktop cache variant** — `data-wpr-features="… wpr_mobile"` vs `wpr_desktop` | Clay | drop the attribute |
| **WP Rocket lazy-load applied inconsistently** — real URL in `src` on one capture, in `data-lazy-src` with an inline SVG placeholder in `src` on the next (same root cause as the row above) | Clay | restore `data-lazy-src` into `src` so both forms normalize to one artifact |
| **Layout size class measured at runtime** — `class="row outer wide"` vs `"row outer"` | Lee | Texas already stripped CivicPlus `wide`/`narrow`, but only on elements whose class mentioned "widget"; broadened to layout containers |
| **Elementor responsive-visibility classes** — `elementor-hidden-tablet/mobile/desktop` present on one capture, absent on the next | Okeechobee | drop them; purely presentational, consistent with dropping `style` outright |
| **Dublin Core date holding the page generation time** — `<meta name="DC.Date" content="2026-08-03T11:54:31-04:00">` | Volusia | treat as a volatile meta |
| **An unrendered server-side template expression** — `aria-hidden="False.ToString().ToLowerInvariant()"`, a CivicPlus bug where the C# was never evaluated, and whose operand flips `True`/`False` between captures | Palm Beach | replace the whole value with a placeholder |
| **Stackable (WordPress) block ids regenerated per request** — `class="stk-block stk-b2y6ajz" data-block-id="b2y6ajz"` becoming `stk-signnvf` | Orange | canonicalize, but only the base36 tokens: some Stackable ids are persisted with the saved post and are hex-looking (`b885ea1`), while the regenerated ones contain letters outside `a-f`, so requiring a non-hex letter separates them exactly |

Four of these are worth noting as *generalizable* lessons rather than one-offs:

- **A volatile value can hide under a non-volatile attribute name.** Texas's rule
  "drop attributes whose *name* contains csrf/token/nonce" is sound but incomplete —
  DotNetNuke puts its anti-forgery token in a JSON blob under `data-edit-context`.
- **Canonicalizing a random value is not always enough.** If the framework sometimes
  omits the attribute entirely, presence/absence still diffs. The fix is to remove
  the thing, not to stabilize it.
- **Volatile and stable ids can share a prefix.** Stackable emits both, and telling
  them apart needed a property of the token itself (base36 vs hex) rather than its
  prefix. Canonicalizing all of them would have thrown away real signal.
- **The same underlying cause surfaces in several disguises.** CivicPlus measures a
  widget's container at runtime, and that one measurement leaked three ways: into a
  `wide`/`narrow` class, into an `elementor-hidden-*` class, and into an `aria-hidden`
  attribute via a broken template. Fixing the *class* churn did not fix the
  *attribute* churn, which is why each had to be found by running the test again
  rather than reasoned about once.

### Determinism test conducted 

Fetch a page, commit, fetch again immediately (site unchanged) → `git status` must
show **no change** to `page.html` / `page.txt`. Only `meta.json` (timestamp) changes.

```bash
.venv/bin/python scripts/snapshot.py --county leon --county dixie --no-commit
git add -A && git commit -m baseline
.venv/bin/python scripts/snapshot.py --county leon --county dixie --no-commit
git diff --stat -- '*page.html' '*page.txt'   # <- expect empty
```

### Determinism at 67-county scale

Measured, not assumed. Two consecutive full 8-worker runs over all **314 targets**
(628 body artifacts):

| run pair | body artifacts differing | what they were |
|---|---|---|
| Texas rules only | **18 / 630** (7 counties) | eight distinct classes of markup churn — see [the table above](#ten-rules-added-for-florida) |
| after the first eight rules | **1 / 630** | Palm Beach's unrendered CivicPlus template expression |
| after nine rules | **2 / 630** | Orange County's regenerated Stackable block ids |
| after all ten rules | **0 / 628** | clean |

`page.txt` never differed in either pair: all the churn was in markup, which is why
`page.txt` is the recommended starting point for reading diffs.

All 314 targets returned HTTP 200 with `error: null`, and only 1 of 314 needed a
headless render — Florida SOE sites are markedly more static than Texas's metro
county sites.

Expect a *small* number of changed files on any given re-run once this is scheduled,
for two reasons that are not normalization leaking:

- **Genuine content changes.** Counties really do publish things between runs.
  Detecting these *is the point of the tool*.
- **Access variance on bot-protected sites.** A site can serve a challenge or a 403
  instead of the page. `meta.json.http_status` and `meta.json.error` tell you
  immediately when a diff is this rather than real content.

If a diff is neither of the above, something volatile survived normalization — find
and strip it in `normalize.py`, and add it to the table above.

---

## Reading the diffs

```bash
git log --oneline                      # one line per snapshot run
git show <commit>                      # everything that changed in a run
git log -p -- snapshots/leon/elections/page.txt   # content history of one page

# What changed across an election (pick commits before/after election day):
git diff <before> <after> -- 'snapshots/**/page.txt'
```

`page.txt` is the best starting point (lowest noise). Drop to `page.html` for
structural changes (links, layout), and check `meta.json` for status/redirect/
render-mode/vendor changes when the body didn't move.

---

## Phase 1: building the manifest

### Where the homepages came from

The Florida Department of State, Division of Elections publishes an authoritative,
machine-readable directory of all 67 SOE offices, including each office's website:

- <https://dos.fl.gov/elections/contacts/supervisor-of-elections/>
- → `qryCountyInfo_Excel-<mmddyyyy>.xlsx` (snapshot used: **2026-07-07**)

`scripts/_build_counties_seed.py` records that extract as `manifest/counties.csv`
(kept as provenance; not part of any run). This is a large advantage over Texas,
where 230 homepages had to be guessed from domain patterns and content-verified.

**But a state directory is a periodic export, not a liveness check** — and treating it
as a lead to verify rather than as truth is the whole QA premise here. Every URL was
independently re-fetched and re-verified, which found **7 counties whose
state-published URL is out of date**:

| county | state directory says | actually serves |
|---|---|---|
| Dixie | `dixievotes.com` | `dixievotes.gov` |
| Duval | `duvalelections.com` | `duvalelections.gov` |
| Levy | `votelevy.com` | `votelevy.gov` |
| Gadsden | `gadsdensoefl.gov` | `votegadsdenfl.gov` |
| Miami-Dade | `miamidade.gov/elections` | `votemiamidade.gov` |
| Orange | `ocfelections.gov` | `voteorangefl.gov` |
| Putnam | `soe.putnam-fl.gov` | `voteputnamflorida.gov` |

Four are `.com`/`.org` → `.gov` migrations, three are rebrands onto a `vote<county>`
domain. Those 7 carry `batch = 2` in the manifest so the provenance stays visible;
the other 60 are `batch = 1`.

| batch | counties | meaning |
|---|---|---|
| **1** | 60 | homepage is the state directory's URL, verified live |
| **2** | 7 | the directory's URL was stale; the live domain was resolved and verified |

### Two city columns, on purpose

`counties.csv` carries both `seat` (the county seat) and `office_city` (where the SOE
office actually sits). They differ for several counties — Brevard's SOE is in
Melbourne but the seat is Titusville; Pinellas's is in Largo but the seat is
Clearwater; Citrus's is in Lecanto, Nassau's in Yulee, Sumter's in Wildwood. An SOE
page naturally names its own city, so **verification accepts either**, and a test
asserts the two columns still differ for some county rather than being redundant.

### Discovery

```bash
# 1. Verify (and where needed, discover) each county's SOE homepage
.venv/bin/python scripts/discover_homepages.py --workers 10
# 2. Crawl each homepage for the 4 election page types
.venv/bin/python scripts/discover_pages.py --batch 1 --workers 8
# 3. Merge into manifest/targets.csv (idempotent)
.venv/bin/python scripts/merge_targets.py --batch 1
# 4. Re-apply the human QA corrections (a merge would otherwise drop them)
.venv/bin/python scripts/_apply_qa_corrections.py
```

Step 4's ordering matters: `merge_targets.py` rewrites rows from the discovery draft,
so the corrections script must run after it. It is idempotent and has a `--check` mode.

**`discover_homepages.py`** probes the seeded state-directory URL first and only falls
back to Florida SOE domain patterns (`vote<county>.gov`, `<county>votes.gov`,
`vote<county>fl.gov`, …) if that fails. It verifies the content is really that
county's SOE site: the literal phrase "<county> County", **plus** a Florida signal or
the seat / office city, **plus** Supervisor-of-Elections vocabulary — with parked,
error, commercial, tourism and wrong-county rejection.

**`discover_pages.py`** runs a two-level crawl (homepage → elections landing → the
other three types), escalating to headless Chromium when a nav is built in JS, and
crawling per-election "hub" pages as a third level where a county has no standing
polling/early-voting page.

Two escalations from the Texas version were **removed** because they solved problems
that don't exist here: walking a "Departments" nav, and promoting a separate elections
portal. We already start on the elections portal.

Scoring had to be **retuned**, not reused. On a county government homepage the word
"election" is a strong signal; on an SOE site every link says "election", so the
generic weights were near-useless and the specific phrases ("upcoming elections",
"early voting sites", "precinct finder") carry the discrimination instead. Sibling
topics needed explicit negative weights — Leon's "Voter ID" and "Check Your Info"
pages both beat its real elections landing until they were penalized.

### What the verifier rejects, and why

Florida's identity hazards are genuinely different from Texas's, and the code reflects
that rather than inheriting guards for the wrong risk:

| trap | why it matters in Florida | real example |
|---|---|---|
| **Another state's county** | Florida shares county names very widely | Nassau (NY), Duval (TX), Monroe (NY/MI/PA), Jackson (MO/MS/OR), Marion (IN/OR), Polk (IA/OR), Union (NJ), Washington (OR/PA), Franklin (OH), Orange (CA/NY), Lee (AL/VA), Madison (AL/IL), Jefferson (KY/CO/AL) |
| **A sibling constitutional officer** | every county separately elects a Clerk of Court, Property Appraiser, Tax Collector and Sheriff, on lookalike domains with heavy county-government vocabulary | `leonclerk.com`, `polkpa.org` — real county pages, wrong office |
| **Punctuation variants** | the county's own site may not match the seed string literally | "St. Johns" / "St Johns" / "Saint Johns"; "Miami-Dade" / "Miami Dade" |
| **Tourism / EDC / chamber sites** | carry the county name and rank well | a "Visit Monroe County" style visitor site |
| **Statewide portals** | identical for all 67 counties, so capturing one would add no per-county signal *and* misreport a county as having a page | `floridaelectionwatch.gov` (the state's own results portal), `registertovoteflorida.gov`, `dos.fl.gov` |

Notably, Florida has **no** county whose name is a *different* county's seat — the
central Texas hazard. `tests/test_verification.py` asserts that premise rather than
assuming it, so if Florida ever gains such a collision the test fails loudly.

### The statewide-portal rule is the easiest one to break by hand

`floridaelectionwatch.gov` is a perfectly reasonable-looking URL for a county's
`results` row, which is exactly why it must not be there: it is the same page for
every county. A test (`test_no_statewide_portal_captured_as_a_county_page`) re-checks
every manifest URL against the rejection list, so a well-intentioned hand-edit can't
quietly introduce one.

---

## Verifying / auditing the manifest

`scripts/audit_targets.py` re-fetches every URL fresh (plain→headless, same as the
pipeline) and audits whether the content actually belongs to the intended county +
page type, writing findings **back into `targets.csv`** as extra columns (the pipeline
ignores unknown columns):

| column | meaning |
|---|---|
| `county` / `batch` | county name; homepage provenance (see the batch table above) |
| `page_type` | `homepage` · `elections` · `polling` · `early_voting` · `results` |
| `url` | the target; **empty = a recorded gap**, with the reason in `notes` |
| `external` | `true` when the URL's registered domain differs from the SOE homepage's |
| `notes` | provenance: how the URL was found, why the row is a gap, or the human QA judgement |
| `verify_status` | `ok` (live) · `broken` (4xx/5xx/error/non-HTML) · `gap` (no URL) |
| `http_status` | HTTP status of the (final) response |
| `final_url` | filled only when the request redirected elsewhere |
| `audit_confidence` | `confident` · `likely` · `uncertain` · `broken` · `gap` |
| `audit_reason` | why it looks right/wrong (which keywords matched, title, warnings) |
| `flag_for_review` | `yes` when a human should eyeball it |

```bash
.venv/bin/python scripts/audit_targets.py            # audit all, rewrite CSV
.venv/bin/python scripts/audit_targets.py --county leon
```

A human-readable summary is written to `manifest/audit-report.md`.

**Current audit state: 314 / 314 live, 0 broken, 311 `confident`, 3 `likely`, 0 flagged.**

### The human QA pass, and why the audit alone wasn't enough

The automated audit answers "is this page's content about this county and this page
type?" — and by that measure it passed nearly everything on the first attempt. That
question is necessary but **not sufficient**: it cannot tell whether a page is the
*best standing page* of its type. A news bulletin about a polling-place change is
genuinely about polling in that county, and audits `confident`.

So the rows that discovery had scored weakly were opened by hand. **30 corrections
were applied**, recorded in `scripts/_apply_qa_corrections.py` with a reason on every
one. What that pass actually caught:

- **A news item beating the standing page.** Palm Beach's `polling` pointed at a
  CivicAlerts "2026 Primary Election — Polling Place Change" bulletin; the real page
  is `/168/Election-Day-Voting`. Bay's `results` pointed at a canvassing-board
  *schedule* announcement.
- **A stale prior-cycle page.** Highlands's `polling` pointed at `/2024-local-candidates`.
- **The wrong voting method.** Holmes's `early_voting` had been filled with the
  **vote-by-mail** page — a different method entirely. Corrected to a gap, because
  Holmes publishes no early-voting page at all.
- **A sibling page outscoring the landing page.** Leon's `elections` went to "Voter
  ID", then "Election Day"; its actual upcoming-elections page is
  `/Voting/Dates-Deadlines`, found by targeted search since `/Elections` 404s.
- **A per-election URL where a standing one existed.** Hillsborough's `early_voting`
  had fallen back to the 2026 primary hub; `/171/Early-Voting` is standing.
- **A gap that wasn't one.** Miami-Dade's `early_voting` was empty because the page
  isn't linked from anything crawled; `…/voters/early-voting.page` exists and was
  added by targeted probe.
- **Six genuine gaps** where the automated pick was wrong and no correct page exists
  (Baker/`elections`, Lafayette/`elections`, Escambia/`elections`,
  Holmes/`early_voting`, Wakulla/`early_voting`, Sumter/`results`). Each records how that was established —
  nav checked, conventional paths 404, sitemap has no matching URL. **A documented
  absence is better than a plausible wrong URL**, which would otherwise enter the
  diff series looking like real data.

One method note: Sumter's `/202/Election-Results` returns HTTP 200 but redirects
off-site to a community development district, so status alone would have accepted it.
That is why the audit checks content and redirect target, not just liveness.

### Per-election URLs

Some counties publish polling or early-voting detail only inside a per-election page.
Discovery now **penalizes** URL shapes that carry a per-election or per-event id
(`Calendar.aspx?EID=…`, `/event/…`) so a durable page wins when one exists, and where
the volatile URL is the only option the row says so:
`[per-election URL — refresh each cycle]`. Clarity ENR deep links are normalized to
the stable county index the same way. **Re-check these each cycle.**

---

## Editing the manifest

Two CSVs, and **no code changes** are ever needed to add, remove or correct a county.

**`manifest/counties.csv`** — the seed of truth:

| column | meaning |
|---|---|
| `county` | county name, exactly as it should appear everywhere |
| `seat` | county seat — an identity signal when verifying a site |
| `office_city` | city the SOE office is in — a second identity signal, because it differs from the seat for several counties |
| `batch` | `1` (state directory URL) or `2` (directory was stale; live domain resolved) |
| `homepage` | the SOE front page |

**`manifest/targets.csv`** — what the pipeline actually reads. One row per
(county × page type), so 5 rows per county = 335 rows.

Common edits:

```bash
# Fix one URL: edit that row's `url` in manifest/targets.csv, then re-verify it
.venv/bin/python scripts/audit_targets.py --county hillsborough

# Record that a page type doesn't exist: blank the `url` and say why in `notes`
#   (a row with an empty url is a GAP; notes must explain it — a test enforces this)

# Re-discover one county from scratch
.venv/bin/python scripts/discover_homepages.py --county Leon
.venv/bin/python scripts/discover_pages.py --batch 1 --county Leon
.venv/bin/python scripts/merge_targets.py --batch 1
.venv/bin/python scripts/_apply_qa_corrections.py

# Regenerate the review workbook, then confirm the manifest is still consistent
.venv/bin/python scripts/export_xlsx.py
.venv/bin/python -m pytest tests/ -q
```

To add a county beyond the current 67, add a row to `counties.csv`, run discovery +
merge, and update `FLORIDA_COUNTY_COUNT` in `tests/test_manifest.py` (the only place a
count is asserted — deliberately, so the scope can't drift silently).

## The spreadsheet

`manifest/targets.csv` is the source of truth: it is what the pipeline reads and what
diffs cleanly in git. `manifest/fl-county-election-pages.xlsx` is a **generated**
review view of it:

```bash
.venv/bin/python scripts/export_xlsx.py
```

Four sheets — **Targets** (the manifest, with frozen headers, autofilter, clickable
URLs, colour-coded audit verdicts and grey-shaded gap rows), **Counties** (the seed),
**Coverage** (captured/gap counts per page type as live `COUNTIFS` formulas over the
Targets sheet), and **Legend** (every column and colour explained).

Because it's generated, it can't drift from the CSV — **edit the CSV and re-run;
never edit the workbook**. `export_xlsx.py` also verifies its own output: it asserts
the Targets sheet row count matches the CSV and that the Coverage formulas span
exactly the data rows, which is the failure mode Excel would not flag (an off-by-one
range produces a clean file with a quietly wrong number).

> The workbook is written with `fullCalcOnLoad`, so the Coverage numbers populate the
> moment it's opened. `openpyxl` cannot compute formula values itself, so a tool that
> reads only cached values will show those cells blank until the file has been opened
> once in a spreadsheet application.

## Tests

```bash
.venv/bin/python -m pytest tests/ -q
```

**39 tests, no network required.** They assert the invariants everything else relies on:

- **67 unique counties**, with the batch labels partitioning them
- every county has a seat, an office city, and a verified homepage; and `office_city`
  still differs from `seat` somewhere (so the extra column isn't quietly redundant)
- `targets.csv` covers every seeded county with exactly one row per page type
- batch labels agree between the seed and the manifest, and the homepage row's URL
  agrees with the seed (they're edited in different places, so drift is possible)
- every county has a homepage URL — a homepage gap would mean an uncaptured county
- every gap row explains itself in `notes`; `external` is boolean; URLs are http(s)
- **no statewide/national portal appears in the manifest**
- artifacts on disk agree with the manifest (3 files per captured page, **no directory
  for a gap**), `meta.json` is well-formed and matches its row
- the fetch timestamp never leaks into `page.html`/`page.txt`
- identity-verification regressions: Nassau County NY must not verify as Nassau
  County FL, Duval County TX must not verify as Duval FL, a county Clerk of Court or
  Property Appraiser site must not verify as the SOE, "St Johns"/"Saint Johns" and
  "Miami Dade" must verify, a page naming only the SOE office city (not the seat) must
  still verify, and tourism/commercial/parked/error pages must be rejected
- Florida has no county-name/county-seat collision (the premise the identity check
  relies on)

---

## Running at 67-county scale

A full run touches **314 targets**. Three things make that sustainable:

**Bounded concurrency on the plain path only.** `--workers` (default 8) parallelizes
plain HTTP fetches. Headless renders stay **serialized behind a lock**, deliberately:
Playwright's sync API isn't thread-safe, and parallel Chromium renders contend for
CPU, which shifts hydration timing — and that timing is exactly what the determinism
work depends on. Speed does not get to compromise it.

**Politeness.** Every fetch waits `request_delay_ms` (250) plus up to
`request_jitter_ms` (250) of jitter. The jitter matters with a worker pool — without
it, workers synchronize into bursts.

**Resumability.** Progress is written to `logs/checkpoint.json` as each target
completes; `--resume` skips what already finished. A clean finish deletes the
checkpoint. The Actions workflow retries once with `--resume` on failure.

| knob (`config.json` → `fetch`) | default | what it does |
|---|---|---|
| `workers` | 8 | concurrent plain fetches (headless always serial) |
| `request_delay_ms` / `request_jitter_ms` | 250 / 250 | politeness pause before each fetch |
| `plain_retries` | 2 | plain attempts; also retries 5xx |
| `hydration_settle_ms` / `hydration_max_wait_ms` | 2000 / 6000 | post-networkidle DOM-quiescence window |
| `interstitial_max_wait_ms` | 45000 | how long to wait out a bot challenge |
| `js_shell_min_chars` | 500 | below this, escalate to headless |

## Bot protection

County and SOE sites sit behind Akamai, Cloudflare and Imperva. Three client settings
matter far more than any clever workaround (all inherited, and all measured, in
`tx-county-watch`):

| setting | why |
|---|---|
| **Try HTTP/2 *and* HTTP/1.1** | neither works everywhere, and you can't tell which from the URL |
| **A complete browser header set** | protection fingerprints the whole request, not the User-Agent alone |
| **`brotli` installed** | we advertise `br`, so we must be able to decode it |

Observed here: `votemonroeflkeys.gov` returns a Cloudflare interstitial to a bare
`curl` but serves normally to the full header set over h2 — a good reminder that **a
403 usually means the client is unusual, not that the county is blocking research.**

> **A note on how far to take this.** These are three ways of being a *normal,
> standards-compliant* client. Going further — TLS/JA3 fingerprint impersonation,
> stealth browser patches, CAPTCHA solving — means actively defeating a site's access
> controls, which this project does not do. If pages remain blocked, the better routes
> are: run from a normal network, ask the county's elections office to allow a
> research crawler, or record the block honestly.

### Where the pipeline runs still changes what some sites return

Actions runners use datacenter IPs, which bot protection treats more suspiciously than
a residential connection.

> **Filter on `error`, not on `http_status`.** A challenge can clear *after* a 403, so
> status alone can't separate good data from junk. The reliable test is
> `meta.json.error == "bot_challenge_not_cleared…"` → discard; `error == null` → the
> body is real content whatever the status says.

If a county is blocked only on CI, run it locally and push:

```bash
.venv/bin/python scripts/snapshot.py --county monroe && git push
```

## Phase 3: scheduling

Cadence is **not hardcoded** in the pipeline — the scheduler decides frequency.

**GitHub Actions** (`.github/workflows/snapshot.yml`): a daily baseline cron that
always runs, plus an every-3-hours cron that captures when **either** of two gates
opens. `config.json` documents the crons and the fetch knobs.

| gate | where | what it does |
|---|---|---|
| `ELECTION_WINDOW` | repo variable (Settings → Secrets and variables → Actions → Variables) | manual override; `true` raises cadence immediately, `false` drops it. Works in both directions, any time. |
| automatic date window | `WINDOW_START` / `WINDOW_END` in the workflow's gate step | raises cadence for the general election without anyone having to remember. Currently **2026-10-20 → 2026-11-17**. |

The window opens before Florida's mandatory early voting (Oct 24–31) and closes two
weeks *after* election day, because the post-election takedown curve is the point and
it lives in the weeks after Nov 3, not on election night.

Why a date window at all, when the variable was meant to be the single knob: the
August primary was captured at 3-hourly cadence only because the variable happened to
be switched on at the time. Relying on that twice is not a plan. The variable remains
the override; the dates are the floor.

> **Current state (2026-08-26):** the repo is live at
> `github.com/Lolli-AK/fl_county_soe_websites`, Actions is running, and
> `ELECTION_WINDOW` is `false` — so cadence is the daily baseline until the automatic
> window opens on Oct 20.
>
> **The bot pushes to `origin` on every run, so a local clone goes stale fast** — at
> 3-hourly cadence, within hours. Always `git fetch && git status` before trusting a
> local `git log`. A local-only view of history once produced a false "the cron
> produced nothing" conclusion; see `HANDOFF.md` §7.

**Local cron** alternative — note the machine must be awake at the scheduled time:

```cron
# daily baseline at 08:00
0 8 * * * cd /path/to/fl-county-watch && .venv/bin/python scripts/snapshot.py >> logs/cron.log 2>&1
# election window: every 3 hours (enable by uncommenting)
# 0 */3 * * * cd /path/to/fl-county-watch && .venv/bin/python scripts/snapshot.py >> logs/cron.log 2>&1
```

---

## Layout

```
fl-county-watch/
  manifest/
    counties.csv                 # seed of truth: 67 counties
    targets.csv                  # THE manifest: 67 counties x 5 page types = 335 rows
    fl-county-election-pages.xlsx # generated review workbook
    audit-report.md              # broken + flagged rows from the last audit
    batch1_homepages.csv         # Phase 1 intermediate: verified homepages
    batch1_targets_draft.csv     # Phase 1 intermediate: discovered election pages
  snapshots/<county>/<page_type>/{page.html,page.txt,meta.json}
  scripts/
    snapshot.py                  # main: fetch -> normalize -> write -> commit
    normalize.py                 # shared deterministic cleaning transform
    audit_targets.py             # verify + content-audit every manifest URL
    discover_homepages.py        # Phase 1: verify/discover SOE homepages
    discover_pages.py            # Phase 1: find the 4 election pages
    merge_targets.py             # merge discovery into targets.csv
    export_xlsx.py               # generate the review workbook from the CSV
    _apply_qa_corrections.py     # the recorded human QA pass (run AFTER a merge)
    _build_counties_seed.py      # one-shot: generated manifest/counties.csv
  tests/
    test_manifest.py             # 67-county partition, manifest/artifact invariants
    test_verification.py         # identity-verification regressions
  config.json                    # cadence + fetch knobs
  logs/                          # run logs (git-ignored)
  .github/workflows/snapshot.yml
```

## Out of scope

PDF capture/parsing, screenshots/visual diffing, full-site crawling, and alerting
(diffs are reviewed via git). Florida's 67 counties are the **complete** set, so there
is no further batch. The code stays data-driven regardless: any future manifest edit —
a corrected URL, a new page type, even a different state — needs no code change.
