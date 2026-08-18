# Substrate / domain seam

Type: grilling
Status: resolved
Blocked by: 01

## Question

Where exactly is the line between the **general memory substrate** and the **IDE/coding domain** that sits on top of it?

spec.md wants a general-purpose experiential and procedural memory system with an agentic-coding environment as merely the first concrete domain. It also warns against hardcoding state to code repositories, because personal/mobile state must eventually fit. But it equally warns against premature abstraction. The seam has to be drawn somewhere specific.

Decide, for each of the core concepts — episode, event, observation, claim, state, procedure, procedure execution, evidence — which parts are substrate and which are domain:

- Does the concept have domain-specific **columns** (e.g. `repository`, `branch`, `commit_sha` on an episode), or does the domain ride entirely in a typed JSONB payload with a `domain` discriminator?
- If JSONB: what is the registration mechanism for a domain's payload schema, and how does anything validate it? The repo already has a cautionary tale here — `knowledge_nodes.node_type` is a bare TEXT column with no enum and no registry, and it has quietly accumulated `claim`, `failure_mode` and `code_location` as virtual types.
- If columns: what stops the coding domain from permanently shaping the substrate?

Grill these specifically:

- Is a second domain actually coming, or is "general-purpose" aspiration that will cost complexity now and never be exercised? The `tenant_id` precedent in this repo is exactly the failure mode: a column on every table, filtered by zero queries, and multi-tenancy that was decorative. What would make the general/domain split *not* be the next `tenant_id`?
- Which is cheaper to fix later — a substrate that turned out to be too coding-specific, or one that turned out to be needlessly abstract?
- Where do the existing domain-shaped services live relative to this seam: `call_graph.py`, `code_index.py`, `patch_format.py`, `graph_ingest.py`? These are unambiguously coding-specific and already sit inside `backend/app/services/`.

The answer must name the package/module boundary concretely, not just the conceptual one.

## Answer

**A second domain is a design constraint, not a scheduled build.** Nothing in spec.md or the
map commits to a second domain shipping; spec.md's warning against hardcoding to code repos
is a constraint on how domain #1 (coding) gets built, not a roadmap item. Accordingly: no
second-domain adapter gets built now. But the substrate-generic columns already cost nothing
extra to keep generic (id, provenance, bitemporal validity, embedding, links are already
domain-agnostic on every existing table), so the seam still gets drawn for real. The risk
isn't a wasted second domain — it's coding vocabulary silently colonizing the substrate the
way `node_type` already has (7 undisciplined virtual types, no registry, confirmed in
ticket 01's inventory). `tenant_id` is the opposite failure mode (a column built for a domain
that never needed it) and is the reason we don't go further and build actual second-domain
infrastructure.

**Representation mechanism: option A, uniform discriminator + registry**, applied to every
concept that holds concrete domain-shaped facts (episode, the trace-header table and
`trace_events` from ticket 06, state, and procedure once ticket 05 settles it). Concretely,
each such table gets two additional columns:

```
domain           TEXT    -- e.g. 'coding'
domain_payload   JSONB   -- validated shape, specific to that domain
```

validated at write time against a single registry, keyed by `(concept, domain)`, of Pydantic
schema classes — living in the service layer the same way `claims.py` lives today, not a
database-level constraint (no ORM/Alembic in this repo, so this is application-code
discipline, same idiom as `precondition_gate.py`'s Rule-1 checks). Example:

```python
DOMAIN_PAYLOAD_SCHEMAS: dict[tuple[str, str], type[BaseModel]] = {
    ("episode", "coding"): CodingEpisodePayload,
    ("trace_header", "coding"): CodingTraceHeaderPayload,
}
```

`CodingEpisodePayload` carries `repository, branch, commit_sha_before, commit_sha_after,
files_touched, cwd` — exactly the fields ticket 06 left as "this repo's own namespace,
mechanism TBD." **That fog item is resolved by this answer**, not left open: those fields are
`domain_payload` under `domain='coding'`, validated against `CodingEpisodePayload`, not bare
columns and not an unvalidated blob.

**Concepts that do NOT get `domain`/`domain_payload`**: claim, observation, evidence,
procedure execution. These are statements *about* domain-shaped objects (a claim references
episodes/task_nodes via edges) rather than holders of domain-shaped facts themselves — they
stay fully generic. `claims.py`'s existing design (statement + edges, no domain column)
already matches this without any change required.

**Package boundary: named now, moved later.** `backend/app/services/coding/` is the home for
the 7 unambiguously coding-specific files (`call_graph.py`, `code_index.py`, `code_review.py`,
`patch_format.py`, `sandbox.py`, `sandbox_executor.py`, `failure_capture.py`). The 6 mixed
files (`agent_decision.py`, `agent_promotion.py`, `agent_review_orchestrator.py`,
`content_diff.py`, `execution.py`, `method_library.py`) are left unclassified — they don't
cleanly belong to a single seam and forcing a call now would be a guess, not a decision.

**Not a further map ticket.** The physical file move is pure implementation with no further
decision riding on it — per the map's own rule ("the map plans, it does not build," ticket 01
being the sole exception), it doesn't get a ticket. It's a follow-up TODO for whoever
implements this: move the 7 named files into `backend/app/services/coding/` once
implementation starts.
