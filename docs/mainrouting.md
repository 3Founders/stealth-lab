# Main routing handoff: where chat works, how models are routed and served (2026-11-06)

For a session with no memory of earlier work. It covers four things: the website chat, the hosted gateway that serves models, kell's own coding agent (OpenCode plus kell's plugin), and the desktop app. Every file below was opened and read for this note; statements about behaviour come from the files' own header comments and code, not from a live run. Where something is unverified it says so.

Related, narrower docs: `docs/desktop.md` (Rooms sign-in), `docs/desktop_release.md` (shipping), `docs/handoff_opencode_agent_and_app_2026-11.md` (agent build history), `docs/hostingchats.md` (whose key pays), `docs/provider_catalog.md` (connecting providers), `docs/model_routing_plan.md` (the per-goal recommender design; the code is built, see 4a).

## 1. The picture in one page

```
 website chat (routing_kell_frontend)         desktop app (desktop/)             kell CLI / Claude Code / claude.ai
 - BYOK: person's key, browser -> provider    - Code tab: opencode serve child   - hooks, find_ways model plan,
 - kell models (opt-in pilot)                 - Rooms tab: the site's /chat        call_model, dispatch (cheap rungs)
        |  Bearer = website session                |  Bearer = MCP sign-in (kell login)
        v                                          v
   POST /v1/chat/completions , GET /v1/models   (hosted MCP service, backend/app/mcp_server/chat_routes.py)
        |
        v
   backend/app/providers/chat.py  -> registry (who may use which connection) -> adapters (openai_compatible) -> provider
        |  policy, data class, caps, ledger, failover between endpoints and models
        v
   General Compute / DeepInfra / OpenRouter / ... (kell's own keys, server env only)
```

Three ideas to hold:

1. **Two lanes for keys.** BYOK: the person's own provider key, kept in the browser vault, sent straight to the provider, never to kell. Hosted: kell's one key per provider, server-side only; the person carries a scoped sign-in token, never a provider key. No per-user provider keys are ever stored server-side (`docs/hostingchats.md`).
2. **Two tokens, on purpose.** The website session (Supabase, no `client_id`) and the MCP OAuth token from `kell login` (has `client_id`). `backend/app/services/authn.py::supabase_token_allowed` rejects each on the other's surface. Do not "fix" by sharing one.
3. **One governed door for model spend.** `/v1/chat/completions` is the only path a client uses to spend kell's provider money. Everything (policy, caps, ledger, failover) lives behind it, so the website, the desktop app and any harness get the same rules.

## 2. Where chat works

| Surface | State | Notes |
|---|---|---|
| Website chat `/chat` (`routing_kell_frontend/app/chat/page.tsx`) | Live. The reference chat. | Rooms, people, agents, canvas, files. `@kell` answers via the room agent. |
| Desktop Rooms tab | Shipped (`b192d462`); no live sign-in run yet | Embeds the site's own `/chat` in a `WebContentsView`; sign-in is PKCE for Google/GitHub, the site's page for email. Needs `http://127.0.0.1:**` in Supabase redirect URLs (not done). |
| Desktop Code tab | Works against a fake gateway; no real credit-spending prompt run | OpenCode engine; see section 5. |
| `console/app/chat` | "Chat (dev sandbox)", old and small | 16 files vs the site's 72; parity inventory in `docs/chat_parity_console_vs_routing_kell_frontend.md`. Not the live chat. |

### What backs a chat message on the website

`routing_kell_frontend/lib/chat/adapters/resolve.ts::makeResolver` picks per message from the person's saved choice on the Models page. **No fallback between the two:**

- `"key"` (default): their key from the browser vault goes straight to their provider (`lib/byok/chat.ts::streamChat`). Nothing passes through kell. With no key, the room says "Add a key to chat here".
- `"kell"` (opt-in pilot): their session token goes to kell's hosted server (`lib/kell-models.ts::kellTarget`), public material only (`X-Stealthlab-Data-Class: PUBLIC_SOURCE`).

### The @kell room agent

`backend/app/mcp_server/room_agent_routes.py` (`POST /v1/rooms/{room_id}/agent`) reads the room, asks the tool router which tools fit, runs a few rounds of model and tool calls, and posts the reply as @kell. The logic is in `backend/app/services/room_agent.py` (system prompt, transcript, tool rounds, canvas fencing, image handling). Its model call is forwarded to the same deployment's `/v1/chat/completions` with the caller's own bearer, so pilot policy, budget and data-class checks apply unchanged. Fallbacks come from `KELL_ROOM_AGENT_FALLBACKS`; token cap `KELL_ROOM_AGENT_MAX_TOKENS` (default 4096); asks are rate-limited per person per minute in memory.

## 3. How models are served (the gateway)

**Entry: `backend/app/mcp_server/chat_routes.py`** (registered on the hosted MCP service, which runs one worker, so its counters are in memory and reset on restart)

- `POST /v1/chat/completions`: OpenAI Chat Completions, optionally streamed (SSE). `GET /v1/models`: the direct models this person may use, with `stealthlab.{tier, input_per_mtok, output_per_mtok, max_output_tokens, connection, compliance}`. `GET/PUT /v1/tools/prefs`: the "may kell use tools that need a sign-in" preference.
- Auth: `resolve_identity` accepts the website-session token and the `execution:run` scope. Limits: 120 requests/min per client address (before the token check), 20/min per person, body at most 256 KB.
- Headers in: `X-Stealthlab-Org` (must be one the caller belongs to), `X-Stealthlab-Data-Class` (default `USER_PRIVATE`, which the pilot refuses; pilot takes `PUBLIC_SOURCE`, `PUBLIC_DERIVED`, `GLOBAL_PROCEDURE`), `X-Stealthlab-Max-Cost-Usd`. Headers out: unit, connection, cost, and `x-stealthlab-ai-generated: true` on every answer.
- Who pays (`paying_org`): the named org if the caller is a member; else the pilot org (`KELL_PILOT_ORG_ID`, `services/pilot_enrol.py`) when configured; else the governed path decides (one membership: that one; several: refused with "name one").
- A cap hit is `403 policy_denied` with display text.

**Core: `backend/app/providers/`**

| File | Job |
|---|---|
| `chat.py` | The orchestrator surface. Routes the request to a connection offering the model and returns the provider's reply as-is (the harness owns the tool loop). Forwards an allowlist of fields only. Reserves worst-case cost before sending, settles real cost after, releases on failure. Failover between endpoints and fallback models. `list_models` builds `/v1/models`. |
| `service.py` | `call_unit`, the single place a model or agent is invoked. Checks in order: visibility, data class, platform egress policy (INV-07) for kell-owned keys, `max_cost_usd`, endpoint safety. Also `offering_connections`, key rotation, budget hold/settle. |
| `registry.py` | Where connections come from (JSON file named by `STEALTH_PROVIDER_CONNECTIONS_FILE`, DB table, agent registry) and `visible()`, the one place that decides who may use a connection. |
| `model_catalog.py` | What each model is (licence, origin, context) and, once an operator verifies it, where it is served and at what price (`STEALTH_MODEL_CATALOG_FILE`). Ships no prices; an unpriced offering produces no unit. |
| `adapters.py` | One per endpoint kind: `openai_compatible` and `a2a`. Never reads secrets itself, never follows redirects. |
| `health.py` | Circuit breaker per endpoint: 30 s cool-down doubling to 5 min; 15 min after an account refusal. Per process. |
| `secrets.py` | `env:NAME` credential references resolved at call time only. |
| `url_guard.py` | SSRF guard for user-supplied endpoints. |
| `presets.py` | Provider presets for the Providers page (no prices, no data-handling claims). |
| `shims.py` | Anthropic Messages and OpenAI Responses to and from Chat Completions (text, tools, streaming; not images, thinking, server-side tools). |
| `types.py` | `Connection`, `UnitSpec`, unit naming `model|scaffold` (`deepseek-v3.2|direct`). |

`backend/app/api/chat_completions.py` is the same surface on the full API app, plus `POST /v1/responses` (Codex CLI) and `POST /v1/messages` and `/v1/messages/count_tokens` (Claude Code via `ANTHROPIC_BASE_URL`). The hosted MCP service exposes only the chat-completions side.

**Money:** `backend/app/mcp_server/billing_routes.py` (`GET /v1/billing/wallet`, orders and plans), `services/wallet.py` (balance, orders, payment credit), `services/free_credits.py`, `services/accounts.py`. A person's wallet account id is their user id; its policy needs a positive prepaid balance. Zero balance makes the gateway refuse with a "buy credits" message. The desktop app turns that into "You need a plan to run models". Plans live in the `billing_plan_rows` table (admin dashboard), with the built-in copy in `routing_kell_frontend/lib/plans.ts`.

**Open gaps from `docs/hostingchats.md` (not closed):** streaming usage accounting when a stream is cut (the code says the provider's usage is trusted, with a worst-case fallback), a per-user in-flight stream cap, and rotation audit by key generation.

## 4. Routing: choosing which model

Three separate mechanisms. Do not mix them up.

**A. Server-side advice (find_ways, triage, recommender).** `find_ways` can return a `model_plan` ladder ("try A; if its check fails, B") when the client passes `candidates`; `report_result` stops on a pass and names the next model on a failure; `call_model(model="auto")` runs the next one. Triage: `POST /triage` returns `{kind, work, confidence, provider, category, needs_retrieval, tool?}` (`mcp_server/find_ways_triage.py`). The per-goal statistical recommender is built, in `backend/app/routing/` (file list in section 4a). `docs/model_routing_plan.md` still says "plan only", which is out of date. `services/route_decision.py` is the older goal-route logic, not the recommender. Whether the recommender has been fitted on production data was not checked; with no fit it falls back to the bundled public prior (`routing/prior_bundle.py`).

**B. Client-side: Claude Code and Cursor.** Hooks cannot switch the main session's model, so:
- `packaging/npm/lib/dispatch.mjs`: the plan's cheap rungs run before the session's model sees the prompt (open models through `kell exec`, in a worktree, verified by the project's check). A pass applies the change and stops the prompt; all fail: the prompt goes on with what was tried.
- `packaging/npm/lib/model_guard.mjs`: makes the plan bind when the agent delegates to a subagent (denies a call that does not ask for the plan's model).
- `packaging/npm/lib/model_speed.mjs`: records how long each cheap model takes to win in this repo (`.kell/routing.md`) and caps a run at about 3x its p90.
- `packaging/npm/lib/anthropic_bridge.mjs`: loopback bridge so Claude Code can drive any open model (Messages API to chat completions); the provider key stays in the bridge process.
- `packaging/npm/lib/agent/` (`step_agent.mjs`, `stall.mjs`, `provider_errors.mjs`, `installs.mjs`): kell's own small step agent, stall detection, out-of-credit detection, install blocking.

**C. Kell's own agent on OpenCode** (section 5): routes by triage plus escalation on failing tests.

### 4a. Files that handle routing for the kell agent

Read in this order: the client asks, the server triages and plans, the agent runs the ladder and reports back.

**Server entry points (`backend/app/mcp_server/server.py`)**

| Function (line) | Job |
|---|---|
| `triage_route` `POST /triage` (1706) | One round trip, one cheap judgment: `{kind, work, confidence, provider, category, needs_retrieval, tool?}`. The OpenCode plugin and the Claude Code and Cursor hooks call this first. Rate limited. |
| `find_ways` (3092) | The lookup. With `candidates=["model|scaffold", ...]` or `my_model`, `_attach_model_plan` (3410) adds `model_plan`: the ladder and an `instance_key`. `_find_ways_triage` (3254) runs the triage inside it. |
| `recommend_models` (2567) | Older manual route: the caller supplies candidates and carries `previous_attempts` itself. |
| `report_model_run` (2637) | Older manual route: records one attempt for a `recommend_models` key. |
| `report_result` (2710) | The current route. One call per attempt with the `instance_key`; the reply names the NEXT model or says stop. |
| `call_model` (2876) | Runs a named model, or `model="auto"` for the next rung, on the models and agents this deployment has connected. |
| `delegate` (3025) | Hands a self-contained sub-task to a cheap model with a check. |

**Server triage helpers (`backend/app/mcp_server/`)**: `find_ways_triage.py` (classify the request before any search; only `reusable_task` goes on to the search), `find_ways_governor.py` (caches identical requests, bounds how often find_ways is called per caller), `find_ways_detail.py` (`detail="summary"` shortens the reply so it weighs less in the agent's context).

**The recommender (`backend/app/routing/`)**: recommends the ladder with the best expected utility `U = V*P(accepted and correct) - L*P(accepted and wrong) - E[cost]`, subject to a reliability constraint. It does not change retrieval.

| File | Job |
|---|---|
| `plan.py` | The agent-facing flow. The server holds the state: `model_plan` (first ladder plus `instance_key`), `load_instance`, `report_result` (rebuilds the next rung from what was stored). Candidates are not a built-in list: they come from registered candidate providers (`register_candidate_provider`), the caller's own model (`caller_unit`) and what the caller sends. |
| `service.py` | What the MCP tools call. `recommend` builds posterior draws for each candidate (prior for any unseen Goal, model or Procedure), adjusts with local observations, and picks the ladder. `record_observation` stores an attempt and queues a refit. |
| `ladder.py` | Exact ladder evaluation and choice. Failures of consecutive rungs are correlated through a shared instance difficulty, so a later rung is not overrated. |
| `predict.py` | Decision-time maths on stored posterior draws (numpy only, no JAX in the API process). |
| `model.py`, `fit.py`, `jobs.py` | The hierarchical Bayesian model (NumPyro), the nightly joint refit and per-Goal local refits. Worker only; needs `requirements-routing.txt`. |
| `model_update.py` | Between-nightly update of each model's ability from public observations since the last fit. |
| `costs.py`, `token_calibration.py` | Learns tokens, prices them at decision time from the live price table. |
| `cards.py`, `catalog.py`, `probe.py` | Starting ability for a model nobody has run: from its metadata card, from OpenRouter's live catalogue or the caller's description, or from a short probe on graded anchor problems. |
| `task_categories.py` | One ranking per kind of work; the triage's `category` is passed as `model_constraints.category`. |
| `goal_features.py`, `semantic_codes.py` | Goal difficulty features from the verified patch; a two-level code tree over Way embeddings so an unrun Way borrows from similar ones. |
| `evidence.py`, `public_evidence.py`, `import_public.py`, `prior_bundle.py` | Public benchmark results (SWE-bench runs, OpenRouter cards) used as likelihood; the importer; the bundled prior used when the database has no fit. |
| `store.py`, `config.py`, `admin_cli.py` | Tables (`routing_models`, `routing_prices`, `routing_params`, `routing_posteriors`, observations, decisions); every default (`STEALTH_ROUTING_*` env); `python -m app.ingestion.admin routing-*` commands. |

**Client side, how the agent asks and obeys (`packaging/npm/lib/`)**

| File | Job |
|---|---|
| `hook.mjs` | The Claude Code `UserPromptSubmit` hook (`kell hook-prompt`). Calls `POST /triage`, then `find_ways` with the arguments that ask for a model plan (`category` from the triage). Fails open: any error means no injection. |
| `opencode/native.mjs` | The OpenCode router: triage, `chooseModel`, `nextEscalation`, `nextFallback`, outcome recording (section 5). |
| `dispatch.mjs`, `dispatch_run.mjs` | Run the plan's cheap rungs before the session's model; write `.kell/run.md` so the session's model can pick up where they stopped. |
| `dag_run.mjs`, `dag.mjs` | Split a task into one node per file from the closest past fix; per node, pick cheap rungs or the strong rung (long past piece: strong first; an area where cheap models keep losing, `areas.md`: the session's model). |
| `accept.mjs`, `detect_check.mjs` | Decide whether a cheap answer is accepted (the check alone, or a preregistered rule of signals); find the project's own test command so dispatch has a check. |
| `model_guard.mjs`, `subagent_hook.mjs` | Make the plan bind when work goes to a subagent (deny or steer to the plan's model); measure the subagent by re-running the node's check and record it with `report_model_run`. |
| `cursor_dispatch.mjs`, `cursor_hooks.mjs` | The same for Cursor. |
| `locate.mjs`, `locate_model.mjs` | Where the task is: search, then one cheap model call narrows to files and functions. |
| `model_speed.mjs`, `area_history.mjs`, `areas.mjs`, `routing_timing.mjs` | Per-model win times and caps; per-area win history; optional routing latency log (`STEALTHLAB_ROUTING_TIMING=1`). |
| `exec/select.mjs` | Executor registry and selection (the verified-version gate). |
| `exec/runtime.mjs` | One `achieve()` run: attempts in disposable worktrees, verified by the caller's checks, with `escalate=n` extra attempts on the next untried rung. |
| `exec/hosted.mjs`, `exec/evidence.mjs` | The only two hosted calls from the executor layer (`recommend_models`, `report_model_run`); every terminal state becomes one report or an outbox entry; only whitelisted, redacted fields leave the machine. |
| `executors/*.mjs` (`opencode`, `claude`, `codex`, `gemini`, `cline`, `openhands`, `stealth`, `profile`, `common`, `index`) | One adapter per local agent. `profile.mjs` maps a model name to its provider endpoint in `exec.json`; `stealth.mjs` runs kell's own step agent. |
| `packaging/npm/bin/kell.mjs` | The CLI entry that wires `hook-prompt`, `hook route-subagent`, `hook route-report`, `exec`, `setup`. |

Note: the OpenCode plugin (`native.mjs`) uses only the triage and its own rules. It does not call `find_ways`' `model_plan` or the recommender, so the fitted ladder reaches OpenCode only through `hook-prompt` (Layer 1 runs kell's hooks), and I did not check whether that result changes the OpenCode model.

## 5. Kell agent (OpenCode plus plugin)

Engine: OpenCode pinned at 2.2.5. Plugin: `packaging/npm/lib/opencode/`.

| File | Job |
|---|---|
| `lib/opencode/index.mjs` | The plugin. Layer 1 runs Claude Code-style hooks (kell's `hook-prompt`, `hook capture-tool`) inside OpenCode with the same stdin JSON and exit rules. Wires the 60 s no-progress watchdog and per-turn usage. |
| `lib/opencode/native.mjs` | Layer 2 (`KELL_OC_NATIVE=on`): **the model router.** On each prompt calls the server's `/triage`; `chooseModel` picks strong for `work === "unclear_change"` or no verdict, else cheap. Escalates to strong after 2 failed test runs (`nextEscalation`, test command regex `isTestCommand`). On a provider failure retries once, then switches to the fallback model (`nextFallback`, at most one switch per turn). Optionally adds remote tool servers (`addableRemote`, `KELL_OC_ADD_REMOTE=on`). Records each turn to `<server>/tools/outcome`. |
| `lib/opencode/lib/usage.mjs` | `turnUsage`: sums tokens and cost for the last turn from the session messages. |
| `lib/opencode/lib/mcp_token.mjs` | Rewrites the Authorization header of kell's MCP entry in OpenCode's config with a fresh token at every plugin start (option A). A connect already open still fails with 401. |
| `lib/opencode/lib/run_hook.mjs`, `settings.mjs`, `tool_names.mjs` | Runs one command hook; reads and merges `hooks` from settings files; maps OpenCode tool names and args to Claude Code's. |
| `lib/opencode/lib/watchdog.mjs` | Logs once when no progress event arrives for 60 s (logs only, never kills). The cold-start hang did not reproduce in 16 runs. |
| `lib/opencode_setup.mjs` | `kell setup --client opencode`: copies the plugin to `~/.stealthlab/opencode/plugin/`, writes `hooks.json`, `config.json` (mode, cheap, strong), OpenCode's global `opencode.json` (the `gc` provider, plugin, MCP server, default model). |
| `lib/opencode_cli.mjs` | `kell opencode ...`: refreshes the saved token first, then runs OpenCode, so the first turn avoids the 401. |

Defaults (CLI path, own-key lane): provider `gc` (General Compute, key from the environment). Cheap is `gc/minimax-m2.7`, strong is `gc/deepseek-v3.2` (output capped at 4k tokens; General Compute refuses 8k or more). Mode `session` by default: the cheap model runs the session and switches to strong after 2 failed test runs. `dispatch` is the Claude Code flow, selected with `KELL_OC_MODE=dispatch`.

Env knobs: `KELL_OC_CHEAP_MODEL`, `KELL_OC_STRONG_MODEL` / `KELL_OC_DEFAULT_MODEL`, `KELL_OC_FALLBACK_MODEL`, `KELL_OC_ADD_REMOTE`, `KELL_OC_DEBUG=1` with `KELL_OC_STATE_DIR` (writes `native.log`), `KELL_OC_DATA_CLASS`.

Known limit: triage has no difficulty field, and its `confidence` is confidence in the label, not in the task being easy. So "start cheap, escalate on failing tests" is the only honest rule; there is no pre-emptive hard-task detector beyond `unclear_change`.

## 6. Desktop app (`desktop/`, Electron plus Next)

Electron main starts `next start` on a free port as a child process and opens one window on it. The engine (`opencode serve`) is started by the Next server (`lib/engine.ts`), owned by that child, and dies with its tree.

**Electron (`desktop/electron/`, built with `npm run electron:build`)**

| File | Job |
|---|---|
| `main.ts` | Single instance; starts the Next child; opens the window; registers IPC (`kell:rooms-*`, `kell:account-open`, `kell:open-signin`, `kell:pick-folder`); auto-updater (`electron-updater`, repo `3Founders/kell-desktop` is a placeholder); kills the child tree on exit. |
| `preload.ts` | The only bridge: exposes `window.kellDesktop`. |
| `rooms.ts` | The Rooms `WebContentsView` on `<site>/chat` (partition `persist:kell-web`); PKCE sign-in for Google/GitHub; email sign-in through the site; reads who the rooms are signed in as; sign-out; opens `kell login`'s link in the default browser. |
| `signin.ts` | A small window for a tool's sign-in (GitHub, Notion, ...). |

**Next side (`desktop/app/`, `desktop/lib/`)**

| File | Job |
|---|---|
| `app/page.tsx` | The whole UI: Code and Rooms tabs, chat stream, model strip, diff and undo, tool cards, settings, "need a plan" state, Connect banner. |
| `app/api/engine/route.ts` | Start, stop and status of the engine for a folder. |
| `app/api/prompt/route.ts` | `POST {sessionID?, directory, text}`: creates the session if needed, subscribes to engine events, streams them to the UI as SSE until the turn ends (`session.execution.succeeded/failed` or `session.idle`). On the personal lane, checks the wallet first and emits `need_plan` at zero or on a gateway refusal. |
| `app/api/settings/route.ts` | Reports signed-in state, account and model lane from `~/.stealthlab/config.json`; names the personal models before the engine starts. |
| `app/api/signin/route.ts` | Runs `kell login --no-browser` and returns the authorize link; `DELETE` runs `kell logout`. Only checks that a token exists; never returns it. |
| `app/api/diff/route.ts`, `app/api/undo/route.ts` | Diff of what the model changed; Apply or Undo (`kell undo`). Snapshots in `lib/snapshots.ts`. |
| `app/api/tools/route.ts` | Tool suggestion cards: `POST /tools` on the hosted server with the saved token. |
| `lib/engine.ts` | Finds `opencode.exe` (env `KELL_OC_ENGINE`, `kell setup` profile, global install, or the unpacked platform package in the installer); spawns `serve --hostname 127.0.0.1 --port 0`; parses the port and password from stdout; HTTP Basic auth to its `/api/*`; lazy ESM import of `@opencode/client`. |
| `lib/pooled.ts` | **The model-lane chooser for the app.** See below. |
| `lib/cli.ts` | Finds the bundled `kell` CLI (`vendor/kell-mcp/bin/kell.mjs` unpacked beside `app.asar`) so sign-in and setup work on a machine that never installed kell. |
| `lib/oc-types.d.ts`, `lib/theme.ts`, `lib/chat/*`, `components/chat/*` | Client types, theme, smooth text and spring scroll, markdown and message UI copied from the site's chat. |

### Which lane pays (`desktop/lib/pooled.ts`, order matters, `engineStart` in `engine.ts`)

1. **Organization lane** (`pooledEngineConfig`): `kell join` wrote `~/.stealthlab/exec.json` with a profile per model (`key_from: "stealthlab"`) and the company's gateway address. Calls go to the company gateway with the person's kell sign-in as bearer, plus `x-stealthlab-org`; the company's stored key pays under its policy.
2. **Own-key lane** (`ownKeyLane`): a developer ran `kell setup --client opencode` and set a provider key; the engine keeps OpenCode's own global config.
3. **Personal lane** (`personalEngineConfig`): a signed-in person in no team. `GET <origin>/v1/models` with the sign-in lists models; wallet is read from `GET /v1/billing/wallet` (`walletRemaining`). `KELL_GATEWAY` overrides the host for tests; default is `https://mcp.kell.org.in`.

`buildConfig` writes one inline OpenCode config for lanes 1 and 3: kell's gateway as an OpenAI-compatible provider, the bearer in the child's environment (`OPEN_MODEL_API_KEY`, never in the config text), **default model = the highest-priced model; fallback = the cheapest** (sorted by input+output price; unpriced models are skipped for "strong"). It sets `x-stealthlab-data-class` (default `USER_PRIVATE`, override `KELL_OC_DATA_CLASS`) and `x-stealthlab-fallback-models: <strong>,<cheap>` so the gateway retries elsewhere on a dead or out-of-credit provider. If the plugin is installed it keeps it, and adds kell's MCP server with the bearer.

Important consequence to check before relying on it: `buildConfig` sets **`model: kell/<strong>`**, so a desktop turn starts on the strong model, and the plugin's `native.mjs` (cheap-first by triage) only applies if the plugin is installed and `KELL_OC_NATIVE=on`, and it parses refs like `gc/minimax-m2.7`, not `kell/...`. Whether cheap-first routing works inside the desktop app was not verified in this note.

## 7. Website pieces the desktop app depends on (`routing_kell_frontend/`, Vercel)

| File | Job |
|---|---|
| `app/api/desktop-config/route.ts` | Serves the public Supabase URL and anon key so the app can run PKCE (503 if `NEXT_PUBLIC_SUPABASE_*` is missing on Vercel). |
| `app/chat/page.tsx`, `components/chat/RoomView.tsx` | The rooms the Rooms tab shows. |
| `app/sign-in/page.tsx`, `lib/supabase.ts` | Email and Google/GitHub sign-in; session stored under `sb-<ref>-auth-token`. |
| `app/oauth/consent/page.tsx`, `lib/oauth-consent.ts` | The "Allow" page for `kell login` (the coding token's path). |
| `lib/kell-models.ts` | Opt-in hosted pilot models: contract with `/v1/chat/completions` and `/v1/models`. |
| `lib/chat/adapters/resolve.ts`, `assistant.ts`, `lib/byok/*` | BYOK versus kell choice per message; browser vault (AES-GCM, non-extractable device key); client-side daily cap. |
| `lib/plans.ts`, `lib/billing.ts` | Plans and top-ups the "need a plan" link leads to (`/pricing`). |
| `app/download/route.ts`, `public/desktop.ps1` | Download redirect to the newest `kell-Setup-*.exe`; `irm https://kell.org.in/desktop.ps1 | iex`. Not yet pushed. |

## 8. State, open items, and what I did not verify

**Done and on main:** gateway endpoints (`/v1/models`, `/v1/chat/completions`, `/v1/billing/wallet`), personal lane in the app (`f1f56e01`), Open project folder picker (`c0892298`), Rooms sign-in (`b192d462`), OpenCode agent with watchdog, token refresh, fallback and escalation.

**Not done (needs the owners):**
1. Supabase redirect allow list: add `http://127.0.0.1:**` or Google/GitHub fail in the Rooms tab.
2. Confirm the Vercel deploy serves `GET /api/desktop-config`.
3. Rebuild the installer (`cd desktop && npm run dist`); the installed app predates the Rooms sign-in change.
4. A live check never run: Google, GitHub and email sign-in through the real app; a real coding prompt that spends credit; a clean-machine install; the updater against a real release.
5. Create the public repo `3Founders/kell-desktop`; publish the release; push the website `/download` and `desktop.ps1` (runbook: `docs/desktop_release.md`).
6. Hosted-chat gaps from section 3 (stream cut accounting, in-flight cap).
7. Windows installer is unsigned ("Windows protected your PC").

**Open design question for the next session:** the desktop app does not do cheap-first routing today unless the plugin runs native mode against a model ref it understands (section 6). Decide whether the app should (a) leave routing to the gateway's fallback header only, (b) enable `KELL_OC_NATIVE=on` with `kell/<cheap>` and `kell/<strong>` refs derived from `/v1/models` pricing, or (c) move routing server-side. Option (b) is the smallest change: `chooseModel` already takes `{cheap, strong}` and `parseModel` splits on the first `/`, which would work for `kell/<id>`.

**Not verified in this note:** which platform connections and prices are configured in production (they sit in an operator file and Railway env, not in the repo); whether the Railway service `kell-mcp` has `KELL_WALLETS=on` (the release runbook says the personal lane needs it); the uncommitted edits to `routing_kell_frontend/app/page.tsx` and `next.config.mjs` shown in `git status`.

## 9. Commands

```powershell
# desktop (from desktop\)
npm run electron:build ; npm run dist           # installer -> release\kell-Setup-<version>.exe
# kell agent on OpenCode
kell setup --client opencode ; kell opencode    # isolated profile steps: handoff_opencode_agent_and_app_2026-11.md
cd packaging\npm ; npm test                      # one test file at a time; last recorded 852 pass, 3 skipped
# backend offline tests for the gateway
cd backend ; python -m pytest tests\test_chat_routes_offline.py tests\test_tool_gateway_offline.py -q
```

Test counts above come from `docs/handoff_opencode_agent_and_app_2026-11.md`, not from a run in this session.
