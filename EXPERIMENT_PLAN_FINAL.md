# Experiment Plan — Final, Consolidated (post Part C)

## Final conclusion: what we're actually testing

A bi-temporal HTN/TMS graph — where "reusable" means a structurally-matched, precondition/postcondition-typed procedure, not a name or embedding match — outperforms flat memory (naive RAG, static skill files, raw context-stuffing) on four separable axes. No single benchmark tests all four, so each experiment isolates one axis against your own system's naive-memory baseline, not a leaderboard.

1. **Reusability** — does retrieval find the *correct* existing procedure (and correct existing *subtasks*, per Part C) for a genuinely new problem?
2. **World-model soundness** — does the accumulated graph help the next related task, without AFTER/EvoMemBench's documented noise-injection failure mode?
3. **Debate + Update** — when the world changes, does the TMS correctly supersede the stale procedure, where flat memory doesn't?
4. **SLM trajectory transfer** — does a small model, handed a validated masked trajectory, close the gap to a frontier model, at real token savings?

**Supersedes `GRAPH_GROUNDED_CODING_AGENT_PLAN.md`**: these benchmarks ship their own execution/grading harnesses (AFTER's pytest validator, τ³-bench's `ACTION`/`partial_action_reward`, EvoMemBench's pre-sequenced episodes), which solves the "no execution sandbox exists" blocker that plan never got past. Also compute-appropriate (SLM via Groq/Cerebras, frontier via API) — no execution infra to build first.

---

## Experiment 1 — Reusability (updated: now tests Part C explicitly)

**Substrate:** AFTER (primary) + EvoMemBench CROSSEP-TOOL/WEB/EMB (secondary, live-execution confirmation).

**Hypothesis A (task-level, as originally designed):** given only a task's instruction text, `hierarchical_search`/`find_reusable_nodes` retrieves the correct skill at precision@1 above a BM25 baseline and a flat-embedding baseline, on held-out tasks.

**Hypothesis B (subtask-level, new — this is the gap the earlier design had):** for a held-out task whose gold solution is COMPOSITE (needs 2+ AFTER skills, e.g. `xlsx` + `statistics`), `resolve_subtask_reuse` correctly recognizes each already-known component individually — not just whether the whole task matches one thing wholesale. This is the actual "6 new, 4 existing, instantly recognized" claim, tested on a real benchmark instead of a synthetic fake-DB harness.

**What's under test:** the 0.90/0.70 cosine (0.55/0.25 lexical fallback) two-tier threshold, confidence-adaptive beam descent through `OWNS`/`PARENT_OF`, exact leaf comparison — AND, for Hypothesis B, whether per-op batched search (`batch_hierarchical_search`) actually finds cross-branch component matches on real task data, not just the synthetic scattered-leaves test already run.

**Controls:** same embedding model across all retrieval methods; library-building task pool never overlaps the held-out test set.

**One instance, from the start:** `tasks/de/sales-pivot-analysis/` (Data Engineer, `population.pdf` → pivot output), held out from library construction. Library holds AFTER's other 21 skills as `TaskNode`s with masked `io_schema` (Rule 2) and `preconditions`/`postconditions` (Rule 1). Hypothesis A: feed the instruction text, check rank-1 retrieval. Hypothesis B: if the gold solution is genuinely composite, decompose the held-out task (via `/v1/decompose`), and check whether `resolve_subtask_reuse` correctly matches the `xlsx`-handling and `statistics`-handling subtasks to their existing library counterparts, individually, rather than requiring the whole composite to match one node. Failure mode to watch: retrieving a superficially similar but structurally wrong skill or subtask (generalization-vs-memorization line).

---

## Experiment 2 — World-model soundness

**Substrate:** EvoMemBench CROSSEP-KNOW (primary) + AFTER cross-role transfer (secondary, adversarial).

**Hypothesis A:** across a task sequence sharing background context, accuracy on task N+1 improves when the graph from tasks 1..N is available, without the noise-injection degradation EvoMemBench documented for naive memory.

**Hypothesis B:** a procedure evolved in one role's context, tested against a superficially similar but structurally different task from another role, gets correctly *declined* by precondition/postcondition matching — where a flat name/embedding match would wrongly apply it (AFTER's documented -4.8 to -7.5 point cross-role loss).

**What's under test:** whether separating reusable structural content (Rule 1/2) from role/domain metadata actually prevents the leak AFTER's flat `SKILL.md` format suffered from.

**One instance, from the start:** two CL-Bench "Procedural Task Execution" tasks sharing a background context (CROSSEP-KNOW's own grouping). Task A's procedure stored as a `KnowledgeNode` (`provenance = company_ingested`) with explicit preconditions; Task B run later in-sequence, measure success-rate delta with/without the graph. Separately: one AFTER skill evolved under "Data Engineer" vs. a "Software Engineer" task sharing surface keywords ("validate the output") but different actual preconditions (schema-conformance vs. test-suite-passing) — confirm postcondition mismatch blocks retrieval; log what a flat-embedding baseline does with the same pair (predicted: wrongly retrieves it).

---

## Experiment 3 — Debate + Update

**Substrate:** `banking_knowledge` (τ³-bench, primary) + EvoMemBench INEP-KNOW Selective Forgetting (secondary, cheap in-episode check). AFTER doesn't apply — no temporal/revision dimension.

**Hypothesis:** when a policy is superseded, a graph-grounded agent correctly follows the new procedure on tasks dated after the change; flat-RAG (no bi-temporal invalidation) continues serving the stale one or confuses both.

**What's under test:** the bi-temporal `t_invalid`/`SUPERSEDES` mechanism specifically, plus whether the debate protocol that PRODUCES the `invalidate_edge`/`update_task_node` ChangeSet actually changes downstream agent behavior — not just whether the row is correctly marked invalid in the table.

**One instance, from the start:** seed a `KnowledgeNode` — "Refunds require the original receipt" (`t_valid = T0`). At T1, inject "As of T1, refunds under $50 don't require a receipt." Run through the real debate protocol; confirm panelists propose citation-by-node-id ops, old node gets `t_invalid = T1`, `SUPERSEDES` edge created. Then run a `banking_knowledge`-style $30-refund-no-receipt task dated after T1 through both the graph-grounded agent and a flat-RAG baseline (same two documents, embedded, no invalidation logic). Expected: graph-grounded follows the new rule; baseline follows the old one or hedges.

---

## Experiment 4 — SLM trajectory transfer

**Substrate:** AFTER static-skill-valuation, extended into a 2×2. SLM arm via Groq/Cerebras (~7-8B, comparable to FISSION-GRPO's tested scale), LLM arm via frontier API.

**Hypothesis:** an SLM given a `TaskNode`-formatted trajectory (masked `io_schema`, ordered `REQUIRES` chain, `preconditions`/`postconditions`) closes a meaningful fraction of the gap to frontier unaided performance, at substantially lower tokens/episode; the same trajectory given to the frontier model moves accuracy little (near-ceiling either way) but shows a similar compression ratio.

**What's under test:** whether the masking discipline specifically (Rule 2 — typed slots, not literals) is what lets a small model succeed via slot-filling rather than planning.

**Design:**

| | No skill | TaskNode trajectory |
|---|---|---|
| **SLM (~8B)** | baseline tokens/success | test tokens/success |
| **LLM (frontier)** | reference ceiling | control — should barely move |

**One instance, from the start:** same held-out `sales-pivot-analysis` task as Experiment 1, for cross-experiment comparability. Cell 1: SLM, raw instruction + `population.pdf`, no skill — record tokens, pass/fail via AFTER's `test_outputs.py`. Cell 2: same model/task, given the `TaskNode` retrieved in Experiment 1 (Hypothesis A or B, whichever actually resolved it), formatted as an ordered masked trajectory (`{TARGET_FILE_PATH}`, not the literal path). Cells 3/4: repeat with frontier model. Expected: 3→4 barely moves, 1→2 moves substantially — same shape AFTER's own static valuation table shows for their format, now demonstrated on a task your own pipeline retrieved, not one handed to the model directly.

---

## Build/verification order, given what's real vs. simulated right now

1. **Live-DB verification first** (`integration_check_v2_hierarchy.py`, `pgvector`'s `avg()` support) — every experiment above depends on `hierarchical_search`/`resolve_subtask_reuse` actually working against a real database, which is still unconfirmed. All correctness claims so far are from a fake-DB test harness, real but not the same as production Postgres.
2. **Experiment 1** first among the four — smallest, most direct test of the core retrieval mechanism, and Hypothesis B specifically validates Part C (the newest, least-tested piece) against real task data instead of synthetic.
3. **Experiment 3** next — reuses infrastructure you already have most of (debate protocol, bi-temporal model), least new benchmark-harness work.
4. **Experiments 2 and 4** last — 2 needs CL-Bench sequencing set up, 4 needs Groq/Cerebras SLM access wired in; both depend on Experiment 1's retrieval working correctly first, since both reuse its output.
