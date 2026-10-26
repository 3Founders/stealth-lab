# Routing latency: results (2026-10-23)

The question from [latencycheck.md](latencycheck.md): how long does routing take, from the moment the prompt hook has
the prompt until the dispatcher starts the first cheap model, or decides not to dispatch?

**Short answer:**
- **Warm, on prompts that were dispatched:** routing takes **6.3 s at the median and 18.7 s at p95** (n=21).
- **About 85% of that is two model calls made one after the other:** the brief (41%) and locate's narrowing step (44%).
- **The triage judge, `find_ways` and the server's plan together are about 11%.** `plan_ms` itself is a median of 66 ms.
- **The first prompt after a server restart is much slower (13–33 s), and can lose its triage verdict entirely** (one
  hit the 30 s timeout).

All numbers are **localhost only**: a loopback server on this machine. A remote server adds a network round trip to
triage and to `find_ways`, which these numbers do not include. Nothing here is extrapolated to a remote server.

## Setup

- **Date and commit:** 2026-10-23. Based on `357fe1a`, with the timing code from this change.
- **Machine:** Windows 10, Node 24, the user's PC.
- **Server:** `experiments/abtest/run_backend.py`, the A/B test server: production code on the local scratch Postgres
  `kel_abtest` (127.0.0.1:55432), with its JEV judge and the router's learned state from the earlier series.
  - `backend/scripts/start_mcp_local.ps1`, named in the prompt, does not exist, so this server was used instead.
- **Client:** the real Claude Code prompt hook (`runPromptHook`), run in-process by `packaging/npm/scripts/measure_routing.mjs`:
  - `STEALTHLAB_ROUTING_TIMING=1`;
  - dispatch installed (`config.json` `dispatch: true`);
  - the A/B harness's `exec.json` (gemma, gpt-oss, minimax and glm-5.3-flash on General Compute and z.ai);
  - `ANTHROPIC_MODEL=claude-opus-5-5`;
  - check `python -m pytest -q`;
  - product defaults for everything else, including the brief and locate.
- **What is real:**
  - the `/triage` judge;
  - `find_ways` with its model plan;
  - dispatch's gate, including its provider quota probe;
  - the brief (a model call);
  - locate's narrowing step (a model call).
- **The one stand-in is the cheap model's run itself.** It fails at once, so each prompt ends right after routing and
  no cheap model worked on, or was billed for, the task. Routing stops at the first cheap model's start, so this
  doesn't change what is measured.
- **The project:** a small Python repository (`calc.py`, `stats.py`, one test file) with a `.kell/` folder.
  - For the "no .kell" prompts: the same files in a folder that is not a git repository, so no `.kell/` can be
    created.
- **Prompts:** 54 in total, listed in `measure_routing.mjs`:
  - **code changes that should dispatch:** 12 prompts, run twice (24);
  - **questions and chit-chat:** 10;
  - **code changes in a folder with no `.kell`:** 8;
  - **cold:** 4 runs, each the first call after the server started (1 before the main runs, 3 after restarts).

## Results

### Warm: code changes that were dispatched (n=21 of 24; the other 3, see below)

| Part | n | p50 | p95 | max |
|---|---|---|---|---|
| Triage (`/triage`, one JEV call) | 12 | 462 ms | 685 ms | 685 ms |
| `find_ways` round trip | 12 | 1,427 ms | 2,516 ms | 2,516 ms |
| `plan_ms`, the server's own plan time (part of `find_ways`) | 24 | 66 ms | 150 ms | 295 ms |
| Gate in `dispatch()` | 21 | 2 ms | 1,402 ms | 7,883 ms |
| Brief (model call) | 21 | 2,572 ms | 8,077 ms | 8,245 ms |
| Locate (local search + model narrowing) | 21 | 2,601 ms | 9,776 ms | 10,303 ms |
| Setup before the first rung | 21 | 8 ms | 12 ms | 495 ms |
| **Routing total** (prompt to first cheap model) | 21 | **6,268 ms** | **18,691 ms** | **21,052 ms** |

Notes on this table:
- **Triage and `find_ways` come from the first pass only (n=12).** On the second pass the same prompts hit caches: the
  hook remembers a triage verdict for 2 minutes, and the server answered `find_ways` in about 120 ms. The second pass
  had triage p50 15 ms and `find_ways` p50 121 ms. Real prompts are rarely repeated, so the first pass is the honest
  number.
- **The gap between `find_ways` and `plan_ms`** (about 1.4 s against 66 ms at p50) is the rest of the call:
  - the MCP handshake (initialize, initialized, tools/call);
  - retrieval;
  - the repo's claims and library payload.

  In the folder without `.kell`, the same call took only 59 ms at the median. So most of the gap comes with the
  `.kell` library payload and its retrieval, not with the network.
- **The gate is 2 ms except when its provider quota probe runs.** The probe's result is cached on disk for 5 minutes,
  so it runs about once per 5 minutes. When it runs, it probes 1–2 providers one after the other, each capped at 5 s.
  That accounts for the 1.4–7.9 s rows.
- **Not dispatched (3 of 24):** the stand-in cheap runs always "fail", so `.kell/areas.md` built up losses (13 in
  `calc.py`, 8 in `stats.py`). Dispatch's area rule (skip after 3 losses with no win, but still try now and then) then
  sent those prompts straight to the session's model. That's a real gate decision, but caused by the stand-in, so those
  rows are left out of the table above. Their routing took 151 ms to 3.2 s.

**Share of routing time, summed over the 21 dispatched prompts:**

| Part | Share |
|---|---|
| Locate | 44% |
| Brief | 41% |
| `find_ways` | 8% |
| Gate | 5% |
| Triage | 3% |
| Setup | 0% |

### Warm: questions and chit-chat (n=10)

| Part | n | p50 | p95 | max |
|---|---|---|---|---|
| Triage | 10 | 566 ms | 684 ms | 684 ms |
| Routing total | 10 | 567 ms | 17,343 ms | 17,343 ms |

- **Nine of the ten ended at triage** (`work=answer` or `conversation`, no lookup), in under 0.7 s. Seven were then
  answered by a cheap model; that answering is not routing and is not timed here.
- **The tenth was judged `answer` but needing a lookup.** It ran `find_ways` (2.9 s), started 13.8 s after triage
  ended, because the hook's cheap-answer attempt ran in between.

### Warm: code changes with no `.kell` (n=8)

| Part | n | p50 | p95 | max |
|---|---|---|---|---|
| Triage | 8 | 371 ms | 486 ms | 486 ms |
| `find_ways` | 8 | 59 ms | 67 ms | 67 ms |
| Routing total | 8 | 507 ms | 640 ms | 640 ms |

All 8 were not dispatched: there is no `.kell/`, so there's no dispatch path. No model plan was computed
(`plan_ms` is absent).

### Cold: the first call after the server starts (n=4)

| Run | Triage | `find_ways` | `plan_ms` | Gate | Brief | Locate | Routing total |
|---|---|---|---|---|---|---|---|
| Cold 0 (before the main runs) | **30,361 ms, no verdict (timeout)** | failed | – | – | – | – | 30,365 ms, not dispatched |
| Cold 1 (after a restart) | 21,271 ms | 2,003 ms | 264 ms | 5,613 ms | 2,072 ms | 1,408 ms | 33,006 ms |
| Cold 2 (after a restart) | 5,234 ms | 1,949 ms | 257 ms | 2,799 ms | 1,008 ms | 1,633 ms | 13,423 ms |
| Cold 3 (after a restart) | 5,141 ms | 2,248 ms | 235 ms | 3,698 ms | 947 ms | 1,486 ms | 14,246 ms |

- **Cold triage is 5–30 s, against about 0.5 s warm.** Cold 0 hit `STEALTHLAB_HOOK_TRIAGE_TIMEOUT_MS` (30 s), got no
  verdict, and then `find_ways` failed too. So **the first prompt after a server start was not dispatched at all.**
  The server-side cause (judge or model warm-up, the Bayesian prior load, sign-in key fetches) was not isolated.
- **Cold `plan_ms` is about 250 ms, against 66 ms warm.** The plan's own cold cost is small.
- **The cold gate (2.8–5.6 s) is the quota probe.** Each cold run used a fresh `STEALTHLAB_HOME`, so its 5-minute cache
  was empty. That's a client-side cost, not a server one.

## Answer: how much of arm C's added time is routing?

Arm C's median was 56 s against arm A's 33 s, so **about 23 s added** (`HANDOFF_abtest_2026-10-11.md`). The later
re-run measured 42–55 s against 15–37 s, which is 18–27 s added.

- **Routing:** warm routing on a dispatched prompt has a median of **6.3 s**. That's **about 27% of the 23 s added**,
  23–35% of the re-run's range, and about 11% of arm C's whole 56 s median.
- **The cheap model's work and the check were not measured here.** They make up the rest of the added time: about
  17 s at the median, if the numbers combine. The handoff's split (a cheap win adds a median of 22 s, a cheap failure
  99–164 s) still describes that part.

These numbers are not from the same runs, so the shares are an estimate. Routing was measured on a small repo on
localhost. The benchmark's 23 s came from the harness on real tasks.

**Routing is over 20% of the added time.** As the prompt asks, here is the biggest piece and one proposed change.
It is **not** made in this item.

- **Biggest piece:** the brief and locate's narrowing step, 85% of routing together, with a combined median of 6.1 s.
  They run one after the other, because locate's search uses the brief's search terms.
- **Proposed change: run them at the same time.**
  - Start locate's narrowing with the prompt's own words while the brief runs.
  - When the brief returns, rerun only the local search (milliseconds) with its extra terms. Ask the narrowing model
    again only if that search finds a file the first pass did not.
  - **Expected saving:** about the shorter of the two calls per prompt, roughly **2.5 s at the median (about 40% of
    routing), and 4–8 s at p95.** This is an estimate from the rows above, not measured.
  - **Main risk:** on vague prompts, narrowing without the brief's terms may pick worse files. The rerun rule exists for
    that case.

Two smaller findings, also not changed here:
- **Cold triage can time out and silently skip dispatch on the first prompt after a server start.** A warm-up request
  when the server starts would remove it.
- **The quota probe checks providers one after the other.** Checking them at the same time would cap the
  once-per-5-minutes gate cost at 5 s instead of 10 s.

## What was not measured

- **A remote server.** No hosted server was reachable from this machine, so every number is localhost only. A real
  deployment adds a network round trip to triage and to `find_ways` (the MCP handshake is 3 requests).
- **Real Cursor.** This needs a person with Cursor installed. The Cursor path (`cursor_dispatch.mjs`) carries the same
  timing marks, but it was not run here.
- **Real customer sessions, or a large repository.** Locate's local search on a big repo may be slower than on this
  3-file project.
- **The cheap model's work and the check,** on the same runs as the routing numbers.
- **The hook process's own start-up** (Node, imports, reading settings), before the hook has the prompt. The timing
  line records it as `process_ms` when the hook runs as its own process. In this in-process driver, that field counts
  from the driver's start, so it is ignored here.

## How to reproduce

```bash
# 1. scratch Postgres and the local test server (docs/HANDOFF_abtest_2026-10-11.md section 7)
PATH="/c/Program Files/PostgreSQL/18/bin:$PATH" pg_ctl -D "C:/Users/user/pg-scratch/data" -o "-p 55432 -c listen_addresses=127.0.0.1" -l "C:/Users/user/pg-scratch/pg.log" start
python experiments/abtest/run_backend.py            # leave running; serves http://127.0.0.1:8765

# 2. warm runs (from packaging/npm)
node scripts/measure_routing.mjs --url http://127.0.0.1:8765/mcp --token-file ../../experiments/abtest/.local/token.txt --out <dir>/warm1 --label warm --keys backend/.env
node scripts/measure_routing.mjs --url http://127.0.0.1:8765/mcp --token-file ../../experiments/abtest/.local/token.txt --out <dir>/warm2 --label warm --kinds change --repeat 2 --keys backend/.env

# 3. cold: restart the server, then one prompt; repeat
node scripts/measure_routing.mjs --url http://127.0.0.1:8765/mcp --token-file ../../experiments/abtest/.local/token.txt --out <dir>/cold_rN --label cold --kinds change --limit 1 --keys backend/.env

# 4. tables
node scripts/summarize_routing_timing.mjs <dir>/*/home/routing_timing.jsonl
```

Notes:
- **The `warm1` run above predates setting `ANTHROPIC_MODEL` in the driver.** Its 12 code changes got no model plan
  and are not in the dispatched table. Its questions and no-`.kell` rows are used.
- **To time real sessions,** set `STEALTHLAB_ROUTING_TIMING=1` in the hook's environment. Each prompt appends one line
  to `~/.stealthlab/routing_timing.jsonl`, with no prompt text: only its length and a 12-character hash.
