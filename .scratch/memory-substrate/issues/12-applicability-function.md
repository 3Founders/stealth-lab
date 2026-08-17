# Applicability function

Type: grilling
Status:
Blocked by: 05, 10

## Question

How is `applicability(P, S_current)` represented and evaluated?

spec.md is emphatic that reuse must **not** be based solely on semantic similarity. Applicability is a combination of: explicit preconditions, current state, scope, exclusions, temporal validity, environment compatibility, procedure verification status, semantic similarity, and relevant local graph neighbourhood. "Embeddings find analogues. The graph and state determine relevance."

The relevant existing facts:

- Today's entire precondition machinery is `backend/app/services/precondition_gate.py`: preconditions and postconditions are **short lowercase string tags** in JSONB, compared by Jaccard overlap against a 0.25 threshold. Not logical predicates, not evaluated against any world state.
- The gate **passes trivially when either side is empty**. Combined with the next fact, this means it is almost always a no-op.
- **Nothing upstream produces postconditions automatically.** `decomposition.py` says so in-source: there is no LLM-extraction step, so the tags only work when a caller hand-supplies them. The only real producer in the repo is `call_graph.py`-derived `touches:<file>` tags in `symbolic_htn_agent.py`.
- The HTN agent has real precondition/postcondition hooks, but the shipped implementations are: precondition = **advisory only, never fails** (it just writes a `path_hint`), postcondition = a hard gate that runs tree-sitter and refuses completion on syntax errors. Both are useful; neither is a logical predicate over state.
- `reuse_detection.py` uses raw cosine with thresholds (0.90 full, 0.70 partial) deliberately, because reuse needs a number with a threshold rather than a fusion rank.
- `TypedPreconditionHTNAgent` exists as an opt-in strict call-graph gate, and `htn_agent.py` explicitly disclaims having any SMT/Z3 machinery.

Decide:

- What is a precondition, concretely? A string tag, a structured predicate over state fields, executable code, or an LLM judgement? Each has a different cost, a different failure mode, and a different producer problem.
- How do the nine factors **combine**? A weighted score, a hard filter chain, or a filter chain with a scored tail? spec.md's ordering implies hard constraints first and similarity last, but does not say what happens when a hard constraint is simply unknown.
- What are `scope` and `exclusions` in representation terms, and who narrows them? spec.md's failure classification says a scope violation should narrow scope or exclusions — so they must be machine-writable, not just human-authored.

Grill these:

- **The producer problem is the real problem.** The repo already has precondition machinery that is inert because nothing generates the tags. What stops this design from repeating that exactly? Name the producer for every field of the applicability check before designing the check.
- Trivially-passing gates are worse than no gates, because they look like safety. Should an unknown precondition *fail closed* (procedure not applicable) or *fail open* (fall back to similarity)? Fail-closed makes the system useless early, when nothing has preconditions; fail-open makes it silently similarity-only, which is what spec.md forbids.
- What is the honest minimum for milestone 1 — and is a measurable "false reuse rate" (spec.md lists it as a metric) more valuable than a sophisticated applicability model that nobody can validate?
- What does "relevant local graph neighbourhood" mean as an actual query, and is it distinguishable from ticket 14's locality work, or is it the same thing?
