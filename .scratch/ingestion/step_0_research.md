# Step 0 research — nebius SWE-rebench OpenHands trajectories (chat-message normalizer)

Date: 2026-09-28
Corpus: `nebius/SWE-rebench-openhands-trajectories`, pinned revision `35455389ab51bf5e2306bfd436ef72d0f98bf882`
Parent benchmark: `nebius/SWE-rebench`, pinned revision `89cdfbab4ab1bd8f5a658bb212d1b63624f4f881`

Method: (1) primary-source literature read, (2) Exa search of the live card/schema/community, (3) local verification
against 50 streamed rows plus 3 parent-dataset rows. Corrections found locally are marked **[CORRECTED]** and take
precedence over both the dataset card and our own plan/deep-dive notes.

---

## 1. The parent benchmark: SWE-rebench

`arXiv:2505.20411` — NeurIPS 2025 Datasets & Benchmarks. Verified correct (our plan's id is right).
<https://arxiv.org/abs/2505.20411>

- **Pipeline (4 stages, all automated):** task collection from ~450k PRs linked to issues created **before
  2025-05-01**; environment configuration (version inferred from `git tag`, `pip freeze`/`conda env export`
  pinned after success to defeat dependency drift); execution-based verification in buildah containers with
  **final images published**; quality assessment with a Qwen2.5-72B classifier fine-tuned on SWE-bench Verified
  (Issue Clarity 79% acc, Task Complexity 81%, **Test Patch Correctness 67%**).
- **Dataset:** 21,336 tasks / 3,468 repos. Benchmark subset 294 tasks / 169 repos. Mean 14.56 F2P tests.
- **Success predicate** is the F2P+P2P triple: ≥1 test from the test patch fails before the solution patch, all
  initially-failing pass after, all initially-passing keep passing.
- **Admitted quality problems (their own words):** "extracting consistently high-quality, verifiable SWE tasks …
  is an inherently imperfect process"; the fully automated pipeline "may result in some tasks being imperfectly
  described or unsolvable"; the quality assessor "cannot fully replicate nuanced human judgment". The install
  recipe generator was validated on only 18 repositories. Python-only.
- **Most important line for us (§2.4):** "RL agents might generate trajectories that appear as failures but are
  actually due to task imperfections … leading to incorrectly penalizing the agent." **`resolved=0` is not a
  uniformly negative example.**
- **No model cutoff is published.** The decontamination primitive is temporal and comparative (issue-creation date
  vs model release date); contaminated evaluations are *marked*, not removed.

`arXiv:2602.23866` — SWE-rebench-V2, ICML 2026, same first author. <https://arxiv.org/abs/2602.23866>
The authors' own successor, and the best available source on known defects: it adds an ensemble of LLM judges to
filter "unsound instances" and ships per-instance metadata flagging **overly restrictive tests**, **underspecified
descriptions**, **test brittleness**, **external dependencies**, and **inline-test contamination**. **None of this V2
metadata exists for the V1 instances our trajectories were generated from.**

### The oracle is not sound — independent measurements

- `arXiv:2410.06992` (SWE-Bench+, 2024) <https://arxiv.org/abs/2410.06992>: manual screening of 251
  SWE-Agent+GPT-4 successes found **solution leakage 32.67%**, **incorrect fixes passing weak tests 12.75%**,
  incomplete fixes 14.74%, wrong files 3.59%; only 36.25% genuinely correct. **31.08% of passed patches are
  suspicious due to weak tests.** Filtering drops SWE-bench Verified from 22.4% → 10.0%.
- `arXiv:2506.12286` (The SWE-Bench Illusion, 2025) <https://arxiv.org/abs/2506.12286>: file-path identification
  from issue text alone reaches up to 76% on Verified but ≤53% on repos outside SWE-bench; 5-gram function
  reproduction Verified 34.9% vs outside-repo 13.9% — i.e. **both instance-level and repository-level
  memorization** are measurable.
- `arXiv:2505.23419` (SWE-bench-Live) reports OpenHands + Claude 3.7 Sonnet at 19.25%, i.e. large
  distribution shift from the curated benchmarks.

**Implication for us:** `resolved=1` is necessary but not sufficient for "this is a verified procedure". It is a
*floor*, not a proof. The per-instance evidence chain (repo → license → F2P tests) has to be carried, and the
`model_patch` is the thing that makes the outcome checkable at all.

---

## 2. The trajectory corpus

### 2.1 What the card says vs what the parquet actually contains

Card: <https://huggingface.co/datasets/nebius/SWE-rebench-openhands-trajectories> ·
Blog: <https://nebius.com/blog/posts/openhands-trajectories-with-qwen3-coder-480b> (2025-12-23).
Repo tree at the pin: `.gitattributes`, `LICENSE`, `README.md`, `config.toml`, `tools.json`,
`trajectories.parquet` (2.08 GB). CC-BY-4.0 (full legal text read at the pin). `last_modified` 2025-12-27.
Discussions tab is **empty** — no community reports of broken rows or field drift.

There is **no paper for the trajectory release.** The card's only citation is a BibTeX entry with
`journal={Nebius blog}`. All corpus claims are **vendor self-reported**; the one independent check is `arXiv:2607.17205`.

**[CORRECTED] The real schema has 10 columns, not 9, and two names differ from the card.** Verified by
streaming 50 rows at the pinned revision:

| column | type | notes |
|---|---|---|
| `trajectory_id` | str | e.g. `chatcmpl-27dd0152801d38b5a37ff92d729c9fc1` — unique per trajectory |
| `instance_id` | str | e.g. `PlasmaFAIR__sdf-xarray-24`; joins the parent dataset and its Docker image |
| `repo` | str | e.g. `PlasmaFAIR/sdf-xarray` |
| `trajectory` | list | OpenAI-style chat messages — **confirmed, the plan's correction is right** |
| `tools` | list | **[CORRECTED] undocumented in the card's field table**: the 5 OpenAI function-calling definitions the model saw (`execute_bash`, `str_replace_editor`, `think`, `task_tracker`, +1) |
| `model_patch` | str | **[CORRECTED] also absent from the card's field table**: final modifications as a unified diff (27,897 chars in sample row 0) |
| `exit_status` | str | `'submit'` or an OpenHands error message |
| `resolved` | int | 0/1 |
| `gen_tests_correct` | float \| null | **float, not int**; `null` when no tests were generated |
| `pred_passes_gen_tests` | float \| null | **[CORRECTED] the name is PLURAL.** Card, `docs/ingestion_sources_plan.md:54` and `.scratch/arxiv_ingestion_sources_deep_dive.md:13` all say `pred_passes_gen_test` (singular) — **that field does not exist** |

**[CORRECTED] No license field exists anywhere in the row.** Per-item licensing must be joined from the parent
`nebius/SWE-rebench`, which does carry `license_name` per instance (verified: `'Apache License 2.0'`,
`'MIT License'` — full license *names*, which `repo_license_policy.identify_spdx_from_text` maps to SPDX).

### 2.2 Message shape (verified, 50 rows / 6,502 messages)

- Every message carries **all five** keys: `content`, `name`, `role`, `tool_call_id`, `tool_calls`. Uniform.
- **[CORRECTED] Gotcha: `tool_calls` is the literal string `"None"` on non-assistant messages, not JSON `null`.**
  A naive `if msg["tool_calls"]` is truthy for the string `"None"`. The card's own snippet has this bug.
- Assistant tool call: `{"function": {"arguments": "<JSON string>", "name": "<tool>"}, "id": "chatcmpl-tool-…",
  "type": "function"}`. **`arguments` is a string** and must be `json.loads`-ed (3,226/3,226 sampled were `str`).
- Tool result: `{"content": <str>, "name": "<tool>", "role": "tool", "tool_call_id": "chatcmpl-tool-…"}`.
- Role mix over 50 rows: assistant 3,226 · tool 3,176 · user 50 · system 50. **Exactly one system and one user
  message per trajectory** (the task prompt).
- Assistant `content` is `None`/empty on 932/3,226 messages.
- Tool-name distribution (the only 4 that appeared): `execute_bash` 1,628 · `str_replace_editor` 1,363 ·
  `think` 128 · `task_tracker` 57. **The corpus is overwhelmingly shell + file editing.**
- Messages per row: min 81, max 201, mean 130 (this is *messages*; merged events will be roughly half).

### 2.3 Statistics verified locally (50 rows)

- `resolved`: 23/50 = 46% (card implies 32,161/67,074 = 48%).
- `exit_status`: `submit` 47/50 (94%); `RuntimeError: Agent reached maximum iteration. Current iteration: 100,
  max iteration: 100` **3/50 (6%)**. **The 100-turn cap is a censoring mechanism and it is observable.**
  Neither the card nor the blog publishes this distribution.
- Test-generation 2×2 (our 50 rows): `(gen_tests_correct=F, pred_passes_gen_tests=T)` **16** ·
  `(F,F)` 29 · `(T,T)` 5 · `(T,F)` 0. `null` for both: 26.
- 46 distinct repos in 50 rows.

### 2.4 The under-used asset: a free test-gaming detector

`gen_tests_correct` and `pred_passes_gen_tests` are **not** redundant, and Nebius states no other public trajectory
dataset ships an evaluation of agent-generated tests. They factor into a 2×2 that separates test-gaming from
test-competence:

| | `pred_passes_gen_tests` high | low |
|---|---|---|
| `gen_tests_correct` high | good tests, patch satisfies them → **strongest positive evidence** | honest failure; the tests are reusable negative knowledge |
| `gen_tests_correct` low | **test-gaming signature** — own tests pass own patch but do not discriminate the golden fix | uninformative |

`resolved=0 ∧ gen_tests_correct low ∧ pred_passes_gen_tests high` is a **direct, pre-computed test-gaming
detector**. No published source reports the distribution of either field. This is a cheap, defensible quality
signal available before we spend anything.

### 2.5 Independent structural check that the parquet is sound

`arXiv:2607.17205` (Han, *A Systematic Evaluation of Trajectory Data Curation for LoRA Fine-Tuning of Code
Agents*, preprint, no venue) loaded the same 67,074-row corpus and reports a completeness gate with
**truncation ratio 1.0 for all 67,074 trajectories** and a thought–action–observation parse gate that
**removed zero rows**. The parquet is not corrupt at row level. Treat as a careful single-author replication.

---

## 3. Using agent trajectories as a knowledge source

### 3.1 The corpora, and why they are not knowledge sources

- **SWE-smith** `arXiv:2504.21798` — 50k synthetic instances from 128 repos; SWE-agent-LM-32B at 40.2% Pass@1 on
  SWE-bench Verified. Trajectories used as **SFT data with binary outcome filtering**; no systematic
  quality–quantity ablation. Schema differs from ours: `messages` is a JSON **string**, `resolved` is **bool**,
  plus `model`, `traj_id`, `patch`. **Third distinct shape — confirms the plan's note.**
- **SWE-Gym** `arXiv:2412.21139` (ICML 2025) — 2,438 real instances, 11 repos; trained on only **491** trajectories
  with "no signs of saturation at 491".
- Neither measures retrieval or reuse quality. They are corpora, not memories.

### 3.2 Procedure/workflow induction

- **Agent Workflow Memory** `arXiv:2409.07429` — induces a *workflow* ("a goal with a common routine") from
  trajectories; +24.6% relative step-wise SR on Mind2Web, +51.1% on WebArena, **+7.9% over human-written
  workflows**; gains grow with distribution gap (+8.9 to +14.0 absolute). Retrieval quality **not measured**.
- **ReasoningBank** `arXiv:2509.25140` (ICLR 2026) — memory items `{Title, Description, Content}` from **both
  successes and failures**, retrieved by **embedding similarity only** (no hybrid, no RRF, no reranker);
  up to +8.3 points on WebArena. Retrieval quality **not measured**.
  **[CORRECTED]** the correct id is 2509.25140; `2509.04690` is an unrelated math-physics paper.
- **SKILL-DISCO** `arXiv:2606.26669` — the most useful effect size here: ablating distillation and inducing
  **per successful trace** yields a *larger* skill library (43 vs fewer) but collapses success **99.3% → 53.0%**
  and inflates turns 3.2 → 11.5. **Per-episode extraction is not a cheaper approximation of distillation; it is
  actively worse.** Authors' own limitation: "procedural tasks only" (FSM-defined), so transfer to Python repos
  is unestablished.
- **Skill-Pro** `arXiv:2602.01869` (ICML 2026 spotlight) — skills with explicit activation/execution/termination
  conditions; the only work in this set reporting **reuse rate** as a first-class quantity. Magnitude not
  extracted here.
- **ACE** `arXiv:2510.04618` (ICLR 2026) — playbook of bullets; names two pathologies to design against:
  **brevity bias** and **context collapse** (iterative rewriting erodes detail).
- **SkillGen** (Microsoft Research, production writeup) — models skills as **interventions**, comparing outcomes
  on the same instances **with and without** the skill so that both repairs and regressions are counted. This is
  a causal per-instance measurement, not an aggregate. `[UNVERIFIED]` — overview only, no arXiv id, no effect sizes.

### 3.3 The two findings that should shape our design

**(a) Retrieval quality is essentially unmeasured in this field.** Across SWE-smith, SWE-Gym, AWM, ReasoningBank,
SkillWeaver, SKILL-DISCO, ACE and the CBR literature, **none** report an IR metric. The single exception is
`arXiv:2511.21730`, *A Benchmark for Procedural Memory Retrieval in Language Agents* (preprint, industry
affiliation, ALFWorld, 78 hand-coded + 336 AgentInstruct trajectories, 6 methods, MiniLM + ChromaDB, MAP/NDCG/
P@k/R@k @ k=5). Its findings: embedding retrieval reaches **79–86% MAP in-distribution but drops 30–42% under
vocabulary shift** (headline 84% → 59% MAP); **10× more contextual detail buys only 9.9%** — architectural
constraints, not corpus scale or representation richness, set the ceiling; and the **LLM judge had Cohen's
κ = 0.178 on borderline cases** (89.5% specificity on clearly-irrelevant pairs), i.e. systematically stricter than
its domain experts. Its cautionary framing: an agent "may appear to retrieve well by exploiting familiar
vocabulary, or fail due to poor action generation despite retrieving appropriate demonstrations."

**(b) A retrieved bad record is self-reinforcing.** `arXiv:2505.16067` measures Pearson r ≈ 1 between input
similarity and output similarity for retrieved memory; a noisy retrieved output is "replicated and even amplified"
and written back into memory, so a one-step memory error compounds. Two mechanisms from that paper are directly
useful to us: **misaligned experience replay** (a record that looks correct but hurts) and the observation that
**future task evaluations are free quality labels** for stored memory — cheap retroactive gating in an
append-only substrate. `arXiv` MemoryAgentBench (ICLR 2026) adds: **every** memory architecture tested fails
selective forgetting, which is the published analogue of our bi-temporal `t_invalid` semantics.

### 3.4 Case-Based Reasoning

`arXiv:2504.06943` (CBR review for LLM agents) formalises retrieval as selecting `C_q ⊆ L` maximising `sim(q, P_i)`
subject to a selectivity threshold, and states the field's own gap: "developing comprehensive benchmarks that assess
the distinctive capabilities of CBR-enhanced agents **remains an open challenge**". `arXiv:2606.05250` (persistent
CBM for data science) is the most concrete design — a **five-gate retention filter** (G1 execution success, G2 a
numeric metric is extractable, G3 direction-aware improvement, G4 **novelty** vs nearest neighbour unless strictly
better, G5 LLM metadata enrichment that **never rejects**) — and the most honest evaluation of it: across 108
retrieval events **no reuse-detection check ever returned a negative outcome**, so the reuse metric had **zero
negative instances and therefore no discriminative power**. Downstream it produced one small win and one small
loss (NOMAD RMSLE 0.0632 vs 0.0608, i.e. worse).

**The CBR lesson for us: a yield metric with no negative instances measures nothing.** Our pilot must count what
was *rejected*, not only what was accepted.

---

## 4. Quality risks specific to this corpus

1. **`resolved=0` is not a clean negative.** The parent paper says failures may be task-imperfections. Treating all
   failures as "what doesn't work" will import noise. Step 1's failure-Claims work must be gated on the 2×2, not
   on `resolved=0` alone.
2. **Turn-cap censoring at 100.** 6% of our sample hit `Agent reached maximum iteration`. Those are scaffold
   terminations, not model decisions. `exit_status` separates them and is free.
3. **Lossy chat serialisation.** `config.toml` sets `enable_history_truncation=false`, `condenser.type=noop`,
   `enable_condensation_request=false`, and the blog describes a deliberately *linear* history "allowing
   efficient training on the complete sequence of steps". A linear history is not the real harness state: parallel
   tool calls, retries, and internal reasoning the agent never emitted are absent. `tools` is a sidecar precisely
   because the event history was not preserved. **We are reading a training export, not a replay log.**
4. **Two fields undocumented by the card** (`tools`, `model_patch`) and one misnamed
   (`pred_passes_gen_tests`). Anything built against the card text alone would have failed on `KeyError`.
5. **Third-party code exposure.** CC-BY-4.0 on the derivative corpus does not relicense the 1,823 upstream
   repositories or the GitHub issue text. The plan's per-item license gate is the only control that addresses this.
6. **Contamination.** Our held-out SWE-bench Verified / SWE-rebench ids must be excluded, and per `arXiv:2506.12286`
   **repository-level** exclusion matters too — the repo already has a loader that returns `scored_repos` for
   exactly this reason.

---

## 5. Exa sweep — live card, community, adjacent loaders

Searched for the current card/schema, reported format changes or broken rows, and existing chat-message →
trajectory normalizers.

- The HF **discussions tab is empty** — zero issues, zero field-drift reports. Absence of reports is not evidence
  of absence of problems; it means nobody is watching.
- **Documented field drift in the repo's own commit history:** `3545538` "feat: update schema in doc" landed
  *after* the parquet was uploaded, so the README we validate against is not the README current when the data was
  written. `ebb5599` shipped a card containing the literal placeholder `For more details see our report in
  [Nebius blog](LINK-TO-BE-ADDED)`. **This is the argument for pinning the revision, and for treating the card as
  documentation rather than as a schema contract.**
- **The card's comparison table misreports its competitors** (it lists `SWE-bench/SWE-smith-trajectories` as
  49,897 rows / 21,513 successful; that dataset's own card says 76,002 rows / 4.22 GB), and its markdown header
  is malformed so the "Ours" values sit under ambiguous headers. **Do not cite its competitor columns; read
  statistics from the blog table.**
- **Adoption is real but says nothing about quality:** 7 model repos, 2 Spaces, ~5,400 downloads/month.
- No reusable public "chat messages → trajectory" normalizer surfaced for this shape. The closest reusable prior
  art is the card's own `filter_and_deserialize` snippet, which has the `"None"`-string bug and the
  `arguments`-as-string requirement described above. **We are writing this mapper; there is nothing to copy.**
- Corpus-wide practitioner guidance (SWE-smith, SWE-Gym, R2E-Gym) converges on **binary outcome filtering with no
  quality ablation** — which is exactly the gap this step is filling.

---

## 6. Implications for extracting verified reusable procedures

1. **Write the normalizer; there is no prior art to copy, and the card is not a reliable schema.** Pin the
   revision, read the names from the parquet, and pin the 10 verified column names in a proving test so a future
   drift is a loud failure rather than a silent zero.
2. **Join the per-item license from `nebius/SWE-rebench`.** Hard rule 1 is per item. The corpus-level CC-BY-4.0 is
   recorded in `source_locator` as corpus provenance, not used as the gate. `license_name` arrives as a full
   license name and must go through `identify_spdx_from_text` → `classify_spdx`.
3. **Merge tool results into their originating assistant tool call via `tool_call_id`, order-preserving.** This is
   the same invariant `_pair_history` holds in `openhands.py`; the join key here is `tool_call_id`, not `cause`.
   Handle `"None"`-as-a-string and `arguments`-as-a-JSON-string.
4. **Carry the `model_patch` as a real, redacted, size-capped event — not as header metadata.** The writer does
   *not* redact `agent_traces.metadata`; it does redact and cap `tool_input`/`tool_output`. A 28 KB third-party
   diff must go through the chokepoint. This is what makes the outcome checkable downstream.
5. **Use `exit_status` to separate scaffold truncation from model failure,** and require
   `gen_tests_correct`/`pred_passes_gen_tests` agreement before treating a `resolved=1` row as strong positive
   evidence. Both are free.
6. **Exclude held-out ids at instance *and* repository level**, and report the exclusion count.
7. **Measure rejections, not just yield.** A 1,000-row pilot that reports only "items produced" cannot distinguish
   a good filter from a broken one — the CBR literature's zero-negative-instances failure mode.
8. **Expect one episode per trajectory.** Merging assistant+tool roughly halves event counts (130 messages → ~65
   events), so most trajectories land under the 200-event subdivision threshold as a *single* episode. That is
   consistent with `openhands.py`, but it means the per-episode semantic extractor will see a whole trajectory as
   one unit. Flag for step 1 rather than special-case it now.
9. **Do not let the pilot's own retrieval number be the acceptance criterion.** Per §3.3 the field has one
   non-peer-reviewed data point, and per `arXiv:2607.17205` corpus-scale curation has an unreplicated ceiling.
   Step 0's job is cost and yield, not a quality claim.

---

## 7. Sources

| source | id | year | tier | establishes |
|---|---|---|---|---|
| SWE-rebench | arXiv:2505.20411 | 2025 | peer-reviewed (NeurIPS D&B) | pipeline, F2P+P2P predicate, 21,336 tasks, admitted task-imperfections |
| SWE-rebench-V2 | arXiv:2602.23866 | 2026 | peer-reviewed (ICML) | unsound-instance filtering; per-instance confounder metadata (absent from V1) |
| SWE-Bench+ | arXiv:2410.06992 | 2024 | peer-reviewed | 31.08% of passed patches suspicious; leakage 32.67% |
| The SWE-Bench Illusion | arXiv:2506.12286 | 2025 | preprint | instance- and repository-level memorization, measurable |
| SWE-bench-Live | arXiv:2505.23419 | 2025 | preprint | distribution shift: 19.25% where curated sets report far higher |
| Trajectory-data curation study | arXiv:2607.17205 | 2026 | preprint (single author) | 0/67,074 rows truncated or unparseable; 64.4 mean turns (resolved) |
| SWE-smith | arXiv:2504.21798 | 2025 | preprint | 50k synthetic instances; SFT-only use; third message shape |
| SWE-Gym | arXiv:2412.21139 | 2025 | peer-reviewed (ICML) | 491 trajectories, no saturation; environments |
| Agent Workflow Memory | arXiv:2409.07429 | 2024 | preprint | workflow induction; +7.9% over human-written workflows |
| ReasoningBank | arXiv:2509.25140 | 2026 | peer-reviewed (ICLR) | success+failure memory items; embedding-only retrieval |
| SKILL-DISCO | arXiv:2606.26669 | 2026 | preprint | per-trace induction 99.3%→53.0% vs distilled |
| Skill-Pro | arXiv:2602.01869 | 2026 | peer-reviewed (ICML) | reuse rate as a first-class metric |
| ACE | arXiv:2510.04618 | 2026 | peer-reviewed (ICLR) | brevity bias; context collapse |
| Memory add/delete impact | arXiv:2505.16067 | 2025 | preprint | r≈1 input/output similarity; error amplification; free future-eval labels |
| Procedural memory retrieval benchmark | arXiv:2511.21730 | 2025 | preprint (industry) | 84%→59% MAP under vocabulary shift; judge κ=0.178 |
| MemoryAgentBench | ICLR 2026 | 2026 | peer-reviewed | all architectures fail selective forgetting |
| CBR review for LLM agents | arXiv:2504.06943 | 2025 | preprint | retrieval as thresholded similarity selection; benchmarks "an open challenge" |
| Persistent CBR for data science | arXiv:2606.05250 | 2026 | preprint | five-gate retention filter; 108 reuse events, **zero negatives** |
| Dataset card | nebius/SWE-rebench-openhands-trajectories @35455389 | 2025 | dataset card | 67,074 / 32,161 / 1,823 repos / CC-BY-4.0 — **two names wrong, two columns undocumented** |
| Nebius blog | nebius.com/blog/posts/openhands-trajectories-with-qwen3-coder-480b | 2025 | production writeup | 3,792 resolved *issues*; linear history, no truncation/condensation |
