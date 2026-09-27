# Ingestion sources plan: verifiable procedural knowledge (2026-09-27)

Candidate sources of step-by-step, checkable procedures for coding agents, verified against the source pages
where possible. The starting list came from a Perplexity survey (reproduced in the session). Numbers below
marked **verified** were read from the Hugging Face Hub API on 2026-09-27 (card license, split row counts,
repo size, last-modified). Everything else is from the survey and still needs checking before ingestion.

## How each source enters Kel (existing code)

| Path | Code | Feeds |
|---|---|---|
| Agent trajectories (OpenHands export shape) | `backend/app/services/ingestion_sources/openhands.py` -> `normalized_trajectory.py` -> `trace_worker.write_normalized_trajectory` | the same `agent_traces`/`trace_events` pipeline the Claude Code collector uses -> episodes -> claims -> procedures |
| SKILL.md files | `ingestion_sources/skill_md.py`, `document_adapters/skill_markdown_adapter.py`, job `ingest_skill_package` | procedures directly |
| Docs / guides / runbooks (URL, HTML, Markdown, PDF, GitHub) | `document_adapters/*`, job `ingest_document` | procedures via `compile_skill_artifact` |
| Whole repositories (workflows, scripts, recipes) | `repo_ingestion.py` (DOMAINS registry), job `ingest_repo` | procedures with URL-only references (bytes only for `executable_source`) |
| Verified task solutions (issue -> patch -> tests) | `verified_solution` artifacts + `source_locator` (commit 68efb02) | Procedures that carry their verified patch |

**Gaps:** there is no Hugging Face dataset reader yet. Each HF source needs a thin loader that turns a row into
the adapter shape above. Trajectories that are *not* in OpenHands format (SWE-agent/SWE-smith `messages`) need
a second normalizer in `normalized_trajectory.py`'s style.

## Hard rules for every source

1. **License is checked per item, not per compilation.** A CC-BY-4.0 dataset *card* does not relicense the code
   or text inside it. SWE-rebench carries a `license_name` per task. SKILL.md collections keep each repo's
   license. The SPDX gate applies per item (an allowlist; `NOASSERTION` rejected).
2. **Share-alike (CC-BY-SA, e.g. GHALogs, Stack Overflow)** is blocked until legal decides how derived
   procedures are licensed.
3. **The experiment stays isolated.** SWE-bench / SWE-rebench / SWE-Gym material overlaps the benchmark repos.
   Ingest it only into production, never into the experiment databases (`kel_swebench`,
   `kel_swebench_rebench`), which `experiments/swebench/swe_env.py` keeps local and separate. Any
   *published* claim about Kel on SWE-bench / SWE-rebench must say which sources production held.
4. **Only verified outcomes become procedures.** Take `resolved=True` trajectories and merged, test-carrying
   PRs. Failed trajectories are kept as evidence (failure claims), never as procedures.

## Sources (verified where marked)

### A. Agent trajectories and verified task solutions: highest value, closest to the product loop

| Source | Verified facts | Verification signal | Notes / survey corrections |
|---|---|---|---|
| `nebius/SWE-rebench-openhands-trajectories` | **verified:** cc-by-4.0, 2.08 GB, modified 2025-12-27 | per-trajectory eval result | OpenHands format: **our existing adapter reads it**. OpenCode survey (read from the card): **67,074 trajectories, 32,161 successful** (Perplexity's 84,480 is wrong). Qwen3-Coder-480B, OpenHands 0.54. Take resolved only. |
| `nebius/SWE-agent-trajectories` | OpenCode survey: cc-by-4.0, 80,036 rows, 5.6 GB (not re-checked) | test-execution logs | SWE-agent format: **needs a new normalizer**. Includes failures, which suit failure and recovery claims. Largely the same tasks as the row above, so dedupe (see rules). |
| `SWE-Gym/OpenHands-Sampled-Trajectories` | **verified:** 6,055 rows, 0.30 GB, 2024-12-23, **license not declared on card** | resolved flag | OpenHands format, but blocked until a license is confirmed. |
| `nvidia/SWE-Zero-openhands-trajectories` | **verified:** cc-by-4.0, **12.21 GB**, modified 2026-05-05 | eval result | Survey: issues from permissively-licensed repos only (MIT/Apache/BSD). Largest; ingest a sample first. |
| `SWE-bench/SWE-smith-trajectories` | **verified:** mit, 76,002 rows, 4.22 GB, 2025-07-19 | `resolved` | SWE-agent `messages` format: **needs a new normalizer**. Claude 3.7 Sonnet trajectories. The three splits (tool / xml / ticks, about 24-26k rows each) are the **same trajectories in three formats**: ingest ONE split (`tool`). |
| `nebius/SWE-rebench` | **verified:** cc-by-4.0, **27,878 rows** (test 21,336 + filtered 6,542), 2025-12-23 | FAIL_TO_PASS/PASS_TO_PASS + prebuilt image | Survey said 6,440, which is actually SWE-bench-extra. Ingest as `verified_solution` (issue + gold patch + tests), license per `license_name`. |
| `nebius/SWE-rebench-V2` | **verified:** cc-by-4.0, 32,079 rows, 2026-05-12 | tests + images | **Not in the survey.** Newest; check its overlap with our SWE-rebench held-out set before any experiment use. |
| `nebius/SWE-bench-extra` | **verified:** cc-by-4.0, 6,376 rows, 2025-05-28 | tests | Survey listed it as unavailable with unknown license; it is on HF, cc-by-4.0. |
| `SWE-Gym/SWE-Gym` | **verified:** mit, 2,438 rows, 2025-05-10 | F2P/P2P | Tasks only (11 Python repos). |
| `SWE-bench/SWE-smith` | **verified:** mit, **59,136 rows**, modified 2025-12-14 | test harness per task | Survey said 50,137. Synthetic bugs (injected, then fixed), so lower value as real-world procedures. |
| `internlm/SWE-Fixer-Train-110K` | **verified:** mit, 1.47 GB, 2025-03-17 | issue -> patch | Survey: "URL unknown, likely CVE", which is wrong; it is a real issue-fix corpus. |

### B. SKILL.md collections: cheap to ingest (adapter exists), quality varies

| Source | Verified facts | Notes |
|---|---|---|
| `FayeZC/SkillMD-138K` | **verified:** cc-by-4.0 (compilation), 0.56 GB, 2026-04-08 | 138k deduplicated skills from about 20.5k repos; `html_url` lets us resolve each repo's license. |
| `mvaccargiu/gitskills` | **verified:** cc-by-4.0 (metadata), **13.43 GB, 11.3M rows across splits**, 2026-09-11 | Survey said 3.8M files; the card's split counts total 11.3M (multiple configs). Filter by stars and license, then dedupe against SkillMD-138K. |
| agentskills.in / skills.sh registry | survey: 216k indexed skills | Registry API; the source of both datasets above. Good for keeping them fresh. |

SKILL.md files state *intent*, not a verified outcome. Ingest them as candidate procedures with applicability
gating. They earn evidence only when a run succeeds with them.

### C. Codemods, rules and migration recipes: executable, test-backed, narrow

| Source | Facts (OpenCode survey, license from the GitHub API; not re-checked) | Notes |
|---|---|---|
| `coderabbitai/ast-grep-essentials` | Apache-2.0, about 0.6 MB | Structural rewrite/security rules with valid/invalid test cases: small and verifiable. Language-agnostic, so the best fit here for a Python-first launch. |
| OpenRewrite: `rewrite-java-dependencies`, `rewrite-jackson`, `rewrite-openapi` | Apache-2.0 per the GitHub API | **License changes by module:** Moderne moved some recipe modules to its Source Available License, so re-check each module's LICENSE **at the exact commit ingested** and record that commit. Java/JVM only, so lower priority for a Python-first launch. Tests use `RewriteTest` before/after. |
| `github/codeql` (queries and libraries) | MIT (the queries repo) | **Trap:** the queries are MIT, but the CodeQL **CLI** that runs them is, as we understand its terms, free only for open-source code; analysing private commercial code needs GitHub Advanced Security. Verify the CLI terms before shipping any such check. Ingest queries as *knowledge* (vulnerability class -> how it is detected). Never ship a procedure whose check runs the CodeQL CLI on a customer's private repo. |
| Node.js userland migrations (`npx codemod`) | MIT, 50+ codemods (Perplexity, unverified) | Node API migrations, each with tests. |
| jscodeshift / ast-grep engine | MIT | Engines, not content. ast-grep is a **verifier** (section E). |
| codemod.com playbooks | license unknown | Blocked until licensed. |
| `Instagram/LibCST` | GitHub reports NOASSERTION (MIT with PSF-licensed parts) | Dropped: mixed license. |

### D. CI workflows: large, useful for CI procedures, license caution

| Source | Survey facts (unverified) | Notes |
|---|---|---|
| GHALogs (D2KLab, Zenodo) | 116k workflows / 25k repos, **CC-BY-SA-4.0**, run logs + pass/fail | Share-alike: blocked (rule 2). |
| GitHub Actions workflow histories (Zenodo 10259013) | 160k histories / 32k repos, CC-BY-4.0 | Workflow-file histories. Pair them with pass/fail runs to get evidence. |
| Workflow evolution dataset (arXiv 2602.14572) | not public | Skip. |
| Dependabot / Renovate PRs | no dataset | Scrape through the GitHub API: bot PRs with CI status are version-bump procedures with a built-in pass/fail. Medium effort, high signal. |
| `clouddrove/github-shared-workflows` | Apache-2.0; README: 41 workflows (OpenCode survey) | Reusable workflows with no tests of their own. Verify them with actionlint + zizmor (section E). Low priority. |
| `actions/starter-workflows` | NOASSERTION | Dropped. |

### D2. Operations docs

| Source | Facts (OpenCode survey) | Notes |
|---|---|---|
| `kubernetes/website` | CC-BY-4.0, about 556 MB repo | License is fine, but these are operations guides with no success check. Low priority for a coding-first launch; each procedure needs a synthesized check. |

### E. Verifiers: tools used as checks, not ingested

A procedure is only as verified as its check. These tools make whole classes of procedure checkable; Kel's
`check` field calls them instead of ingesting them as content:

| Verifier | License | Verifies | Use |
|---|---|---|---|
| `rhysd/actionlint` | MIT | GitHub Actions workflow syntax and semantics | Check for any "create or fix a CI workflow" procedure: exits 0 with no findings |
| `zizmorcore/zizmor` | MIT | GitHub Actions security (injection, excessive permissions, unpinned actions) | Paired with actionlint: no findings at or above medium, from the SARIF/JSON output |
| `ast-grep/ast-grep` | MIT | Structural presence or absence of code patterns | Check for refactor and migration procedures ("no remaining `old_api(` calls") |
| Project test suites (pytest etc.) | per repo | Behaviour | The default check for code procedures (the SWE-style F2P/P2P model) |

Not a verifier for customer code: the CodeQL CLI (see section C).

### F. Dropped by the license gate (no license on the source, or NOASSERTION)

`SWE-Gym/OpenHands-Sampled-Trajectories` (no card license), `CIRCL/vulnerability-cwe-patch` (none),
`rufimelo/DeltaSecommits` (none on card), `actions/starter-workflows`, `Instagram/LibCST`,
`secureIT-project/CVEfixes` (NOASSERTION). Re-admit only on an explicit, commercial-compatible license.

## Dedup and provenance rules

- **One trajectory per (task, outcome) across sources.** The nebius OpenHands and SWE-agent sets largely cover
  the same SWE-rebench tasks: key by `instance_id` and keep the best resolved trajectory per task, plus at most
  one failed trajectory for failure claims.
- **One format per corpus:** SWE-smith trajectories, `tool` split only.
- **SKILL.md corpora:** SkillMD-138K and gitskills overlap heavily. Dedupe by content hash; the gitskills
  compilation license (CC-BY-4.0) does NOT cover the files, so gate each file on its repo's license.
- **Record the source commit or revision** on every ingested item (`source_locator`), so a license re-check
  applies to exactly what was ingested.
- **Held-out exclusion for claims:** before publishing any Kel result on SWE-rebench / SWE-bench, list which
  of those tasks production holds, or exclude our held-out ids
  (`experiments/swebench_rebench/runs/design.json` `test`) from production ingestion.

## About the survey's "papers show 12-28% absolute gains"

SWE-Gym (+14 points), SWE-smith (40.2% for SWE-agent-LM-32B), SWE-Dev and Agent-RLVR report gains from
**fine-tuning or RL on this data**, not from retrieving it at task time as Kel does. TDFlow's 88.8% / 94.3% uses
human-written tests. None of these is evidence that retrieval-time procedures help. The retrieval-time evidence
is ReasoningBank (+4.6 on SWE-bench Verified), ACE (AppWorld) and AWM (WebArena), plus our own experiments.

## Order

1. **SWE-rebench OpenHands trajectories (resolved only).** Zero new code; the adapter exists. Run a
   1,000-trajectory pilot and measure: procedures produced, dedup rate, judge spend per procedure.
2. **Verified solutions: SWE-Gym + SWE-rebench (+V2) + SWE-bench-extra** as `verified_solution` artifacts
   (issue -> patch -> tests). Needs the HF row loader. Per-item license from `license_name`.
3. **nebius SWE-agent trajectories.** Needs the SWE-agent normalizer; deduped against step 1 by `instance_id`.
4. **SWE-smith trajectories** (`tool` split). Same normalizer as step 3. Synthetic bugs, so lower real-world value.
5. **ast-grep-essentials** rules, and wire the **section E verifiers** (actionlint, zizmor, ast-grep) as check
   types so CI and refactor procedures become verifiable.
6. **SkillMD-138K / gitskills** as candidate procedures, license-gated per file, deduped by content hash.
7. **OpenRewrite** modules (after a per-commit LICENSE check), **CodeQL queries** (as knowledge only),
   **SWE-Zero** (12 GB, after step 1's yield justifies the judge spend), Dependabot/Renovate scrape,
   kubernetes docs.
8. Blocked until decided: GHALogs (share-alike), and the section F sources.

Each step lands as a job type or loader with its proving test (the repo's hard rule), and the pilot numbers
go into `ingestion_problems.md`.
