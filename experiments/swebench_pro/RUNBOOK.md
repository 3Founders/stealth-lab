# Running SWE-bench Pro end to end (ingest → sweep → grade)

The full operational path: get the dataset, stand up the graph DB, ingest it,
run the agents, read the result. Everything here was exercised for real this
session — the sequence, the flags, and every failure mode in
[Troubleshooting](#troubleshooting) were hit and fixed against a live run, not
written from reading the code cold. Where something is inferred rather than
personally re-verified end to end from a completely empty machine, it's
flagged.

For *why* the experiment is shaped this way — the three arms, the DAG agent,
the holdout mechanism, the statistics — see [`GRAPH_EXPERIMENT.md`](GRAPH_EXPERIMENT.md).
This file is the runbook, that one is the design doc. `README.md` and
`DESIGN_EXPLAINED.md` describe the earlier, smaller pilot (`run_experiment.py`)
that this superseded — not what's covered below.

---

## 0. Prerequisites

- **Docker Desktop running.** Not just installed — actually started. Both the
  Postgres container and every grading container need it. If any command
  below fails with `failed to connect to the docker API at
  npipe:////./pipe/dockerDesktopLinuxEngine`, this is why — start Docker
  Desktop and retry.
- **Python 3.13**, repo's existing `backend/requirements.txt` installed
  (`agent.py`/`htn_agent.py` import `app.services.*` directly, so the backend
  env is what these scripts run under, not a separate one).
- **API keys**: `GENERAL_COMPUTE_API_KEY` (the agent's LLM calls) and
  `VOYAGE_API_KEY` (embeddings) in `backend/.env`. Also set
  `USE_GENERAL_COMPUTE=true` — the client construction in
  `run_graph_experiment.py` calls `settings.require("general_compute_api_key")`
  directly regardless of that flag, but other code paths in the backend gate
  on it, so set it for consistency.
- ~**5-10 GB free disk** for the dataset cache plus whatever grading images
  accumulate mid-run (`remove_image` cleans up after each instance, but a
  crash mid-sweep can leave one behind — `docker images` to check, `docker
  image prune` to clear).

## 1. Start Postgres

The experiment uses a **dedicated** database (`stealthlab_swebench`), separate
from the backend's own `DATABASE_URL` target — same server, different
database, so ingesting a few hundred SWE-bench instances never touches
production ontology data.

**If the container already exists** (check `docker ps -a --filter
name=stealthlab-pg`):

```bash
docker start stealthlab-pg
```

**If it doesn't** (fresh machine — this exact command is inferred from the
DSN in `graph_ingest.py:73`, not independently re-verified against a truly
empty machine this session):

```bash
docker run -d --name stealthlab-pg \
  -e POSTGRES_PASSWORD=stealthlab \
  -p 5433:5432 \
  pgvector/pgvector:pg17
docker exec stealthlab-pg psql -U postgres -c "CREATE DATABASE stealthlab_swebench;"
```

Then apply the schema (safe to re-run — the migration runner has its own
ledger, ticket 17/memory-substrate map):

```bash
cd backend
DATABASE_URL=postgresql://postgres:stealthlab@localhost:5433/stealthlab_swebench python3 scripts/migrate.py
```

Confirm it's actually up before continuing — `docker ps --filter
name=stealthlab-pg` should show `Up`, not `Exited`. This container being
stopped (Docker Desktop closed, machine restarted) is one of the two most
common ways a run fails before it starts; see
[Troubleshooting](#troubleshooting).

## 2. Get the dataset

`graph_ingest.py:load_dataset()` reads from the Hugging Face cache directly —
no download step in the script itself:

```bash
hf download ScaleAI/SWE-bench_Pro --type dataset
```

This populates `~/.cache/huggingface/hub/datasets--ScaleAI--SWE-bench_Pro/`,
which is exactly where `load_dataset()` looks (`graph_ingest.py:163-165`,
`glob.glob(...snapshots/*/data/*.parquet)`). No separate config needed.

## 3. Ingest into the graph

```bash
cd experiments/swebench_pro
python graph_ingest.py                      # all 731 instances
python graph_ingest.py --limit 100          # a subset, for a faster first pass
```

Writes one `task_node` + one `knowledge_node` (`node_type='code_location'`)
+ a linking edge per instance, embedding the issue text via Voyage as it
goes (`CachedEmbedder`, resumable — a rerun skips what's already embedded).

**Two follow-up passes, both optional and both idempotent:**

```bash
# Joint (issue + gold-diff) embeddings -- measurably better retrieval
# (p=0.0066, n=400 -- see GRAPH_EXPERIMENT.md §3), separate column, doesn't
# touch the first pass's `embedding` column.
python graph_ingest.py --joint-embeddings

# Backfill gold diffs onto already-ingested nodes. No re-embedding -- the
# diff is deliberately never part of the embedding text.
python graph_ingest.py --backfill-patches
```

Running two ingestion/embedding jobs **concurrently** needs `--cache-path`
set to distinct files each — `CachedEmbedder` rewrites its whole cache file
per batch, and two processes sharing one path silently drop each other's
entries on the last write (`graph_ingest.py`'s own `--cache-path` help text).

## 4. Run the experiment

```bash
cd experiments/swebench_pro
py -3 run_graph_experiment.py \
  --arms no_memory,graph_memory,htn_memory \
  -n 20 --max-steps 200 --steps-per-subgoal 20 \
  --model deepseek-v3.2 \
  --out my_run.jsonl
```

**Resumable.** Results append to the `--out` JSONL and an instance already
present there is skipped on rerun — an interrupt costs at most the instance
in flight, not the whole sweep.

### Key flags

| flag | default | notes |
|---|---|---|
| `-n` / `--n-instances` | 20 | `select_instances` is **deterministic** (fixed seed, sorted) — same `-n` always picks the same instances |
| `--instance-ids` | — | comma-separated, overrides `-n`. Also the safe way to **partition a sweep across parallel processes** — see below |
| `--arms` | `no_memory,graph_memory,htn_memory` | any comma subset |
| `--max-steps` | 28 | total step budget per instance. **200** matches the official SWE-bench Pro protocol (200-turn budget) |
| `--steps-per-subgoal` | HTN agent's own default (9) | HTN-only, per-node round size. **20** pairs with `--max-steps 200` for protocol-faithful numbers |
| `--model` | `gemma-4-31B-it` | see [Model choice](#model-choice) below — this default has been unreliable this session |
| `--embedding-column` | `embedding` | `embedding_joint` only exists on `task_nodes` post-step-3, and restricts retrieval there |
| `--out` | `graph_experiment_joint.jsonl` | use a **fresh filename per real attempt** — `load_done` marks any instance already in the file as done regardless of whether that row is `valid` |

### Model choice

`gemma-4-31B-it` isn't in GeneralCompute's documented model catalog (it's
configured as their internal judge model) and showed three distinct
infrastructure-level failures across this session's runs — a slow `400
provider_error`, a `429` "high demand," and an outright connection failure.
`deepseek-v3.2`, `gpt-oss-120b`, and `minimax-m2.7` are all in the real
catalog. `deepseek-v3.2` also showed provider instability under load (a
timeout and a `500` in one run) — if a run comes back with several `api_error`
rows, that's very likely the provider, not the agent (see
[Troubleshooting](#troubleshooting)); try a different `--model` before
assuming a code regression.

**Model choice must stay fixed across a comparison.** Switching models
between the pre-fix baseline and a later run confounds "did the fix help"
with "is this model better" — pick one model per comparison and say so in any
write-up.

### Running two sweeps in parallel

Two `run_graph_experiment.py` processes with the same `-n` **will collide**:
`select_instances` picks the identical instances, and each process's `finally:
remove_image(...)` (`run_graph_experiment.py:321`) deletes the shared Docker
image when it finishes — whichever finishes first pulls the rug out from the
other, mid-grade.

Safe parallelization means **partitioning explicitly** with non-overlapping
`--instance-ids` lists and separate `--out` files, then concatenating:

```bash
# terminal 1
py -3 run_graph_experiment.py --instance-ids <first half> --out part1.jsonl ...
# terminal 2, at the same time
py -3 run_graph_experiment.py --instance-ids <second half> --out part2.jsonl ...
# after both finish
cat part1.jsonl part2.jsonl > combined.jsonl
```

Get the deterministic ordering to split with:

```bash
python -c "
from run_graph_experiment import select_instances, DEFAULT_SCRIPTS
from graph_ingest import load_dataset
picked = select_instances(load_dataset(), 20, DEFAULT_SCRIPTS)
print(','.join(s['instance_id'] for s in picked))
"
```

Running two sweeps at once also doubles concurrent request volume against
whichever LLM provider you're using — worth remembering if that provider is
already showing signs of instability (see above).

## 5. Read the results

```bash
python -c "
from run_graph_experiment import summarise
import json
rows = [json.loads(l) for l in open('my_run.jsonl')]
print(json.dumps(summarise(rows), indent=2))
"
```

`summarise()` reports per-arm resolve rate and an exact binomial McNemar test
per arm pair on discordant pairs only. **An instance only counts toward
`n_usable` if every requested arm produced a `valid` run** — a row killed
mid-episode by a provider error can't contribute to a paired comparison, so
one bad arm silently drops the whole instance from the denominator. Check
`n_usable` against `n_total` before trusting a resolve rate; a big gap between
them means infrastructure ate part of the run, not that the agents actually
failed that many instances.

**Reporting discipline** (same as every result reported this session): count
`no_patch` as failure, never as a token saving — an arm that gives up cheaply
is not the same as an arm that succeeded cheaply. State the subset is
filtered (gold-verified, 1-4 hand-editable files) and therefore easier than
the full 731, so it is not a like-for-like comparison against any published
SWE-bench Pro leaderboard number.

---

## Troubleshooting

Every one of these was hit for real this session, in this order of frequency.

**`failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine`**
Docker Desktop isn't running. Start it, then retry — this also blocks
`stealthlab-pg` from being reachable even if the container itself is fine,
since Docker Desktop *is* the daemon.

**`ConnectionRefusedError` from `asyncpg.create_pool`**
`stealthlab-pg` is stopped. `docker ps -a --filter name=stealthlab-pg` — if
`Exited`, `docker start stealthlab-pg`. Confirm with `docker ps` (no `-a`)
before rerunning the experiment; a container mid-startup can still refuse
connections for a few seconds.

**`tok=0 tools=0` and `stop_reason: api_error`, `agent_error: APITimeoutError`
or `APIConnectionError` or `InternalServerError`**
Both `Agent._complete` and `HTNAgent._chat` retry these automatically
(`is_transient()`/`backoff_seconds()` in `agent.py`, shared by both agents as
of this session — they used to be two separately-maintained copies and only
one got fixed, which is exactly this failure mode if you're on an older
checkout). If it's still happening after that fix, the provider outage
outlasted the ~4-attempt retry window — not a bug, just bad luck; rerun, or
switch `--model`.

**`HarnessError: pull failed for ... no such host` / `dial tcp: lookup
registry-1.docker.io`**
DNS blip while pulling the grading image. `pull_image` (`pro_harness.py`)
retries transient network failures automatically as of this session. A
`manifest unknown` or `unauthorized` in the same error is a **real** failure
(missing/private image) and won't be retried — check the image name/tag
rather than assuming it's transient.

**Two sweeps' rows both show unexpected `EXCLUDED: gold_patch_does_not_resolve`
or one succeeds while the other fails on the identical instance**
Concurrent sweeps colliding on a shared Docker image — see
[Running two sweeps in parallel](#running-two-sweeps-in-parallel). Partition
with `--instance-ids`, don't run two unpartitioned sweeps at once.

**A rerun with the same `--out` skips instances you expected to retry**
`load_done` (`run_graph_experiment.py:134`) treats any instance already
present in the file as done, **including invalid/`api_error` rows**. Use a
fresh `--out` filename for a real retry, not the same one.

**`HybridRetriever.__init__() got an unexpected keyword argument
'embedding_column'`**
This experiment imports the real backend directly
(`app.services.retrieval.HybridRetriever`, `app.config.settings`), so an
uncommitted fix to either file silently reverts if something discards
working-tree changes (`git checkout -- <file>`, an editor's "discard
changes," `git stash`/`reset --hard`). If this recurs, those two files are
the first place to check — see `GRAPH_EXPERIMENT.md` §9.

**A `git push` on this repo times out (`HTTP 408`) after a slow, large upload**
Unrelated to the experiment itself, but hit this session: large embedding
cache files (`.cache/`, `.cache_joint/`) can end up committed. Check
`git rev-list <base>..HEAD --objects | git cat-file --batch-check` for
anything over a few MB before pushing.
