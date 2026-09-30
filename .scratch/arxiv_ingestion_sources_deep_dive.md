# arXiv Ingestion Sources Deep Dive — corrected, license-screened shortlist

**Date:** 2026-09-28  
**Purpose:** sources whose records can become verified, reusable procedures for coding agents: a goal, concrete steps or tool actions, and an executable check.  
**Method:** arXiv abstract/paper pages were opened for identity; Hugging Face Hub API / dataset cards, GitHub `licenses` API, LICENSE files, Zenodo records, and official artifact pages were opened for license, counts, dates, and download method. Post-search audits looked specifically for stale counts, wrong arXiv IDs, training restrictions, contamination, and broken download paths.  
**Screening rule:** drop missing/`NOASSERTION`/non-commercial/unknown licenses unless the row is explicitly marked conditional; per-item upstream licenses always override a dataset-card license.

## A. Best trajectory and task sources

| Rank | Source | arXiv / primary | License | Size / last update | Executable signal | What is great | What is a problem / mitigation |
|---:|---|---|---|---|---|---|---|
| 1 | `nvidia/Open-SWE-Traces` | arXiv:`2606.16038` (WIP) | CC-BY-4.0 | 511,668 total rows across v1.0/v1.1/v1.2; 42.6 GB; 9 languages; modified 2026-09-22 | `resolved ∈ {-1,0,1}`; tool messages; per-row SPDX `license` | Largest permissive corpus; per-row license filter; 9 languages; explicit resolved signal | Work-in-progress paper. 56k git-hacking trajectories were removed on 2026-08-26; filter `resolved=1` and exclude git-hacking metadata. Chat-message `messages`, not OpenHands event history: needs a normalizer. |
| 2 | `nebius/SWE-rebench-openhands-trajectories` | Dataset card; parent arXiv:`2505.20411` | CC-BY-4.0 | 67,074 trajectories, 32,161 resolved, 1,823 repos, 2.08 GB; modified 2025-12-27 | `resolved`, `gen_tests_correct`, `pred_passes_gen_test`; Docker images from SWE-rebench | Best-documented real executed traces; generated-test validation; clear comparison table | No arXiv paper of its own. The `trajectory` field is OpenAI chat messages, not the OpenHands event shape; the current plan's “existing adapter reads it” is wrong. Requires a chat-message normalizer. |
| 3 | `nebius/SWE-rebench-V2` | arXiv:`2602.23866`, ICML 2026 | CC-BY-4.0 | 32,079 tasks, 20 languages, 3,617 repos, ~2.3 GB; modified 2026-05-12 | Prebuilt images, fail-to-pass tests, environment validation | Per-instance metadata flags restrictive tests and underspecified descriptions; newest broad task backbone | LLM-judge soundness filter validated against human SWE-bench annotations; still a quality signal, not proof. Check overlap with our held-out ids. |
| 4 | `ByteDance-Seed/Multi-SWE-bench_trajs` | Tasks arXiv:`2504.02605`, NeurIPS 2025 D&B | CC0-1.0 for trajectories | 4.73 GB; modified 2025-12-19 | Leaderboard trajectories + logs; expert labels | Most permissive trajectory license; 7 non-Python languages; 4,723-instance training set | Paper v1 says 1,632 instances/7 languages; camera-ready says 2,132/8 including SWE-bench Verified Python. Use the non-Python set and exclude our held-out ids. Task repo itself has `license: other`; only `_trajs` is CC0. |
| 5 | `nebius/SWE-rebench` V1 | arXiv:`2505.20411`, NeurIPS 2025 | CC-BY-4.0 | 21,000+ issue–PR pairs, 3,400+ Python repos; 7,500 Docker images; modified 2025-12-23 | Automated environment setup + test execution | Reproducible task backbone; explicit per-task `license_name`; decontamination reference | Frozen-ish relative to V2; only use after excluding benchmark-held-out ids. |
| 6 | `SWE-bench/SWE-smith-trajectories` | arXiv:`2504.21798` | MIT | 49,897 trajectories / 21,513 resolved, 129 repos; modified 2025-07-19 | `resolved` + final `patch`; SWE-agent `messages` | Large, permissive, explicit resolution | Synthetic injected bugs, not real issue comprehension. Needs a SWE-agent message normalizer; use `tool` split only. |
| 7 | `R2E-Gym/R2E-Gym-V1` | arXiv:`2504.07164` | Apache-2.0 | 8,700+ procedurally curated executable tasks; 6.1 GB; modified 2026-07-23 | Test-generation + execution environments | Apache-2.0 at gym scale; explicitly separates test-based and execution-free verifiers | Synthetic task generation; trajectories are a separate card and must be license-checked independently. |
| 8 | `SWE-bench-Live/SWE-bench-Live` | arXiv:`2505.23419` | MIT dataset; per-repo licenses | 1,565 tasks / 164 repos; monthly verified additions; modified 2026-09-04 | FAIL_TO_PASS / PASS_TO_PASS, per-instance images, `test_cmds`, `log_parser` | Actively updated, contamination-resistant, verified split | Dataset MIT tag is not enough: the repo table includes GPL-3.0, GPL-2.0 and AGPL-3.0 projects. Filter per repository before ingest. |
| 9 | `SWE-Perf/SWE-Perf` | arXiv:`2507.12415`, ICML 2026 | Apache-2.0 | 140 performance tasks; modified 2025-08-05 | Performance/behavioral tests + executable environment | The only clean performance-improvement check in the set; different applicability signal from unit tests | Very small; license per instance still required. |
| 10 | `nvidia/SWE-Zero-openhands-trajectories` + `SWE-Hero-openhands-trajectories` | arXiv:`2604.01496` | CC-BY-4.0 | 300k execution-free + 13k execution-backed; modified 2026-05-05/08 | Resolved labels; execution vs non-execution contrast | Lets us measure how much apparent competence survives without execution grounding | Execution-free traces are not procedures; ingest only as failure/diagnostic evidence until a check exists. |

## B. Best non-SWE agent-environment sources

| Rank | Source | arXiv | License | Size / update | Check | Great | Problem / mitigation |
|---:|---|---|---|---|---|---|---|
| 1 | Terminal-Bench 2.0 / Harbor | arXiv:`2601.11868` | Apache-2.0 harness; MIT task mirrors | 89 tasks; active 2.1 | Per-task pytest against final container state | Human-reviewed tasks, `solution.sh`, `test_outputs.py`, expert/junior time | Official leaderboard placement has rules; run locally without claiming official placement. |
| 2 | Toolathlon-Verified | arXiv:`2510.25726` | Trajectories CC-BY-4.0; task repo license not declared | 108 tasks, 32 apps, 604 tools; verified 2026-06-30 | Deterministic per-task eval scripts | Realistic tool breadth; no LLM judge in the check | Task repo has no license; use only if the artifact license is confirmed. Canary forbids training-corpus use. |
| 3 | τ²-bench | arXiv:`2506.07982` | MIT | Retail 115 / airline 50 / telecom 114; v1.0.0 2026-03-18 | Final DB-state hash + required communicate-info; `ENV_ASSERTION` | Deterministic state check and replayed reference actions | User simulation is LLM-backed; this is a fidelity limit, not a check limit. |
| 4 | OSWorld / OSWorld-Verified | arXiv:`2404.07972` | Apache-2.0 | 369 tasks; verified 2025-07-28 | 134 execution-based evaluation functions on final VM state | Mature, objective checks, verified trajectories | No reference steps in the task card; Windows subset is copyright-blocked. |
| 5 | AppWorld | arXiv:`2407.18901` | Apache-2.0 + encrypted redistribution condition | 750 tasks, 9 apps, 457 APIs | State-diff containment: expected state is a subset of the delta; catches collateral damage | Accepts any valid path; strong state-based check | Encrypted bundles must not be redistributed unencrypted; training/serving are exempt but local-only storage is required. |
| 6 | AFTER | arXiv:`2606.23127` | Paper CC-BY-4.0 vs HF Apache-2.0 conflict | 382 tasks; active | Per-task tests with oracle-side solution/tests | Explicit cross-task/cross-role/cross-model skill transfer | License conflict: gate on HF Apache-2.0 or quarantine; human review required. |
| 7 | DABstep | arXiv:`2506.23719` | CC-BY-4.0 | 450+ tasks; 2025/2026 | Exact factoid answer match | Objective and cheap | Hard tasks are permutations of ~23 core questions; no reference steps. |
| 8 | SkillEvolBench | arXiv:`2605.24117` | **No LICENSE found** | 180 tasks, 6 envs | Verifier-backed structured feedback | Deliberate acquisition→frozen-deployment design | Blocked until a LICENSE is found. |
| 9 | EvoAgentBench | arXiv:`2607.05202` | HF Apache-2.0 vs paper CC-BY-SA conflict | 528/267 tasks | Inherited hidden tests / pass@1 | Ability-graph labels and train/test support | License conflict: quarantine until resolved; GDPVal slice is judge/expert graded. |

## C. Security-fix corpora with real oracles

| Rank | Source | arXiv / primary | License | Size | Check | Decision |
|---:|---|---|---|---|---|---|
| 1 | PatchEval-Verified | arXiv:`2511.11019` | Apache-2.0 | 1,000 CVEs; 230 Docker sandboxes | PoC stops firing after patch + functionality tests pass | **Admit**; the Verified revision exists because original PoCs over-fit patch shape. This is the best licensing-clean repair corpus found. |
| 2 | SEC-bench | arXiv:`2506.11791` | MIT | 200 CVE instances, 29 C/C++ projects | Sanitizer verdict; PoC generation/patching tasks | **Admit**; C/C++ only. |
| 3 | ARVO | arXiv:`2408.02153` | **NOASSERTION** | 6,138 reproduced vulns, 311 projects | PoC fires on vulnerable build, not fixed build; tests as regression | **Conditional only**; do not ingest until artifact license is resolved. |
| 4 | Vul4J | Zenodo `10.5281/zenodo.6383527`; no arXiv | Data CC-BY-4.0; toolchain GPL-3.0 | 79 PoV + 50 SpotBugs-only rows | `vul4j reproduce` / `validate-patch` | **Admit PoV rows only**; segregate static rows. Spring entries have bit-rotted; read `STATUS.md`. |
| 5 | SecBench.js | ICSE 2023 DOI `10.1109/ICSE48619.2023.00096`; no arXiv | CC-BY-1.0 artifact | 600 npm vulnerabilities | Exploit payload + independent validation oracle | **Admit**; artifact license is permissive, but inspect the tar's code LICENSE before vendoring. |
| 6 | CVE-Factory / LiveCVEBench | arXiv:`2602.03012` | License not stated on abstract page | 190 tasks, 14 languages, 153 repos | Functional + vulnerability-present/resolved tests | **Quarantine** until LICENSE and manual verification are settled. |
| 7 | CyberForge | arXiv:`2608.06471` | License not verified | 1,034 validated vulns, 80 projects, 63 CWEs | Differential PoV: injected build passes existing tests, PoV fires only on injected build | **Quarantine** pending license; steal the oracle design regardless. |
| 8 | CVEfixes | arXiv:`2107.08760`; Zenodo `10.5281/zenodo.4476563` | Data CC-BY-4.0; GitHub tool NOASSERTION | 11,873 CVEs, 4,249 projects, 27 languages | No executable oracle | **Observations only**; never promote to procedures. Do not vendor the tool. |
| 9 | PrimeVul | arXiv:`2403.18624` | Unstated | 6,968 vulnerable functions, 755 projects | No executable oracle; before/after pairs | **Observations only / quarantine** pending license. |
| 10 | SecretBench | arXiv:`2303.06729` | **No license** | 97,479 candidates, 15,084 labeled | Manual secret labels, no execution | **Hold**; gated BigQuery/GCS, live-format secrets, unspecified license. |

## D. Permission / isolation sources to use as evaluation designs, not corpora

| Source | Identifier | Finding | How to use it |
|---|---|---|---|
| Authorization-First Retrieval (AFR) | ACL `2026.trustnlp-main.15` | Retrieve-then-filter exposed unauthorized context in 86.1% of base queries; answer leakage was 29.5–41.3% | Adopt structural-exposure = 0 as an ingestion/retrieval gate; report leak rate paired with authorized recall. |
| VaultRAG | `github.com/AgentPostmortem/VaultRAG` | ACL predicate in the retrieval CTE; removing it raised leak rate 0→81.8% with recall unmoved | Copy the `test_empty_corpus_does_not_score_green` guard; never certify isolation against an empty corpus. |
| PoisonedRAG | arXiv:`2402.07867` | Write-side poisoning reaches every later reader | Keep ingestion writes and reader retrieval in the same threat model; do not ingest untrusted canaries. |
| Indirect prompt injection | arXiv:`2302.12173` | Retrieved third-party content is an instruction channel | Route every source through redaction and treat retrieved text as untrusted. |

## E. Corrected arXiv identifiers and stale counts

The following commonly copied identifiers were checked and are **wrong**:

| Wrong id | What it actually is | Correct id / status |
|---|---|---|
| `2505.03719` | Decentralized optimization | No arXiv paper for SWE-bench Multilingual; use SWE-bench `2310.06770` and dataset card. |
| `2412.15704` | LDP poisoning | SWE-bench Multimodal = `2410.03859`. |
| `2505.24846` | MiCRo preference learning | SWE-rebench = `2505.20411`. |
| `2505.12739` | VLC-RF secrecy capacity | SWE-Gym = `2412.21139`. |
| `2412.17440` | XAI in aeronautics | SWE-Perf = `2507.12415`. |
| `2508.02673` | Decision-diagram numerics | Multi-SWE-bench = `2504.02605`. |
| `2505.11970` | Real-time scheduling survey | SWE-bench-Java = `2408.14354` (work in progress; superseded). |
| `2505.22486` | Adversarial training / EBMs | SWE-Fixer = `2501.05040`; the 110K training set has no traces/checks and is not a procedure corpus. |

Stale/incorrect counts corrected:

- `nvidia/Open-SWE-Traces` is **not** 207,489 rows anymore: 511,668 total rows across v1.0/v1.1/v1.2; 151k current v1.0 after git-hacking removal, 253k v1.1, 107k v1.2; 42.6 GB; CC-BY-4.0.
- `nebius/SWE-rebench-openhands-trajectories` is 67,074 / 32,161 resolved / 1,823 repos / 2.08 GB, not 84,480.
- `nebius/SWE-rebench-V2` is 32,079 tasks / 20 languages / 3,617 repos; Open-SWE-Traces is 9 languages. Do not merge the counts.
- BIRD Mini-Dev v1 is 498; current Mini-Dev V2 is 780. Do not report “500” without the version.
- The OpenHands trajectory card stores OpenAI chat messages (`role`, `content`, `tool_calls`, `tool_call_id`), not the OpenHands event history parsed by `ingestion_sources/openhands.py`. The existing adapter claim is false; add a chat-message normalizer.

## F. Drops and conditional holds

**Drop for license or shape:** `SWE-bench/SWE-bench`, `SWE-bench_Verified`, `SWE-bench_Multimodal` (dataset cards have no license); `SWE-bench Pro` (NOASSERTION plus commercial held-out repos); `SWE-bench-java` (WIP/no released dataset); `SWE-Fixer-Train-110K` (issue/patch pairs, no trace or executable check); `SWE-Gym/OpenHands-Sampled-Trajectories` (no card license); `ARVO` (NOASSERTION artifact); `SecretBench` (no license, gated, live-format secrets); `VJBench`/`PrimeVul`/`DiverseVul`/`ReposVul` repos (no LICENSE); `ToolBench` and `CRMArena`/`CRMArena-Pro` (CC-BY-NC); `GAIA`/`DSBench`/GDPVal standalone (LLM/expert judge only); `SkillEvolBench` (no LICENSE).

**Correct the plan's over-strict drops:** `actions/starter-workflows` has a verbatim MIT LICENSE file even though the GitHub API reports `NOASSERTION`; read the LICENSE file before quarantining. `Instagram/LibCST` is MIT with an enumerated PSF-licensed carve-out; admit with a per-directory license resolver, not a blanket drop. `Toolathlon-Trajectories` is CC-BY-4.0, but the task repo itself is unlicensed; the pair is conditional.

**Contamination warning:** SWE-bench Verified deltas are not capability deltas. Recent arXiv work (`2609.06780`, `2609.27891`) shows git-history and gold-solution leakage can inflate results. Keep `resolved=1` plus provenance filtering; never use a benchmark's own held-out ids in production ingestion.

## G. Web-search findings worth keeping

**Great:** per-row SPDX licenses (Open-SWE-Traces), explicit resolved labels plus generated-test checks (Nebius), CC0 trajectories (Multi-SWE), Apache-2.0 performance tasks (SWE-Perf), differential PoV oracles (ARVO/CyberForge/PatchEval), and state-diff containment (AppWorld).

**Problems found only after searching:** the OpenHands adapter assumption was wrong; the SWE-smith trajectory card is 76,002 rows across three formats, not three independent corpora; nvidia removed 56k git-hacking trajectories; Multi-SWE's paper count differs between preprint and camera-ready; BIRD/SWE-rebench contamination caveats materially change what a score means; several popular arXiv IDs point to unrelated physics/security papers; and multiple “datasets” are evaluation harnesses rather than ingestible rows.

**Recommended minimum ingest set:**

1. `nebius/SWE-rebench-openhands-trajectories` (real executed traces; chat-message normalizer).
2. `nvidia/Open-SWE-Traces` (scale + per-row SPDX; filter WIP/git-hacking).
3. `nebius/SWE-rebench-V2` (tasks with quality flags).
4. `ByteDance-Seed/Multi-SWE-bench_trajs` (CC0, non-Python coverage).
5. `PatchEval-Verified` (security repairs with real PoC/functionality oracles).
6. `SWE-bench-Live` (freshness, per-repo license filter).
7. `Terminal-Bench 2.0`, `τ²-bench`, `OSWorld`, and `AppWorld` (deterministic environment checks).

Only `resolved=True` trajectories, merged test-carrying PRs, and instances whose per-item license passes the gate become procedures. Everything else is evidence, a benchmark reference, or a quarantine candidate.
