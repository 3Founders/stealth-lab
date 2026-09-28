# Ingestion build prompts: steps 0-8

One section per step. Give a model **the Common section plus exactly one step section**. Each step starts with
a literature review and an Exa search, then builds, then writes a summary. Steps 0-2 are sequential (0's numbers
set the budget for the rest); 3-7 can run in parallel after step 0; step 8 waits for step 0's yield numbers.

---

## Common: read this before any step

### Context
StealthLab/Kel turns sources (documents, repositories, agent trajectories, verified solutions) into
evidence-backed **Goals, Procedures and Claims**, served to coding agents through MCP (`find_ways`) and the
Claude Code hook. `docs/findings.md` is the current evidence: knowledge delivered by the hook raised open-model
agents from 52.0% to 60.6% solved (p = 0.004); the product direction is **routing with a check**, so knowledge
that carries a *checkable outcome* is worth the most.

### Read first
1. `CLAUDE.md`: hard rules, lanes, conventions. **Breaking a hard rule invalidates the work.**
2. `docs/ingestion_sources_plan.md`: the source inventory, licenses, dedup and isolation rules, and the
   2026-09-28 corrections (the working-tree version is newer than the committed one; read the file on disk).
3. `.scratch/arxiv_ingestion_sources_deep_dive.md`: per-source correction record (identifiers, row counts,
   license reversals, format findings).
4. `ingestion_problems.md`: throughput bottlenecks (P1-P6) and the evidence queries.
5. The code you will extend:
   - `backend/app/services/ingestion_sources/`: `normalized_trajectory.py` (the harness-neutral trajectory
     model), `openhands.py` (reads the **OpenHands event-history** export shape only), `skill_md.py`,
     `github_corpus.py`, `repo_procedural.py`, `dispatch.py`, `manifest.py`, `document_adapters/`
   - `backend/app/services/trace_worker.py::write_normalized_trajectory`: the ONE writer for trajectories
   - `backend/app/services/ingestion_jobs.py`: job types (`ingest_document`, `ingest_skill_package`,
     `ingest_repo`, ...) and handlers
   - `backend/app/services/repo_license_policy.py` (`DEFAULT_ALLOWLIST`) and `screening.py` (`CHECK_TYPES`,
     a closed vocabulary)
   - `verified_solution` artifacts + `source_locator` (commit 68efb02)
   - `backend/app/ingestion/admin.py`: `register-shard`, `shard-status`, `shard-weight`, `count-source`,
     `judge-health`, `metrics`
   - `backend/app/services/trace_redaction.py::redact_event`: the redaction chokepoint

### Correction you must honour
The nebius SWE-rebench OpenHands trajectory corpus stores **OpenAI-style chat messages** (`role`, `content`,
`tool_calls`, `tool_call_id`), **not** the OpenHands event history that `openhands.py` parses. Any claim that
"the reader exists" for it is wrong. A **chat-message normalizer** (producing `NormalizedTrajectory`) is
required for that corpus, the nebius SWE-agent set and Open-SWE-Traces; SWE-smith's `messages` are a third,
non-identical shape. Verify the actual row schema yourself before writing code.

### Research protocol (every step, before code)
1. **Literature review:** find and read (primary sources: arXiv, the dataset/tool paper, dataset cards,
   official docs) the work behind this source and the closest work on *using* it: what it contains, how it was
   collected and validated, known quality problems, and how others turned it into reusable knowledge or
   training signal. Note what that means for extracting **verified, reusable procedures**.
2. **Exa search:** use the Exa MCP tools (search, then fetch the page) for: the current dataset/tool version,
   license and card; recent (2025-2026) papers, blog posts or issues reporting problems with it (contamination,
   broken tests, license disputes, duplicates); and existing loaders/normalizers you could learn from. **Read the
   page before citing it; never report a number you didn't read.**
3. **Verify locally:** download a small sample (e.g. 50 rows) and confirm the schema, the outcome field, the
   license field and sizes against what the card says.
4. Write findings to `.scratch/ingestion/step_<N>_research.md` (sources with URLs and dates, schema, license
   facts, quality risks, implications), **then** build.

### Rules for every step
- **Scope + provenance on everything entering storage** (hard rule 2): source id, revision/commit, row or
  instance id, extractor version, license (SPDX) in `source_locator`.
- **License per item, never per compilation.** The SPDX **allowlist** gates each item (`NOASSERTION`, missing,
  non-commercial and share-alike are rejected). Record the rejection reason counts.
- **Only verified outcomes become Procedures.** `resolved=True` / merged / checks-passed items become Procedures;
  failures become failure Claims (evidence of what doesn't work), never Procedures.
- **Isolation:** never ingest into the experiment databases (`kel_swebench*`, `kel_*` on 127.0.0.1:55432).
  Before any production ingestion of SWE-bench/SWE-rebench-family data, **exclude the held-out ids** in
  `experiments/swebench*/runs/design.json` (`test`, `calibration`) and record the exclusion count.
- **Dedupe** by the rules in the plan (one trajectory per (task, outcome), content hashes, task id across sources).
- **Redaction:** every free-text field on a trace path goes through `redact_event`.
- **Spend:** every LLM call is recorded in the spend ledger. Stay within **$10/day** unless the user raises it;
  report projected spend before any run larger than the pilot.
- **Production writes need the user's go-ahead.** Build and test against a local shard first (`register-shard`
  with a local Postgres), and only propose the production run with numbers.
- **Code conventions:** `scope_predicates()` for tenant SQL, `tenant_transaction` for writes, evidence via
  `execution/evidence.py`, `uuid7()` ids, migrations additive and numbered (a new file), proving tests in the
  same change, offline tests with hand-rolled fakes (`DATABASE_URL` unset), lane prefix on commits, test counts
  in every commit message, rebase before push, never force-push. **Never let a blocking call run inside an
  `async def`**: use `app.utils.aio.run_blocking`.
- **Schema/spec are frozen:** if you need a new check type or column the spec doesn't allow, write a numbered
  question on `.scratch/build-board.md` with a proposed default and continue with what you can.

### Deliverable (every step)
`.scratch/ingestion/step_<N>_SUMMARY.md`: what you researched (links), what you built (files), tests (counts
before/after), the run you did (items in / accepted / rejected by reason / deduplicated / Procedures, Claims,
Goals created / spend / bytes per item / wall time), risks, and the exact command to run at full scale with its
projected cost and time. Final chat message: one paragraph and the path.

---

## Step 0: local pilot (1,000 resolved SWE-rebench OpenHands trajectories into a local shard)

**Goal:** measure what everything else will cost. This step produces the numbers that set every later budget.

**Research (then build):**
- Literature: the SWE-rebench paper (arXiv 2505.20411) and the trajectory release (card of
  `nebius/SWE-rebench-openhands-trajectories`): model (Qwen3-Coder-480B), scaffold (OpenHands), how `resolved`
  was decided, known issues; prior work on distilling procedures/skills from agent trajectories (e.g.
  ReasoningBank, Agent Workflow Memory, SWE-smith/SWE-Gym trajectory use).
- Exa: the dataset card and schema today; issues reporting format changes or broken rows; any public
  chat-message → trajectory normalizers.
- Local: sample 50 rows and document the exact message schema, the resolved/outcome field, the instance id, the
  repo and license fields.

**Build:**
1. A **chat-message normalizer** in `ingestion_sources/` (its own module; it must not change `openhands.py`'s
   contract) that maps one row to `NormalizedTrajectory` (tool calls + results merged into events, final patch,
   outcome from the resolved field, task metadata), with a `SourceAdapter` for the HF dataset (streamed, revision
   pinned). Route through `write_normalized_trajectory` (the one writer).
2. A pilot command (e.g. `admin ingest-trajectories --source nebius-openhands --limit 1000 --resolved-only`)
   against a **local shard**, excluding the held-out ids, with license gating per row.
3. Instrumentation: per item, record knowledge items produced (Goals, Procedures, Claims), duplicate hits,
   judge/extraction calls and dollars, bytes stored, wall time.

**Acceptance:** 1,000 rows processed (or the reason for every row not processed); a table in the summary with
knowledge items per trajectory, duplicate rate, model dollars per item, bytes per item, items/hour, and the
**projection for the full ~32k**; proving tests for the normalizer (fixtures from real sampled rows, redacted),
the license gate and the held-out exclusion. No production writes.

---

## Step 1: SWE-rebench OpenHands trajectories, resolved only (~32k)

**Goal:** the highest-value knowledge: realistic multi-step fixes in real Python repositories, plus failures kept
as failure Claims.

**Research (then build):**
- Literature: as step 0, plus how others filtered agent trajectories for quality (redundant steps, flailing,
  test-gaming) and how they scored trajectory quality.
- Exa: failure modes reported for this corpus; whether any of its tasks overlap SWE-bench Verified/our designs.
- Read step 0's summary first. If it doesn't exist, stop and do step 0.

**Build:**
1. Scale step 0's path: resumable, idempotent batches (re-running a batch creates nothing new), per-batch spend
   caps, progress in `admin metrics`.
2. Failures: ingest a bounded sample (e.g. up to one failed trajectory per task) as failure Claims linked to the
   same Goal, never as Procedures.
3. Raw rows go to object storage (reference by URI in `source_locator`), not Postgres; derived knowledge goes
   to shards per the sharding plan.
4. A budget plan: at step 0's measured $/item, how many days at $10/day, and what a higher daily cap buys.

**Acceptance:** a 5,000-row production-candidate batch on the local shard with the same metrics table as step 0;
dedup against step 0 by instance id; the held-out exclusion count; the full-run command with projected days,
dollars and shard bytes. Production run only after the user approves.

---

## Step 2: verified solutions (issue → patch → tests)

Sources: SWE-rebench (27.9k), SWE-rebench-V2 (32.1k), SWE-bench-extra (6.4k), SWE-Gym (2.4k).

**Goal:** checkable knowledge. Each item has tests, which is exactly what routing with a check needs, and it
gives a new population on which to re-test the confirmed hook result.

**Research (then build):**
- Literature: SWE-rebench (2505.20411), SWE-rebench-V2 (2602.23866), SWE-Gym (2412.21139), SWE-bench-extra's
  card; the validation pipelines (how FAIL_TO_PASS was established, LLM-assessed quality labels, known broken
  tests).
- Exa: current cards, languages in V2 (the table says "check its language mix"), overlap between these sets,
  reported contamination.
- Local: sample each and confirm fields (`license_name`, `docker_image`, `FAIL_TO_PASS`, quality metadata).

**Build:**
1. A thin **Hugging Face row reader** (streamed, revision-pinned) and a mapping to `verified_solution` artifacts
   (issue text → Goal resolution; gold patch → the verified solution; tests → the Procedure's check), using the
   existing `verified_solution` + `source_locator` model.
2. License per task from `license_name`, through the allowlist.
3. Cross-source dedup (same repo + base_commit + instance) and the held-out exclusion (our SWE-bench and
   SWE-rebench designs).
4. Quality filter: use the datasets' own quality labels where present; record the distribution.

**Acceptance:** 500 items per source on a local shard with the metrics table (items, accepted/rejected by reason,
dedup hits, $/item, bytes/item); proving tests for the reader, mapping, license gate, dedup and exclusion; a
language/repo breakdown for V2; the full-run command and projection.

---

## Step 3: SkillMD-138K, license-gated per repository, deduplicated by content

**Goal:** the cheapest large coverage win: 138k skills from ~20.5k repositories, every language and tool. Skills
state intent, not verified outcomes, so they enter as **candidates** and earn trust only when a run succeeds.

**Research (then build):**
- Literature: the SkillMD-138K paper (the card's arXiv link) and "How AI Agent Skills Are Written, Adapted, and
  Maintained" (gitskills); studies of skill quality and of skills "in the wild" (arXiv 2604.04323: gains collapse
  when agents must retrieve from large skill pools; quality filtering and refinement recover part of it).
- Exa: the Agent Skills spec (SKILL.md frontmatter), common low-quality patterns, prompt-injection risks inside
  published skills.
- Local: sample 200 rows; measure how many resolve to a repository license, and to which SPDX ids.

**Build:**
1. Use the existing `skill_md.py` / `ingest_skill_package` path; add the dataset reader. Resolve each repo's
   license from `html_url` (GitHub licenses API, cached, rate-limited), and gate per the allowlist.
2. Content-hash dedup (and near-duplicate detection if cheap), and a star/recency prior.
3. **Prompt-injection screen** on skill text before it becomes procedure text (served text is acted on by other
   users' agents): flag and quarantine, don't silently drop.
4. Keep identity-judging cost down (the main cost): batch judging, the per-document goal cache, and skip-judging
   for exact duplicates.

**Acceptance:** 2,000 skills on a local shard: license outcome distribution, dedup rate, quarantine count,
judge calls and $ per skill, Goals created vs matched; proving tests; the projection for 138k.

---

## Step 4: verifiers as check types (actionlint, zizmor, ast-grep) + ast-grep-essentials rules

**Goal:** make CI and refactoring knowledge *checkable*, which the routing loop needs. This is mostly code, not
ingestion.

**Research (then build):**
- Literature/Docs: actionlint, zizmor and ast-grep official docs (exit codes, output formats, versions),
  ast-grep's rule-test format, the ast-grep-essentials repository (Apache-2.0, 0.6 MB); prior work on
  verifier-gated agents (checks' false-accept rates).
- Exa: current versions and install methods on Linux/macOS/Windows; known false positives.

**Build:**
1. Check types for these verifiers. `screening.CHECK_TYPES` is a closed vocabulary and the spec is frozen: if
   adding a type needs a spec change, file the numbered board question with a proposed default, and implement
   behind the proposal (or as a parameterized existing type) so it can land once approved.
2. A check runner (local, sandboxed, timeouts, pinned tool versions, no network) that returns pass/fail plus the
   finding list, usable by the routing check path.
3. Ingest ast-grep-essentials rules as Procedures whose check is the rule's own valid/invalid test cases (license
   per file, Apache-2.0).

**Acceptance:** each verifier passes and fails on fixture workflows/code; all ast-grep-essentials rules ingested
on a local shard with their tests passing through the runner; proving tests; the board question if one was
needed.

---

## Step 5: SWE-agent trajectories (nebius) and SWE-smith trajectories (tool split only)

**Goal:** outcomes from other models and scaffolds (SWE-smith is Claude 3.7 Sonnet), so the per-Goal model
recommender learns across models, not just one.

**Research (then build):**
- Literature: SWE-smith (2504.21798), the nebius SWE-agent trajectory card, SWE-agent's trajectory format; how
  model-routing work (the repo's `docs/model_routing_plan.md`) consumes cross-model outcomes (IRT-style ability,
  scaffold effects).
- Exa: schema of each set today; SWE-smith's synthetic-bug caveats; licensing (MIT / CC-BY-4.0) per item.
- Read step 0's summary (the chat-message normalizer); SWE-smith's `messages` are a different shape, so verify.

**Build:**
1. Reuse step 0's chat-message normalizer for the nebius SWE-agent set; add a SWE-smith `messages` normalizer
   (tool split only).
2. Dedupe against step 1 by task id (keep the best resolved trajectory per task; at most one failure).
3. Emit routing observations for each outcome (model, scaffold, accepted, goal, check kind `benchmark`) into the
   routing tables. **Production lacks those tables (migrations 115-124 pending)**: target a local shard with the
   migrations applied, and flag the production dependency.

**Acceptance:** 1,000 rows from each set on a local shard; dedup hits against step 1; routing observations
written and readable by `recommend_models`; proving tests; projections.

---

## Step 6: CI workflow histories (Zenodo, CC-BY-4.0, 160k) and Dependabot/Renovate pull requests

**Goal:** very broad, checkable knowledge: builds, tests and dependency upgrades in every language, where the CI
result or the merged PR is the check.

**Research (then build):**
- Literature: "A dataset of GitHub Actions workflow histories" (IEEE 2024, Zenodo 10259013), GHALogs (MSR'25;
  note it is CC-BY-SA, so it is excluded), studies of Dependabot/Renovate PR acceptance and breakage.
- Exa: the Zenodo record and license today; GitHub API rate limits and search qualifiers for bot PRs
  (`author:app/dependabot`, `author:app/renovate`); the ToS for API use.
- Local: sample 200 workflow histories and 200 bot PRs (merged vs closed, CI status).

**Build:**
1. Workflow histories: a reader turning each workflow change + its run outcome into Procedures (passing runs) or
   failure Claims, with the step-4 actionlint/zizmor check attached where possible.
2. Bot PRs: a rate-limited, resumable GitHub API scraper (token from `.env`, never logged) for merged PRs with
   green CI → version-bump Procedures with the CI result as evidence; license per repository via the allowlist.
3. Dedup (same repo + same bump).

**Acceptance:** 1,000 workflow items + 500 PRs on a local shard with the metrics table and license outcomes;
API usage within limits; proving tests; projections.

---

## Step 7: codemods (Node.js userland migrations; OpenRewrite)

**Goal:** JavaScript and Java coverage, which the earlier steps lack, with before/after tests as the check.

**Research (then build):**
- Docs: the Node.js userland-migrations/codemod registry, OpenRewrite recipe docs and **licensing** (some
  modules moved to the Moderne Source Available License: check each module's LICENSE at the exact commit);
  how recipe tests (`RewriteTest`) express before/after.
- Exa: current recipe catalogs, license changes, and the Codemod CLI.

**Build:**
1. A reader for each catalog: recipe/codemod → Procedure (what it changes, when to apply, how to run), with its
   before/after tests as the check (run through a sandboxed runner, as in step 4).
2. License at the pinned commit per module; reject anything not on the allowlist, with reason counts.

**Acceptance:** all Node.js migrations and at least 50 Apache-2.0 OpenRewrite recipes on a local shard, each with
a passing check; license rejection counts; proving tests.

---

## Step 8: later, sampled (SWE-Zero, gitskills, CodeQL queries, Kubernetes docs)

**Goal:** decide, with numbers, whether these large or costly sources are worth it. Do not bulk-ingest.

**Research (then build):**
- Literature/Docs: SWE-Zero / SWE-Hero (arXiv 2604.01496; execution-free vs execution-backed), gitskills
  (11.3M rows, 13 GB), CodeQL (queries MIT; the **CodeQL CLI's license restricts use on private code**:
  queries as knowledge only, never as a check on customer repos), Kubernetes docs (CC-BY-4.0, no checks).
- Exa: current terms and sizes; quality reports.
- Read step 0's yield numbers ($ per useful item). If step 0 isn't done, stop.

**Build:**
1. For each source, a sampled pilot (e.g. 500 items; gitskills only above a star threshold and deduplicated
   against SkillMD-138K) on a local shard.
2. A yield table: useful items per model dollar vs step 1/2/3's yields, and a go/no-go recommendation per source.

**Acceptance:** the yield table with a recommendation per source; nothing beyond the samples ingested; proving
tests for any reader written.
