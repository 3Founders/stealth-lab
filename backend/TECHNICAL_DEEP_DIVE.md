# Technical Deep Dive: Workflow Debate Platform

Companion to the repos. This document shows the actual mechanisms, the actual bugs found and fixed, and an honest account of what's novel versus what isn't, rather than describing the system at a marketing level.

**Repos:** `backend` (V0), `backend_v1`, `backend_v2` (+ matching frontends). All three run `pip install -r requirements.txt && pytest tests/ -q` with zero external dependencies for the test suite itself.

---

## 1. The core loop

Bottleneck detected in a company's workflow → structured debate among heterogeneous AI models → argument-quality and (where possible) empirical evaluation → human approval → bi-temporal write to the knowledge graph, fully auditable back to who argued for it.

The differentiator is this loop, not the knowledge graph. Knowledge graphs and process-mining tools already exist from well-funded competitors (Celonis, UiPath, and similar). A structured, auditable, multi-party debate that resolves into an approved, versioned, non-destructive change does not, as far as we could establish researching the space directly.

## 2. The ontology: bi-temporal, not just versioned

Every fact in the graph carries two independent timelines, not one:

```sql
t_valid     TIMESTAMPTZ NOT NULL,  -- when this was true in the world
t_invalid   TIMESTAMPTZ,           -- NULL = still true
t_created   TIMESTAMPTZ NOT NULL,  -- when the system learned it
t_expired   TIMESTAMPTZ            -- NULL = not yet superseded
```

Updates are invalidate-and-append, never in-place. `KnowledgeUpdater._supersede_task` closes the old row's window, writes a new one, links them with a `SUPERSEDES` edge, and rewires every edge that pointed at the old version forward to the new one, verified live against real Postgres: an edge dependent on a superseded node does not silently orphan.

Point-in-time reconstruction follows directly: querying with `as_of` returns exactly the graph state at that moment, tested by capturing a real checkpoint between two writes and confirming the query at that checkpoint sees neither the pre-existing future state nor a stale artifact of the test's own timing.

## 3. The debate protocol

Modeled on the Nyaya dialectic tradition rather than an ad hoc multi-agent loop:

- **Vada** (cooperative): the default mode. Panelists propose, amend, or pass, fixed round-robin, terminating when a full round produces no movement or a hard cap is hit.
- **Jalpa** (adversarial): a dedicated agent attacks the leading candidate before it reaches evaluation.
- **Vitanda** (destructive refutation): flagged and structurally down-weighted, refutation without a proposed alternative does not count as a real contribution.
- **Nirnaya** (adjudication): the independent evaluator, enforced via `enforce_independence()`, which checks the judge shares no model family with any panelist, not just a different provider account.

Heterogeneity is enforced, not assumed: `assert_heterogeneous()` checks model *family* rather than model name, so two versions of the same base model (e.g. Llama 3.1 and 3.2) correctly fail the check, since they share pretraining lineage and therefore correlated blind spots.

## 4. Evaluation: Layer 1 (argument quality)

Grounded in the classical Nyaya fallacy taxonomy (hetvabhasa), five types, checked deterministically where possible rather than left entirely to LLM judgment:

- Groundedness is *computed*, not judged: every citation in an argument is checked against the real graph via `node_exists()`. An uncited claim scores 0.0. A citation to a real but different node is caught, not just a missing citation.
- The judge fails **closed**: if the evaluation call errors, the candidate is marked failed, never silently passed. Tested directly (`test_layer1_fails_closed_when_judge_is_unavailable`).

## 5. Evaluation: Layer 2 (empirical, where evidence exists)

Real statistical machinery, validated against known-correct references rather than only checked for whether it runs:

- **Welch's t-test**, not Student's t, because it doesn't assume equal variance, which matters when a candidate change affects consistency as well as average performance. Validated to `1e-9` against `scipy.stats.ttest_ind(equal_var=False)`.
- **Sample size**: $n \approx \dfrac{2(z_{\alpha/2}+z_\beta)^2\sigma^2}{\delta^2}$, returns exactly 16 for the canonical $\sigma=\delta$, $\alpha=0.05$, power$=0.80$ case, the standard textbook worked example.
- **Sequential testing** with an O'Brien-Fleming alpha-spending boundary, so checking results early doesn't inflate the false-positive rate. Tested for the defining property directly: an early boundary is extremely strict (`< 0.001` at 25% information), the final boundary approaches nominal alpha.
- **Benjamini-Hochberg** correction across metrics tested simultaneously, reproduces a standard worked textbook example exactly, and preserves the caller's input order rather than sorted order, an easy bug to ship silently.

Only the weakest of three possible evidence tiers is implemented (Tier 3: an LLM estimating a counterfactual, explicitly labeled "model opinion, not measurement" everywhere it surfaces, never presented as fact). Tiers 1 (shadow deployment) and 2 (off-policy evaluation) are honestly blocked, one on an unresolved product decision (does this system execute workflows or only observe them), the other on a genuine data-model gap: the trace schema records executions, not the policy decisions behind them, so there's no logged action-variation for importance sampling to reweight. This is documented in the code, not glossed over.

## 6. V2: inverting the trust model

V0/V1 assumed private, single-company data. V2 assumes public, anonymous, sometimes adversarial input. Three specific pieces worth the technical detail:

**Access control as a single predicate.** Every visibility check in the system flows through one function (`visibility_predicate()`). This was a direct lesson from the earlier version: a `tenant_id` column had existed on every table since the first schema and no query had ever filtered by it, so isolation was decorative. Centralizing the predicate means enabling private visibility later is a change to one function, not an audit of the whole codebase. Verified against the specific leak a naive implementation ships: a public node reachable only *through* a private edge. Filtering traversal output alone would expose it; the predicate is applied inside the recursive CTE itself, confirmed by constructing exactly that chain (`public → private → public`) and checking an anonymous viewer reaches neither the private node nor the public one behind it.

**A real concurrency bug, found by testing for it specifically.** The first rate-limiter implementation wrapped check-then-insert in a database transaction, which reads as sufficient and isn't: under Postgres's default isolation level, concurrent requests each see a snapshot without the others' uncommitted inserts, so ten simultaneous requests against a limit of three were all allowed. Reproduced deliberately with `asyncio.gather` across ten concurrent calls, fixed with a per-key advisory lock, reverified under the same load.

**Prompt-injection defense as a structural guarantee, not a prompting technique.** The riskiest new capability in V2 is generative task decomposition: a member of the public describes a problem and the system invents new graph structure from it, the first point in the entire system where untrusted text reaches an LLM directly. Four layers, in explicit order of what they can actually guarantee:

1. Delimiting untrusted text, defeated by a fence-guessing attacker.
2. An instruction-hierarchy system prompt, not enforceable.
3. Pattern scanning for known attack phrasings, catches nothing novel by construction.
4. **Capability restriction**, the only real guarantee: generated content may create new nodes and connect them to each other, and nothing else. It cannot modify, invalidate, or attach to anything that already exists, because those operations are not reachable from generated input at all, checked structurally via an explicit allowlist (`GENERATIVE_OP_TYPES`), not by trusting the model to behave.

The design assumption is that layers 1-3 will eventually be defeated and it must not matter when they are. Tested accordingly: the load-bearing test constructs a generator that is *already* fully hijacked and emitting hostile operations, then asserts the capability check still contains it. The same check runs again at approval time, not just at generation, so a proposal tampered with in storage between the two still cannot escalate, verified against real Postgres by attempting exactly that.

## 7. What was actually found by testing, not just written

Eight real defects, all found by running real code against a real database or under real concurrent load rather than only against mocks:

1. Every JSONB write pre-serialized values in Python and cast them in SQL, which silently corrupted how the connection decoded JSON on *subsequent* reads once a custom type codec was registered, affecting five files across the codebase before being traced to its root cause and fixed everywhere.
2. Trigger deduplication checked for an already-open debate, not an already-recorded trigger, allowing duplicate triggers for the same bottleneck in the gap between the two.
3. A variance calculation was numerically unstable on near-constant data, exactly what deterministic replay metrics look like, replaced with a stable two-pass calculation.
4. A pricing-relevant field used relative delta, undefined at a zero baseline, meaning the best possible outcome (0% to 100% success) reported as no value at all.
5. Layer 2 was fully built and fully unit-tested, and never actually reachable from the live API, the orchestrator was constructed without the argument that enables it. Found by checking the call site directly, not by assuming the wiring matched the tests.
6. The frontend never rendered Layer 2 results even after the backend returned them, a staleness gap from sequencing the two builds separately.
7. The rate limiter's concurrency race, described above.
8. A Next.js version with a published critical CVE was caught via `npm audit` before any application code was written against it.

## 8. Honest novelty assessment

Not everything here is novel, and claiming otherwise would undercut the parts that are:

| Piece | Assessment |
|---|---|
| Knowledge graph + bottleneck detection | Not novel. Celonis, UiPath, and several funded competitors do this well already. |
| Structured multi-party debate → eval → approval → auditable update | The actual differentiator. No direct competitor found running this exact closed loop. |
| Distillation of small models for narrow tasks | Sound engineering, industry-standard practice, not a differentiator on its own. |
| Bi-temporal, non-destructive update semantics | A known technique from temporal-database literature, applied deliberately here, not invented here. |
| The capability-boundary defense against prompt injection | A specific, structural design choice, tested against a deliberately hijacked model, distinct from prompting-only defenses common elsewhere. |

## 9. Test coverage

148 offline tests (no database, no network, no API keys) covering debate convergence and termination, statistical functions against reference values, citation verification, the capability boundary under simulated hijack, rate-limit and cost logic in isolation. **Stale as of the Experiment 1/3 session below — 244 offline tests as of that point**, see Section 11 for what was added and why.

Seven scripts run the same code against a real, disposable Postgres instance rather than mocks: bi-temporal traversal and update correctness, the debate state machine's transactional behavior, the full loop from trigger to persisted evaluation, the distinction between "found nothing wrong" and "every model call silently failed," access control including the leak case above, rate limiting under real concurrency, and applying an approved public decomposition including both escalation attempts being refused.

**What has not been tested:** any interaction with a real frontier model. Every test in the project runs against a scripted response or a small local model, since paid API access was unavailable during development. This is the single largest known unknown and is documented as such rather than implied to be solved.

## 10. What's queued, not hidden

Job queue for public-scale traffic (debates currently run synchronously in the request handler), real authentication (a placeholder header currently, safe only because nothing is private yet), the public review-and-reward surface, identity and payments, the Prover-Estimator asymmetric debate protocol (needed once rewards create a documented incentive to mislead), and the stronger two tiers of empirical evaluation, one blocked on a product decision, one on a data-model gap, both documented precisely rather than left vague.

## 11. Experiment 1 (retrieval), Experiment 2 (adversarial gate), and Experiment 3 (debate + update) — real results, graded by confidence

All three stopped here deliberately, as a checkpoint, not because any is finished. Claims below are graded High / Medium / Open rather than stated uniformly, because they're not uniformly certain.

### Experiment 1 — retrieval

**High confidence:** 76.7% precision@1 vs. 31.8% lexical baseline on 129 real AFTER tasks, non-overlapping 95% CIs. A real, large effect on real data. The `hierarchical_search` beam bug (higher-scoring leaves silently dropped) was found and fixed against real production data before this measurement. **Confirmed, not assumed:** this number is unaffected by the later PEP ingestion — `run_experiment_1.py`'s retrieval queries `task_nodes` exclusively (`CROSS JOIN task_nodes n`); PEPs were ingested purely as `knowledge_nodes`, a different table entirely.

**Open:** the `de` role underperforms (63%) — diagnosed (abstract skill descriptions vs. jargon-heavy task instructions), not fixed. No live access to AFTER's real skill descriptions from the current sandbox (Hugging Face unreachable) to verify a fix without guessing. Needs either richer skill descriptions or a hybrid lexical+vector blend for that role, as a real, separate piece of work. Also open: single measurement, never repeated; no cross-corpus interference test run (though structurally ruled out per above).

### Experiment 2 — adversarial gate (precondition/postcondition check)

Built and offline-tested before this session (`precondition_gate.py`), but its own docstring claimed it was "NOT YET WIRED" into the real matching pipeline — **stale and wrong**, confirmed by checking the actual code: `hierarchy.py`, `subtask_reuse.py`, and `reuse_detection.py` all genuinely import and call it, with a real SQL fetch of each candidate's stored properties. Fixed the docstring rather than leave it misleading.

**Real-data test, corrected once mid-flight:** the first attempt tested AFTER's 22-entry skill library, which is deliberately generic and role-agnostic by design — finding zero cross-role collisions there was expected, not informative, and `index_after_tasks.py`'s own docstring said as much directly ("real adversarial precondition differences live at the TASK level, not the skill level"). Corrected to test the right thing: real task *instructions* (not the skill library) restricted to the ~116 tasks genuinely involved in a cross-role shared skill tag.

**High confidence, real result:** of 628 genuine cross-role pairs, 1 (~0.16%) crosses the real production match threshold (0.90) — sparse, same character as banking_knowledge's 2/698 conflict rate. The gate correctly blocks it (`template-filler-with-placeholders` [pm] vs `template-filler-validation` [swe], sim=0.913). Zero false negatives found on the complement check (same-role high-similarity pairs correctly pass through — note the raw run reported 2 same-role hits, but both were the same underlying task pair double-counted because it shares two skill tags; the honest count is 1 distinct pair). n=1 is real evidence the mechanism works on the one genuine instance available, not a statistical success rate — same honest limit as Experiment 3's original banking n=2 before PEPs densified it. The underlying `role:X` tag remains a coarse proxy for real preconditions, unvalidated at that level of granularity by this test.

**Gap closed with synthetic tests, deliberately, after the real-data run:** real `role:X` tags are always binary (same role = 100% overlap, different role = 0%), so the gate's partial-overlap Jaccard threshold logic (`>= 0.25`) was proven correct in isolation (unit tests) but never exercised through the real wired database path. Added two tests (`test_precondition_gate_allows_partial_overlap_above_threshold_end_to_end` / `_blocks_..._below_threshold_...`) using realistic multi-tag postconditions on both sides of the threshold, run through the actual `resolve_subtask_reuse` → `hierarchy.py` code path, not a reimplementation.

### Experiment 3 — debate + update

**High confidence:** 27/32 real, machine-labeled PEP `Superseded-By` pairs correctly resolved in direction (0 wrong-direction, 0 false-positive misdiagnoses among scored outcomes) — a categorical improvement over banking_knowledge's n=2 (1 correct, 1 genuine misdiagnosis, ceiling reached at that corpus's real-conflict count). Three real, structural bugs found via actual failing production-shaped data (not hypothesized) and fixed with both a mechanical guard and test coverage:
- Rate-limit failures in `gather_responses` had zero retry/backoff — a single 429 silently dropped a panelist's entire turn, indistinguishable in the logs from a genuine model failure. Fixed with `_call_with_retry`, scoped to rate-limit-shaped errors only.
- An agent sending a missing/invalid `candidate_id` on "amend" was always discarded as a pass, even when exactly one candidate existed and the intent was unambiguous. Fixed with a narrow, deliberately-bounded recovery (only when `len(by_id) == 1`; still discarded when 2+ candidates make the target genuinely ambiguous).
- Date fabrication: banking's original 10/31-vs-11/12 substitution, and a second, different shape of the same failure class found on PEPs — a synthesized `effective_period` field (not present in either source) with an invented one-day-early boundary. Fixed via an explicit prompt ban on inventing derived date-range fields, verified via a direct re-test on the exact two affected pairs (0/5 grounding flags after the fix, where both pairs previously flagged).

**A genuine structural bug found, not just a data issue:** `ChangeSet.validate_ops()` flags *any* empty `ops` list as a structural problem, and Layer 1's `passed` check requires zero structural problems — meaning a legitimate "false positive, no action needed" resolution (explicitly sanctioned by the prompt) was **structurally guaranteed to fail Layer 1**, regardless of how correct the diagnosis was. Confirmed via real transcripts: 3 PEP pairs (438→470, 563→649, 563→749) where every panelist unanimously and correctly concluded no update was needed, and still failed. Fixed two ways: (1) prompt now defaults to adding a durable distinguishing annotation rather than proposing nothing — matches what 27/32 successful debates already converged on by themselves, and avoids leaving a flagged pair exactly as re-flaggable as before; (2) a `no_action_justified` flag kept as a backstop for the rare case where even an annotation adds nothing, with its own Layer 1 carve-out, its own Postgres column (migration required — see below), and its own classification label (`NO_ACTION_JUSTIFIED_UNVERIFIED`, kept distinct from `CORRECT`/`FALSE_POSITIVE_MISDIAGNOSIS` since direction can't be mechanically verified for a no-op).

**Open, not yet re-verified:** PEP 438→470, 563→649, and 563→749 were re-tested against the *date-fix* prompt only, before the no-action/annotate-default fix existed. Never re-run against the final combined prompt stack. Each pair tested once; no repetition to bound variance under LLM non-determinism. Only tested on clean, machine-labeled, unambiguous supersession — zero coverage of messier real-world conflict types (contradictory-but-not-superseding, adversarial, genuinely ambiguous). One untested assumption: the "default to annotate" prompt change assumes it's always acceptable to write a small metadata annotation — untested against a real company corpus with governance/permission constraints where even non-substantive edits might be restricted.

**Deployment-blocking, not just a caveat:** `migration_add_no_action_justified.sql` (adds a column `INSERT INTO candidates` now writes to in two call sites, `loop.py` and `human_participation.py`) **must run before** the updated backend deploys — those files will hard-error on every debate, not just PEP ones, if the column doesn't exist yet.

### General fixes vs. corpus-specific measurements — not the same thing

The bug fixes above (date preservation, rate-limit retry, candidate-id recovery, no-action handling) are general — none of the code is PEP-specific, and the date-fabrication fix in particular was validated as general by being caught fixing *two different* corpora (banking, then PEPs) independently. `ingest_peps.py` and `run_experiment_3_pep_corpus.py` are correctly corpus-specific, same category as the existing banking ingestion/runner pair — every corpus needs its own harness. **The 27/32 number itself does not generalize** — it reflects this corpus's unusually clean, formally machine-readable ground truth (`Superseded-By` headers), not a general capability estimate. Real company knowledge conflicts won't usually arrive this well-labeled.
