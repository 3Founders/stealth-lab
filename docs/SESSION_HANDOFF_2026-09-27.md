# Session handoff: Kel experiments, knowledge delivery fixes (2026-09-27)

This document lets a new session pick up where this one stopped. Read it top to bottom, then do **Next steps**.

## Ground rules the user has set (keep them)

- **Production:**
  - never touch or contaminate production;
  - experiments use local, isolated Postgres only: `127.0.0.1:55432`, databases `kel_*` and `sl*`;
  - `backend/.env` `DATABASE_URL` points at hosted Neon, so **never** run e2e tests without exporting a local `DATABASE_URL` (the test conftest strips `.env`'s value).
- **Secrets:** never print or store API keys or connection strings. Check key presence by name only.
- **Commits:** commit and push only when asked. "Push everything good" has been standing practice.
  - Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
  - If the push is rejected: `git pull --rebase` (an unrelated stash exists; leave it alone).
- **Runs in progress:** don't change anything that is running; let it finish.
- **Experiment discipline:**
  - preregister (with a hash) before scored runs;
  - log deviations;
  - never re-run graded episodes;
  - re-run only infrastructure call errors.
- **Writing:** explain the diagnosis before coding when asked, and keep explanations concrete.

## Where things stand

### Headline results so far

1. **Routing** (strong, replicated): 32–62% cheaper than Sonnet at equal or better accuracy on BigCodeBench and DS-1000 rounds 1–3. Caveats: placeholder open-model prices, estimated Sonnet tokens, single-shot tasks, public benchmarks.
2. **Knowledge on DS-1000 (transfer tasks):**
   - rounds 1–3 (single-shot notes): +2 to +4, not confirmed;
   - **round 4** (agent loop, `experiments/ds1000/PREREGISTRATION_4.md`, results in that file):
     - **KN** (round-3 note pasted into the agent's prompt): **+8.5 [+3.2, +14.2], p = 0.003**, the first significant knowledge effect;
     - **KP** (Kel used the product's way, the agent drives `find_ways`): +3.3, p = 0.37, not confirmed; KP − KN = −5.3;
     - Sonnet via Claude Code: KP − AG +3.7, not significant. Sonnet agent alone 79%; best open-model arm 61%.
   - The agent loop is weaker than single-shot for open models (gemma writes scripts that rebuild example data).
3. **Why KP lost the knowledge** (diagnosed from per-task traces):
   - (a) adoption: gpt-oss made 0 `find_ways` calls;
   - (b) about half the calls returned nothing usable, because variants are different Goals, so the answer was `ambiguous` or `no_match` with no example;
   - (c) weak agents mis-arbitrate ambiguous candidates;
   - (d) the `plan_and_run` ceremony costs steps and tokens.

   Part of KN's gain is a format effect: code examples in context stop gemma writing scripts.

### Fixes implemented in this session (in code, flags OFF by default except the governor)

| Fix | Where |
|---|---|
| `related_examples` on every `find_ways` outcome (provenance model) | `backend/app/services/retrieval_service.py` (`related_example_goals`, `related_examples`), `goal_choice.choose_goal(collect=)`, `server._find_ways_impl` (`_with_related`); flag `KNOWLEDGE_RELATED_EXAMPLES` (+ limit / drop confidence); needs `KNOWLEDGE_VERIFIED_EXAMPLES` |
| Suggested candidate on `ambiguous` | `server._suggested_candidate`; flag `KNOWLEDGE_SUGGESTED_CANDIDATE` |
| Governor (cache, loop breaker, budget, minimum size) | `backend/app/mcp_server/find_ways_governor.py`; wired in `server.find_ways`; `FIND_WAYS_GOVERNOR` (default **on**); applies only to HTTP requests (keyed on `mcp-session-id`) |
| Per-client call statistics | `_record_find_ways` detail now includes `client` (MCP clientInfo + user agent) and `governor` |
| Tool description: when to call, when not | `find_ways` docstring |
| `plan_and_run`: use `suggested` and `related_examples`, don't re-ask, small-task fast path | `backend/app/mcp_server/prompts.py` |
| **Claude Code knowledge hook** (deterministic delivery) | `packaging/npm/lib/hook.mjs`, `bin/stealthlab-mcp.mjs hook-prompt`, installed by `install` into `~/.claude/settings.json` (`--no-hooks` to skip; `uninstall` removes only ours); env `STEALTHLAB_HOOK=off`, `_MIN_WORDS` (6), `_TIMEOUT_MS` (25000), `_MAX_CHARS` (8000) |
| Agent options for experiments | `app/execution/coding_agent.Agent(tools=, tool_max_chars=, system=)`; unset = unchanged |

**Tests:**
- npm: `node --test` in `packaging/npm` passes 17 of 17.
- Backend offline: `tests/test_find_ways_round4_fixes_offline.py` passes 6 of 6.
- Backend e2e: `tests/test_find_ways_related_examples_e2e.py` + `tests/test_verified_solutions_e2e.py` pass 5 of 5, with `DATABASE_URL=postgresql://postgres@127.0.0.1:55432/slr`.
- Full offline suite: no new failures against baseline (`scratchpad/fail_now3.txt`, 27; now 16).

**Not built** (from the tool-call-policy notes the user pasted):
- a proxy for non-hook hosts;
- per-model tuning based on the new client statistics;
- DeepSeek `reasoning_content` pass-through and text-tool-call parsing: only relevant if we proxy DeepSeek.

**Production rollout** (after round 5 confirms):
- turn on `KNOWLEDGE_VERIFIED_EXAMPLES`, `KNOWLEDGE_RELATED_EXAMPLES` and `KNOWLEDGE_SUGGESTED_CANDIDATE`;
- migration 124 must be applied on every database first;
- publish the npm package so `install` adds the hook.

### Experiment code

- **Shared product-arm machinery:** `experiments/kel_product_arm.py`.
  - Contents: KelBridge with per-episode MCP session headers for the governor; a sandbox that keeps `.stealth/` out of the answer; an agent with the `find_ways` and `read_procedure_claims` tools; production-style delivery (MCP instructions in the system prompt, `plan_and_run` as the user message).
- **DS-1000:** `experiments/ds1000/`.
  - Round 4: `run_r4.py`, `analyze_r4.py`, `sonnet_r4.py` + `kel_cli.py` (Sonnet through Claude Code subagents), `backfill_r4.py`; output in `runs4/` (gitignored).
  - Round 5: `run_r5.py`, `hook_format.mjs` (the shipped hook formatter), `PREREGISTRATION_5.md`.
  - Knowledge database: `kel_ds1000_r4` on `127.0.0.1:55432`.
- **SWE-bench** (for Chaitanya, who has Docker; he has **not started**; tell him to pull main):
  - protocol and operator steps in `docs/knowledge_side_improvements.md`; code in `experiments/swebench/`;
  - arms A0, K, E, C1, C2, A0r and **KP** (product workflow, `kprod.py` + `kprod.py survey`), with `KNOWLEDGE_VERIFIED_EXAMPLES` on via `experiment.json` `kel_settings`;
  - **to decide:** also turn on the round-5 flags and add a KH (hook) arm to SWE-bench once round 5 confirms, before he starts.
- **Research prompts:** Perplexity and Gemini novelty prompts were given in chat. The user noted the pitch should include the network effect and contribution economy (Global Commons, contributor profiles and leaderboard). Suggested next measurement: a contributor-scaling curve for routing (1/2/4/8 disjoint contributor slices, then held-out cost and accuracy).

## Next steps (in order)

1. **Commit state:** everything above is pushed in the commit that adds this file (check `git log`).
2. **Run round 5** (smoke already passed on two tasks):
   ```bash
   cd experiments/ds1000
   python run_r5.py AG5   # ~20 min
   python run_r5.py KP5   # slow (~10 s per find_ways); use --workers 3; General Compute rate-limits (429): re-run the same command to fill call errors
   python run_r5.py KH
   ```
   - Then write `analyze_r5.py`, modelled on `analyze_r4.py`: primary KH − AG5 pooled transfer, plus the secondaries listed in `PREREGISTRATION_5.md`.
   - Before the first scored episode, hash `runs5/design.json` + `PREREGISTRATION_5.md` + `run_r5.py` + `../kel_product_arm.py` into `runs5/design.sha256`, and commit.
3. **If KH or KP5 confirms:**
   - update `PREREGISTRATION_5.md` with the results;
   - propose the production flag rollout and the npm publish (ask the user first);
   - add the KH arm and the flags to SWE-bench before Chaitanya starts.
4. **Optional:** Sonnet on round 5 through Claude Code subagents (`sonnet_r4.py` pattern; mind the user's usage limit: run batches in waves of about 5).
5. **Open item from earlier:** remove the 6 invalid General Compute keys from `.env` (the user's task).
