# RUN #1 Independent Verification

**Lane:** research · **Date:** 2026-08-26 · **Task:** board Lane RESEARCH item 4
**Inputs:** `C:\Users\user\stealth-lab\experiments\harness\real_arms_results.jsonl` (11 rows,
gitignored per lane rule — read-only access to the integrator checkout) +
`...\real_spend.jsonl` + fixtures `experiments/harness/fixtures/micro/{procedures,tasks}.json`
+ harness code at main `d709968`.
**Method:** fresh recomputation from raw JSONL with an independent classifier
(`.scratch/research/run1_verify.py`, pure stdlib, does NOT import harness scoring),
cross-checked against the shipped `scoreboard.build_summary`. Exact binomial McNemar via
`math.comb`; small-tail doubling, matching mcnemar_power.py:24-29.

## Verdict table

| Board claim | Recomputed | Status |
|---|---|---|
| 95 calls, \$0.2659 | 95 attempts, 44 billed, 51×429, \$0.2659, 12,444in/23,479out tok | CONFIRMED exactly |
| A 6/10 · B 7/10 (1 false-reuse) · C 7/10 (0) | Per-arm-valid denominators: A 6/10, B 7/10, C 7/10; FR B=1 (mic-dep-002), C=0 | CONFIRMED, but see denominators note |
| Stale-refusal C 6/6 vs B 0/6 → McNemar p≈0.031 | Arithmetic reproduces ONLY under one frame (below): 6 discordant pairs, p=0.03125 | REPRODUCED WITH TWO MATERIAL CAVEATS |
| Resolution B-vs-C not significant (1-1 discordant) | Invalid-as-fail frame: 1-1, p=1.0 ✓; strict pairing: 0 discordant pairs ("test has no input") | CONFIRMED (frame-dependent) |
| dep-003/env-001 universal failures = coverage gaps | dep-003 unseen/no offers, 0/3 arms; env-001 only stale offer, 0/3 arms | CONSISTENT |

## Caveat 1 — the headline p-value is frame-dependent

Stale-offer tasks (fixtures `stale_offer`, ground-truth `stale:true` = {refund-auto-v1,
dep-pinbump-v1, pdf-sheet-v1, env-setup-v1}): mic-refund-001, mic-refund-003, mic-dep-002,
mic-pdf-002, mic-pdf-003, mic-env-001, mic-env-002 (7 tasks).

Refusal facts (mechanical, from `refused_procedure_ids` vs fixture truth):
**B refused 0 of 7; C refused 6 of its 7 opportunities** (unparseable on mic-refund-003).

Paired exact McNemar (B vs C), by inclusion rule:

| Frame | Discordant pairs | exact two-sided p |
|---|---|---|
| Shipped-scoreboard discipline (task valid on ALL arms; drops refund-003 & pdf-003) | 5 (0 vs 5) | **0.062500 — NOT ≤ α** |
| Pairwise B∧C-valid (drops refund-003 only) | 5 (0 vs 5) | **0.062500 — NOT ≤ α** |
| All rows, invalid counts as non-refusal (board's implicit rule) | 6 (0 vs 6) | **0.031250 — the board number** |

The jump from p=0.0625 to p=0.03125 across the α=0.05 line is produced entirely by
charging arm B's single UNPARSEABLE decision (mic-pdf-003,
`invalid_reason=unparseable_decision_after_repair`) as a missed stale-refusal paired
against C's valid refusal there. An unparseable decision is not evidence the model would
not have refused. Honest statement: **p ∈ [0.03125, 0.0625], n too small either way;
direction unanimous; the log's "(at the k>=6 significance floor)" hedge implicitly
acknowledges this but the board's flat "p~0.031" should carry the caveat.**

Related frame notes: (a) the "10 valid tasks" denominator is PER-ARM (A and B lose
pdf-003, C loses refund-003 — different task sets); the shipped scoreboard's own default
output prints usable=9: A 5/9, B 6/9, C 6/9, stale B 0/5 vs C 5/5. (b) The shipped
scoreboard computes NO stale-refusal McNemar anywhere — its pairwise footer covers
pass/fail only, where B-vs-C has ZERO discordant pairs ("p=N/A — the test has no input").
The 0.031 figure was computed outside the shipped instrument.

## Caveat 2 — bigger than the stats: C's refusals were recorded by the substrate gate, not the model

The C_journal evidence trail proves **every one of C's six correct stale refusals was
recorded mechanically by StubSurface.check_applicability BEFORE the model ever saw the
stale card** — each shows `check_applicability verdict=false` followed by
`record_refusal reason="assumptions no longer hold (gate)"`. The stub's verdict is a
deterministic lookup of the fixture's ground-truth flag (`verdict = not p["stale"]`,
mcp_surface.py:88); on a gate-false task openrouter_arms.py:561-567 appends the refusal
and states plainly: "the model never sees the card."

The ONE task where the model itself faced the staleness decision is mic-refund-003
(`substrate_bypasses_gate: true` → gate fails open → card offered). There real-C returned
an **unparseable decision after repair** (scored neither refuse nor reuse). Arm B
structurally cannot record refusals at all: RealMemoryAgent sends only the rag prose blob
— no procedure ids appear in its prompt (openrouter_arms.py:506-510) — so its
`refuse[]` is empty by construction, though its notes twice SAY "refused" in prose
(mic-refund-003, mic-dep-002).

Consequences, in order of importance:

1. **The 6-vs-0 outcome is fixture-determined, not model behavior.** Given the fixtures
   and valid episodes, it was guaranteed by surface design. Treating design-determined
   outcomes as paired Bernoulli trials makes ANY inferential reading (including
   "p≈0.031") inappropriate — the p-value quantifies sampling noise that does not exist
   in the mechanism scored.
2. What RUN #1 actually showed at system level: a gated procedure surface refuses stale
   offers deterministically; a conventional-RAG surface has no refusal interface. That IS
   §40's "the system can refuse" (spec 39 invariant 11) and matches scripted-parity —
   but it is NOT evidence that ox-alpha detects stale procedures when offered one. Direct
   model-level evidence: n=1 attempt (refund-003), invalid.
3. The board line "Stale-refusal C 6/6 vs B 0/6 -> McNemar p~0.031" invites a
   model-level misreading and should be rephrased before any external use (the log's own
   "no public phrasing" guard already points this way).

## Competitive delta refresh — is stale-procedure-refusal a published metric?

Question: does ANY published system report decision-time refusal of a ground-truth-stale
offered procedure as a named evaluation metric? **Answer: no — but the 2026 literature
has converged on the problem from four adjacent directions, all of which must now be
cited.** (Refresh of `.scratch/research/competitive-sweep-mem0-letta-zep-hipporag-awm.md`;
addendum filed there.)

Nearest neighbors, closest first:

1. **STALE benchmark** — "Can LLM Agents Know When Their Memories Are No Longer Valid?"
   (arXiv:2605.06527, May 2026). 400 expert-validated implicit-conflict scenarios;
   three probing dimensions: State Resolution, **Premise Resistance** (rejecting queries
   that presuppose a stale state), Implicit Policy Adaptation. Best model 55.2% overall.
   Measures stale-DETECTION over personalization/episodic fact memory as QA accuracy —
   not refusal of an offered procedure inside a tool-execution loop. This is the paper
   that makes caveat 2 acute: the field's framing of this capability is MODEL detection,
   which our current instrument delegates to the substrate gate.
   <https://arxiv.org/abs/2605.06527>
2. **TEPA** — Revoking Stale Memories for Conflict-Robust Language Agents
   (arXiv:2608.07429, Aug 2026). Validity as an explicit memory state; keyed precedent
   revocation on contradiction; revoked history preserved for audit (architecturally very
   close to our truth_state=OUT + tombstone spine). Metrics: post-drift retrieval
   accuracy (TEPA 0.950 vs append-only 0.210 under reversal). Memory-layer mechanism +
   retrieval metrics; no agent decision-time refusal metric.
   <https://arxiv.org/abs/2608.07429>
3. **Library Drift** (arXiv:2605.19576, AWS, FAGEN@ICML 2026) + **Ratchet**
   (arXiv:2605.22148, AWS) — self-evolving skill-library lifecycle: outcome-driven
   retirement, per-skill contribution scores, eviction-margin bounds, judge-reliability
   thresholds for retiring skills. Governance is LIBRARY-side (retirement policy);
   metrics are contribution/pass@1 — not the facing-agent refusal act.
   <https://arxiv.org/abs/2605.19576> · <https://arxiv.org/abs/2605.22148> ·
   <https://github.com/amazon-science/Self-Evolving-Agents-Ratchet>
4. **AFTER benchmark** — Managing Procedural Memory in LLM Agents
   (arXiv:2606.23127, Jun 2026). 382 enterprise tasks; transfer axes (cross-task/
   cross-role/cross-model). No staleness axis at all.
   <https://arxiv.org/abs/2606.23127>
5. Prior sweep still holds: AWM ships recall-by-relevance with documented "procedural
   drift" failure and no refusal metric (<https://arxiv.org/abs/2409.07429>,
   <https://futureagi.com/glossary/agent-workflow-memory/>); τ-bench folds policy
   compliance into pass^k reward, no stale-refusal metric (<https://taubench.com/>);
   Mem0/Letta/Zep-Graphiti/HippoRAG ship recall/bi-temporal facts, nothing procedural
   (see sweep file).

Positioning consequence: "refuses a stale offered procedure, graded against withheld
ground-truth staleness, inside an execution loop" remains an UNCLAIMED metric as of
2026-08-26 — but STALE's Premise Resistance is close enough that claiming novelty in a
deck requires citing it and naming the difference (procedure-level, execution-context,
substrate-audited refusals vs QA-level premise rejection).

## Recommendations (non-blocking)

1. Rephrase the RUN #1 board/log line: keep 6-vs-0 as DESCRIPTIVE system-level result;
   drop or heavily qualify the McNemar p (report "p∈[0.031,0.062], frame-dependent" at
   minimum); never present it as model-level detection evidence.
2. Before headline collection, add a model-decides stale tier (all tasks poisoned-gate
   style, i.e. `substrate_bypasses_gate` everywhere, cards actually offered to the
   model) — otherwise §40's strongest result stays substrate-owned and incomparable to
   STALE's model-level probe. Note refund-003's unparseable rate (1/1) when sizing n.
3. Question #6 (neutral situation overlay) grows in importance: with the gate owning
   refusals, arm-level resolution deltas are the live signal, and those are exactly what
   comprehension-bias contaminates.
4. Consider having the scoreboard print refusal ATTRIBUTION (gate-recorded vs
   model-initiated, separable from C_journal reason strings) beside the stale_refusal
   column — RUN #1 would then self-document caveat 2.

## Artifacts

- Verification script: `.scratch/research/run1_verify.py` (rerunnable; reads the
  integrator checkout's results/spend files read-only)
- Raw data: `C:\Users\user\stealth-lab\experiments\harness\real_arms_results.jsonl`,
  `...\real_spend.jsonl` (gitignored, machine-local)
- Code refs: scoreboard.py:59-63 (usable-tasks rule), scoring.py:63-84 (stale grading),
  mcp_surface.py:81-91 (deterministic gate), openrouter_arms.py:555-567 (auto-refusal
  path), openrouter_arms.py:496-524 (arm B prompt without ids),
  mcnemar_power.py:24-41 (exact test).
