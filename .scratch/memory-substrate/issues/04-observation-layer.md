# Observation layer

Type: grilling
Status:
Blocked by: 01, 02

## Question

How is the observation layer between raw events and claims represented, and what produces observations?

spec.md's example chain: raw event `Edit file X` → observation `Authentication implementation was modified` → claim `User commonly validates authentication changes immediately after editing`. Observations must retain source event IDs, episode ID, extractor/model, confidence, timestamp, provenance, and extraction version — and observations are explicitly **not** automatically facts.

The relevant existing facts:

- **No observation layer exists.** Nothing in the repo interprets events semantically.
- `episodes` is effectively write-only: one writer (`loop.py`, debate transcripts only), zero readers. `episode_type` permits `document` and `trace` but nothing ever writes them.
- The closest existing analogue is `failure_capture.py`, which stores the model's own `subgoal_failed(reason)` sentence as a `knowledge_nodes` row rather than the raw trajectory — with an explicit rationale that "a 20-30 call transcript is mostly list_dir/search noise and embeds badly." That is an observation in all but name.
- `hierarchy.py` accepts a `summarizer` parameter but every caller gets `_default_summary`, a deterministic string concat. No LLM summarizer is passed anywhere in the repo.

Decide:

- Own table, `knowledge_nodes` node type, or a property on the episode? (Coordinate with 03 — the answers should be coherent, not necessarily identical.)
- What is the cardinality? One observation per event, per event group, per episode? spec.md says "a semantic interpretation of one or more events," which means the source-event link is many-to-many and needs somewhere to live.
- **What actually produces observations?** This is the ticket's sharpest question. spec.md demands deterministic local processing first and forbids making every tool call trigger an LLM call. So: which observations are derivable deterministically (files touched, tests run and their outcome, commands executed, commits made), and which genuinely need a model?
- `extraction version` is required on every observation for replay. What is versioned — the extractor code, a prompt, a model id, or all three as a compound key?

Grill these:

- Is the observation layer earning its place, or is it a layer of indirection between events and claims that a claim extractor could read events directly to avoid? What breaks if observations are skipped?
- Confidence on an observation: spec.md says justification is canonical and confidence derived. What is an observation's confidence *derived from*, when it is the first interpretive step and has no supporting evidence beneath it but the events themselves?
- If the deterministic extractors cover most of the value, does the LLM extractor belong in milestone 1 at all?
