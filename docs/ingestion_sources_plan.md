# Ingestion sources plan: verifiable procedural knowledge (2026-09-27)

Candidate sources of step-by-step, checkable procedures for coding agents, verified against the source pages
where possible. The starting list came from a Perplexity survey (reproduced in the session). Numbers below
marked **verified** were read from the Hugging Face Hub API on 2026-09-27 (card license, split row counts,
repo size, last-modified). Everything else is from the survey and still needs checking before ingestion.

**Revalidated 2026-09-28 against arXiv, dataset cards, GitHub `licenses` APIs, LICENSE files and Zenodo.**
The full correction record — right/wrong arXiv identifiers, stale row counts, license reversals, and the
post-search problems found in each corpus — is
`.scratch/arxiv_ingestion_sources_deep_dive.md`. Section numbers below are unchanged; rows marked
**arXiv-checked** were opened on the primary page on 2026-09-28.

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

**Correction (2026-09-28, arXiv-checked):** the nebius OpenHands trajectory corpus stores OpenAI-style
chat messages (`role`, `content`, `tool_calls`, `tool_call_id`), **not** the OpenHands event history that
`ingestion_sources/openhands.py` parses. The "our existing adapter reads it" claim below was wrong. A
chat-message normalizer is required for it, the nebius SWE-agent set, and Open-SWE-Traces; SWE-smith's
`messages` are a third, non-identical shape.

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
| `nebius/SWE-rebench-openhands-trajectories` | **verified:** cc-by-4.0, 2.08 GB, modified 2025-12-27 | per-trajectory eval result | **arXiv-checked:** 67,074 trajectories, 32,161 resolved, 1,823 repos (the survey's 84,480 is wrong). Qwen3-Coder-480B, OpenHands 0.54. `trajectory` holds **chat messages, not OpenHands events**, so this needs a normalizer (see the correction above). Take resolved only. |
| `nvidia/Open-SWE-Traces` | **arXiv-checked** (arXiv:2606.16038, WIP): cc-by-4.0, **42.6 GB, 511,668 rows across v1.0/v1.1/v1.2**, 9 languages, modified 2026-09-22 | `resolved ∈ {-1,0,1}`, tool messages, **per-row SPDX `license`** | Largest permissive corpus and the only one with a per-item license field. 56k git-hacking trajectories were removed 2026-08-26; filter them out. Chat-message `messages`; needs a normalizer. Paper is work-in-progress. |
| `ByteDance-Seed/Multi-SWE-bench_trajs` | **arXiv-checked** (tasks arXiv:2504.02605, NeurIPS 2025 D&B): **cc0-1.0 trajectories**, 4.73 GB, modified 2025-12-19 | leaderboard trajectories + logs, expert labels | Most permissive trajectory license we found. 7 non-Python languages; exclude SWE-bench Verified Python and our held-out ids. The *task* repo is `license: other`; only `_trajs` is CC0. |
| `nebius/SWE-agent-trajectories` | OpenCode survey: cc-by-4.0, 80,036 rows, 5.6 GB (not re-checked) | test-execution logs | **arXiv-checked** (arXiv:2505.20411 is SWE-rebench; the trajectory card has no arXiv paper). Chat-message format: needs a normalizer. Includes failures, which suit failure and recovery claims. Largely the same tasks as the row above, so dedupe (see rules). |
| `SWE-Gym/OpenHands-Sampled-Trajectories` | **verified:** 6,055 rows, 0.30 GB, 2024-12-23, **license not declared on card** | resolved flag | OpenHands format, but blocked until a license is confirmed. |
| `nvidia/SWE-Zero-openhands-trajectories` + `nvidia/SWE-Hero-openhands-trajectories` | **arXiv-checked** (arXiv:2604.01496): cc-by-4.0, ~300k execution-free + 13k execution-backed, modified 2026-05-05/08 | resolved labels, execution vs non-execution contrast | Largest; ingest a sample first. The execution-free traces are **not** procedures — hold them as failure/diagnostic evidence until a check exists. |
| `SWE-bench/SWE-smith-trajectories` | **verified:** mit, 76,002 rows across three formats, 4.22 GB, 2025-07-19 (arXiv:2504.21798: 49,897 trajectories / 21,513 resolved) | `resolved` | SWE-agent `messages` format: **needs a new normalizer**. Claude 3.7 Sonnet trajectories. The three splits (tool / xml / ticks) are the **same trajectories in three formats**: ingest ONE split (`tool`). Synthetic bugs, so lower value as real-world procedures. |
| `nebius/SWE-rebench` | **arXiv-checked** (arXiv:2505.20411, NeurIPS 2025): cc-by-4.0, **21,000+ tasks / 3,400+ Python repos**, 7,500 Docker images, 2025-12-23 | FAIL_TO_PASS/PASS_TO_PASS + prebuilt image | Survey said 6,440, which is actually SWE-bench-extra. Ingest as `verified_solution` (issue + gold patch + tests), license per `license_name`. |
| `nebius/SWE-rebench-V2` | **arXiv-checked** (arXiv:2602.23866, ICML 2026): cc-by-4.0, **32,079 tasks, 20 languages, 3,617 repos**, ~2.3 GB, 2026-05-12 | tests + images + per-instance quality flags | **Not in the survey.** Newest and broadest backbone; its metadata flags underspecified descriptions and restrictive tests. Check its overlap with our SWE-rebench held-out set before any experiment use. |
| `nebius/SWE-bench-extra` | **verified:** cc-by-4.0, 6,376 rows, 2025-05-28 | tests | Survey listed it as unavailable with unknown license; it is on HF, cc-by-4.0. |
| `SWE-Gym/SWE-Gym` | **arXiv-checked** (arXiv:2412.21139): mit, 2,438 rows, 2025-05-10 | F2P/P2P | Tasks only (11 Python repos). |
| `R2E-Gym/R2E-Gym-V1` | **arXiv-checked** (arXiv:2504.07164): apache-2.0, 8,700+ procedurally curated executable tasks, 6.1 GB, 2026-07-23 | generated tests + execution environments | Apache-2.0 at gym scale. Synthetic generation, so real-world value is below the nebius sets. |
| `SWE-bench-Live/SWE-bench-Live` | **arXiv-checked** (arXiv:2505.23419): mit dataset card, 1,565 tasks / 164 repos, refreshed monthly, 2026-09-04 | FAIL_TO_PASS / PASS_TO_PASS, per-instance images, `test_cmds` + `log_parser` | **The MIT card does not cover the repos**: the per-repo table includes GPL-3.0, GPL-2.0 and AGPL-3.0. Filter per repository before ingest. Contamination-resistant by construction. |
| `SWE-bench/SWE-smith` | **verified:** mit, **59,136 rows**, modified 2025-12-14 | test harness per task | Survey said 50,137. Synthetic bugs (injected, then fixed), so lower value as real-world procedures. |
| `SWE-Perf/SWE-Perf` | **arXiv-checked** (arXiv:2507.12415, ICML 2026): apache-2.0, 140 performance tasks, 2025-08-05 | performance + behavioral tests in an executable env | The only clean *performance*-improvement check we found; gives Kel a non-unit-test applicability signal. Small, so per-instance license still applies. |
| `internlm/SWE-Fixer-Train-110K` | **verified:** mit, 1.47 GB, 2025-03-17 | issue -> patch | **arXiv-checked** (the paper is arXiv:2501.05040): issue/patch pairs with **no trace and no executable check**. Downgraded from section A to section F: not a procedure corpus. |

### A2. Security-repair corpora with a real oracle (arXiv-checked 2026-09-28)

Most vulnerability corpora are observation-only: they have a before/after diff and nothing that executes. These
four have a check that can fail, which is what a procedure needs. Full table in the deep-dive note.

| Source | Verified facts | Oracle | Decision |
|---|---|---|---|
| `nebius/PatchEval-Verified` (arXiv:2511.11019) | apache-2.0, 1,000 CVEs, 230 Docker sandboxes | PoC stops firing after the patch **and** functionality tests still pass | **Admit — best licensing-clean repair corpus found.** The "Verified" revision exists precisely because the original PoCs over-fit patch shape; that caveat is the value, not a defect. |
| SEC-bench (arXiv:2506.11791) | mit, 200 CVE instances, 29 C/C++ projects | sanitizer verdict | **Admit**, C/C++ only. |
| Vul4J (Zenodo 10.5281/zenodo.6383527) | data cc-by-4.0, toolchain GPL-3.0, 79 exploit rows | `vul4j reproduce` / `validate-patch` | **Admit the 79 PoV rows only**; the 50 static-analysis rows have no executable oracle. Several Spring entries have bit-rotted — read `STATUS.md` per entry. Do not vendor the GPL tool. |
| SecBench.js (ICSE 2023, DOI 10.1109/ICSE48619.2023.00096) | cc-by-1.0 artifact, 600 npm vulnerabilities | exploit payload + independent validation oracle | **Admit.** Inspect the code LICENSE inside the tarball before vendoring; the artifact card is not the code license. |
| ARVO (arXiv:2408.02153) | **NOASSERTION**, 6,138 reproduced vulns / 311 projects | PoC fires on the vulnerable build and not on the fixed build | **Conditional only.** Best-designed repair oracle we found, but the artifact license blocks ingestion until resolved. |
| CVE-Factory / LiveCVEBench (arXiv:2602.03012) | license not stated, 190 tasks / 14 languages | functional + vulnerability-present/resolved tests | **Quarantine** pending license and manual verification. |
| CyberForge (arXiv:2608.06471) | license not verified, 1,034 validated vulns | differential PoV: the injected build passes the existing tests and the PoV fires only on it | **Quarantine** pending license; copy the oracle design regardless. |
| CVEfixes (Zenodo 10.5281/zenodo.4476563) | data cc-by-4.0, tool NOASSERTION, 11,873 CVEs | **none** | **Observations only.** Never promote to procedures; do not vendor the tool. |
| PrimeVul (arXiv:2403.18624) | license unstated, 6,968 vulnerable functions | **none** | **Observations only / quarantine.** |
| SecretBench (arXiv:2303.06729) | **no license**, gated BigQuery/GCS, 15,084 labeled secrets | manual labels only | **Hold.** Gated access, live-format secrets, and no license. |

### A3. Agent environments: not corpora, but the cleanest check definitions available (arXiv-checked)

These ship *tasks plus verifiers* rather than trajectories, so they are procedure templates and calibration
sets, not ingest rows. They matter because a verifier is reusable: the same check shape (container state diff,
DB state hash, deterministic script) covers our own procedures.

| Source | Verified facts | Check shape | Notes |
|---|---|---|---|
| Terminal-Bench 2.0 / Harbor (arXiv:2601.11868) | apache-2.0 harness, MIT task mirrors, 89 tasks, active 2.1 | per-task pytest against final container state | Human-reviewed tasks with `solution.sh` and `test_outputs.py`. Run locally; do not claim official leaderboard placement. |
| τ²-bench (arXiv:2506.07982) | mit, 115 retail / 50 airline / 114 telecom tasks | final DB-state hash + required communicate-info; `ENV_ASSERTION` | Deterministic state check with replayed reference actions. The user simulator is LLM-backed — a fidelity limit, not a check limit. |
| OSWorld / OSWorld-Verified (arXiv:2404.07972) | apache-2.0, 369 tasks, verified subset refreshed 2025-07-28 | 134 execution-based evaluation functions on final VM state | Mature and objective. Task cards carry **no reference steps**, and the Windows subset is copyright-blocked. |
| AppWorld (arXiv:2407.18901) | apache-2.0 + encrypted-redistribution condition, 750 tasks / 9 apps | state-diff containment: the expected state must be a *subset* of the observed delta, which catches collateral damage | Accepts any valid path rather than one reference trace. Bundles must not be redistributed unencrypted; local-only storage required. |
| Toolathlon-Verified (arXiv:2510.25726) | trajectories cc-by-4.0, **task repo license undeclared**, 108 tasks / 32 apps / 604 tools | deterministic per-task eval scripts | Realistic tool breadth, no LLM judge in the check. Conditional until the task repo's license is confirmed; the canary forbids training-corpus use. |
| AFTER (arXiv:2606.23127) | **license conflict:** paper cc-by-4.0 vs HF apache-2.0; 382 tasks | per-task tests with an oracle-side solution | Explicit cross-task / cross-role / cross-model skill transfer. Quarantine until the conflict is resolved. |
| DABstep (arXiv:2506.23719) | cc-by-4.0, 450+ tasks | exact factoid answer match | Cheap and objective, but the hard tasks are permutations of ~23 core questions and there are no reference steps. |
| SkillEvolBench (arXiv:2605.24117) | **no LICENSE found**, 180 tasks / 6 environments | verifier-backed structured feedback | The acquisition→frozen-deployment design is the interesting part. **Blocked** until a LICENSE exists. |
| EvoAgentBench (arXiv:2607.05202) | **license conflict:** HF apache-2.0 vs paper cc-by-sa; 528/267 tasks | inherited hidden tests / pass@1 | **Quarantine** until resolved; the GDPVal slice is judge/expert graded. |

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
| Node.js userland migrations (`npx codemod`) | MIT, 50+ codemods (Perplexity, unverified) | Node API migrations, each with tests. |
| jscodeshift / ast-grep engine | MIT | Engines, not content. ast-grep is a **verifier** (section E). |
| codemod.com playbooks | license unknown | Blocked until licensed. |
| `Instagram/LibCST` | **corrected 2026-09-28:** MIT with an enumerated PSF-licensed carve-out (not a blanket NOASSERTION) | **Re-admitted with a per-directory license resolver.** The survey read GitHub's `NOASSERTION` and dropped it; the LICENSE file is actually MIT with specific PSF subdirectories. Ingest as a **verifier** for Python codemod procedures (section E), and gate each recipe on the license of the directory it came from. |
| `github/codeql` (queries and libraries) | MIT (the queries repo) | **Trap:** the queries are MIT, but the CodeQL **CLI** that runs them is, as we understand its terms, free only for open-source code; analysing private commercial code needs GitHub Advanced Security. Verify the CLI terms before shipping any such check. Ingest queries as *knowledge* (vulnerability class -> how it is detected). Never ship a procedure whose check runs the CodeQL CLI on a customer's private repo. |
| OpenRewrite: `rewrite-java-dependencies`, `rewrite-jackson`, `rewrite-openapi` | Apache-2.0 per the GitHub API | **License changes by module:** Moderne moved some recipe modules to its Source Available License, so re-check each module's LICENSE **at the exact commit ingested** and record that commit. Java/JVM only, so lower priority for a Python-first launch. Tests use `RewriteTest` before/after. |

### D. CI workflows: large, useful for CI procedures, license caution

| Source | Survey facts (unverified) | Notes |
|---|---|---|
| GHALogs (D2KLab, Zenodo) | 116k workflows / 25k repos, **CC-BY-SA-4.0**, run logs + pass/fail | Share-alike: blocked (rule 2). |
| GitHub Actions workflow histories (Zenodo 10259013) | 160k histories / 32k repos, CC-BY-4.0 | Workflow-file histories. Pair them with pass/fail runs to get evidence. |
| Workflow evolution dataset (arXiv:2602.14572) | **arXiv-checked 2026-09-28:** the paper exists and the dataset is **public on Zenodo** (record `10.5281/zenodo.18756610`) | **Re-admitted**; the survey's "not public" is stale. Read the Zenodo license before ingesting and record the DOI as the `source_locator`. |
| Dependabot / Renovate PRs | no dataset | Scrape through the GitHub API: bot PRs with CI status are version-bump procedures with a built-in pass/fail. Medium effort, high signal. |
| `clouddrove/github-shared-workflows` | Apache-2.0; README: 41 workflows (OpenCode survey) | Reusable workflows with no tests of their own. Verify them with actionlint + zizmor (section E). Low priority. |
| `actions/starter-workflows` | **corrected 2026-09-28:** GitHub's API says `NOASSERTION`, but the repository ships a verbatim **MIT LICENSE file** | **Re-admitted, conditional** on re-reading that LICENSE file at the exact commit ingested. This is the general lesson: the API field is a hint, the LICENSE file is the license. Rule 1 already says the gate is per item. |

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
`rufimelo/DeltaSecommits` (none on card), `secureIT-project/CVEfixes` (data cc-by-4.0 but the GitHub tool is
`NOASSERTION` — do not vendor the tool), `ARVO` (NOASSERTION artifact), `SecretBench` (no license, gated
access, live-format secrets), `SWE-bench/SWE-bench` + `SWE-bench_Verified` + `SWE-bench_Multimodal` (no license
on the dataset cards), `SWE-bench Pro` (NOASSERTION plus commercial held-out repos), `SkillEvolBench`
(no LICENSE found), `AFTER` and `EvoAgentBench` (paper-vs-repo license conflict, quarantined until resolved).

Re-admit only on an explicit, commercial-compatible license. **Removed from this list on 2026-09-28:**
`actions/starter-workflows` and `Instagram/LibCST` — both were dropped on a GitHub API `NOASSERTION` reading
and both actually carry permissive LICENSE files (MIT, and MIT with a PSF carve-out respectively). Read the
LICENSE file; do not gate on the API field alone.

## Contamination and licensing caveats that change what a number means

- **SWE-bench Verified deltas are not capability deltas.** Recent arXiv work (`2609.06780`, `2609.27891`)
  shows git-history and gold-solution leakage can inflate agent scores. Keep the held-out exclusion rule below
  and record which sources production held for any published claim.
- **Corpus overlap is the norm, not the exception.** The nebius OpenHands and SWE-agent trajectory sets cover
  largely the same SWE-rebench tasks; `nvidia/Open-SWE-Traces` re-solves the same repos. Dedupe by
  `instance_id` before counting anything as a yield measurement.
- **nvidia removed 56k git-hacking trajectories on 2026-08-26.** Re-check the current row count and the
  removal note at ingest time rather than trusting a cached count.
- **Write-side poisoning reaches every later reader** (PoisonedRAG, arXiv:2402.07867). Keep ingestion writes
  and reader retrieval in one threat model, and do not ingest untrusted canaries.

## Corrected arXiv identifiers (2026-09-28)

The identifiers below are in circulation but point at unrelated papers. The full table, with the correct ids,
is in `.scratch/arxiv_ingestion_sources_deep_dive.md` section E.

| Wrong id | Actually | Correct |
|---|---|---|
| 2505.03719 | decentralized optimization | no arXiv paper for SWE-bench Multilingual; SWE-bench is 2310.06770 |
| 2412.15704 | LDP poisoning | SWE-bench Multimodal 2410.03859 |
| 2505.24846 | preference learning | SWE-rebench 2505.20411 |
| 2505.12739 | secrecy capacity | SWE-Gym 2412.21139 |
| 2412.17440 | XAI in aeronautics | SWE-Perf 2507.12415 |
| 2508.02673 | decision-diagram numerics | Multi-SWE-bench 2504.02605 |
| 2505.11970 | real-time scheduling survey | SWE-bench-Java 2408.14354 (work in progress) |
| 2505.22486 | adversarial training | SWE-Fixer 2501.05040 |

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

Reordered 2026-09-28 against the revalidated evidence. Step 1 is no longer "zero new code" — the corpus is
chat messages, so the normalizer is the first real deliverable and it unblocks the three largest corpora.

1. **Chat-message trajectory normalizer** (HF row loader -> `role`/`content`/`tool_calls` -> trace events).
   One normalizer serves nebius OpenHands, nebius SWE-agent and Open-SWE-Traces; SWE-smith's `messages` are a
   separate shape. Proving test: round-trip one known row per corpus through `write_normalized_trajectory`.
2. **nebius SWE-rebench OpenHands trajectories (resolved only).** Best-documented real executed traces: 32,161
   resolved with generated-test validation. Run a 1,000-trajectory pilot and measure procedures produced, dedup
   rate, and judge spend per procedure. This is the yield number that decides whether the rest is worth it.
3. **Verified solutions: SWE-rebench V1 + V2 + SWE-Gym + SWE-bench-extra** as `verified_solution` artifacts
   (issue -> patch -> tests). Per-item license from `license_name`; V2's per-instance quality flags gate which
   tasks are usable. Needs the HF row loader.
4. **`nvidia/Open-SWE-Traces` (resolved=1, git-hacking filtered).** Largest corpus, 9 languages, and the only
   source with a per-row SPDX field. Sample it; do not pull 42.6 GB before step 2's yield justifies it.
5. **Multi-SWE-bench `_trajs` (CC0).** Non-Python coverage under the most permissive trajectory license we
   found. Exclude SWE-bench Verified Python and our held-out ids.
6. **PatchEval-Verified** (apache-2.0) as the first security-repair corpus, plus SEC-bench. Both have real
   PoC/sanitizer oracles, so their procedures are checkable rather than asserted.
7. **SWE-bench-Live**, filtered per repository: the MIT card does not override the GPL/AGPL repos in its table.
8. **ast-grep-essentials** rules, **LibCST recipes** (per-directory license resolver), and the **section E
   verifiers** (actionlint, zizmor, ast-grep) as check types, so CI and refactor procedures become verifiable.
9. **SkillMD-138K / gitskills** as candidate procedures, license-gated per file, deduped by content hash.
10. **SWE-Perf** (140 tasks) for a non-unit-test applicability signal; **Terminal-Bench 2.0 / tau2-bench /
    OSWorld / AppWorld** as verifier-shape references, not as ingest rows.
11. Lower priority: OpenRewrite (per-commit LICENSE check), CodeQL queries (knowledge only), SWE-Zero /
    SWE-Hero (execution-free traces stay evidence until a check exists), Dependabot/Renovate scrape, kubernetes
    docs, workflow-evolution Zenodo record.
12. Blocked until decided: GHALogs (share-alike), and the section F sources.

Each step lands as a job type or loader with its proving test (the repo's hard rule), and the pilot numbers
go into `ingestion_problems.md`.
