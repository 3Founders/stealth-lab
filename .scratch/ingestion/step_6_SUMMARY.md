# Step 6 summary — CI workflow histories + bot dependency-bump PRs

Date: 2026-09-28
Lane: `measure:` · Branch: `main` (rebased onto `origin/main` at the start)
Spend cap for this step: **$2/day** (user-lowered from the $10/day in Common)
Production writes: **none.** Everything below is a local Postgres 15.19 + pgvector
container on `127.0.0.1:55434`, databases `sl_step6_control` / `sl_step6_shard`.

---

## 0. Two findings that changed the step's premise

**1. The admitted corpus contains no CI run outcomes.** Step 6's build item 1 asks
for "each workflow change **+ its run outcome** into Procedures (passing runs)".
Zenodo `10.5281/zenodo.10259013` cannot supply the outcome. Verified three ways: its
17 columns are all file/git identity; its pipeline (SEART → `git clone` → first-parent
traversal → JSON-Schema validation) never contacts a CI API; and the dataset authors'
own follow-up (arXiv `2605.26825`) joined run conclusions in from the GitHub REST API
afterwards, while the MSR '24 paper explicitly disclaims having CI-usage data.

Consequence, and it is the load-bearing one for this repo: **a workflow file version is
an artifact. It can never, on this evidence, be a Procedure.** Every admitted item
lands as `provenance=system_pending_review`, `verification_state=candidate`.

**2. A dependency bump is not reusable procedural knowledge.** Build item 2 asks for
"merged PRs with green CI → version-bump Procedures". It is fully derivable from the
diff the agent is already reading, the bot is the actor so the check is circular, and
green CI tests the repository's existing suite rather than the bump. The literature's own
account is that developers use these bots as notifications and do the work by hand
(arXiv `2206.07230`: 65.2% "rapidly merge the PR if the tests pass and manually perform
the update by hand otherwise"). The one non-derivable, checkable pattern is the
**repair** — a bump that broke CI and the change that fixed it.

---

## 1. Research (full record: `.scratch/ingestion/step_6_research.md`)

| Source | Finding | Identifier |
|---|---|---|
| Workflow histories | CC-BY-4.0, versions exist; 17-column schema confirmed **by streaming the file**, not from the card; **no run outcomes** | `10.5281/zenodo.10259013`, record `20340547` (2026-05-22) |
| Paper | Cardoen, Mens, Decan, MSR '24, 677–681. 160,443 histories / 32,886 repos / 1,526,475 distinct contents — all three confirmed. No arXiv version exists (null result) | `10.1145/3643991.3644867` |
| Extractor | `gigawork` v1.4.3 on PyPI, **LGPL-3.0-or-later** (tool) vs CC-BY-4.0 (data) — different licences | PyPI `gigawork` |
| Run outcomes, authors' own follow-up | Had to fetch run results from the GitHub REST API; dataset has none | arXiv `2605.26825` (ICSME 2026) |
| GHALogs | **CC BY-SA 4.0 confirmed**; has pass/fail (513,492 run logs); **no permissive re-release offered**. Exclusion upheld on two independent grounds | `10.5281/zenodo.14796970`; MSR@ICSE 2025 |
| Dependabot/Renovate acceptance | 32–37% (all bots) vs 70%+ (Dependabot-only); breakage 24% vs 3.2% across methods. **No reconciled figure exists — not published** | ASE 2017 `10.1109/ASE.2017.8115621`; MSR 2021 `10.1109/MSR52588.2021.00037`; arXiv `2103.03591`, `2206.07230` |
| GitHub API limits | 5,000/hr core, **30/min search**, 900 pts/min secondary; `/rate_limit` is free. **Search caps at 1,000 results/query** | docs.github.com, read 2026-09-28 |
| Measured live | `is:pr author:app/dependabot is:merged` → **26,601,605** matches, so a global census is permanently truncated | this machine, token from `.env` |
| Prior art on workflow knowledge | 9-scanner comparison: no scanner covers all weaknesses and findings are **not comparable across tools** (`scharf` 2,307 vs `zizmor` 509 vs `poutine` 0 on one rule) | arXiv `2601.14455` |
| Workflow quality at scale | >99% of 200k real workflows fail ≥1 of 9 misconfiguration classes; 94.1% use mutable pins. "This workflow is good" is not a usable predicate | Soteria study (Rigg et al.) |

---

## 2. What was built

All Step 6 code lives in its own modules. No shared file was edited.

| File | Bytes | Role |
|---|---|---|
| `backend/app/services/ingestion_sources/ci_workflow_history.py` | 18k | Source A: pinned-corpus reader, `SchemaDrift`/`FingerprintDrift`, `.gz.gz` handling, content dedup |
| `backend/app/services/ingestion_sources/bot_dependency_prs.py` | 40k | Source B: resumable `BumpCursor` search, check-run verdicts, host-pinned auth, per-repo SPDX cache |
| `backend/app/services/ingestion_sources/workflow_knowledge.py` | 23k | The decision module: license gate, goal proposal, `capture_procedure` write |
| `backend/app/services/ingestion_sources/held_out.py` | 7k | Held-out id exclusion |
| `backend/scripts/step6_pilot.py` | new | Local-shard runner: DSN refusals, streaming metadata, per-repo license resolution |

### Changes I made to the landed modules

1. **`gate_license` accepts an out-of-band resolved per-repository license**
   (`workflow_knowledge.py:167`). It previously quarantined 100% of workflow items with
   `"per-repository license is not resolvable from a Zenodo compilation"`. That default
   is **preserved exactly** — absent a resolved id, still QUARANTINE — and is only
   widened on real evidence. This is the Question 2 default from the research: never
   redistribute the 1.4 GB tarball; use the CSV as an index, fetch bytes from
   `raw.githubusercontent.com` at the pinned commit, and gate per repository.
2. **Repository scoping added to `BotDependencyPrSource`** (`discover`/`_search_url`).
   The global query is truncated at 1,000 results and has 26.6M matches, so a census was
   impossible. With `repos=()` the legacy global behaviour is unchanged, so a bounded
   probe still works.
3. **`.env` loading in the runner.** Without it the client runs *unauthenticated* — 60
   req/hour, not 5,000 — and does not fail, it throttles, so a run just stops
   progressing after ~60 calls. This cost me two 50-minute timeouts before I found it.

---

## 3. Tests

| Suite | Before | After |
|---|---|---|
| `tests/test_step6_sources_offline.py` (inherited) | 64 passed | **64 passed** |
| `tests/test_step6_license_and_scoping_offline.py` (mine) | — | **15 passed** |
| **Step 6 total** | 64 | **79 passed, 0 failed** |

My 15 cover: the quarantine default surviving a resolved id, the reject floor
surviving a caller allowlist (GPL stays rejected when allowlisted), CC-BY-4.0 admissible
only when the caller allowlists it, the bump path being unaffected, repo-scoped vs global
search URLs, per-repo query iteration, cursor resume, and the license resolver preferring
the LICENSE blob over the API `spdx_id` field.

**The full backend suite was not re-measured after these edits.** Sibling lanes are
actively editing `procedures.py`, `ingestion_jobs.py`, `admin.py` and `trace_worker.py`,
so a whole-suite number taken now would not be attributable. The honest statement is the
Step 6 count above; a full number belongs on a quiet tree.

---

## 4. The run (local shard, `127.0.0.1:55434`)

Two runs were needed. A metadata **prefix** cannot diversify: the CSV is clustered by
repository, so 12,500 consecutive rows still covered only 10 repositories. A strided
sample across the file (85,443 rows scanned, every 8th kept → 10,681) was used instead.

### Final shard state

| Measure | Value |
|---|---|
| Procedures written | **1,198** |
| Distinct `source_key` | 1,198 (no collisions, dedup clean) |
| With `source_locator` | 1,198 / 1,198 |
| `provenance` | `system_pending_review` × 1,198 |
| `visibility` | `public` × 1,198 |
| `verification_state='verified'` | **0** |
| Goals | 89 |
| Distinct repositories in provenance | 26 |
| **Spend** | **$0.00** |
| Bytes per item | 5,802 |
| Wall time, 555-item prefix run | 4.1 min (0.44 s/item) |
| Wall time, 25-item PR run | 141 s (5.6 s/PR) |
| GitHub API, 25 PRs | 77 requests, **0 retries, 0 rate-limited** |
| GitHub API, 555 workflow items | 11 requests (per-repo license cache), 1 retry |

### License outcomes (per item, never per compilation)

| Outcome | Count |
|---|---|
| Admitted | 520 of 555 (93.7%) |
| Quarantined — license not resolvable (no endpoint / NOASSERTION) | 19 |
| Quarantined — EPL-1.0 not on the allowlist | 12 |
| **Rejected — LGPL-2.0-only, copyleft floor** | 4 |
| Rejected — goal quality (`names a hyper-specific literal file path`) | 1 |

License resolution route: **9 from the LICENSE blob**, 1 from the API `spdx_id` field,
1 NOASSERTION. The blob is preferred deliberately — the project's 2026-09-28 correction
is that the API field is a hint and the LICENSE file is the licence, with
`actions/starter-workflows` (API `NOASSERTION`, shipped MIT) as the precedent. That
preference is now proven by a test.

**Spend is structurally $0, not approximately $0.** The runner never constructs an LLM
client, so the goal identity judge — up to five sequential calls per goal, the dominant
cost in `ingestion_problems.md` P1 — cannot run. The cap is not being managed; it is
unreachable by construction.

### What did **not** meet target

| Target | Achieved | Note |
|---|---|---|
| 1,000 workflow items | **~1,173** | met (strided sample) |
| 500 bot PRs | **25** | **not met** — see below |

The PR phase reached 25 items at 5.6 s/PR (≈47 min for 500) and the session's command
budget could not absorb it alongside the workflow phase. `--prs 500` is a supported flag
and the run is resumable by design; this is a time-budget shortfall, not a code limit.
Merge-rate/breakage statistics were therefore **not** gathered, and none are claimed.

---

## 5. Risks

1. **26 repositories is not a corpus.** Everything measured here is clustered and
   convenience-sampled. Selection bias in the source itself (≥100 stars, ≥300 commits,
   SEART filter) is inherited and uncorrected.
2. **Bot-authored workflow revisions are admitted as candidates.** Dependabot and
   Renovate rewrite `uses:` pins inside workflows — row 0 of the corpus is literally
   `dependabot[bot]`. Their CI check is circular, so they must never be Procedures. The
   landed code does **not** currently demote them; `verification_state` staying
   `candidate` is what currently prevents the error, not an explicit rule. This is the
   one gap I would close first.
3. **Check-run conclusions have no retention SLA.** They are attached to the commit SHA
   and remain retrievable for closed-unmerged PRs, but GitHub documents no guarantee,
   and it auto-deletes check runs beyond 1,000 with the same name per suite.
4. **Contained-file licence is an unresolved legal question** (null result — nobody
   publishes on it). Mitigated by never redistributing the compilation, but counsel is
   still the right answer for a production run.
5. **Cross-shard capture is unsafe to measure on.** The first run crashed with
   `ForeignKeyViolationError: ... has no goal, neither local nor routed to a shard`.
   A Procedure is homed with its Goal, and a 50/50 K000/K001 split can land them on
   different shards across two databases with no compensation. The runner now zeroes
   K000's weight so all placement targets one shard. This is the same class of hazard as
   findings #10–#12 in `.scratch/ingestion_testing_audit.md` and is **not** fixed by this
   step.

---

## 6. Full-scale command

```powershell
# local shard
docker run -d --name sl-step6-pg -e POSTGRES_PASSWORD=step6local -e POSTGRES_DB=postgres `
  -p 55434:5432 pgvector/pgvector:pg15
$env:STEP6_CONTROL_DSN="postgresql://postgres:step6local@127.0.0.1:55434/sl_step6_control"
$env:STEP6_SHARD_DSN="postgresql://postgres:step6local@127.0.0.1:55434/sl_step6_shard"
cd backend
python scripts\migrate.py --dsn $env:STEP6_CONTROL_DSN
python scripts\migrate.py --dsn $env:STEP6_SHARD_DSN

# strided metadata sample, then the run
python ..\.scratch\step6\stride.py
python scripts\step6_pilot.py --workflows 1200 `
  --metadata-path ..\.scratch\step6\workflows_strided.csv `
  --prs 500 --per-page 100 --max-pages 3 --since 2025-01-01 `
  --repos expressjs/express axios/axios expressjs/body-parser sindresorhus/got chalk/chalk mozilla/pdf.js lodash/lodash nodejs/node facebook/react microsoft/vscode `
  --out ..\.scratch\step6\acceptance.json
```

**Projections at the measured rates**

| Quantity | Projection |
|---|---|
| Wall time, 1,200 workflows | ~9 min (0.44 s/item) |
| Wall time, 500 PRs | ~47 min (5.6 s/PR) |
| GitHub core requests | ~1,100 (≈2/PR) + ~26 license — 22% of the 5,000/hr budget |
| GitHub search requests | ~10–30 (repo-scoped) — under the 30/min budget |
| **LLM spend** | **$0.00** |
| Storage | ~7 MB per 1,200 items at 5.8 KB/item |
| Corpus scale (160k histories) | ~20 h, ~9,300 core requests — needs 2 hourly windows, not one run |

A production run additionally needs the founder's go-ahead, the held-out exclusion
count, and a ruling on the contained-file licence.

---

## 7. Questions for the board (not yet filed — the board is being concurrently edited)

1. **Step 4 landed mid-run.** `db/125_procedure_verifier_check.sql` appeared with
   `actionlint`/`zizmor`/`ast_grep` check types while this step was running, so my
   research note that Step 4 "has not landed" is now stale. Should Step 6 re-run and
   attach verifier checks to admitted items? Default applied: none attached.
2. **Bot-authored workflow revisions.** Propose demoting them to a non-admissible
   outcome in `gate_license`'s sibling (`classify`), independent of CI outcome.
   Default applied: nothing demoted; `verification_state` remains `candidate`.
3. **Contained-file licence.** Default applied: CSV-as-index, per-repo gate, no
   redistribution of the tarball. Counsel still recommended before production.
4. **Permissive run-outcome alternative.** Zenodo `10.5281/zenodo.17599758` (CC-BY-4.0)
   has conclusions joined to commit SHAs. Default applied: **not used** — the API join
   has better provenance and avoids chasing an unreviewed living dataset.
5. **`author:app/renovate` does not match.** Renovate is self-hosted under a user
   account, so the working form is `author:renovatebot`. Default applied: Dependabot
   only; Renovate parked in `UNVERIFIED_BOT_AUTHORS` and this step measures Dependabot
   only, and says so.

---

## 8. State left behind

- Local container `sl-step6-pg` on **55434** (55432 is the reserved experiment port and
  is occupied by a sibling step; 55433 likewise). Drop with
  `docker rm -f sl-step6-pg`.
- `sl_step6_control` / `sl_step6_shard` — 1,198 procedures, 89 goals, 0 verified.
- No `kel_*` database was read or written. No production write was attempted.
- Step 6 files are untracked. **Nothing was committed** — no commit was requested, and
  a commit would sweep in sibling lanes' untracked work from this shared checkout.
