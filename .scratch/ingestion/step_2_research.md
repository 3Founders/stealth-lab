# Step 2 research — verified solutions (issue → patch → tests)

Date: 2026-09-28 · Sources read at the URLs below · All row counts and field
distributions read from the Hugging Face `datasets-server` API and the
parquet, not from card prose. Where a card and the parquet disagree, the
parquet wins and the card is flagged.

Evidence tiers: `[PEER-REVIEWED]` `[PREPRINT]` `[DATASET CARD]` `[PRODUCTION WRITEUP]`.

## 1. Sources and identifiers

| Source | HF repo | Revision pin used | Rows (verified) | Claimed |
|---|---|---|---:|---|
| SWE-rebench | `nebius/SWE-rebench` | `89cdfbab…f4f881` | **27,878** (`test` 21,336 + `filtered` 6,542) | 27.9k ✓ |
| SWE-rebench-V2 | `nebius/SWE-rebench-V2` | `475dd5e8…eba1e0` | **32,079** (`train`) | 32.1k ✓ |
| SWE-bench-extra | `nebius/SWE-bench-extra` | `11dcbfb3…5c92a5` | **6,376** (`train`) | 6.4k — card says 6,415, blog 6,411; **parquet is 6,376** |
| SWE-Gym | `SWE-Gym/SWE-Gym` | `bb94ed9e…59cb` | **2,438** (`train`) | 2.4k ✓ |

- SWE-rebench — Badertdinov et al., arXiv:2505.20411, **NeurIPS 2025 D&B** `[PEER-REVIEWED]`. Execution code: `github.com/SWE-rebench/SWE-bench-fork`. The paper describes only the 21,336 `test` rows; the `filtered` split is **undocumented in both card and paper**, and is exactly the subset with a non-null `docker_image` (21,336 − 14,794 nulls = 6,542).
- SWE-rebench-V2 — arXiv:2602.23866 `[PREPRINT]`, ICML 2026 per the arXiv comments field (author-asserted; not confirmed on a proceedings site). The real successor, not a mirror. Adjacent: `nebius/SWE-rebench-V2-PRs` (126,300 rows), which the authors themselves release as **lower-confidence**.
- SWE-Gym — Pan et al., arXiv:2412.21139, **ICML 2025** `[PEER-REVIEWED]`.
- SWE-bench-extra — **no paper exists.** Citable provenance is the Nebius blog "Scaling data collection for training software engineering agents", 2024-12-20 `[PRODUCTION WRITEUP]`. Superseded by SWE-rebench.

## 2. The four findings that shaped the code

**(a) `FAIL_TO_PASS` is a real `list[str]` in all four datasets** — not a JSON string, which is how SWE-bench itself stores it. The SWE-bench-extra **card documents these columns as `str` and is wrong** (`/info` reports `{"feature": {"dtype": "string"}, "_type": "Sequence"}`). A reader written against the card would have crashed or silently produced one giant test name. `_as_str_list` accepts both and never raises.

**(b) There is no `resolved` / `resolved_by` field in any of the four.** Every row is a validated task by construction: upstream ran the test patch, then the solution patch. Nothing in the code may test `resolved`.

**(c) The four sources disagree on the license field, and this was the largest engineering item.**

| Source | Field | Shape | Effect on our allowlist |
|---|---|---|---|
| SWE-bench-extra | `license` | lowercase SPDX slug, 8 values, **0 nulls** | cleanest; ~99% ALLOW |
| SWE-rebench-V2 | `license` | SPDX-ish, 16 values, 348 nulls, **5,038 × `custom-check-github`** | `custom-check-github` is GitHub's "found a LICENSE I could not map" placeholder — a non-identification, like `NOASSERTION` |
| SWE-rebench V1 | `license_name` | **GitHub Licensee display NAME**, ~56 spellings of ~15 licenses, 1.5–2.5% null | fed raw to `classify_spdx`, **~96% quarantine** for a non-licensing reason |
| SWE-Gym | **absent** | none | **every row is a license reject** |

Predicted, then measured: with the display-name normalizer, V1 yields 500 ALLOW / 516 license decisions. Without it, the largest Python corpus is quarantined on a one-suffix-character mismatch. `test_v1_display_name_would_be_quarantined_without_the_normalizer` pins this as a regression test.

`identify_spdx_from_text` cannot help: it is a license-**body** header matcher and these rows carry no license text, only a name. So the normalizer is a lookup table of observed spellings, and anything unseen returns `None` → QUARANTINE, never a guess.

Dataset card licenses (CC-BY-4.0 for the Nebius sets, MIT for SWE-Gym) cover **the packaging only**. Every card says so verbatim: *"please respect the license of each specific repository on which a particular instance is based."*

**(d) Only V2 is multi-language.** Measured over all 32,079 rows: py 7,243 (22.6%) · go 6,144 (19.2%) · ts 4,204 (13.1%) · js 4,138 (12.9%) · rust 3,123 (9.7%) · java 1,716 · php 1,445 · kotlin 889 · julia 793 · elixir 416 · scala 411 · swift 362 · dart 251 · c 230 · cpp 182 · csharp 173 · r 157 · clojure 105 · ocaml 58 · lua 39. V1, SWE-Gym and SWE-bench-extra are Python-only. **The paper's own §3.7 figures (py 21.6% / go 20.6%) disagree with the released data**; the measured table is used.

## 3. Validation pipelines — what actually proves an outcome

| | criterion | flakiness control | LLM quality gate | gate accuracy |
|---|---|---|---|---|
| SWE-bench-extra | "tests execute correctly" | none | none | — |
| SWE-rebench V1 | ≥1 F2P ∧ all F2P pass ∧ all initially-passing still pass | none | `meta.llm_score` | complexity 81%, clarity 79%, **test-patch validity 67%** |
| SWE-rebench-V2 | ≥1 F2P after **full-suite** paired runs | **3× rerun, keep only stable** | 3 judges + `B1..B6` flags | shipped config: prec 0.83, **rec 0.10, F1 0.17** |
| SWE-Gym | **"gold patch passes _more_ tests than original"** — a net-improvement test, materially weaker than a specified-test-identity test | none (but ~200 human hours) | none | — |

Two consequences. First, **the LLM quality labels are not gates** — one in three V1 `test_score` labels is wrong, and V2 ships the config with 10% recall, so ~90% of underspecified issues survive. We record their distribution and do not filter on them. Second, the strongest available *hard* signals are structural, and are the ones the code uses: V1's `PASS_TO_FAIL` (gold patch breaks a passing test) and `FAIL_TO_FAIL` (task's own tests are broken).

## 4. Contamination — the finding that shaped the held-out gate

- **V2 has no decontamination stage at all.** Its 5-stage funnel (Preliminary Collection → Setup Synthesis → Execution Validation → Issue-Clarity Filtering → Metadata Enrichment) contains no SWE-bench exclusion. All 12 SWE-bench repos are permissively licensed and would survive every one of its filters. Its median `created_at` is 2022-09-06, inside every frontier model's training window.
- V1's §3.2 "decontamination" is **temporal only** (marking leaderboard entries created before a model's release date), not a SWE-bench exclusion.
- SWE-Gym and SWE-bench-extra **both state repo-level exclusion** and both claims check out (11 SWE-Gym repos, none of them a SWE-bench repo).
- Corroboration: Aleithan et al., *SWE-Bench+*, arXiv:2410.06992 `[PREPRINT]` — 32.67% of successful patches involved solution leakage and 31.08% had weak tests; filtering both drops resolve rate 12.47% → 3.97%. And *The SWE-Bench Illusion*, ICSE 2026, DOI 10.1145/3786583.3786882 `[PEER-REVIEWED]`: Verified performance "substantially reflects memorization of training sequences," and the signal **disappears for data post-dating the model** — which is an argument for this corpus, not against it.

**Design consequence:** instance-level exclusion alone is insufficient. Our experiments score on 21 named repositories, so a *different* instance from the same repository teaches the agent the same codebase the held-out instances live in. `load_held_out` therefore excludes by instance id **and** by `scored_repos`, with the repo-level default stated as a judgement call and switchable.

## 5. Reader contract this had to satisfy

`app/services/ingestion_sources/base.py` — `SourceAdapter` is a **sync** Protocol (`discover()`/`fetch()`/`fingerprint()`); `SourceArtifact.content` must be Markdown `parse_skill_md()` already understands, and `license_metadata` keys `license` + `spdx_id` are what arm `screening.spdx_license_signal` downstream.

`dispatch.py` is `TRAJECTORY_ADAPTERS` — trajectory-only. A corpus source does **not** belong there, which is why this step touches no shared file. Precedent for a corpus source is `skillmd_dataset.py` (reader) + `app/ingestion/skillmd_cli.py` (CLI).

`verified_solutions.py` provides `preserve(pool, procedure_row_id=, code=, task=, language=, locator=, ...)` and `durable_locator()`, which needs commit **and** path **and** (repository or uri) — so every artifact carries `commit` and `path`.

## 6. Open questions (numbered, with proposed defaults)

1. **SWE-Gym has no license field** — 2,438 rows, 11 known repos, 0 admissible. Proposed default: ship as a license rejection and do not ingest. Alternative: a hand-maintained per-repo license map for those 11 repos. **Needs a founder ruling**; the first is the default applied here.
2. **Repo-level held-out exclusion is stricter than the written rule.** Proposed default: on. Flip with `include_repos=False`.
3. **`custom-check-github` (5,038 V2 rows, 15.7%)** is a non-identification. Proposed default: QUARANTINE, matching the "never guess a license" posture. Resolving them would need a GitHub licenses-API call per repo.
4. **ZPL-2.1 / MPL-2.0 / AFL-3.0 / WTFPL** normalize cleanly but are not on `DEFAULT_ALLOWLIST`. Proposed default: leave them to the policy (QUARANTINE). Widening the allowlist is a separate decision.
5. **`ingest_skill_package` now requires a configured General Compute LLM client** (founder directive 2026-09-15 removed the deterministic fallback), so any real ingest spends money. Proposed default: dry-run first, pilot second, both before production.
6. **V2's `image_name` is one Docker repo per *GitHub* repo with a per-instance tag**, so 3,743 public images does not imply per-instance coverage. Per-instance pullability for V2 is **not established**.

## 7. Bibliography

- arXiv:2505.20411 — SWE-rebench. NeurIPS 2025 D&B. `[PEER-REVIEWED]`
- arXiv:2602.23866 — SWE-rebench-V2. ICML 2026 (author-asserted). `[PREPRINT]`
- arXiv:2412.21139 — SWE-Gym. ICML 2025. `[PEER-REVIEWED]`
- arXiv:2410.06992 — SWE-Bench+ (leakage / weak-test rates). `[PREPRINT]`
- DOI 10.1145/3786583.3786882 — The SWE-Bench Illusion. ICSE 2026. `[PEER-REVIEWED]`
- Nebius blog, 2024-12-20 — "Scaling data collection for training software engineering agents" (only citable provenance for SWE-bench-extra). `[PRODUCTION WRITEUP]`
- HF dataset cards + `datasets-server` `/size`, `/info`, `/statistics`, `/rows` for all four repos, read 2026-09-28. `[DATASET CARD]`
- Docker Hub namespaces `swerebench/*` (8,127 repos) and `swerebenchv2/*` (3,743 repos), read 2026-09-28.

**Known limitation of this research:** the `datasets-server` `/filter` and `/search` endpoints returned HTTP 500 for every dataset tried, so empirical `instance_id` membership probes across corpora could not be run. Cross-corpus overlap claims above are marked documented or inferred, never measured.
