# Claim representation

Type: grilling
Status: resolved
Blocked by: 01, 02

## Question

How are claims represented: a dedicated `claims` table, `knowledge_nodes` rows with `node_type='claim'`, or a hybrid?

spec.md explicitly requires the tradeoff be explained *before* implementing, and explicitly says not to over-normalize if the existing `knowledge_nodes`/`edges` architecture can represent this cleanly.

The relevant existing facts:

- `backend/app/services/claims.py` already implements the "node type" option, deliberately without a migration. A claim is a `knowledge_nodes` row with `node_type='claim'`, and truth state (`IN`/`OUT`) lives in `properties` JSONB. Its docstring argues the case: a claim can be `t_valid` (not deleted) but `truth_state='OUT'` (no longer believed), and the two are orthogonal.
- `capture_claim` and `relate_claims` have **zero callers** anywhere in the repo outside `backend/tests/test_claims.py`. Nothing produces a claim today.
- `edges` already supports `SUPERSEDES` natively and `CONTRADICTS` / `CLAIM_OF` via the `custom_edge_type` escape hatch.
- Bi-temporal validity (`t_valid`/`t_invalid`/`t_created`/`t_expired`) is real and enforced on reads across the whole backend.
- `episode_links` is the designated justification pointer and has exactly one writer and zero readers.

spec.md's required claim fields: `claim_id`, owner, subject, predicate, object/value, claim type, status, confidence, temporal validity, provenance, `created_at`, `invalidated_at`, extraction/version metadata. Required relations: supports, contradicts, derived_from, depends_on, supersedes, valid_during, observed_at.

Grill these:

- Subject/predicate/object is a triple. `knowledge_nodes` has `name` + `properties` JSONB and no triple structure. Does shoving a triple into JSONB give up the querying that makes claims useful, or is the query pattern actually "fetch claims for this subject" — which one indexed JSONB path serves fine?
- The existing design's strongest argument is that a claim inherits temporal validity, provenance, embeddings, visibility scoping and graph traversal *for free* from `knowledge_nodes`. A dedicated table re-implements every one of those. Is that argument decisive, or does it just relocate the cost?
- Counter-pressure: `node_type` is untyped TEXT with no registry. Adding claims as a fourth virtual type on a column that already carries three makes the polymorphism load-bearing without making it explicit. What makes that acceptable — or what has to change first?
- If hybrid: what exactly lives in each place, and what keeps them consistent?
- Does the answer here bind the answer for observations (04) and procedures (05)? If claim goes one way and procedure the other, is that incoherent or appropriate?

## Answer

**Representation: ratify and extend `claims.py`'s existing approach** — `node_type='claim'`
on `knowledge_nodes`, not a dedicated table, not a hybrid. Decisive factor: a claim's *access
pattern* (temporal filtering, visibility scoping, provenance, embedding-based retrieval) is
identical to a knowledge_node's, and nothing currently produces a claim (`capture_claim`/
`relate_claims` have zero callers outside `test_claims.py`), so this is a clean-slate call,
not legacy to work around. A dedicated table would re-implement bitemporal validity,
provenance, visibility, and graph traversal from scratch for no behavioral difference. One
concrete fix carried with this decision: `capture_claim()` currently omits `embedding` from
its INSERT (`claims.py:77-81`) — it should set it, so claims actually surface through the
existing retrieval stack instead of being invisible to it.

**The `node_type` counter-pressure is real and gets a real fix, scoped narrowly.** Adopt a
`NODE_TYPE_SCHEMAS: dict[str, PydanticModel]` registry, validated at write time in the
service layer, for `node_type='claim'` going forward — the same pattern ticket 02 established
for `domain_payload`, generalized to `node_type` itself. `ClaimProperties` validates
`subject, predicate, object, truth_state, claim_type, confidence, extraction_version` before
insert. **Scoped to `claim` only** — the other 6 existing virtual types (`failure_mode`,
`hierarchy_group`, `code_location`, `policy`, `policy_document`, `fact`) are NOT retroactively
migrated onto this registry as part of this ticket; that's real but separate cleanup, and
folding it in here would turn a claim-representation decision into an unscoped `node_type`
migration.

**Triple storage**: subject/predicate/object as three keys inside `properties` JSONB
(validated by the registry above), not real columns — confirmed no index exists on
`properties` or `node_type` anywhere in the schema today, so neither representation had a
querying advantage to begin with. Add exactly one expression index matching the actual access
pattern (`episode_links`-driven "why do we believe this," not general triple-store joins):

```sql
CREATE INDEX ON knowledge_nodes ((properties->>'subject')) WHERE node_type='claim';
```

**The ATMS "assumption environment" gap (claims believed only under an explicit
`{repo, commit, branch, dependency_lock_hash, ...}` set, per de Kleer's ATMS, not asserted
unconditionally) is deferred, not built now.** Nothing produces a claim yet, so there's no
real usage pattern to design the assumption-environment shape against — building it
speculatively risks a `tenant_id`-style guess. Moved to the map's fog, to resurface once
observation→claim production actually starts (ticket 04).

**Coherence with observations (04) and procedures (05): no forced uniformity — apply the same
test, not the same answer.** The test: does the concept share `knowledge_nodes`' access
pattern, or does it have a genuinely distinct shape/volume/lifecycle (the same test ticket 06
used to justify three separate tables over one discriminated table)? Observations likely pass
this the same way claims do (→ `node_type`-based). Procedures likely don't (parameter
schemas, verification stats, lifecycle states → probably a dedicated table). Diverging there
would be coherent, not inconsistent, as long as the same test produced both answers.
