# Step 6 research: CI workflow histories and dependency-bump PRs

Date: 2026-09-28
Agent: step 6 build
Spend: $0.00 (no LLM calls made during research; all findings from reading primary sources and the live APIs)

## Headline finding that changes the step

**The workflow-history corpus contains no execution data.** Its 17 columns are all
commit/file-level. There is no `conclusion`, no `run_id`, no per-job result, no log.
Verified directly against the live file (see "Local verification" below), not just the card.

This means Source A **cannot be the evidence layer**. Step 6's build instruction 1 —
"turning each workflow change + its run outcome into Procedures (passing runs) or failure
Claims" — is not executable against this dataset. There is no run outcome to read.

The clean citation is GHALogs' related-work description of the IEEE dataset, which states
Cardoen et al. "do not provide performance or outcome data of the workflows' executions."

Consequence, and it is the right architecture anyway: **Source A is a candidate
generator** (like Step 3's SkillMD), and **Source B (live GitHub API) is the only place
outcomes can come from**. The "check" for a workflow-derived Procedure can therefore never
be its own historical CI run.

## Source A: GitHub Actions workflow histories

### What it actually is

- Paper: "A dataset of GitHub Actions workflow histories", IEEE MSR 2024 (Lisbon, 15–16 Apr 2024).
  Preprint PDF: `https://orbi.umons.ac.be/bitstream/20.500.12907/48470/1/main.pdf`
- Zenodo concept DOI (resolves to latest): `10.5281/zenodo.10259013`
- Tool: `gigawork` 1.2.0, released on PyPI.
- License: **CC-BY-4.0** (Creative Commons Attribution 4.0 International), confirmed on
  every version record fetched (2024-04-30 `10947687`, 2024-10-25 `13985548`, 2025-04-15
  `15221545`, 2026-05-22 `20340547`).

### Version drift (read this before quoting any number)

| Zenodo record | Date | Repos | workflows.csv.gz | Note |
|---|---|---|---|---|
| 10947687 | 2024-04-30 | 32,886 | 113.9 MB | matches the paper's numbers |
| 13985548 | 2024-10-25 | 32,886 | 191.3 MB | same workflow tarball hash as 15221545 |
| 15221545 | 2025-04-15 | 32,886 | 191.4 MB | |
| **20340547** | **2026-05-22** | **52.9K** | **311.3 MB** | **current**; "1M+ … K+ repositories", extraction dated 2025-03-26 |

The prompt's "160k" is the paper's 2023 figure (160,443 workflow histories across 32,886
repos, 1,526,475 workflow files). The current record is roughly 3x that. **Quote the
version DOI, never the concept DOI alone**, or the number will be ambiguous.

### Schema — verified by ranged download, not from the card

`workflows.csv.gz` header, exactly as read from record 20340547:

```
repository,commit_hash,author_name,author_email,committer_name,committer_email,
committed_date,authored_date,file_path,previous_file_path,file_hash,previous_file_hash,
git_change_type,valid_yaml,probably_workflow,valid_workflow,uid
```

17 columns. `git_change_type` ∈ {A, D, M}. `valid_yaml`/`probably_workflow`/`valid_workflow`
are booleans computed by gigawork. `uid` is `repo/path/<blob sha>`.

**No outcome column of any kind.** No `conclusion`, `status`, `run_id`, `duration`,
`jobs_passed`, or log reference.

The YAML bodies are in a separate 1.4 GB `workflows.tar.gz` keyed by `file_hash`. So a
workflow item requires a **second fetch** to get its content — this is a two-part source,
not a single-row read, and any adapter must model that.

### Quality problems

- The paper's own numbers imply substantial redundancy: 156,933 identical workflow files
  (10.36% of the dataset) were deduplicated by SHA-256 at extraction.
- ~23% of snapshots fail YAML parse in the related MSR'24 extraction work, and a
  `valid_yaml` column has shipped broken before. **The boolean columns are convenience
  flags, not ground truth** — re-parse and re-validate, do not trust them.
- Repos were filtered to ≥300 commits, ≥100 stars, non-fork, created before 2023-01-01,
  with a commit after 2023-01-01. So the corpus is **skewed toward mature, popular,
  pre-2023 repos** and under-represents new projects. Do not treat its language or tool
  distribution as the real-world distribution.
- Collection used full `git clone` in Oct 2023; 168 repos failed to clone and were dropped
  silently. There is no failure manifest.

### Why GHALogs stays excluded

GHALogs (MSR 2025) is **CC-BY-SA-4.0**, confirmed via the Zenodo API. Share-alike
contaminates any derived corpus we build on top of it, so it is excluded on license
grounds. It is also, per the research, the only large public source that has both run
outcomes and logs — which is exactly what makes excluding it costly and correct. We take
the license hit rather than the contamination risk.

## Source B: Dependabot / Renovate PRs

### API mechanics — verified live against api.github.com

Token: `PERSONAL_GITHUB_TOKEN` from `backend/.env` (`ghp_`, 40 chars). Never logged.
Authed rate limits observed: **core 5000/hr, search 30/min, code_search 10/min,
graphql 5000/hr**.

**The bot-author qualifier works, and the documented form is correct.** Tested directly:

| query | result |
|---|---|
| `is:pr is:merged author:app/dependabot` | total_count ≈ 26,602,443, and all 5 sampled items returned `user.login=dependabot[bot]`, `user.type=Bot` |
| `is:pr is:merged author:dependabot[bot]` | total_count ≈ 26,602,371 — near-identical, so both forms resolve |

GitHub's docs document `author:app/USERNAME` for integration accounts. The `[bot]` form
also works. **Recommend `author:app/dependabot`** (documented) and treat `author:renovate`
as unverified until tested — a probe for it hit a secondary rate limit before returning.

**Rate limits are the real constraint on this source, and the failure mode is a 403 with
`"You have exceeded a secondary rate limit"`, not a 429.** Four back-to-back search calls
tripped it. Any scraper must:
- read `X-RateLimit-Remaining` / `X-RateLimit-Reset` and `Retry-After` on every response,
- treat **403 with a secondary-limit body** as retryable, not as a permission error,
- back off well beyond the documented minimum, because the documented minimum is not what
  the secondary limiter enforces.

### Retention — the binding architectural constraint

GitHub Actions run history and check results are retained ~90 days by default, and
repositories can cut that to 1 day. **Merged-PR metadata persists indefinitely; the check
outcomes on that commit do not.**

Consequence: a *historical* green-CI evidence chain for already-merged PRs **cannot be
assembled from the API today.** Evidence for a merged bump PR is only obtainable at the
moment of merge, going forward. This is a retention fact, not an API gap, and it is the
single most important design constraint on Source B.

### Licensing / ToS

- Public repository metadata and public file content are minable under GitHub's Terms of
  Service for API use, subject to the API's stated rate limits and to attribution for
  CC-licensed repository content.
- License for any *extracted knowledge* is **per repository**, resolved from the repo's own
  SPDX id, and gated through `repo_license_policy.classify_spdx`. Never a single license
  for the compilation.
- One thing I could not resolve and will not guess: the legal status of an abstract
  *method* extracted from a CC-BY-4.0 file. The dataset is CC-BY-4.0, which permits
  adaptation with attribution, but whether the *extracted procedure* is a derivative work
  is a question for counsel. Flagged, not decided.

## The "green CI" question — can CI be the check?

Partly. The literature is clear that green CI is **necessary but far from sufficient**,
via at least six independent mechanisms:

1. **42.1%** of GitHub Actions workflows build with **no test step at all** — green means
   nothing was checked.
2. **~11%** of *successful* CI jobs are rerun — silent failures, ignored exit codes.
3. **67.73%** of rerun GHA builds are **flaky**.
4. Dependency-fault injection: tests miss **>50%** of injected faults.
5. Official AtCoder suites accept **589 of 20,375** submissions that are verifiably buggy.
6. Developers merge with red CI; "required status checks" are not always required.

So: **a green CI run is admissible as corroborating evidence, and must never be the sole
promotion path to `verified`.** In this codebase that maps to a specific, already-enforced
rule — `execution/evidence.py::REQUIRED_EVIDENCE_TYPES_FOR_VERIFIED` is
`("execution_result", "reproduction")`, and a host self-report (an externally-observed CI
run) is stamped `record_execution_outcome@1:self_report`, which
`evidence_trust.is_trusted_writer` classifies as `CLAIMED_SUCCESS`/`UNKNOWN` and never
`VERIFIED_*`. The architecture already encodes the right answer. Step 6 must route CI
evidence as `experiment` or `observation` (witness types, no promotion) unless a run
originates from our own sandbox.

## Two hard blockers found

### Blocker 1 — CC-BY-4.0 is not on the allowlist

`repo_license_policy.DEFAULT_ALLOWLIST` is exactly 8 ids: `MIT`, `Apache-2.0`,
`BSD-2-Clause`, `BSD-3-Clause`, `ISC`, `0BSD`, `Unlicense`, `CC0-1.0`. Deliberately
absent, per the module's own docstring: "Extending the allowlist is a deliberate ruling
about a license nobody has judged yet."

`classify_spdx("CC-BY-4.0")` therefore returns **QUARANTINE**, reason "not on the disclosed
permissive allowlist". So Source A as specified ingests **0 items**.

This is the allowlist working correctly, not a bug. Filed as a numbered board question
with a proposed default rather than silently widening a frozen policy.

### Blocker 2 — step 4's verifiers do not exist

`screening.CHECK_TYPES` is a closed 8-value vocabulary that is 1:1 with a DB CHECK
constraint (verified live on the shard):
`prompt_injection, trust_escalation, secret_exposure, pii, license, malicious_executable,
source_trust, unsafe_locator`.

There is no `actionlint` or `zizmor` check type, and `.scratch/ingestion/` did not exist
before this step, so step 4 has not run. The spec and schema are frozen, so adding a check
type needs a migration plus a ruling. Step 6 therefore attaches **no** step-4 check and says
so honestly in the summary.

## What is actually buildable

1. **A workflow-file reader** implementing the existing `SourceAdapter` Protocol
   (`discover`/`fetch`/`fingerprint`) that maps a workflow YAML revision to a
   **candidate** Procedure — never verified, never evidence-backed.
2. **A rate-limited, resumable, host-pinned GitHub API client** for bot PRs, with the
   token stripped on any non-GitHub hop (the existing `github_corpus` client reuses one
   `Authorization` header across all redirect hops, which leaks the token to any public
   host; do not copy that).
3. **A held-out exclusion loader** — it does not exist anywhere in `app/` today, and the
   Common rules require it before any SWE-bench-family ingestion.
4. **Honest license accounting** for both sources, with rejection counts by reason.

## Sources

- Cardoen et al., "A dataset of GitHub Actions workflow histories", IEEE MSR 2024.
  `https://orbi.umons.ac.be/bitstream/20.500.12907/48470/1/main.pdf` `[PEER-REVIEWED]`
- Zenodo record 20340547 (current version, 2026-05-22).
  `https://zenodo.org/records/20340547` `[DATASET CARD]`
- Zenodo concept DOI `10.5281/zenodo.10259013` (all versions) `[DATASET CARD]`
- Zenodo record 10947687 (2024-04-30, matches the paper's figures) `[DATASET CARD]`
- GitHub REST search docs, author + is qualifiers.
  `https://docs.github.com/en/search-github/searching-on-github/searching-issues-and-pull-requests` `[PRODUCTION WRITEOUT]`
- GitHub REST API rate limits, incl. secondary limits.
  `https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api` `[PRODUCTION WRITEOUT]`
- GHALogs (MSR 2025), CC-BY-SA-4.0 — read for its related-work description of the IEEE
  dataset's missing execution data, and excluded from ingestion on license grounds.
- Local verification: ranged download of `workflows.csv.gz` from record 20340547, header
  and rows read directly; live rate-limit probe against `api.github.com`; live
  `author:app/dependabot` search with returned `user.login` verified.
