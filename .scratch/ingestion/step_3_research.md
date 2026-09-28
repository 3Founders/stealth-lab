# Step 3 research — SkillMD-138K (license-gated, content-deduped)

Date: 2026-09-28
Agent: step-3 lane (`measure`-style, local shard only)
Spend cap for this step: **$2/day** (tightened from the Common section's $10 by the user).

---

## 0. Corrections to the step brief (read this first)

Three premises in the Step 3 section of `.scratch/prompts/ingestion_build_prompts.md` are
wrong, and two of them would have produced a broken ingester.

| Brief says | Reality | Consequence |
|---|---|---|
| "the SkillMD-138K paper (the card's arXiv link)" | **There is no arXiv link on the card.** I read the whole card; it cites no paper. | Do not cite a paper for this dataset. It has no paper. |
| "arXiv 2604.04323 … skills in the wild" is the dataset's study | 2604.04323 is a **different corpus** (34,198 skills from skillhub.club + skills.sh, MIT/Apache-filtered). It does **not** use or cite SkillMD-138K. | Use it as the *precedent for the license gate*, not as the dataset's paper. |
| "How AI Agent Skills Are Written, Adapted, and Maintained" (gitskills) | That title is **arXiv:2607.00911** (Gao, Lulla, Lin, Baltes, Treude, Zahedi). No gitskills/mvaccargiu attribution. | Cite 2607.00911 by id. |
| "resolve each repo's license from `html_url`" | `repo` is a **mirror** for **12.7%** of rows; `html_url` points at the mirror, so the resolved license is the *aggregator's*, not the author's. | Added an origin-recovery gate. This is the single highest-value change in this step. |

---

## 1. Dataset facts — MEASURED, not taken from the card

Measured by reading the parquet footer and column chunks over HTTP range requests
(`pyarrow` + `fsspec`) on 2026-09-28, and by the HF API.

| Fact | Value | How verified |
|---|---|---|
| HF id | `FayeZC/SkillMD-138K` | HF API |
| Rows | **138,133** | parquet metadata |
| Repos | **20,556** distinct in `repo` | parquet column scan |
| Storage | 560,428,690 bytes (534 MiB) | HF API `usedStorage` |
| Created / last modified | 2026-04-08 22:25:25 / **22:32:31 UTC** (commit `0d73048a`) | HF API. **7 minutes after creation; never updated since. ~5.8 months stale.** |
| Splits | one (`train`), no train/val/test | HF API |
| Parquet | 2.6 / snappy / **1 row group / 1,229 total** | parquet metadata |
| Dataset viewer | **BROKEN** — `TooBigContentError` (540 MB single row group vs 300 MB scan limit) | card page |
| Human quality reports | **none.** 1 discussion, by the `parquet-converter` bot. 5 likes, 330 downloads/month. | HF API |

### Schema (all 9 columns, zero nulls anywhere)

| # | Column | Type | Distinct | Example |
|---|---|---|---|---|
| 0 | `content_hash` | large_string | 138,133 | `16a9695b7b9ace5085eb8e9c0c29a879a8b21e9a0a306abebfa66f112cb8d163` |
| 1 | `repo` | large_string | 20,556 | `NeverSight/skills_feed` |
| 2 | `path` | large_string | 123,315 | `data/skills-md/yanko-belov/code-craft/aaa-pattern/SKILL.md` |
| 3 | `stars` | int64 | — | `104` |
| 4 | `source` | large_string | 4 | `registry` |
| 5 | `html_url` | large_string | 138,133 | `https://github.com/NeverSight/skills_feed/blob/main/…` |
| 6 | `content` | large_string | — | (540 MB compressed / 1.06 GB uncompressed, one column chunk) |
| 7 | `lines` | int64 | — | `218` |
| 8 | `words` | int64 | — | `810` |

**Columns that do NOT exist** (each one is a plan assumption that had to be replaced):
no `license`, no per-repo SPDX, no `forks`, **no recency/last-commit date**, no `language`,
no category/taxonomy, no split, no `name`/`description` (frontmatter lives inside `content`),
no numeric repo key.

**Card discrepancies, both corrected in code:**
- Card says `content_hash` uses "first 16 chars as file ID". **Measured: all 138,133 values
  are the full 64-hex SHA-256.** The 16-char note is a loader-side convention.
- Card says `source` ∈ {search, clone, registry}. **Measured 4 values:** `registry` 124,253
  (90.0%), `search` 6,665 (4.8%), `clone` 5,373 (3.9%), `''` 1,842 (1.3%, undocumented).

### Distributions (measured)

```
source   registry 124253 90.0% | search 6665 4.8% | clone 5373 3.9% | '' 1842 1.3%
stars    0 -> 42,474 (30.7%) | 1-4 -> 29,470 (21.3%) | 5-19 -> 14,389 (10.4%)
         20-99 -> 10,137 (7.3%) | 100-499 -> 32,315 (23.4%) | 500-9999 -> 6,942 (5.0%)
         10000+ -> 2,406 (1.7%)   median 3   mean 822.6 (meaningless)   max 344,007
lines    min 1 | p25 78 | median 169 | p75 323 | p99 1195 | max 10005
words    min 1 | p25 338 | median 687 | p75 1244 | p99 4574 | max 170038
```

The 0-star / 100–499-star bimodality is a **mirror artifact**, not a population: median
stars for rows in the top-10 repos is 104, for every other row is 2. **Use `stars` as a
coarse provenance signal only; never as a mean, and never as the license proxy.**

### The mirror problem — the most consequential finding

**12.7% of rows (17,492) live in aggregator repositories.** `NeverSight/skills_feed`
alone holds 17,284 rows (12.5%) and its paths encode the origin:

```
repo = NeverSight/skills_feed
path = data/skills-md/yanko-belov/code-craft/aaa-pattern/SKILL.md
              └──── origin owner ────┘ └── origin repo ──┘
```

Parsing that pattern yields **3,249 distinct implied origin repos** from those rows alone.
Top-10 repos hold 36,424 rows (26.4%); top-25 hold 29.8%. Other aggregators:
`majiayu000/claude-skill-registry` (5,706), `jeremylongshore/claude-code-plugins-plus-skills`
(4,356), `openclaw/skills` (3,184), `aiskillstore/marketplace` (1,994).

**Collision evidence that hash ≠ identity:** 123,315 distinct paths for 138,133 rows;
**5,573 paths appear in more than one row, covering 20,391 rows.** `SKILL.md` alone appears
738 times. The same logical skill exists under many hashes (fork edits) and many `repo`
values. The dataset's own dedup was **exact SHA-256 only** (138,133/138,133 distinct) — it
removed byte-identical copies and nothing else.

### License facts (the gating gap, stated by the dataset itself)

- Compilation/curation: **CC-BY-4.0**, scoped by the card to "the curation, deduplication,
  metadata, and documentation".
- Individual skills: "retains the copyright and license of its original author/repository".
- The card's own instruction: "users should consult the original repository's license" —
  i.e. **the publisher hands the per-row licensing problem back to the ingester.**

CC-BY-4.0 on the compilation tells you nothing about whether row 91,204 is redistributable.

---

## 2. Prior work

### arXiv:2604.04323 — "How Well Do Agentic Skills Work in the Wild" `[PREPRINT]`
Liu, Ji, An, Jaakkola, Zhang, Chang (UCSB / MIT CSAIL / MIT-IBM Watson). Submitted
2026-04-06, v1 only, CC BY 4.0. Code: `github.com/UCSB-NLP-Chang/Skill-Usage`.

**Their corpus is NOT ours:** skillhub.club + skills.sh metadata, skills re-downloaded from
original repos, **filtered to MIT and Apache-2.0**, empty names/descriptions removed, deduped
by file content → **34,198 skills**. That 4.1× license filter is the closest published
precedent for the gate in this step.

**The central claim, quoted:** "the benefits of skills are fragile: performance gains degrade
consistently as settings become more realistic, with pass rates approaching no-skill baselines
in the most challenging scenarios."

**Measured ladder (pass rate %, Claude Opus 4.6 / Kimi K2.5 / Qwen3.5-397B):**

| Setting | Claude | Kimi | Qwen |
|---|---|---|---|
| Curated, force-loaded (upper bound) | 55.4 | 38.5 | — |
| Curated (agent chooses) | 51.2 | 38.9 | 31.6 |
| Curated + distractors | 43.5 | — | 33.7 |
| Retrieved w/ curated (top-5 of 34k) | 40.1 | 33.5 | 26.7 |
| Retrieved w/o curated | 38.4 | 19.8 | 19.7 |
| **No skills (baseline)** | **35.4** | **21.8** | **20.5** |

Four numbers that shaped this build:

1. **Realistic retrieval buys 3.0 points over no skills at all** (38.4 vs 35.4) and is
   **negative** for the two non-Claude models (19.8 vs 21.8; 19.7 vs 20.5). Their words:
   "irrelevant retrieved skills can actively mislead the agent… by spending effort loading and
   following unhelpful instructions that would have been better ignored entirely."
   → *A large unfiltered pool is a net loss. This is the single strongest argument for
   gating hard rather than ingesting broadly.*
2. **Even the upper bound is weak.** Agents loaded all curated skills in only **49%** of
   Claude trajectories when they were sitting in context; **31%** with distractors. The
   failure is not retrieval — it is that `name` + `description` are the entire routing signal
   and they are not good enough. → *Frontmatter conformance and description quality are the
   highest-leverage ingestion-time gates, not a nice-to-have.*
3. **Retrieval ceiling:** R@5 = 65.5 (hybrid w/ content), R@3 = 57.3; direct dense retrieval
   is 18.7 points worse than agentic at R@3. **~1/3 of curated skills are unreachable at k=5.**
4. **Refinement is a multiplier, not a generator** (LLM-judge coverage 4.01 where refinement
   works, 3.49 where it fails): "refinement acts more like a multiplier on existing skill
   quality rather than a generator of new knowledge." The exception is instructive — Kimi on
   SkillsBench **drops 33.5→26.7**: refinement is counterproductive when the model misjudges
   which skills matter.

Contested by the authors themselves: SkillsBench's curated skills are criticized as overfit
(their Figure 1 example is effectively a step-by-step solution guide); harness dominates
behavior (Kimi loads skills 86% of the time vs Claude's 62% yet scores no better —
**skill-loading rate is not skill utility**); single refinement iteration; no cost figures.

### arXiv:2602.12670 — SkillsBench `[PREPRINT]`
Li, Liu, Chen, You, et al. (~70 authors, multi-lab). 87 tasks, 18 model–harness configs.
Curated skills: **33.9% → 50.5%** pass rate. Independent corroboration of the "don't
over-bundle" signal: **"Focused Skills with at most three modules outperform larger or
exhaustive bundles."**

### arXiv:2607.00911 — "From Registry to Repository" `[PREPRINT]`
Gao, Lulla, Lin, Baltes, Treude, Zahedi. v1 2026-07-01, v2 2026-07-06, cs.SE.
18,463 skills from `skills.sh` + 23,199 from 5,876 repos; 3,709 recovered reuse links;
444 modifications coded. Zenodo `10.5281/zenodo.21032973`.

- Six content categories: scoping/orchestrating, execution lifecycle, output quality,
  **governing agent conduct**, domain knowledge, user/agent coordination.
- Reuse is a **one-time copy**: of 2,462 reused skills, **1,841 adopted near-verbatim at body
  similarity ≥ 0.99**; only 621 altered.
- Skills are **rarely resynced**: only 47.4% of reused skills got any local change; of the
  1,295 never updated, **40.2% had an upstream that later changed** and the local copy never
  received it. Among reused-and-updated skills whose upstream also changed, **62.3%
  diverged independently**.
- Maintenance is additive (58.8% updated ≥1×). "Governing agent conduct" is nearly untouched.
- **Actionable:** "users should monitor agent activation. Because vague criteria can prevent
  invocation, adopters **enumerate the exact scenarios in which a skill should trigger**."
- **Reusable parameter for my near-dup gate: 0.99 body similarity** for reuse linkage.

### arXiv:2607.01456 — "From Anatomy to Smells" `[PREPRINT]`
Hong, Imani, Ahmed (UC Irvine), v2 2026-07-03. **The quality-gate paper.**
238 real skills → 13 higher-level / 44 lower-level components; 29 sources → 26 authoring best
practices inverted into 26 "skill smells". Detector: 5 static (rule-based) + 21 semantic
(LLM classifier), **weighted F1 0.78, P 0.79, R 0.78**.

- **237/238 (>99%) contain ≥1 smell. Exactly one file is clean.** Mean 10.5 smells/file.
- Most prevalent: **"Rationalization Loophole" at 94%** — the file never forbids the agent
  from talking itself out of a required step.
- `Oversized SKILL.md` is a **static** smell, **defined as >5,000 words**.
- Only 3 of 26 smells carry numeric thresholds: oversized body, lengthy name, lengthy description.
- **Temporal, and the one that matters for a substrate:** 142 skills tracked over 1,199
  commits / 35 weeks. Smells introduced early show **no measurable tendency to be fixed**;
  "developers currently treat SKILL.md files as write-once documentation rather than
  executable code."
- **Their corpus was filtered down from 133,149 `skills.sh` packages** by weekly downloads
  and repo diversity. **A best-case, popularity-filtered sample still fails 99%.** Our
  unfiltered 138k is worse.

### Local sample verification (mine, n=125 re-fetched live from raw.githubusercontent.com)

- **125/125 had parseable YAML frontmatter with both `name` and `description`.** The format
  is genuinely well adopted. (A useful *negative* result.)
- Frontmatter key frequency: `name` 125, `description` 125, `allowed-tools` 27, `version` 25,
  `license` 22, `metadata` 16, `model` 8, `author` 8, `source` 7, `argument-hint` 6, `risk` 5,
  `triggers` 5, `tags` 5, `user-invocable` 5, `compatibility` 5. **Nine of these are not in
  the spec** — they are Claude Code plugin/skill extensions, so a strict validator must not
  reject on them.
- **Only 22/125 (17.6%) carry an explicit `license:`** (MIT 18, Apache-2.0 2, 2 pointing at a
  bundled LICENSE.txt). **82.4% say nothing** → per-repo resolution is unavoidable, not optional.
- **Spec violations in `name`: 9/125 (7.2%)** — 4 uppercase, 5 out-of-charset. Real examples:
  `Erlang Distribution`, `UX Wireframe Designer`, `claude:challenge`.
- `description` over the 1024-char spec limit: **2/125**, observed max 1,625. Median 241.
- Body: median 605 words, p95 2,293, max 5,110. **Zero skills under 20 words in this sample**
  (the trivially-short junk mode is not visible in a uniform sample, but `lines` min = 1 in
  the corpus columns says it exists).
- Marker rates in raw text (prevalence data for calibrating a screener, **not** findings):
  `.env` 11/125, `api key` 6, `silently` 5, `webhook` 4, `curl http` 1, `base64 -d` 1,
  `--dangerously` 1.

**Not established, and I am not going to pretend otherwise:**
- No human has published a quality report on this dataset. Its existence is not endorsement.
- The card reports **no quality filtering at all**. Given `max words = 170,038` and ~206
  `create-skill.md`-style false positives of the crawl, no size or boilerplate filter ran.
- No published rate for generated/AI-spam skills in this corpus.

---

## 3. The Agent Skills spec `[OFFICIAL DOCS]`

Read from `agentskills.io/specification`; spec repo `github.com/agentskills/agentskills`
(Apache-2.0, created 2025-12-16, last push 2026-08-09). Originally Anthropic's, released as an
open standard. Reference validator: `skills-ref` in that repo (`validate`, `read_properties`,
`to_prompt`).

```
skill-name/
├── SKILL.md          # required
├── scripts/          # optional
├── references/       # optional
└── assets/           # optional
```

| Field | Required | Constraints |
|---|---|---|
| `name` | **yes** | 1–64 chars; `a-z`, `0-9`, `-` only; no leading/trailing `-`; no `--`; **must match parent directory name** |
| `description` | **yes** | 1–1024 chars; non-empty; must say **what it does AND when to use it** |
| `license` | no | name or reference to a bundled license file; keep short |
| `compatibility` | no | 1–500 chars |
| `metadata` | no | map string→string |
| `allowed-tools` | no | space-separated; **experimental** |

Progressive-disclosure budget: metadata ~100 tokens (loaded for *all* skills at startup),
instructions <5,000 tokens, keep `SKILL.md` under 500 lines, references one level deep.

**The spec defines no content-quality bar.** The operational bar is
`agentskills.io/skill-creation/best-practices`: "Start from real expertise" (explicitly
warning against LLM-generated generic skills — *"vague, generic procedures… rather than the
specific API patterns, edge cases, and project conventions that make a skill valuable"*),
"Add what the agent lacks, omit what it knows", "Aim for moderate detail", "Provide defaults,
not menus", **"Favor procedures over declarations"**, and "**enumerate the exact scenarios in
which a skill should trigger**".

**Naming collisions: no normative answer.** The spec requires `name` == directory name but is
silent on uniqueness across a collection. Microsoft's `agent-framework` uses first-writer-wins
+ warning; Strands raises in strict mode. A 138k corpus with median name length 16 drawn from
English will collide constantly. **I must pick a policy; the literature does not.**

---

## 4. Security findings

| Source | Tier | What it establishes |
|---|---|---|
| **arXiv:2601.10338** "Agent Skills in the Wild" (Quantstamp/NTU/Southern Cross/UNSW/Griffith) | `[PREPRINT]` | 42,447 skills collected, 31,132 analyzed. **26.1% contain ≥1 vulnerability**, 14 patterns / 4 categories. **Data exfiltration 13.3%, privilege escalation 11.8%, 5.2% high-severity suggesting malicious intent.** Detector (static + LLM): 86.7% P / 82.5% R. **Script-bundling skills 2.12× more likely to be vulnerable (OR=2.12, p<0.001).** Semgrep/Bandit: near-zero recall on instruction-level threats. |
| **arXiv:2602.06547** "'Do Not Mention This to the User'" (NTU et al.) | `[PREPRINT]` | **98,380 skills from two registries. 157 confirmed malicious, 632 vulnerabilities, mean 4.03/malicious skill.** **84.2% of vulnerabilities (532/632) live in `SKILL.md` natural language** vs 8.5% in code. **73.2% contain "shadow features."** Named coercive patterns: "NON-NEGOTIABLE", "SEVERE VIOLATION", "do NOT mention in conversation", "DO NOT ASK THE USER". Three skills weaponize Claude Code's own infra (`--dangerously-skip-permissions`, shipped `.mcp.json` MCP hijack, hook exfiltration). **>50% of cases from one threat actor using templated brand impersonation at scale.** After disclosure, maintainers removed **157/157 (100%)** — a curation outcome, not a detector result. |
| **arXiv:2510.26328** (Schmotz, Abdelnabi, Andriushchenko, ELLIS Tübingen / MPI-IS) | `[PREPRINT]` | Prompt injection via skills, demonstrated on a modified Anthropic `pptx` skill. Mechanism: **a benign "allow and don't ask again" approval carries over to the harmful action.** Critically for me: *"prompt injection defenses that are based on simply detecting instructions in data are, by definition, not valid as Agent Skills are all instructions"* and a scanner *"inherits the scanner's own jailbreak surface."* Conclusion: **"users should only rely on verified Agent Skills."** |
| **arXiv:2602.14211** SkillJect | `[PREPRINT]` | Payload hidden in an **auxiliary `.sh`/`.py` helper**, SKILL.md rewritten with a front-loaded inducement. 34–57% ASR on 100 ClawHub.ai skills, Claude Sonnet 4.6. **The payload needs no explicit malicious text in the docs** — cross-file hiding defeats doc-only scanning. |
| **arXiv:2602.20156** Skill-Inject | `[PREPRINT]` | 202 injection–task pairs; **up to 80% ASR** with frontier models; exfiltration, destructive action, ransomware-like behavior. **Finds that model scaling and simple input filtering do NOT fix this.** Skills are dual-use: identical text is legitimate in one context and exfiltration in another. Recommends treating skill files as untrusted by default. |
| **arXiv:2609.14079** SkillSecurer | `[PREPRINT]` | Agentic generate/detect/localise/remediate loop; reports an Injection Detection Rate. |
| **Dec 2025: Cato CTRL — "GIF Creator" skill → MedusaLocker** | `[PRODUCTION WRITEUP]` (secondhand; cited by 2601.10338, primary not read) | A benign-advertised image skill silently downloaded and executed ransomware. "Consent gap": once approved, a skill gains persistent read/write/download/network without further prompts. Mapped to OWASP Agentic Top 10 as Identity and Privilege Abuse. |

### The synthesis that drives my screen

1. **The prose is the attack surface, not the code** (84.2% in SKILL.md text).
2. **Signature matching on "ignore previous instructions" is insufficient and arguably the
   wrong frame** — every line of a skill *is* an instruction (2510.26328), and identical text
   flips verdict with context (2602.20156). The productive heuristics are **coercive/secrecy
   language** and **doc-vs-behavior comparison** (shadow features), per 2602.06547.
3. **Single-file screening is insufficient** (SkillJect; OR=2.12 for script bundlers).
   **This dataset contains only `SKILL.md` — no script content — so I cannot run cross-file
   analysis at all.** That is a real limitation, and it argues for *refusing* skills that
   reference bundled scripts I cannot inspect.
4. **The null result:** *no published work establishes a screening procedure with high recall
   against skill-borne injection.* 2602.20156 says explicitly it "will not be solved through
   model scaling or simple input filtering." **Treat screening as triage with a recall hole,
   never as a trust gate.**

---

## 5. Existing loaders (leads, not vetted)

- **`skills-ref`** (`agentskills/agentskills`, Apache-2.0) — the reference validator. Uses
  `strictyaml`, not PyYAML. Raises on missing/unterminated `---`, YAML error, non-mapping
  frontmatter. **Operates on a skill *directory*, not a single file** — I'd have to
  materialize a dir per row. Accepts lowercase `skill.md`.
- **`microsoft/agent-framework`** `agent_framework/_skills.py` — most security-conscious
  implementation found: XML-escapes metadata before prompt injection, guards resource reads
  against traversal and symlink escape, rejects scripts resolving outside the skill dir.
  Duplicate names: warn + first-wins. `DeduplicatingSkillsSource` exists.
- **`strands-agents/sdk-python`** `vended_plugins/skills/skill.py` — `from_url` does the
  exact `html_url`→raw pattern I need; has a **`strict=` flag** and a `_fix_yaml_colons`
  fallback for malformed YAML. **That fallback is evidence that malformed frontmatter is a
  known, expected wild condition** (one of my 125 samples had exactly that failure shape).
- **`EthanLiu6/agent-skills-python`** — layered SDK, spec validation + best-practice checks.
- **`agentskills-fs` / `agentskills-sdk`** (PyPI) — **10 MB default `max_file_bytes` rejection
  before reading into memory**, path-traversal validation. My `max words = 170,038` row makes
  that gate non-optional.
- **`10xHub/Agentflow`** `agentflow/core/skills/loader.py` — lenient-but-logged ingestion model:
  rejects non-mapping frontmatter, requires `name`+`description`, `..`/absolute resource paths
  rejected, logs and skips on validation failure. **Best model for my adapter's disposition
  discipline.**
- Scanners named in related work (leads only, I ran none): Cisco Skill Scanner, Skill Vetter,
  SlowMist, ClawGuard, SkillScan, SkillSecurer.

---

## 6. Implications for this build

**Gate, in this order:**

1. **Mirror check first, then origin-repo license.** Recover origin from
   `data/skills-md/<owner>/<repo>/` where possible; for aggregator rows with no recoverable
   origin → **quarantine, do not admit**. Then resolve the *origin* repo's SPDX via the GitHub
   API through the existing `repo_license_policy.classify_spdx` allowlist. `NOASSERTION` and
   absent LICENSE are **unknown, not permissive** (this is the existing module's stated
   posture, and it is right).
2. **Frontmatter conformance, hard.** `name` (1–64, `a-z0-9-`, no leading/trailing/double
   hyphen) and `description` (1–1024, non-empty). Measured 7.2% bad `name`. A bad `name` is
   **permanent index poison** — reject outright, do not repair.
3. **Static size gate** at 5,000 words (2607.01456's `Oversized SKILL.md` threshold, and the
   10 MB `max_file_bytes` precedent). My corpus max is 170,038 words.
4. **Description quality** as a scored signal, not a hard gate: does it state *when to use*?
   2604.04323's 49% activation rate and 2607.00911's "enumerate the exact scenarios" are the
   evidence. This is where ingestion effort buys the most downstream performance.
5. **Prompt-injection screen** via the existing `screening.screen_document_text` (reuses
   skill_ingestion's own detectors), **plus** the coercive/secrecy-language patterns named by
   2602.06547, which the existing detector does not have. Quarantine, never silent drop.
6. **Near-dup** at the 0.99 body-similarity linkage rate 2607.00911 used, on top of exact
   content hash. The dataset's own dedup was exact-hash only and provably left 20,391 rows
   sharing 5,573 paths.

**Spend discipline:** the dominant cost is identity judging, which is why the existing
`_build_goal_prefetch_requests` / `_prefetch_goal_requests` machinery matters (batch judging +
per-document goal cache). **Exact content-hash duplicates must skip judging entirely.**

**What I will not claim:** a measured yield for the full 138k. The pilot gives a per-item
yield and a projection; it does not prove the projection. And with 26% of rows in mirrors
whose origins are unrecoverable, the admissible set may be far smaller than 138k — the honest
headline may be "this source is much smaller than advertised after gating", which is a finding,
not a failure.

## 7. Bibliography

| id | what it establishes here | tier |
|---|---|---|
| HF `FayeZC/SkillMD-138K` | schema, counts, CC-BY-4.0 compilation scope, mirror paths | `[DATASET CARD]` + my parquet measurement |
| arXiv:2604.04323 | gains collapse under realistic retrieval; MIT+Apache gate precedent; 49% activation; R@5=65.5; refinement is a multiplier | `[PREPRINT]` |
| arXiv:2602.12670 | curated skills 33.9→50.5%; "at most three modules" | `[PREPRINT]` |
| arXiv:2607.00911 | 0.99 body-similarity reuse linkage; trigger-scenario advice; six content categories | `[PREPRINT]` |
| arXiv:2607.01456 | 26 smells, >99% prevalence, >5,000-word oversized threshold, F1 0.78 detector | `[PREPRINT]` |
| arXiv:2601.10338 | 26.1% vulnerability rate, 13.3% exfiltration, OR=2.12 script bundling | `[PREPRINT]` |
| arXiv:2602.06547 | 84.2% of vulns in SKILL.md prose; 73.2% shadow features; coercive language list | `[PREPRINT]` |
| arXiv:2510.26328 | instruction-detection defenses are ill-posed; consent carry-over; "only rely on verified Skills" | `[PREPRINT]` |
| arXiv:2602.14211 | cross-file payload hiding defeats doc-only scanning; 34–57% ASR | `[PREPRINT]` |
| arXiv:2602.20156 | 80% ASR; scaling and filtering do not fix it; dual-use | `[PREPRINT]` |
| arXiv:2609.14079 | agentic detect/remediate loop (IDR) | `[PREPRINT]` |
| `agentskills.io` specification | the frontmatter contract and its numeric limits | `[OFFICIAL DOCS]` |
| `agentskills/agentskills` `skills-ref` | reference validator, strictyaml | `[OFFICIAL DOCS]` |
| `microsoft/agent-framework`, `strands-agents`, `agentskills-fs`, `10xHub/Agentflow` | loader patterns, traversal guards, size caps, lenient-logged dispositions | `[PRODUCTION WRITEUPS]` |
