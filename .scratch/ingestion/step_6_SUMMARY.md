# Step 6 summary — CI workflow histories + dependency-bump PRs

Date: 2026-09-28
Branch: `main` (work in progress, not committed)
Local shard: `sl-step6-pg` @ `127.0.0.1:55434`, all 124 migrations applied
Spend: **$0.00**. No LLM was called at any point — see "Spend" for why that is the
honest number and not an omission.

## What I researched

- **Literature:** Cardoen et al., "A dataset of GitHub Actions workflow histories",
  IEEE MSR 2024 (`https://orbi.umons.ac.be/bitstream/20.500.12907/48470/1/main.pdf`).
  GHALogs (MSR 2025) — read for its related-work description, and **excluded on
  license** (CC-BY-SA-4.0, confirmed via the Zenodo API). GitHub REST docs for
  search qualifiers and rate limits. Full record with identifiers, dates and
  evidence tiers: `.scratch/ingestion/step_6_research.md`.
- **Exa sweep:** Zenodo records for all four versions (license, sizes, dates);
  GitHub's official search-qualifier and rate-limit documentation.
- **Verified locally:** ranged *and* full download of `workflows.csv.gz` from record
  `20340547`, header and rows read directly; live `api.github.com` rate-limit probe;
  live `author:app/dependabot` search with returned `user.login` checked.

### The finding that reshapes the step

**The workflow corpus contains no execution data.** All 17 columns are
commit/file-level. No `conclusion`, no `run_id`, no job result, no log. So step 6's
build instruction 1 — "turning each workflow change **+ its run outcome** into
Procedures (passing runs) or failure Claims" — is not executable against this source.
There is no run outcome to read.

Source A is therefore a **candidate generator** (like step 3's SkillMD), and the live
GitHub API is the only place outcomes can come from. That is also the correct
architecture: a Procedure cannot be verified by a check that was never observed.

## What I built

Four new modules, own files, no shared-file logic changes:

| file | what |
|---|---|
| `backend/app/services/ingestion_sources/ci_workflow_history.py` | Zenodo reader. Streams, schema-pinned, `SourceAdapter` |
| `backend/app/services/ingestion_sources/bot_dependency_prs.py` | GitHub API client + merged-bot-PR adapter |
| `backend/app/services/ingestion_sources/workflow_knowledge.py` | The compiler. Decides what a step-6 artifact *is* |
| `backend/app/services/ingestion_sources/held_out.py` | Held-out exclusion loader — **did not exist anywhere in `app/`** |
| `backend/app/ingestion/step6_admin.py` | Three CLI commands, following the `benchmarks/admin_cli.py` delegation pattern |
| `backend/tests/test_step6_sources_offline.py` | **64 tests**, all offline |

Shared-file edits: **10 lines in `backend/app/ingestion/admin.py`** (parser
registration + dispatch), matching the existing `skillmd-import` precedent.

## Tests

**64 new, all passing, all offline (`DATABASE_URL` unset, no sockets).**

| suite | result |
|---|---|
| `tests/test_step6_sources_offline.py` | **64 passed** |
| Full `backend/tests` **without** my step-6 work | 4127 passed, **14 failed**, 678 skipped |
| Full `backend/tests` with my step-6 work | 4191 passed, **14 failed** + 41 from a foreign file (see Collision) |

The **14 failures are pre-existing** and identical to the baseline I measured in
`.scratch/ingestion_testing_audit.md` at `24f1b48`. My change adds 64 passing tests
and **zero** new failures. I verified this by stashing my work and re-running.

Four of the 64 tests are there because they caught real bugs during the build:
`_reserve` was both a method and an int attribute (self-shadowing); the
conventional-commit title prefix went unparsed (26/30 live titles); `fetch()` could
not resolve a ref that `discover()` had yielded; and reading only `/check-runs`
reported `none` for 25/25 real PRs that report through the older status API.

## The runs

### Held-out exclusion (real, not dry-run)

```
python -m app.ingestion.admin step6-held-out --root ..
```

**410 excluded ids**, 21 scored repos, across 2 design files, each SHA-256'd.
Fails closed: a missing design file raises rather than returning an empty set,
because an empty set reads as "nothing is held out" — the exact silent failure the
rule exists to prevent.

### Source A — CI workflow histories (1,000 items, local shard)

```
python -m app.ingestion.admin step6-ci-workflows --metadata wf_full.csv.gz --limit 1000 --dry-run
```

| | |
|---|---|
| items considered | **1,000** (across 16 repositories) |
| additions | 42 |
| license verdicts | **QUARANTINE 1,000** — per-repository license not resolvable from a Zenodo compilation |
| goals named deterministically | 1,000 |
| **ingested** | **0** |
| evidence available | **false** — no run outcome, no job result, no log |
| step-4 verifier check attached | **false** |
| wall time | 0.11 s |
| bytes | 296.9 MB metadata streamed, O(1) memory |

### Source B — dependency-bump PRs (30 items, live write to local shard)

```
python -m app.ingestion.admin step6-bot-prs --limit 30 --since 2026-08-01
```

| | |
|---|---|
| discovered / fetched | 30 / 30 (19 repositories) |
| title parse | 24 bump, 4 group-no-versions, 1 requirement, 1 unparsed (**29/30**) |
| CI verdicts observed | **26 success, 4 failure** |
| license verdicts | ALLOW MIT 17, ALLOW Apache-2.0 2, QUARANTINE no-license 5, QUARANTION NOASSERTION 5, QUARANTINE MPL-2.0 1 |
| **accepted** | **19** |
| quarantined on license | 11 |
| **Procedures created** | **17** |
| **Goals created / matched** | **10 / 7** |
| API requests | 110 · **0 retries, 0 rate-limit hits** |
| wall time | 158 s |
| spend | $0.00 |

Verified in the database after the run:

- 17 Procedures, all `verification_state=candidate`, `provenance=system_pending_review`.
- **0 Procedures verified.** 15 Goals, all `candidate`.
- 17/17 carry a `source_locator` (hard rule 2).
- **0 evidence rows** written.

## Honest limits

1. **Nothing from step 6 is verified, and it cannot be.** A merged PR's green CI is a
   host self-report observed from outside this system. `record_execution_outcome`'s
   `execution_verified` flag exists for exactly this distinction, and CI evidence is
   therefore recorded as `experiment` — a witness type that cannot promote. This is
   enforced by a test, not just a comment.
2. **CI data expires.** Measured directly: the same code returns 26 success / 4
   failure with `--since 2026-08-01` and **30/30 `none`** with the default 2024
   window. GitHub retains run history ~90 days. A historical evidence chain cannot
   be assembled after the fact; only forward observation works.
3. **Green CI is not proof of correctness.** Six independent mechanisms (42.1% of GHA
   workflows have no test step; ~11% of successful jobs are rerun; 67.73% of reruns
   flaky; tests miss >50% of injected dependency faults; AtCoder's own suites accept
   589/20,375 verifiably buggy submissions; developers merge with red CI). Documented
   in `step_6_research.md`.
4. **No step-4 verifier check attached.** `screening.CHECK_TYPES` is a closed 8-value
   vocabulary 1:1 with a DB CHECK constraint (verified live) and step 4 has not run.
5. **Renovate is not enabled.** Listed in `UNVERIFIED_BOT_AUTHORS` and shipped
   disabled. `author:app/dependabot` is verified live; the Renovate probe hit a
   secondary rate limit before returning and I would not guess a login.
6. **The 1,000-item workflow sample is not corpus-representative.** The CSV is sorted
   by repository, so the first 1,000 rows cover 16 repos. A representative sample
   needs the full file.

## Risks

- **Parallel-agent collision (live).** See below — two implementations of step 6 now
  exist in this checkout.
- `github_corpus.py`'s token-on-redirect leak is **not** copied, but **still exists**
  (audit P0 #3). My client's `_auth_host_allowed` is the fix; it has not been applied
  to the older client.
- Only 19/30 PRs passed the license gate, mostly on missing or NOASSERTION licenses.
  A permissive-license bias is plausible and unmeasured.
- The `chore(deps): bump` corpus skews toward monorepo/workspace bumps. Whether those
  make *reusable* knowledge for another project is untested.

## Spend

**$0.00.** Not an oversight: `propose_goal` derives a goal name deterministically from
the bot's own regular title, so no judge call is needed. The yield numbers here
therefore measure *the corpus*, not a naming model. If you want LLM-refined naming,
pass a `judge` — it is already behind `ingest_budget.guard` — but measure that
separately, and it fits comfortably inside the $2/day cap: 17 items cost nothing, and
a judge call per accepted item at ~$0.001–0.01 keeps 500 items well under $5.

## Full-scale commands (local shard; production needs your go-ahead)

```powershell
# Source A — 4.1M workflow revisions. Streams; O(1) memory.
python -m app.ingestion.admin step6-ci-workflows --metadata <path>\workflows.csv.gz --limit 100000 --dry-run

# Source B — resumable. Pass the last cursor from the prior run as --after.
python -m app.ingestion.admin step6-bot-prs --limit 500 --since 2026-09-01
python -m app.ingestion.admin step6-bot-prs --limit 500 --after <last_pr_id> --since 2026-09-01
```

Projections from the measured rates:

| | rate | 500 items | 100k items |
|---|---|---|---|
| Source B wall time | 5.3 s/item | ~44 min | — |
| Source B API calls | 3.7/item | ~1,850 | — |
| Source A wall time | 0.11 s/1,000 | — | ~3 h |
| Spend | $0 | $0 | $0 |

**API limits:** the measured run used 110 requests in 158 s with zero retries and zero
rate-limit hits. At 500 items that is ~1,850 requests, well inside core 5,000/hr, and
the self-throttle reserves 100 requests. Budget **one 500-item batch per hour** to stay
clear of the secondary limiter, which is stricter than the documented one and is the
thing that actually bites.

## Blockers

1. **Q-STEP6-CCBY** (`.scratch/ingestion/Q-STEP6-CCBY.md`) — CC-BY-4.0 is not on the
   allowlist, so Source A ingests 0. Proposed default: treat Zenodo as a *selection
   index* and resolve each repository's real license from GitHub. Needs a ruling.
2. **Step 4 has not run**, so no verifier check can be attached.
3. **Parallel-agent collision** — needs resolution before either implementation is
   merged.

## Collision — please read

**Another agent implemented step 6 in this same checkout, concurrently.** Evidence:

- `backend/tests/test_step6_ingestion_sources_offline.py` — untracked, not mine,
  41 failures, imports a `gh_client` module that did not exist and has since been
  deleted (the other agent is mid-refactor).
- Their file targets a *different* design: green+merged bumps demoted to Claims
  (I produce candidate Procedures), and a routing table keyed on `step_6_research.md`
  §A6 (a research file with a different section scheme than mine).
- Filenames collide exactly: `ci_workflow_history.py`, `bot_dependency_prs.py`,
  `workflow_knowledge.py`, `held_out.py`. Their versions were written to those paths
  after mine; my code is what is on disk now, and my 64 tests pass against it.
- Also present and not mine: `skillmd_dataset.py`, `skillmd_gate.py`,
  `skillmd_offline.py`, `skillmd_pilot.py`, `codemod_*.py`, `openrewrite.py` — steps 3
  and 7 work by another agent.

I did not delete or edit any of it, and I did not touch the 41 failures, because
reverting another agent's WIP is not mine to do. One of these two implementations
should win, and `admin.py` will have two `step6-*` command sets otherwise.
