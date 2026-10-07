# .stealth library: this repository's own solved problems and model routes

Workstream B of [plan_2026-10_priors_library_survey.md](plan_2026-10_priors_library_survey.md) (§3, §5.3).

## Why

The evidence (plan §0): same-repo past fixes with their real diffs lifted "right cause" from 27 to 42 of 96
(p=0.023); steps-only knowledge did nothing; repo-first retrieval found useful Goals 22-24% of the time vs 10%
over the whole corpus. So a repository's own solved problems, with their code, are the core; global knowledge
enhances them.

## Files (`.stealth/`)

| file | what | committed? | written by |
|---|---|---|---|
| `SUMMARY.md` | ≤ 3 KB, read whole: repo, facts per topic, units (grouped above 12), newest library entries, routes, how to look things up | no (generated) | `library index` |
| `library.md` | GOAL / PROC / STEP lines, one entry per random id `L-xxxxxx` | yes (`merge=union`) | `library add`, survey history mining |
| `library/solutions/<id>.diff` | the diff that solved the entry | yes | `library add` |
| `library/archive-<yyyy-mm>.md` | entries moved out when library.md passes 64 KB (oldest verified first) | yes | `library add` |
| `index/library.idx` | `id|status|outcome|unit|g|verified_at|start|end|block_sha|title`, with `source_sha` | no | `library index` (self-healing) |
| `index/terms.idx` | `term|ids`: touched paths, file names, symbols from the diff, title words, tags, unit | no | `library index` |
| `routing.md` | ROUTE lines (server-rendered from `model_plan`) and OBS counts per route/model | no (per machine) | hook, `library route` / `obs`, capture hook |

The grammar lives in `backend/app/stealth/library.py` (server side: parse what the client sends, render ROUTE
lines) and `packaging/npm/lib/library.mjs` (client side: everything that touches files and git). Shared fixtures in
`packaging/npm/test/fixtures/library` hold both to the same canonical text, idx and parse.

Robustness rules: lines belong to an entry by id prefix, never position, so a `git merge` with `merge=union`
parses to every entry; conflicting versions of one line resolve the same way in every reader (later
`verified_at`) and are reported; free text is percent-escaped reversibly (a `check=` with a shell pipe survives);
line ranges are disposable (the idx is trusted only while its `source_sha` and each block's hash match).

## find_ways (all optional; without them the reply is unchanged)

- `library_rows` (the idx, ≤ 64 KB): up to 6 entries (preselected by word overlap; failed attempts skipped) are
  judged by the same contextual judge in the same batch as the global top 8 — in addition, never instead.
  Matches return as `library_matches`. A global Goal an entry names (`g=`) is fetched and judged even below the
  fused cut, sorts first at equal confidence, and breaks an `ambiguous` tie when it is the only library-named Goal
  in the tie (`goal_judgment.tiebreak`).
- `repo_identity` (`{repo_id, public_name?, strength?}` from `meta.json`, written by the survey scanner): a strong
  identity also judges the 3 of this repo's own global Goals nearest the request (public benchmarks of the same
  `owner/name` when the user shares the name; private Goals scoped `repository` = the hashed id). A weak identity
  (`p:` id or `strength: weak`) is never used to match other Goals. Index: `db/146_benchmarks_repo_index.sql`.
- `route_obs` (ROUTE + OBS lines, ≤ 16 KB): OBS counts of routes whose `g` is the resolved Goal condition the model
  plan; the reply adds `routing_rows`. The conditioning is importance reweighting of the stored, aligned posterior
  draws by the binomial likelihood of the local counts (each attempt its own instance, check model included) —
  same model and latents as `fit.local_refit`, without its ~45 s NUTS run and without persisting one user's counts
  to the shared store. `model_plan.local_evidence.ess` reports how far the local counts moved the posterior. The
  counts are kept with the decision (counts only) so `report_result`'s next rung uses them too.

Privacy: like `repo_claims`, these are request-scoped — not logged, not stored (except the counts above). The
governor's cache key includes `repo_identity` and the library rows, so a library-less cached reply is never
reused for a library request. No diff, step text or check command ever leaves the machine.

## Client

- The knowledge hook (`hook-prompt`) sends `library payload` automatically, shows `library_matches` first with the
  entry's steps and its local diff, and saves `routing_rows` into `routing.md`.
- The capture hook counts the final test verdict of a resolved prompt as one OBS on that Goal's route (no token
  needed, local only, only where `.stealth/` exists).
- `stealthlab-mcp library index|check|show|refresh|add|route|obs|payload` (see `library help`).

## Not done / open

- Quality not yet measured end to end with the library in place: that is Workstream D's job (local-only vs
  +enterprise vs +global arms).
- `library_matches` preselection is lexical (word overlap) before the judge; a paraphrased title with no shared
  words is not judged when the library has more than 6 usable entries.
- `ALLOWED_SNAPSHOT_FILES` (encrypted project sync) does not include `library.md` yet — a product decision.
- Syncing a library entry to the global corpus is still `report_discovery` / `submit_way`, by hand.
