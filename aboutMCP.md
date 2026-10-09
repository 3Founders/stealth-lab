# About the MCP: what we discussed, what exists, and how it should work

Written 2026-10-09 from the working session. Each statement is tagged: **[built]** is in the repo and tested offline,
**[partly]** exists with a stated gap, **[proposal]** is a design not yet built, **[unverified]** came from a web source
or has not been run against the real thing. Nothing here is a measured result, and no savings number is claimed.

## 1. The idea in one paragraph

StealthLab is a verified procedural memory for AI agents. The MCP server is the door agents use. The new goal: let a
company's developers keep using Cursor and Claude Code, while the MCP (a) gives them proven, evidence-backed procedures
and (b) routes the work that does not need a frontier model to cheaper open models, under the company's own policy and
budget, with every step recorded. The saving comes from routing and from giving small models the right context. It is a
hypothesis until the benchmark in `docs/testingplan.md` has been run.

## 2. What the MCP serves today

Source: `backend/app/mcp_server/server.py`. The public surface is `V1_TOOLS`, nine tools.

| Tool | Scope | Read-only | Purpose |
|---|---|---|---|
| `find_ways` | retrieval:read | yes | The main entry. Finds proven ways (procedures) for a request. |
| `discover_tools` | retrieval:read | yes | Searches the hidden tools by need; returns their full arguments. |
| `use_tool` | read, then the target's own scope | no | Runs a hidden tool by name. |
| `recommend_models` | retrieval:read | yes | Recommends a model for a task. |
| `report_result` | knowledge:write | no | Reports whether a model attempt was accepted. |
| `report_model_run` | knowledge:write | no | Reports a model run (model, scaffold, accepted). |
| `report_discovery` | knowledge:write | no | Files a discovery, or a moderation report. |
| `submit_way` | knowledge:write | no | Publishes a new way to the shared library. |
| `call_model` | execution:run | no | Runs a prompt on a connected model or agent. |

**Progressive discovery [built].** By default (`mcp_tool_discovery="progressive"`) `tools/list` returns only
`find_ways`, `discover_tools` and `use_tool`, so an agent's context carries three tool definitions, not nine. The other
six are found with `discover_tools(need=...)` and run with `use_tool(name, arguments)`; hidden tools stay callable by
name. Setting the mode to `all` lists everything.

**Also served [built]:** resources (`stealth://claims/{id}`, `stealth://procedures/{id}/claims`, `stealth://goals/{id}/claims`,
`stealth://formats/library`, `stealth://guides/find_ways`, and two prompt texts), two prompts (`survey_repo`,
`plan_and_run`), health routes, claim and procedure graph pages, and the local-sync bridge routes.

**Stale in the code [partly]:** `_TOOL_SCOPES` still lists many removed tools, and two docstrings still say "~29 tools".
Unlisted tools default to `knowledge:write`, so a new tool is denied to read-only callers until classified.

**`call_model` today [partly]:** it sends only a prompt and optional system text, does not stream, and returns text. It
enforces org policy (kill switch, allowlists, data classes, budgets) and tries the next endpoint or model when one is
down. It cannot return tool calls or edit files.

## 3. The decisions we reached

**3.1 Do not put a gateway in front of Cursor or anyone's subscription.**
- Cursor's "Override OpenAI Base URL" is global (it affects every model), has bugs, and in Agent mode sends the Responses
  format, which Chat-Completions-only endpoints reject. Cursor's staff say BYOK traffic goes through Cursor's servers, so
  the endpoint must be public. Cursor's terms bar reselling the service and, per staff, proxying to its private endpoints.
  [unverified: forum threads, not Cursor's own docs.]
- Anthropic's Claude Code legal page: developers may not collect, store or intermediate Claude.ai credentials, or route
  requests through Free/Pro/Max credentials on users' behalf. Our design never touches them.
- A gateway is acceptable only for a customer's own API keys or their own cloud credentials (Bedrock, Vertex, Foundry),
  billed to them.

**3.2 Keep the user's editor and its model; add a layer.** Cursor/Claude Code stay the editor and the top-level model.
The MCP adds knowledge (`find_ways`) and delegation. Where a routed model should be the *main* model, that happens in a
harness the user controls (OpenCode, Cline, Codex CLI), not inside Cursor's agent.

**3.3 Savings are not 95%.** Seat-priced plans do not get cheaper whatever model runs; only metered usage shrinks;
delegation leaves a floor (the main model still reads and reviews); and retries erode the gap. The testing plan's
target is 50-70%, and even that is unproven. We pitch measurement and safe routing, not a percentage.

**3.4 Open models behind hosted providers.** OpenRouter, General Compute, DeepInfra and Novita as `openai_compatible`
connections; models of the GLM / DeepSeek / Kimi / MiniMax / Qwen / gpt-oss class. Licence (GLM-5.3 reportedly left MIT),
origin (some security teams reject Chinese-origin models) and data handling (zero retention, region, DPA) are per-org
decisions. For Indian customers the DPDP Act makes us a processor; US-only inference needs a stated cross-border
position. [unverified: provider facts come from vendor pages and secondary sources.]

## 4. What has been built, in order [built unless noted]

1. **Provider layer (commit `0656c24`).** Connections carry data-handling declarations (`zdr`, `no_training`, `region`,
   `dpa_signed`; unknown stays unknown) and `request_extras` (for example OpenRouter's `provider.zdr`; an OpenRouter
   connection claiming `zdr` must send it). A metadata-only model catalog (`providers/model_catalog.py`) and example
   connection files. No prices or provider model ids are seeded: they must be verified.
2. **Executors with open-model profiles (`fc41dcd`, `7ced32b`)** in `packaging/npm`: `exec.json` "profiles" map a model to an
   endpoint and the *name* of the env var holding the user's own key. `claude` (Anthropic-compatible endpoint),
   `opencode` (OpenAI-compatible, inline config) and `stealth` (our own small agent, `lib/agent/step_agent.mjs`, zero
   dependencies, step and cost caps, path sandbox, key withheld from shell). The `claude` child gets the vendor key only
   (the user's other credentials removed, an empty config dir, background traffic and experimental betas disabled).
3. **Orchestrator endpoint (`a737465`, `e09d4cb`).** `POST /v1/chat/completions` (tools, streaming), `POST /v1/messages`
   (Claude Code), `POST /v1/responses` (Codex, stateless), `GET /v1/models`. All run the same governed path: visibility,
   data class, egress policy, cost cap, endpoint safety, key rotation and failover, and reserve-then-settle against the
   org's budgets. A real `claude -p` run against the Messages endpoint with a fake provider returned the model's text and
   found two bugs (system turns inside `messages`; `max_tokens` above 16000), both fixed.
4. **Docs.** `docs/provider_catalog.md`, `packaging/npm/OPEN_MODEL_EXECUTORS.md`.
5. **Drafts, committed 2026-10-09:** `docs/testingplan.md` (a 140-task Core across the customer's own domains: SQL, debugging,
   Git, data science / Python / JS / Node.js, SuperGLUE-style evals, HTML pages, Figma reporting, validation frameworks),
   and `docs/research/llm-judge-vs-execution-and-equivalence-methodology.md` (keep correctness executable; audit the
   checkers; a calibrated judge only for open-ended extras).

Not verified: any live provider; Codex against `/v1/responses`; OpenCode's inline provider block against the binary;
the step agent's quality; Anthropic's position on pointing Claude Code at a non-Claude endpoint.

## 5. How the MCP should work in the ideal case [proposal]

### 5.1 Principles
1. **Small surface.** Three tools in context (`find_ways`, `discover_tools`, `use_tool`), everything else discoverable.
   Every tool annotated read-only / destructive / open-world so clients can ask for confirmation correctly.
2. **The server owns credentials; the agent never sees a key.** Developers get short-lived, org-bound tokens through SSO.
   Provider keys live server-side (or in the customer's own vault), shown only as the last four characters.
3. **Governed by the organisation, not the tool.** One policy: kill switch, allowed providers, models, tools and data
   classes, monthly and per-user budgets, and (later) per-team limits. Worst case is reserved before a call and settled
   after; unknown cost is recorded as the upper bound, never zero.
4. **Evidence over opinion.** A procedure is recommended because of recorded outcomes. Routing is learned from
   `report_result` outcomes, and every claim about savings comes from a measured baseline.
5. **Honest scope.** A tool that cannot do something says so. Refusals are explicit and recorded. No fabricated success.
6. **Nothing is deleted.** Updates close the old validity window and append a new row (bi-temporal), with scope and
   provenance on everything stored.

### 5.2 The ideal flow for a task
1. The developer's agent (Cursor, Claude Code) receives a request. A rule or hook sends it to `find_ways` first.
2. `find_ways` returns a proven procedure when one applies (applicability is a hard gate, not a score) and a **model
   plan**: a ladder of units from cheap to strong, chosen from recorded outcomes and current prices, filtered by the
   org's allowlists and data classes.
3. Routing decides *how* to execute by task shape:
   - **Small, well-specified step** → a patch-only worker (`call_model` with the relevant files, returns a diff).
   - **Multi-step step** → an executor (`achieve`) running a harness (`stealth`, `opencode`, optionally `claude`) in an
     isolated worktree with the node's checks, returning a verified diff and its cost.
   - **Hard or ambiguous** → stays with the top-level model.
4. The result is **checked by executable verification** (tests, scope check), not by the model's own say-so. A failing
   check escalates one rung up the ladder.
5. `report_result` / `report_model_run` write the outcome (model, scaffold, cost, accepted) back to the substrate, so the
   next plan is better. Reports that cannot be accepted are held, not guessed.
6. The developer or the calling model reviews the diff and applies it. We never write to the user's checkout silently.

### 5.3 What each tool should be in the ideal case
- **`find_ways`**: stable, cheap, fast (one embedding, a few judge calls, bounded); returns procedures plus the model
  plan; labels contributed text as untrusted data; never writes.
- **`call_model`**: three modes. *Answer* (today). *Patch* (files in, diff out, applied by the caller). *Agent* (a harness
  runs in a sandbox and returns a diff and a summary). Long runs return a task id and are polled, not held open. All modes
  stream progress, honour `max_cost_usd` and `data_class`, and fall back across endpoints and models without hiding which
  one answered.
- **`achieve` / `run_status` / `run_result` / `apply_run` / `cancel_run`** (the local executor server): asynchronous,
  cancellable, with hang detection, scope enforcement, redaction of everything stored, and a refusal to run an adapter
  whose flags have not been checked against the installed binary.
- **Report tools**: idempotent, attributable, with the org recorded on every outcome so acceptance rates are per-org.
- **The OpenAI / Anthropic / Responses endpoints**: not MCP tools but the same governance, for harnesses where a routed
  model is the main model.

### 5.4 Transport and state
- Stateless HTTP transport so any client works and any instance can answer. Long-running work lives in a **durable task
  store** (today's task store is in memory, which is why `--workers 1` is load-bearing). Restart must not lose a task.
- Streaming for chat endpoints; progress events for long tasks; cancellation honoured end to end.
- Per-user rate limits on every endpoint that spends money; the LLM-spend counters and the org ledger both record.

### 5.5 Installation with the least friction
- Configuration is generated, not hand-written: one `stealthlab-connect init` writes `.cursor/mcp.json`,
  `.mcp.json` / `.claude/settings.json`, `opencode.json`, `.codex/config.toml` for the tools it detects. Admins push the
  same through Claude Code's managed settings and Cursor's team marketplace.
- Cursor and Claude Code remain MCP clients only. Replacing the model needs a harness the user controls.

### 5.6 What the organisation sees [proposal]
Spend by day, user, team, model, provider and connection; budget burn; latency (p50/p95/p99) and our own overhead;
error rate per connection; refusals by reason; acceptance rate per model; savings against a written baseline (cost on the
default model minus actual cost) once the baseline is defined; the policy editor; the kill switch; audit export. Roles:
owner, admin, member, viewer today; team lead, executive and auditor are typed in the UI but need backend support.
Aggregates only on the executive view.

### 5.7 Safety rules the MCP must keep
- Prompts stored only after redaction (`trace_redaction`); no transcripts leave from Cursor hooks, only outcome metadata.
- Agents act only inside a throwaway worktree with a scope allowlist; shell access withholds provider keys.
- Untrusted text (contributed ways, repo content) is never treated as instructions.
- A provider's data-handling flags are declarations until a DPA says otherwise; unknown is never read as "yes".
- Never relay, pool or proxy a user's Claude / Codex / Cursor subscription; never ship "Claude", "Codex" or "GPT" in a
  product name.

## 6. What is left [as of the last push]

**Before a company pilot**
1. A live probe with real provider keys (base URLs, zero-retention option, cost and usage reporting, streaming usage).
2. Run the real clients against the endpoints: Codex against `/v1/responses`, OpenCode and Cline against
   `/v1/chat/completions`.
3. Developer tokens for harnesses (SSO login that issues short-lived keys). Not built.
4. Org policies must list `chat_completions` in `allowed_tools`; the admin policy page should show it.
5. Provider paperwork: DPAs, retention and region in writing, OpenRouter's resale clause, model licences, Anthropic's
   answer on non-Claude endpoints, Cursor's position.
6. Real prices and provider model ids in the catalog; without them budgets cannot work.

**Next**
7. Ledger and dashboard: `fell_back`, zero-retention and region per call (telemetry session owns the migration);
   per-connection breakdowns; a connections list and key management; teams; the missing roles; a savings baseline.
8. Harness presets from `stealthlab-connect init`.
9. `call_model` patch and agent modes; a durable task store; the delegation trigger for Cursor.
10. Wire the benchmark suite into `experiments/harness/` and run a pilot (decision rule fixed before the run).

**Smaller**
11. A client that disconnects before reading its first byte leaves a budget hold open until the 30-minute cleanup.
12. Remove stale tool entries and the "~29 tools" docstrings.
13. The testing plan is a draft: pick the baseline model, confirm the dataset names, apply the review's proposed edits, then freeze it before any run.

## 7. Open questions
- Which model is the customer's baseline? Which company is the customer (LatentView's public headcount does not match 2,000)?
- What do "MergeEval", "Screen2Code" and "Figma reporting" refer to in the benchmark list?
- Anthropic: may a user run their own unmodified Claude Code against a non-Claude endpoint with the user's own provider key?
- Where should the savings baseline be taken from, and who signs off its definition?

## 8. Where things are

| Topic | File |
|---|---|
| MCP server and tools | `backend/app/mcp_server/server.py` |
| Provider connections, catalog | `backend/app/providers/` (`registry.py`, `service.py`, `chat.py`, `shims.py`, `model_catalog.py`) |
| OpenAI / Anthropic / Responses endpoints | `backend/app/api/chat_completions.py` |
| Org policy, ledger, budgets | `backend/app/services/org_governance.py`, `backend/app/api/org_admin.py` |
| Local executors and our step agent | `packaging/npm/lib/executors/`, `packaging/npm/lib/agent/` |
| Provider setup and checklist | `docs/provider_catalog.md` |
| Executor open-model notes | `packaging/npm/OPEN_MODEL_EXECUTORS.md` |
| Benchmark plan | `docs/testingplan.md` |
| Judge-vs-execution review | `docs/research/llm-judge-vs-execution-and-equivalence-methodology.md` |
