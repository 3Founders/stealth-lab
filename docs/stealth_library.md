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

## Two layers: knowledge and solved-here (2026-10-08)

`library.md` holds a reusable **knowledge** layer above the solved entries:

| line | what |
|---|---|
| `G|G-…|<title>|parent=<G-id or ->|g=|unit=|tags=` | a Goal; `parent=` builds a hierarchy (`stealthlab-mcp library goal <G> --parent <G>`) |
| `W|W-…|<name>|goal=<G-id>|p=|v=|code=` | a Way (procedure) that achieves the Goal, reusable across problems; `code=` its semantic code (docs/routing_priors.md) |
| `S|W-…:<k>|<kind>|<do>|check=` | the Way's steps |

- **Links:** each solved entry links to the layer: `goal=<G-id>` on its `GOAL` line, `way=<W-id>` on its `PROC` line.
- **Ids are content hashes:**
  - the same normalised title is one Goal;
  - the same step list (kind, text, check) is one Way.

  Two machines that learn the same thing write identical lines, so `merge=union` stays clean.
- **What links entries:**
  - `library add` (or `--goal` / `--way` to attach to an existing one);
  - the survey's history mining;
  - `stealthlab-mcp library link`, which migrates an older file.
- **Size budget:** when the knowledge lines push `library.md` past 64 KB, the oldest entries are archived and
  Goals / Ways nothing uses any more are dropped. A Goal another kept Goal names as its parent stays.
- **Routing:** a plan for a matched entry is keyed to its **Way**: `task_features` names the entry's Goal and
  Way, plus the Way's median fix size. Every problem this repo solved the same way therefore shares one routing
  record and one prior.

## Keeping it clean: `lint`, `tidy`, `drop`, `merge-goals` (2026-10-09)

A library that only grows fills with near-duplicates and dangling links (the same fix recorded from two sessions, a
Goal whose parent was dropped), and the 64 KB cap then archives by age, not by redundancy. Agent memories that stay
useful do one thing on a schedule: merge duplicates, drop what is outdated, repair broken links, keep the entry file
short (supermemoryai/memoryrepo's periodic "dream"). This is the deterministic half of that, local and with no server
or extra spend. It finds the work and shows the evidence; **the agent decides** and runs the commands. Nothing in
`lint` or `tidy` edits `library.md`.

| command | what |
|---|---|
| `library lint` | errors: dangling `goal=` / `way=` / `parent=` links, a Way with no Goal, parent cycles, parse problems (orphan lines, conflicts); warnings: a missing diff file, library.md over 90% of its cap; info: unused Goals / Ways. Exit 1 on an error. |
| `library tidy [--write]` | the worklist, each item with its evidence and the command to run: duplicate entries (same unit, title overlap ≥ 0.7, or ≥ 0.4 when the touched files overlap ≥ 0.5; failed attempts never), duplicate Goals (title overlap ≥ 0.75), Goals that look like a narrower version of another (give them a parent), stale entries, and, from 75% of the cap, what to archive first (failed and stale entries; current mined history is not a reason). `--write` saves it as `.stealth/library/TIDY.md` (generated; not committed). |
| `library drop <L-id> [--superseded-by <L-id>]` | move an entry to `library/archive-superseded.md` (same grammar, greppable, not in the index, so find_ways never offers it), recording what replaced it and when. Its diff stays. Goals and Ways nothing uses any more go with it. |
| `library merge-goals <keep> <drop>` | the same problem under two titles: entries, Ways and child Goals move to `<keep>`; the global goal link and tags are carried over; a merge that would make a Goal its own ancestor is refused. |

Rules the worklist (and `instructions`) tell the agent: search `terms.idx` / `library.idx` before writing and attach with
`--goal` / `--way` instead of adding a near-duplicate; keep each fact in one place; the most recent verified entry wins
unless its diff shows the older one is the correct one; change `library.md` only through these commands; finish with
`library check` then `library lint`.

Honest limit: `library.md` merges by union, which keeps every line from both sides, so a `drop` or `merge-goals` made on
one branch is undone when the other branch's copy is merged in. Nothing breaks: the duplicate is listed by `tidy`
again and the same command removes it again. Run `tidy` after merging.

On this repository's own mined history (1,053 commits, 136 fix entries) `lint` is clean apart from the 100%-of-cap
warning and `tidy` finds one duplicate pair (two Gemini small-budget headroom fixes), no noise. Thresholds are
candidate filters, not verdicts: tune them against a real library before trusting a count.

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
- `stealthlab-mcp library index|check|show|refresh|add|route|obs|payload|lint|tidy|drop|merge-goals` (see `library help`).

## Not done / open

- Quality not yet measured end to end with the library in place: that is Workstream D's job (local-only vs
  +enterprise vs +global arms).
- Done 2026-10-08:
  - `library_matches` preselection stems words and splits camelCase / snake_case / paths. When the overlap
    leaves room, the judge budget is filled with the most recently verified entries, so a paraphrased title is
    still judged. The judge decides; overlap only orders the budget.
  - `library.md` is in `ALLOWED_SNAPSHOT_FILES` (encrypted project sync). Its diffs and `routing.md` are not.
  - `stealthlab-mcp library share <L-id>` drafts the `submit_way` call that offers an entry to everyone: the
    problem, the way and its steps with checks. It never includes the diff, touched paths, unit or route. The
    agent fills the judgement fields and sends it.
  - `stealthlab-mcp plan validate` checks `procedures.md` and `run.md`: ids, `step=` / `claims=` / `deps=`
    references, no cycles, and a concrete `check=` on every node.
  - One routing path. The prompt hook sends `my_model` (the session's model) and `candidates` (the other Claude
    models a Claude Code subagent can run; `STEALTHLAB_HOOK_CANDIDATES` replaces the list,
    `STEALTHLAB_HOOK_ROUTING=off` turns it off). The plan is shown to the agent, and the capture hook reports the
    outcome with `report_result` on that plan's `instance_key`. `plan_and_run` no longer mentions
    `recommend_models` / `report_model_run`; they stay callable for existing callers.
