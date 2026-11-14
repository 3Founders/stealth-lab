# Search: what answers a task, and which paths use the hierarchy

Status as of 2026-11-13. This is the map of every way a task becomes a Goal, a Way, a tool or a model in this repo,
which of those paths expand the abstraction hierarchy, and where each one is blind. The router's own plan and
milestones live in `docs/goal_router.md`; model routing is `docs/model_routing_plan.md`; the desktop delegation is
summarised here and in the code comments.

## 1. Five questions, five answers

| Question | Entry point | Code | Hierarchy? |
|---|---|---|---|
| Which Goal does this task mean? (judged) | MCP `find_ways`, `GET /v1/goals/choose` | `mcp_server/server.py` -> `services/goal_choice.choose_goal` -> `services/retrieval_service.search_goals` + `_route_goal_candidates` | **yes** |
| Which Goal does this task mean? (fast) | `GET /v1/goals/rank`, `GET /v1/goals/route` | `tool_routing/route_api.py` -> `goal_router.rank_goals` / `route_goal` -> `tool_routing/goal_catalog.py` | no |
| Find a Goal by name, for a person | MCP `search_goals`, `GET /v1/goals/search` | catalog first, then `services/goals.search_goals` -> `retrieval_service.search_goal_candidates_page` | no (both tiers are judge-free) |
| Which Way and tool run it? | inside `find_ways` | `procedures` + each step's `binding` (`tool`, `mcp`, `command`, ...) | via the chosen Goal |
| Which model runs the turn? (desktop) | `POST /api/delegate` | `desktop/lib/delegate.ts` -> `find_ways` -> `chooseModel` | via `find_ways` |

"Hierarchy" means the accepted `goal_relations` edges (`SPECIALIZES` / `GENERALIZES`): a match can be a parent or a
child of the Goal the words alone would pick.

## 2. The judged path: `find_ways` and `/v1/goals/choose`

The pipeline in `services/goal_choice.choose_goal` (stages from `app/utils/stage_timer.py`). `find_ways` is the MCP
tool agents and the desktop delegation call; `GET /v1/goals/choose` is the REST twin.

1. **Candidates** (`retrieval_service.search_goals`): FTS and vector legs over `goal_search_index`, each
   `search_top_k = 20`, RRF-fused, cut to `rerank_top_k = 8`. The leg filter is
   `status IN ('active','candidate') AND has_procedures` -- a Goal with no live Procedure is not a candidate.
2. **Judge** (`_judge_all`): a JEV/NLI judge (Gemma/Gemini fallback) rules each candidate `matches` / `partial` /
   `distinct` against the query *and* any request-scoped repo facts. `min_confidence = 0.6` for a firm match;
   batches of 10, concurrency 4.
3. **Hierarchy expansion** (`_route_goal_candidates`): from the judged candidates (or the top
   `hierarchy_seed_max = 3` when nothing matched), walk accepted edges up to `hierarchy_max_hops = 2`, keeping
   `hierarchy_max_fanout = 8` neighbours per direction and `hierarchy_max_candidates = 32` in total; the top
   `hierarchy_judge_top_k = 8` neighbours go through the same judge.
4. **Combine** (`combine_goal_resolution`): flat and hierarchy candidates compete on the same verdicts. A neighbour
   the judge calls a firm match can be the chosen Goal. The flat result is always the baseline.

Failure modes are explicit and must be reported, never guessed:

| Result | Meaning |
|---|---|
| `matches` | firm judged match(es), up to `goal_resolve_max = 3` |
| `partial` | judged partial, carried as the best available |
| `unjudged` | no semantic verdict answered; the top fused Goals go forward, marked unjudged |
| `none` | judged and nothing matched |

Any answer can also carry `degraded` in `meta`, with the reasons (embedding provider, judge outage, truncation); the
result itself is still one of the four above.

`find_ways` labels the lexical re-ranker fallback when no semantic judge answered. The reply carries
`goal_judgment.hierarchy` (`mode`, `used_flat_fallback`, `paths`, limits), which is how you can tell a hierarchical
answer from a flat one.

Measured on 2026-11-13, `find_ways` for `find vulnerabilities in my dependencies` answered in **2.5 s warm** with
`mode: "hierarchical"`, `used_flat_fallback: false`, and one `SPECIALIZES` hop; a cold process took **8-23 s** for the
same shape of query. The variance is the judge and the graph work, not the transport.

## 3. The fast path: the in-process catalog

`app/tool_routing/goal_catalog.py` keeps a process-wide index of the **curated home shard K000** only:

- `status IN ('active','candidate')` and `home_shard_id = 'K000'` -- no relations, no expansion;
- every `goal_names` alias, plus `WAYS_PER_GOAL = 5` procedures per Goal with their bound cards;
- exact -> alias -> token/IDF matching, all local; `TTL_S = 60`; a failed refresh keeps the old catalog;
- `refresh_now()` rebuilds after a create, so `POST /v1/goals` and MCP `add_goal` are retrievable on the next call;
- a user-created Goal is pinned to K000 (`goal_placement_shards` in `services/shards.py`) precisely because this
  catalog reads K000 and nothing else.

Warm cost is ~3-6 ms for `/v1/goals/rank`; a cold build is ~1 s, and `refresh_now` ~0.65 s (measured 2026-11-13).

**What is left out of the results** (2026-11-14):

- a Way materialized from a tool card (`procedures.provenance = 'tool_card'`, 56,741 rows) is the tool itself, not a
  procedure: the Way list carries only Ways somebody wrote, and the same tool is still offered in `tools`. `route_goal`
  also no longer synthesizes a one-step Way per ranked tool (`procedure_id "tool:<card>"`), which had made the Way list
  a copy of the tool list;
- a registry card whose repository is archived, or whose server is another coding agent (Claude Code, Codex, Cursor,
  opencode, aider, Cline, Copilot, Gemini CLI, Jules, Antigravity ...) or a bridge to one, is never ranked by the tool
  router (`app/tool_routing/catalog.py` `hidden_server`, applied to both the bundled snapshot and the database path).
  The name must look like that agent and the description must say it is a coding agent/CLI: "Eclipse Codex" (banking
  regulation) and "journald cursor" are kept.

**The model: Goal -> ranked Ways -> step bindings** (2026-11-14):

- a Goal's `procedures` are its written Ways, in rank order (verified first, then successes, then `goal_score`, then
  newest); each entry now carries `rank`;
- a Goal's `tools` are the cards **bound in those Ways' steps**, not the separate `tool_card_goals` mapping. A Goal
  with no written Way offers no tool in `find_ways`; a tool nobody's Way binds is found with `discover_tools`;
- a tool step carries `locator`: the server's own endpoint (an MCP URL such as `https://mcp.exa.ai/mcp`). A server that
  needs a sign-in mints the person's link when the tool is first used, and `use_tool` returns it;
- the web-search Goal is the first curated example: `Search the web with Exa`, one step bound to `ai.exa/exa` with
  locator `https://mcp.exa.ai/mcp` (`source_key = curated:exa-web-search`). It is the only Way and the only tool that
  Goal returns.

`route_goal` (used by `/v1/goals/route`) tries the catalog first and only falls back to the DB legs
(`_exact` + `_k000_full_text`) when the catalog itself failed. The global search group is a second opinion only when
`GOAL_SEARCH_GROUP=on` (or the catalog is unavailable), because that check was a round trip on the hot path.

## 4. Which paths actually use the hierarchy

| Path | Hierarchy expansion | Notes |
|---|---|---|
| `find_ways` (agents, desktop delegation) | **yes** | judged candidates + bounded expansion, flat as baseline |
| `GET /v1/goals/choose` | **yes** | same `choose_goal` pipeline as `find_ways` |
| MCP `search_goals` | no | catalog first, then `services/goals.search_goals`; both tiers are judge-free |
| `GET /v1/goals/rank` | no | catalog only, by design (sub-second tier) |
| `GET /v1/goals/route` | no | catalog only unless the catalog failed |
| `GET /v1/goals/search` (REST) | no | judge-free candidate legs (`search_goal_candidates_page`) |

Only `choose_goal` expands. Everything a person reaches by typing a name -- `search_goals`, `/search`, `/rank`,
`/route` -- is flat, and says so in its own way (`matched_by`, `resolution`).

Two consequences worth knowing:

- `search_goals` never expands, so a parent or child of the right Goal is invisible there even when the judged path
  would find it. The catalog covers 30 Goals; the judged path covers every indexed Goal with a live Procedure.
- `/rank` and `/route` never expand either. That is a deliberate latency trade, not an accident; unifying them is an
  open item (section 7).

## 5. Coverage today (measured 2026-11-13)

| Thing | Count |
|---|---|
| Goals on K000, `active` | 30 (all `provenance='tool_goal_taxonomy'`) |
| Rows in `goal_search_index` | 20,668 |
| ... on K001-K008 (`candidate`, `public`) | 20,638 |
| Procedures | 56,741 (all `candidate`, all linked to a Goal) |
| Procedure embeddings | 0 |
| `task_runs` | 5 |
| `tool_observations` | 367 |
| Tool cards with 1024-d embeddings | 48,124 |

So the catalog sees **30 of 20,668** Goals. The judged path can see more, but only Goals with `has_procedures`, and
the semantic legs are thin: 0 procedure embeddings and 5 task runs mean the hierarchy and the judge carry almost all
of the quality today. Any first version of a learned router is similarity + prior until outcomes accumulate.

## 6. The desktop delegation (a consumer of the judged path)

Flow, all in `desktop/`:

```
page.tsx  --POST /api/delegate {text, models}-->  lib/delegate.ts
                                                     |-- lib/mcp.ts: initialize + tools/call `find_ways`
                                                     |-- chooseModel: security or unverified Way -> strong rung
                                                     `-- brief: task + Goal + Way + one tool (no files, no history)
page.tsx  --POST /api/prompt {..., model, plan}--->  app/api/prompt/route.ts -> session.prompt({model})
right panel "Delegated"  <-- goal | way | tool | model | why | brief
panel "Ask"  --POST /api/delegate/ask------------>  generate.text({prompt: brief + question, model})
```

- The turn runs on the routed model, so the chat model is the delegated model.
- **The Way's tool does the work, and the turn may not duplicate it.** When the plan has a Way bound to a tool,
  `app/api/prompt` puts a contract into the request and the session instruction: call
  `kell_discover_tools(server="<Way>")` then `kell_use_tool(server="<Way>", name="<tool>", ...)`; do not re-implement
  it with shell, scripts or direct HTTP; if the tool cannot do the job, say so and stop. Without the contract the model
  will find a convenient endpoint and do the scan itself while the panel says the Way ran (observed 2026-11-13: the OSV
  Way was chosen and the turn scanned 15 lockfiles against OSV's batch API instead). The same rule is in
  `lib/tools.ts` for subagents and `kell_delegate`: a handed-off sub-task is not redone in parallel.
- **Status updates are a stream.** `POST /api/delegate` (with `Accept: text/event-stream`) reports `ladder` ->
  `routing` -> `goal` -> `model` -> `plan`; the panel and the chat show each line as it arrives. The scoped call
  reports `sent` -> `waiting` every 2 s -> `answer`. JSON is still available for scripts.
- The message waits at most **2.2 s** for the plan; a late plan still fills the panel. The route caches plans for
  **5 minutes** (`POST /api/delegate`), so a repeat is instant (measured 0.12 s cached vs 2.5-23 s cold).
- The scoped side call sees only the brief: no project path, no files, no shell, no chat history.
- The model ladder comes from `GET /api/settings` (the gateway's list); with no list the plan says the engine's
  default runs the turn. The route asks Settings itself when the page has none.
- A Way bound to a tool (OSV, and Hinoki when P1-09 lands) is named in the panel. The app does not invent a call for a
  tool it has not been given.

## 7. Open gaps

1. **Unify the fast and judged paths.** `/rank` and `/route` should at least read the hierarchy projection, or say
   "flat" in the answer, so the website and desktop Route pages cannot silently miss a parent/child.
2. **Catalog coverage.** 20,638 candidate Goals on K001-K008 are invisible to the catalog. Either build the catalog
   from `goal_search_index` (with visibility/owner filtering) or keep the curated 30 and say so on the page.
3. **`search_goals` is flat.** A name search never expands, so a parent/child of the right Goal is invisible there.
   Either route a weak or empty answer through `choose_goal`, or expose a `hierarchical=true` flag and pay the judge.
4. **`has_procedures` gate.** A Goal with no Way is invisible to the judged path (a new Goal, before its first
   procedure). That is intentional for routing but surprising for search.
5. **Zero procedure embeddings.** The vector leg is name/description only until procedures are embedded.
6. **Model routing.** `chooseModel` is a local ladder heuristic. The per-cluster router
   (`docs/tool_marketplace_design.md`) replaces it when the outcome data exists.
7. **Hinoki.** P1-09 is blocked on the scanner's API base and request schema; nothing calls it until that is known.

## 8. How to check it

```powershell
# The whole loop in one script: add a Goal, prove the catalog ranks it, stream the delegation plan,
# and optionally ask the delegated model (needs the desktop server and its engine):
cd backend
python scripts/test_delegation.py --goal "Send a Slack message" --rationale "the team lives in Slack" `
  --objective "the message is posted" --task "find vulnerabilities in my dependencies" `
  --ask "what did you check?" --url http://127.0.0.1:3400

# The judged path, with the MCP sign-in the desktop already has (no token printed):
#   initialize -> notifications/initialized -> tools/call find_ways
# desktop/lib/mcp.ts is the reusable implementation of those three steps.

# The fast path (website sign-in, not an MCP token):
curl.exe "http://127.0.0.1:8000/v1/goals/rank?q=send%20a%20slack%20message&k=5"
curl.exe "http://127.0.0.1:8000/v1/goals/route?q=find%20vulnerabilities&k=3"

# The desktop delegation plan (dev server on 3400):
curl.exe -X POST http://127.0.0.1:3400/api/delegate -H "content-type: application/json" `
  -d '{"text":"find vulnerabilities in my dependencies","models":{}}'
```

What to look for: `find_ways` -> `goal_judgment.hierarchy.mode` and `used_flat_fallback`; `/rank` -> `matched_by`
(`exact` / `alias` / `fts`) with no hierarchy field; `/api/delegate` -> `goal`, `way`, `tool`, `model`, `why`, `brief`.

## 9. Code map

| File | Job |
|---|---|
| `backend/app/mcp_server/server.py` | `find_ways`, `search_goals`, `add_goal`, `recommend_models` |
| `backend/app/services/goal_choice.py` | judged choice: flat + hierarchy + combine |
| `backend/app/services/retrieval_service.py` | legs, judge, hierarchy expansion, `RetrievalConfig` |
| `backend/app/services/hierarchical_goal_routing.py` | edge walk and bounded expansion |
| `backend/app/tool_routing/goal_catalog.py` | the flat in-process catalog |
| `backend/app/tool_routing/goal_router.py` | `rank_goals`, `route_goal`, ways and tools |
| `backend/app/tool_routing/route_api.py` | the HTTP/desktop shape of `/rank` and `/route` |
| `backend/app/services/goals.py` | `search_goals` (store), `create_goal_from_user`, placement |
| `desktop/lib/delegate.ts`, `desktop/lib/mcp.ts` | the desktop plan and the MCP client |
| `desktop/app/api/delegate/**`, `desktop/app/page.tsx` | the plan/ask routes and the Delegated panel |
