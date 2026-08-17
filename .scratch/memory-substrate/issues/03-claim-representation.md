# Claim representation

Type: grilling
Status:
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
