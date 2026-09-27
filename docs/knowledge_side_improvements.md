# Knowledge side: what production does today, and what to change

This document proposes changes to Kel's knowledge side (Goals, Procedures and `find_ways`), grounded in controlled experiments. Routing (`recommend_models` / `report_model_run`) is out of scope: it is the measured win and needs no change from this work.

Evidence:
- [experiments/ds1000/README.md](../experiments/ds1000/README.md), with preregistrations `PREREGISTRATION.md` and `PREREGISTRATION_2.md`;
- [experiments/bigcodebench/README.md](../experiments/bigcodebench/README.md).

All experiments ran on isolated local databases, never on production.

## Summary of the evidence

Measure: gain in solve rate on tasks related to ones Kel had already seen solved. Three open models (gemma-4-31B-it, gpt-oss-120b, deepseek-v3.2), paired design, family-clustered 95% CIs; ns = not significant.

| What the model is given | Round 1 (64 tasks) | Round 2, fresh sample (104) | Round 2, production-like knowledge base |
|---|---|---|---|
| Kel's Procedure (today's product) | +1.6 (ns) | +2.2 (ns) | +1.6 (ns) |
| The *correct* Procedure, handed directly (oracle) | −0.5 (ns) | — | — |
| Kel's Procedure + the verified code | +3.1 (ns) | **+4.5** (pooled significant; missed the per-model preregistered bar) | +2.2 to +3.8 |
| Plain retrieval: nearest past problem + its verified code | **+8.3** | **+9.9** (replicated) | — |

Findings:
- **Retrieval is not the bottleneck for Procedures.** Even the correct Procedure, handed directly, gives nothing: models already know the steps. What transfers is concrete verified code, shown as a worked example.
- **Kel's retrieval answers "is this safe to execute?", not "what would help?"** It returns knowledge only when the judge says it *applies*. Close variants are correctly rejected, and nothing is returned in their place.
  - Traced example, problem 116: the right family was found, then judged "unrelated 0.49" for the Goal and "not_applicable" for the Procedure, so nothing was returned.
  - Plain retrieval showed the same past problem with its code, and the model adapted it and passed.
  - Kel returned knowledge for about half of related tasks; plain retrieval always returns its nearest example.
- **The experiment setup was not the cause.** Rebuilding the knowledge base the production way (embedded Goals, `judge_mode="model"` identity, the real placement worker, agent-written queries) left the gain at +2 to +4. It mainly cut wrong matches on unrelated tasks (10/20 → 3–5/20).
- **Production builds almost no Goal hierarchy for specific-task content.** Placement accepted 1 edge across 118 Goals. The judge correctly calls sibling tasks *distinct*, and nothing creates broader parent Goals.

Caveats:
- Small samples (84 and 124 tasks per round).
- Single-shot coding tasks only; multi-step agent work is untested.
- Public benchmark, so models may have seen it (this affects all arms equally).
- Open-model prices are placeholders.

## What stays as it is

- **The applicability-gated Procedure path:** `find_ways` → Goal choice → Procedure tier (judge: `applies` / `not_applicable`) → evidence-aware selection. It is right for its purpose, telling an agent what it can safely *follow*, and it rarely returns wrong knowledge.
- **Routing.**

## Proposed changes

### 1. Capture verified solutions (prerequisite)
- **Production today:**
  - Procedures are extracted from agent-run evidence (tool sequences, observations, outcome).
  - The code-solution extractor (`CodeSolutionExtractor`, `code_solution_v1@1`) runs only when evidence holds a *verified code solution*, and no production path records one yet.
- **Change:**
  - When an agent reports a finished run, it also sends the final code or diff and the check that passed (tests or verifier).
  - Kel stores this as an **evidence artifact on the Procedure version**. It is not a Procedure "implementation", so the no-implementations design holds.
  - The artifact inherits the run's visibility and privacy scope, is scanned for secrets, and is size-capped.
- **Why:** every measured knowledge gain came from verified code. Without capture, changes 2 and 3 have nothing to return.
- **Decision needed:** Kel would store verified code produced in users' runs, scoped to each run's visibility.

### 2. Return the verified code with each Procedure
- **Production today:** `find_ways` returns the Way, the steps with the APIs they use, and pitfalls.
- **Change:** each Procedure also carries `verified_example`: the code plus a one-line description of what it solved.
- **Evidence:** +4.5 on a fresh sample (+2.2 to +3.8 production-like), against about +2 for the Procedure alone.

### 3. A `related_examples` channel in `find_ways` (largest expected gain)
- **Production today:** if nothing is judged `applies`, the agent gets nothing. This includes every `ambiguous` and `no_match` answer, about half of related tasks.
- **Change:**
  - **Contents:** every `find_ways` answer, whatever its outcome, carries up to 3 **nearest verified solved examples**: the past task's text, its verified code, and its Goal. Each is labelled *"similar solved problem, not verified to apply; adapt"*.
  - **Search:** a new search index of solved examples in the search database (text plus embedding, hybrid search, access-scoped like everything else).
  - **Gate:** a new, looser judgment kind, `task_example` (relevant / unrelated). An example is dropped only when it is clearly unrelated at high confidence. Applicability judging stays for Procedures.
  - **Code locations:** `retrieval_service` (new `search_examples`), the `find_ways` payload, and the `plan_and_run` prompt (one line on how to use examples).
- **Evidence:** plain nearest-example retrieval gave +9.9 (replicated; +8.3 in round 1), about twice Kel's current gain. It helps even when the example comes from a different but similar task.

### 4. Let the judge express "variant"
- **Production today:** `task_goal` answers `matches` / `partial` / `unrelated`. A close variant (row-wise vs column-wise percentages) comes back `unrelated 0.49`, and a low-confidence `unrelated` still eliminates a top-ranked candidate.
- **Change:**
  - Add a `variant` verdict: same kind of task, different specifics. It counts like `partial` when listing candidates and always passes the `related_examples` gate.
  - A low-confidence `unrelated` on a top-ranked candidate no longer eliminates it.
- **Evidence:** the problem-116 trace.

### 5. Tighten "ways on a candidate"
- **Production today:** on an ambiguous answer, a way is listed if any Procedure under the candidate Goal, including its more specific Goals, is judged `applies`. This gave 10/20 wrong matches on unrelated tasks in the original run (3–5/20 production-like).
- **Change:** list a way only if its own source Goal is judged at least `partial` or `variant` for the request.
- **Priority:** low, but cheap.

### 6. Parent-Goal synthesis (defer; decide after round 3)
- **Production today:** `goal_abstraction_placement` only links existing Goals and accepts edges at confidence ≥ 0.9 (`GOAL_ABSTRACTION_MINIMUM_CONFIDENCE`). Specific-task content yields a flat graph, so the hierarchy walk and the "more specific Goals" lookup rarely have anything to use.
- **Change:** when sibling Goals cluster, a model proposes a broader parent Goal as a *proposed* edge for review, or accepted above the threshold.
- **Why defer:** changes 3–4 already recover "similar" knowledge. Build this only if retrieval still misses.

### 7. Fix the batched judge path (production bug, now)
- **Symptom:** identity and placement batches fail in three ways:
  - JEV returns **HTTP 400** on batched identity requests;
  - Gemini returns **malformed JSON** on batch replies;
  - Gemma's key is **rejected (401)**. It is likely a different key from the one working calls use.
- **Impact:** single judgments (`find_ways`) work. Production duplicate detection and hierarchy building are degraded now.
- **Fix:**
  - diagnose the JEV batch payload;
  - add JSON mode or repair for Gemini;
  - fix the Gemma key configuration;
  - add a judge health check to `LAUNCH_RUNBOOK.md`.

### 8. Close the feedback loop (later)
- **Change:** run reports record which returned Procedure or example the agent used and whether the run passed. That becomes evidence for ranking `related_examples`, reusing the existing evidence machinery.

## Order and validation

1. **Change 7:** production-facing, independent of the rest.
2. **Changes 1–4 (+5), behind a feature flag with tests.**
3. **Round 3:** preregistered, on untouched problem families, with a production-like knowledge base (embedded Goals, `judge_mode="model"`, the real worker, agent-written queries).
   - **Primary:** `find_ways` with `related_examples` vs no notes.
   - **Bar to match or beat:** plain nearest-example retrieval.
4. **Then decide on 6, and on enabling 8 in production.**
