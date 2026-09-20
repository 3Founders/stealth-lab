# Two tiers: local cache vs canonical knowledge

There are two kinds of ingestion. They must stay separate.

| | **Local tier** | **Canonical tier** |
|---|---|---|
| What | `.stealth/*.md` files, edited by people/agents, driven through the simple local MCP frontend | Postgres (control DB + knowledge shards), workers, projections, JEV/NLI |
| Nature | a disposable, human-readable **cache/projection** of what the user is working with | the shared, deduplicated, sharded, auditable knowledge base |
| Needs the network / models? | **No.** Plain files; works offline | Yes (embedding + JEV/NLI); fails closed when the judge is down |
| Sharded? Judged? Projected? | No | Yes |
| Code | `app/stealth/*` (generator, local_sync, pipe_format), the local MCP frontend | `app/ingestion/*`, `services/{goals,procedures,claim_identity,procedure_identity,identity_resolution,retrieval_service,search_projection,shards,object_storage}` |

## The bridge

The only way local content enters the canonical tier is an explicit, per-item **selective sync / publish**
(`stealth/local_sync.py`, `services/publication.py`). From that moment it is just another *source* for the
canonical pipeline. Rules this pass enforces so the two tiers cannot damage each other:

1. **The local tier never depends on canonical infrastructure.** Nothing in this pass touches the `.md` files, the
   projection generator or the local MCP frontend. Local retrieval keeps working with no worker, shard or model.
2. **Local claims flow INTO canonical retrieval as input.** `find_best_way` / `search_procedures` accept the selected
   local Claims (≤ 12) as query context; they are never written back by retrieval.
3. **Private/org rows never leave the home shard** (`shards.writable_shards(visibility != public)`), are never
   compared with public rows for dedup (claims, goals), and are never attached as provenance to a public row.
4. **A user's private write is never blocked by a model outage.** For `visibility != public`, judged identity falls
   back to "create and record `judge_unavailable`" instead of raising. Public canonical ingestion fails closed.
5. **Judged Procedure identity is opt-in per adapter** (`capture_procedure(procedure_dedup=True)`): enabled for the
   canonical adapters (skill ingestion, publication, the bundle handler); NOT enabled for `local_sync`,
   `trajectory_semantics` or `procedure_extraction` (episode/local-project tier).
6. **What changed for the local sync path anyway:** because `local_sync` calls the shared writers
   `find_or_create_goal` and `capture_procedure`, its private rows now get the atomic Procedure→Goal link, are
   projected (with their `visibility`/`owner_id`, so retrieval filters them), and use the model judge for Goal
   identity when one is configured (never blocking, per rule 4). It does not use sharding (rule 3).

If the local cache should become fully DB-independent (pure `.md` + local search), that is a separate decision: the
existing `.stealth/` design deliberately treats the files as a *projection of Postgres*, and `local_sync` as the way back.
