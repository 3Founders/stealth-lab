# Better-Ways Admission Review (2026-09-04)

**Phase:** REVIEW -> ADMITTED -> INGESTED -> VERIFIED, for the 7 candidates
selected by the Better-Ways candidate-testing pass
(`better-ways-candidate-results` @ `54ca96a1d1aae991fe38208f54e4c21c5cce8e8b`,
untouched by this task -- see "Provenance preserved" below).

**This is explicitly NOT the large-scale global ingestion wave.** Only the
4 candidates admitted below were written to the canonical corpus. The other
56 seeds (36 real-artifact candidates minus the 7 selected minus the 6
already-rejected-at-testing-time = 23 untouched `RESOLVED_*` seeds, plus the
24 non-`RESOLVED_*` seeds) remain completely untouched.

`SELECTED != ADMITTED`. Every decision below required an explicit, individual
admission call against the project's real admission criteria (utility,
evidence, novelty, generality, reproducibility, execution-readiness --
virality was not treated as proof of anything). No candidate was forced into
`ADMIT`, and none of the 7 was rejected outright either -- an honest review of
this particular 7 happened to find zero outright rejections, which is
reported truthfully below rather than padded.

---

## System under test / product baseline

- Product: `origin/main` @ `bd768e62a887b13a94fdd118693a5c671df1cf95` -- confirmed
  unchanged (re-fetched) before this task began and re-confirmed unchanged at
  the end (see Step 6 git-state check).
- Ingestion performed through an isolated worktree/branch (`better-ways-admission`,
  branched from `origin/main`), against the SAME live Supabase project
  (`wckeklqxmiglivfolujn`) used throughout this whole evaluation session.
  Nothing was written by ad-hoc SQL -- every procedure/claim/implementation
  write went through the real production service-layer functions
  (`app.services.procedures.capture_procedure`, `app.services.claims.capture_claim`,
  `app.execution.implementation_registry.register`); `task_nodes` rows were
  created via direct `INSERT` mirroring the exact established pattern
  `app.services.skill_ingestion.py::_write_task_nodes` already uses in
  production (there is no separate `capture_task_node()` wrapper anywhere in
  this codebase -- every real service that needs one writes it this way).

## Provenance preserved (task's explicit requirement)

- `better-ways-candidate-results` @ `54ca96a1d1aae991fe38208f54e4c21c5cce8e8b`
  (the research branch/commit with `candidates.jsonl`/`ranking.json`/
  `selected.json`/`rejected.json`/`final_report.md`) was **not modified** by
  this task -- this admission review lives on a separate new branch
  (`better-ways-admission`), reading that commit's artifacts read-only.
- The pre-ingestion deterministic baseline (`evaluation-suite` branch @
  `ebabec1350f4ae6eb1e4d09e29f245fa8a4a6d79`,
  `.scratch/final-v1-deterministic-baseline.md`) and its benchmark case set
  were **not touched** by this task.

---

## The 7, individually reviewed

### C01 -- TokenPilot (structural-summary hook-read)

- **Source:** `https://github.com/Digital-Threads/token-pilot` @
  `a1df2519b034d6f79fac9e22191ab28cb9311960`. License: MIT (declared in
  `package.json`; **no standalone LICENSE file** -- weaker provenance than a
  repo with an actual LICENSE file).
- **Evidence:** real, independently measured (this session's own experiment,
  not a vendor claim): 92.8% token reduction (17,988 raw tokens -> 1,289
  summary tokens, tiktoken `cl100k_base`, real 2,033-line file) AND a real
  ~35x breach of the tool's own documented latency SLA (warm p50 < 30ms;
  measured 1086.9ms before / 1035.5ms after installing the tool's own
  documented accelerator fallback -- architectural, not a missing binary).
- **Measured result:** BOTH of the above are real and independently
  confirmed. Neither is cherry-picked over the other.
- **Limitations:** the latency failure is disqualifying for THIS
  implementation specifically. Weak license provenance (no LICENSE file).
- **Admission decision: B -- ADMIT WITH CONDITIONS.** The underlying
  procedure concept ("structural-summary-first reads") is admitted;
  TokenPilot itself is explicitly NOT registered as an Implementation
  (disqualified by latency). Its negative finding is recorded as its own
  claim, tied to TokenPilot specifically, not to the general procedure.
- **Canonical procedure family:** `structural-summary-before-full-read`
  (shared with C02).
- **Claims created:** 2 (positive token-reduction claim `4f8543cb-c993-4ad4-91d5-ea332e27f0a9`;
  disqualifying latency claim `f0a149d1-df28-40e3-97e0-418b06f0ccfc`), both
  `epistemic_tag=EXPERIMENTAL`.
- **Task nodes created:** 0 dedicated (shares the canonical procedure's 2 task
  nodes with C02 -- see below).
- **Implementations created:** 0 (deliberately -- disqualified).
- **Verification status:** N/A directly (TokenPilot itself has no bound
  Implementation to verify); its claims are attached to and readable from
  the admitted canonical procedure (verified readable back, see Verification
  section).

### C02 -- octocode `view`

- **Source:** `https://github.com/Muvon/octocode` @
  `28b9174f2f9ece1523b40c103655a49a5b1778cc` (tag `0.24.0`). License:
  Apache-2.0 (verified via raw LICENSE fetch at the tag).
- **Evidence:** real, independently measured: 77.0% token reduction
  (6,723 raw tokens -> 1,549 outline tokens, tiktoken `cl100k_base`) running
  `octocode view --format text` against StealthLab's own real 594-line
  `backend/app/services/product_model.py`.
- **Measured result:** 77.0% reduction, single real file, deterministic
  mechanism (tree-sitter), no embeddings/API dependency for this command
  path, prebuilt binary (no toolchain required).
- **Limitations:** n=1 file measured -- generality beyond this one file/repo
  not established. `index`/`search`/`graphrag` subcommands (the
  semantic/graph layer) were not exercised.
- **Admission decision: A -- ADMIT.** Real license, real deterministic
  mechanism, no execution/toolchain blocker, admitted as the bound
  Implementation for the canonical procedure.
- **Canonical procedure family:** `structural-summary-before-full-read`
  (shared with C01).
- **Claims created:** 1 (`1adcc6b4-5117-4ed9-aa3e-9ad53550a1c2`,
  `epistemic_tag=EXPERIMENTAL`).
- **Task nodes created:** 2, shared with C01 as the same canonical procedure:
  `4b635968-40c6-4b23-8211-addf9b426526` (request-structural-outline),
  `cbe950af-9f41-43c0-8861-5e1e696f67e9` (escalate-to-targeted-read).
- **Implementations created:** 1 -- `01a069f1-042d-74eb-b7fd-152d2baa07c2`
  (`octocode view`, `kind=tool`, `provider=Muvon/octocode`,
  `status=candidate`, `verification_status=unverified`).
- **Verification status:** retrieval PASS, applicability PASS (ALLOW),
  implementation resolution PASS (resolves to the registered Implementation
  for the procedure's first task node). Execution NOT exercised this pass
  (see Verification section for why). Not marked verified (correctly).

### C03 -- code-graph-rag

- **Source:** `https://github.com/vitali87/code-graph-rag` @
  `0cfa5eb89ab906c894e8c6236ebe8911d30882b5`. License: MIT (LICENSE file,
  verified).
- **Evidence:** real, independently measured, but PARTIAL. The project's own
  no-database/no-LLM-key benchmark (`benchmarks/bench_indexing.py`) was run
  for real against StealthLab's own `backend/app` directory: 176 files,
  1,998 nodes, 8,811 edges, 59.65s. The query layer (NL->Cypher over
  Memgraph) -- the actual value proposition ("answer 'who calls X'/'where is
  Y' without sending the repo to the model") -- was **never tested**: Memgraph
  is real infrastructure this sandbox does not have.
- **Measured result:** indexing/graph-construction speed only. No query-layer
  result exists.
- **Limitations:** the untested query layer is the whole point of the
  technique. Indexing speed alone does not establish utility for the actual
  claimed use case.
- **Admission decision: C -- HOLD FOR MORE EVIDENCE.** Not ingested. Would
  need a real query-layer measurement (Memgraph available, a real "who
  calls X" question answered correctly) before reconsideration.
- **Canonical procedure family:** none created (held).
- **Claims/task nodes/implementations created:** 0.
- **Verification status:** N/A (not ingested).

### C04 -- Git-worktree isolation

- **Source:** native technique, no third-party artifact (StealthLab already
  uses this pattern operationally, including in this very session's own 8
  worktrees). License: n/a.
- **Evidence:** real, independently measured, the cleanest result of the
  whole testing pass: a real reproducible local experiment (8 real OS
  threads spawning real git subprocesses, twice) measured 1/8 (12.5%) clean
  commits with one shared working directory vs. 8/8 (100%) with one linked
  worktree per agent, at ~0.6s overhead for 8 agents.
- **Measured result:** 1/8 -> 8/8, directly reproduces the claimed mechanism,
  not a proxy.
- **Limitations:** none significant -- high reproducibility, high generality,
  zero new dependency (git already relied on).
- **Admission decision: A -- ADMIT.**
- **Canonical procedure family:** `parallel-agent-git-worktree-isolation`
  (C04 alone).
- **Claims created:** 1 (`d8d9b702-f775-4382-b3fb-96668b130912`,
  `epistemic_tag=EXPERIMENTAL`).
- **Task nodes created:** 2 -- `6e8b7637-8d2e-47bf-906e-564232b0a940`
  (create-linked-worktree-per-agent), `cb488f63-6097-4d26-bc9c-2e78978f84e3`
  (operate-only-within-own-worktree).
- **Implementations created:** 0, deliberately -- this is a documented step
  sequence using git itself, already universally available, not a separate
  bindable service/script. (Confirmed this fits the real schema cleanly: a
  procedure with no bound Implementation is a normal, valid, already-common
  state in this schema -- `implementation_registry.py`'s own docstring notes
  "an implementation with no linked task is a real, storable state", and the
  inverse -- a procedure with no implementation -- is equally unremarkable;
  no workaround was needed.)
- **Verification status:** retrieval PASS, applicability PASS (ALLOW),
  implementation resolution correctly finds none (by design). Not marked
  verified (correctly).

### C05 -- StealthLab's "own" deferred-tool loading

- **Source:** n/a as a third-party artifact. **Important caveat, carried
  through every representation of this candidate:** the actual measurement's
  subject was the evaluating agent's OWN Claude Code harness (its
  `ToolSearch` deferred-tool mechanism), NOT StealthLab's own MCP tool
  surface, despite the candidate's working name implying otherwise. This was
  a proxy-system measurement of the same architectural pattern, not a
  StealthLab-native result.
- **Evidence:** real, independently measured on that proxy system: deferred
  name-only tool listing cost ~5.6 tokens/tool vs. ~377.8 tokens/tool average
  for 4 sampled full schemas (WebFetch, WebSearch, CronCreate,
  PushNotification) -- a 67.5x ratio, disclosed as a conservative lower bound
  (2 of 4 schemas were lightly abbreviated to write the measurement
  reliably).
- **Measured result:** 67.5x, real, but on a proxy system.
- **Limitations:** NOT a StealthLab-native measurement. Must never be
  represented as "StealthLab has verified this in its own product" -- every
  place this claim appears (procedure `evidence_refs`, the claim statement
  itself) carries this caveat explicitly and permanently.
- **Admission decision: B -- ADMIT WITH CONDITIONS.** Admitted only as
  evidence for the general "defer tool-schema loading until needed" claim/
  procedure, always caveated. No Implementation Registry entry (no bindable
  StealthLab-native service to register -- this is architectural/descriptive
  knowledge, not an invocable artifact).
- **Canonical procedure family:** `defer-tool-schema-loading-until-needed`
  (C05 alone).
- **Claims created:** 1 (`722d58e2-ffdc-4880-9a35-e1e7bb58de4b`,
  `epistemic_tag=EXPERIMENTAL`, `properties.caveat=proxy_system_measurement_not_stealthlab_native`).
- **Task nodes created:** 2 -- `87a3fa45-480e-46d0-8563-8b2b0a692d23`
  (advertise-tool-names-only), `8e595f42-6566-482b-8f70-a1ce32991f10`
  (load-schema-on-demand).
- **Implementations created:** 0, deliberately -- same reasoning as C04's
  "no implementation is a valid state", plus there genuinely is no
  StealthLab-native artifact to bind.
- **Verification status:** retrieval PASS, applicability PASS (ALLOW),
  implementation resolution correctly finds none (by design). Not marked
  verified (correctly).

### C06 -- hermes-agent tail-protection

- **Source:** `https://github.com/NousResearch/hermes-agent` @
  `63279301bcbdc185c1b07b98a9312eb0c862f26d`. License: MIT (verified).
- **Evidence:** the project's OWN real unit test suite passes
  (`tests/agent/test_protected_tail_pressure_61932.py`, 2/2, run together
  with C07's test file for 9/9 total). This is evidence the mechanism EXISTS
  and FUNCTIONS in hermes-agent's own codebase -- it is evidence about
  hermes-agent's implementation, not proof the technique improves Stealth.
  No StealthLab-context comparative measurement exists.
- **Measured result:** 2/2 (upstream tests), no StealthLab-side number.
- **Limitations:** exactly the case the task's own review criteria named --
  "a passed upstream test is evidence about the upstream implementation, not
  proof that the technique universally improves Stealth." Tagged
  `SOURCE_DERIVED`, not `EXPERIMENTAL`, for this reason.
- **Admission decision: C -- HOLD FOR MORE EVIDENCE.** Not ingested. Would
  need a real StealthLab-context comparative experiment (does protecting
  recent turns from compression measurably help a real StealthLab-run task)
  before reconsideration.
- **Canonical procedure family:** none created (held).
- **Claims/task nodes/implementations created:** 0.
- **Verification status:** N/A (not ingested).

### C07 -- hermes-agent tool-result pruning

- **Source:** same repo/commit as C06. License: MIT (same, verified).
- **Evidence:** same run as C06 -- the project's own dedicated test file
  (`tests/agent/test_proactive_tool_result_pruning.py`, 7/7) passes. Same
  "evidence about the upstream implementation" caveat as C06 applies
  identically.
- **Measured result:** 7/7 (upstream tests), no StealthLab-side number.
- **Limitations:** identical reasoning to C06.
- **Admission decision: C -- HOLD FOR MORE EVIDENCE.** Not ingested. Same
  bar to clear as C06 before reconsideration.
- **Canonical procedure family:** none created (held).
- **Claims/task nodes/implementations created:** 0.
- **Verification status:** N/A (not ingested).

---

## Totals

| metric | count |
|---|---|
| selected (input to this review) | 7 |
| admitted | 4 (C01 conditions, C02 full, C04 full, C05 conditions) |
| held for more evidence | 3 (C03, C06, C07) |
| rejected outright | 0 (honest result for this particular 7 -- see note below) |
| canonical procedures created | 3 |
| claims created | 5 |
| task nodes created | 6 |
| implementations created | 1 |

**On the zero-rejections result:** the task's own rejection criteria are "no
actionable procedure, cannot be tested, no meaningful improvement, evidence
too weak, provenance/license unacceptable." All 7 selected candidates had a
real actionable procedure, were genuinely tested (or partially tested with
an honestly reported gap, for C03), showed a non-trivial measured result
(positive, negative, or informative), and had acceptable license status
(TokenPilot's weak-but-present MIT declaration was flagged as a limitation,
not disqualifying). An honest review of this specific 7 finds zero outright
rejections -- this is a property of the prior testing pass having already
filtered out the genuinely weak candidates (see the 6 `REJECTED_*`/
`INSUFFICIENT_EVIDENCE` rows in `source_resolution/rejected.json`, none of
which reached the SELECTED set to begin with), not evidence this review was
insufficiently critical.

## Epistemic tags used (task section 3's scheme)

- `EXPERIMENTAL`: C01, C02, C04, C05 -- all four were this session's own real,
  independently-run local measurements, never a quoted vendor claim taken on
  faith. C05 carries an additional mandatory proxy-system caveat, present in
  every representation of it.
- `SOURCE_DERIVED`: C06, C07 -- confirms the mechanism exists and functions
  as the upstream source describes; explicitly NOT a StealthLab-context
  experimental result.
- `USER_REPORTED` / `MODEL_INFERRED`: not used -- neither applies to any of
  these 7.

## Verification (task section 7) -- for every admitted procedure

Proven through real calls against the live Supabase DB, not assumed:

1. **Retrieval**: `find_applicable_procedures(require_verified=False)` finds
   all 3 canonical procedures for a query matching their own real goal text
   -- PASS for all 3. `require_verified=True` (the real default) correctly
   EXCLUDES all 3, since none is verified yet -- confirmed this is the
   expected, correct behavior for a freshly-captured `candidate` procedure
   (ticket 13's own documented "explicit opt-in" bootstrap path), not a
   failure.
2. **Applicability**: `check_procedure_reuse()` returns `ALLOW` for all 3 --
   real hard-constraint cascade evaluated (temporal validity, staleness,
   availability, scope/exclusions, 0 preconditions, invariants), not
   assumed.
3. **Implementation resolution**: `octocode view`'s registered Implementation
   correctly resolves for procedure 1's task node; procedures 2 and 3
   correctly resolve to no Implementation (by design, confirmed via direct
   query against `implementation_tasks`, not merely absence-of-error).
4. **Execution**: NOT exercised this pass. Running `octocode view` for real
   through StealthLab's own `implementation_executor.py` would require
   re-fetching the octocode binary (only present in the candidate-testing
   pass's now-cleaned-up scratchpad) and represents a real, unbounded step
   (spawning an external process) beyond this admission pass's intentionally
   small scope. Procedures 2 and 3 have no Implementation to execute, by
   design. This is a judgment call, documented rather than silently skipped.
5. **Nothing marked verified from ingestion alone**: confirmed by direct
   query -- all 3 procedures remain `verification_state='candidate'`; the
   one Implementation remains `verification_status='unverified'`. No code
   path in this task called `approve_procedure`/`activate`/`verify`.
6. **Evidence genuinely queryable back**: all 5 claims read back from
   `knowledge_nodes` with their `epistemic_tag` intact; each procedure's
   `evidence_refs` JSONB field reads back with the expected count (2 for the
   shared C01/C02 procedure, 1 each for the other two) -- not just written
   and forgotten.

## A real schema finding surfaced during ingestion (documented, not hidden)

`capture_procedure(scope_type="global", domain=<any non-null value>)`
raises `V0Violation: global scope cannot carry an entity_id` -- passing a
`domain` value alongside `scope_type="global"` causes the function's own
`scope_entity_id or domain` fallback to synthesize a spurious entity_id for
an otherwise-correct global-scope call. Worked around by omitting `domain=`
for all 3 procedures (global-scope, no specific domain narrower than
"software engineering technique" was needed or lost by omitting it) --
this is a real, narrow usability rough edge in `capture_procedure`'s own
parameter interaction worth a follow-up ticket, not a blocker to this
admission pass.

A real, structural rate-limit was also hit mid-ingestion: this sandbox's
Voyage API key (same account/limitation confirmed earlier in this session)
allows 3 RPM. The first ingestion run captured 1 procedure + 2 task nodes +
2 claims before hitting the limit on the 4th embedding call in the same
process; the DB was queried directly to confirm exactly what existed before
a paced continuation (>=25s between embedding-requiring calls) completed the
remaining 2 procedures + 4 task nodes + 3 claims + 1 implementation with
zero duplication. No SQL was used to work around the rate limit -- only
pacing and re-querying real state before continuing.
