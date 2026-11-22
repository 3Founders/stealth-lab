# Recurring tasks, triggers and events (web-first)

Research and design note, 2026-11-09. Scope: `routing_kell_frontend/` (the Next.js site on Vercel) and
`backend/` (FastAPI on Railway), with `desktop/` as an **optional** local executor for tasks that need files on
the person's machine.

**Status: research + design only. Nothing in this document is built.**

The short version:

- A person must be able to create, run, pause, edit and review a recurring task **entirely in the browser**,
  with no desktop app installed. That is the bar Claude web, ChatGPT web and Cursor cloud already meet.
- The primary shape is therefore **web UI → backend control plane → cloud executor**. The backend owns the
  automation definition, triggers, run ledger, retries, event dedupe, approvals, notifications and billing.
- The cloud executor runs the actual agent: a sandbox with a repo clone for coding work, a model/tool loop for
  non-code work, GitHub Actions for a repo-only fallback, or the existing delegate relay for bring-your-own
  agents. "Cloud" means the user's laptop can be closed.
- The desktop app is a **second, optional executor**, not a prerequisite. It is for tasks that genuinely need
  local files, local tools or a local MCP server. A web user should never be told "install the desktop app" to
  schedule a normal task.
- Do **not** put the scheduler in a Vercel function. Vercel hosts the UI and can tick a URL; the durable
  schedule, run ledger, idempotency and event dedupe belong in the backend/Postgres.
- MVP order: web/backend scheduled tasks + one cloud executor first; event triggers second; cloud code
  execution hardening third; approvals/org controls/observability fourth; desktop-local executor last.

---

## 1. What exists today, and what is missing

| Surface | What is there now | What is missing for web-first scheduling |
|---|---|---|
| `routing_kell_frontend/` | Next.js site on Vercel; `/chat` rooms, `/console`, `/account`, `/org`, `/docs`; Supabase session; backend API client (`lib/api.ts`, `lib/kel-api.ts`); SSE helper (`lib/sse.ts`). | No `/scheduled` or automations route, no schedule object, no run history, no approvals inbox, no webhook management. The site has no server-side worker; it is request/response. |
| `backend/` | FastAPI on Railway; Postgres/Neon; in-process ingestion loop (`app/services/ingestion_scheduler.py`); durable execution runs and retry/resume (`app/api/runs.py`, `app/execution/`); delegate relay task queue with approvals (`app/services/delegate.py`, `db/155_delegate_relay.sql`); rooms and room events (`db/157_rooms.sql`); room agent `@kell` (`app/services/room_agent.py`); execution sandbox (`app/services/sandbox.py`, `sandbox_executor.py`); audit, authn, org keys, wallet, cost governor, OTel/Sentry. | No user-facing automation object, no scheduler for it, no trigger/event tables, no run ledger for agent sessions, no webhook gateway, no cloud worker that can run a coding agent for a web user. |
| `desktop/` | Electron shell, Next UI, local `opencode serve` engine, prompt streaming, permission replies, session history, changes/undo, project picker, Rooms view, single-instance lock, auto-update. | No timer, no schedule object, no run history, no tray/background mode, no notifications. It also quits when the last window closes (`desktop/electron/main.ts:177`). **This is not the answer for web users; it is an optional local executor.** |

The backend already has the right primitives: a background-loop pattern, a durable run/retry pattern, a
pull-based task queue with approvals, rooms as an event log, a room agent, a sandbox, audit, authn, org keys,
wallet/cost governor and OTel/Sentry. The work is a control plane, a cloud executor and a web UX.

### 1.1 What "without desktop" actually requires

A web-only scheduled task needs all of these:

1. A definition stored server-side and scoped to the signed-in account.
2. A server-side scheduler that survives deploys and does not depend on any browser tab.
3. A cloud execution environment that can run the task with the right tools, repo and credentials.
4. A run ledger and result inbox the user can open in the browser.
5. Notifications that reach the user without the desktop app (web push, email, Slack, room).
6. Billing/quota enforcement, because the run spends server-side money.
7. A clear "this task needs local files" fallback that offers the desktop app, rather than silently failing.

If any one of these is missing, the feature is not web-first; it is a desktop feature with a web button.

---

## 2. Vocabulary

Use these terms consistently in code and UI.

| Term | Meaning |
|---|---|
| **Automation** | The saved definition: name, instructions/prompt, model, tools, permissions, executor, environment, destination, budget. The market also calls this a "scheduled task", "routine", "automation" or "agent". |
| **Trigger** | Why a run starts: a schedule, a webhook event, a GitHub/Slack/Linear event, a room mention, a file change, a manual "Run now". One automation can have several triggers. |
| **Schedule** | The time part of a trigger: cron, RRULE, preset, or one-off timestamp, with an IANA timezone. |
| **Occurrence** | One logical due time of a schedule. It has a stable identity so a retry or a second scheduler cannot run it twice. |
| **Run** | One execution of an automation. It has a status, attempt count, logs, cost, artifacts, and an optional agent session id. |
| **Event** | An inbound signal (webhook, GitHub delivery, Slack event, room message, file change). It is deduplicated by source + event id. |
| **Approval** | A pause in a run waiting for a person to allow or reject a tool call or an external side effect. |
| **Artifact** | A result worth keeping: a diff, PR link, file, report, room post, message. |
| **Executor** | Where the run happens: `cloud` (backend worker/sandbox), `sandbox` (managed microVM), `github_actions`, `delegate` (a registered agent pulls the task), `http` (call an external endpoint), `local` (desktop, optional). |

Two useful axes:

- **Time vs event**: a schedule is predictable and can be precomputed; an event is bursty, duplicated and out
  of order. They need different admission and dedupe logic.
- **Cloud vs local**: cloud is always on and is the default for web users; local has the person's files and
  tools but only runs while the machine and app are awake. The creation flow should show this trade-off, not
  hide it.

---

## 3. What the leading products do

This is a snapshot from the vendors' own docs on 2026-11-09. Product behaviour changes; the patterns are what
matter.

| Product | Schedule | Event triggers | Where it runs | Notable expectations |
|---|---|---|---|---|
| **Claude web / Cowork** | Hourly, daily, weekly, weekdays, or manual; create with Claude or set up manually. `claude.ai/scheduled-task`, reachable from the sidebar's "Scheduled" row. | The web page is schedule/on-demand first; event triggers are a Claude Code cloud feature. | **Remote by default**, so tasks fire when the computer is asleep or the app is closed. Tasks that need a local folder/browser/screen still need the computer awake and online, and the page says so. | Templates (daily briefing, inbox triage, weekly review), approval mode, optional model, optional folder, "Create with Claude" asking clarifying questions before scheduling. |
| **Claude Code** | Cloud: hourly/nightly/weekly or one-off; custom cron, minimum once per hour. Desktop: presets + custom cron; forms reject faster than hourly, but Claude can set down to 1 minute. `/loop` inside a session: 1 minute minimum. | Cloud: API trigger (per-task bearer token, shown once) and GitHub events; one task can combine schedule + API + GitHub. | Cloud runs on Anthropic infrastructure (or an org's self-hosted environment); Desktop runs on the machine; `/loop` is session-scoped. | Worktree isolation, per-task permission mode, run history including skipped reasons, one catch-up run for the most recent missed time in the last 7 days, deterministic jitter, pause/resume, Run now, org toggle. |
| **ChatGPT / Codex** | Scheduled tasks: one-time or recurring, up to hourly on paid plans; free is once per day. Codex automations: custom cron, or RFC 5545 RRULE for advanced schedules. | Event-triggered tasks from Gmail, Slack and GitHub; event tasks cannot be combined with a time schedule; 30 event runs/hour and 720/day across tasks. | Web tasks use uploaded context, connected tools, skills and plugins, but cannot work directly in a local folder. Desktop tasks can use local projects/worktrees. Event triggers are web/mobile only. | Triage inbox, "nothing to report" auto-archive, thread automation (continue the same chat) vs standalone (new chat), sandbox/approval policy, worktree, share task link, plan-based active-task limits (3/5/10/15), auto-pause. |
| **Cursor** | Scheduled triggers: presets or cron; may run late but not early. | GitHub/GitLab/Bitbucket source control, Slack messages/reactions, generic webhooks, Linear, Sentry, PagerDuty. Multiple triggers per automation; any trigger fires. | Cloud agents in a cloud sandbox; no repository, single repo, or multi-repo environment. The user's machine is not required. | Tools per automation, memories across runs, service accounts, run-as identity, PR creation/commenting, approvals, billing per cloud-agent usage, private/member visibility. |
| **Gemini** | Daily/weekly/monthly scheduled actions; up to 10 active; content prepared in the hour before delivery. | No general event triggers in the scheduled-actions feature. | Google-hosted; web, mobile and Mac app. | Notifications, pause/resume, edit from the schedule's chat, auto-off after inactivity, connected apps. |
| **Lindy** | Daily/weekly/monthly schedules, plain-English creation. | Email received/sent, Slack message, calendar event starting/ending; "smart filter" in plain English. | Hosted. | Trigger + prompt + destination as the model; dry run on real data with no writes; convert a routine to a script; delivery to chat/SMS/Slack. |
| **Zapier / n8n** | Schedule by Zapier; n8n schedules hourly/daily/weekly/monthly/custom cron; n8n runs only the published version. | 9,000+ app triggers, webhooks, app events, chat, form, manual. | Hosted or self-hosted. | Human-in-the-loop approval steps; run history; task-based billing; n8n shows next/last run and outcome. |
| **Relay.app** | Custom schedule (working days, first Monday, every 15 minutes). | App/webhook triggers; most poll hourly. | Hosted. | Triggers only fire after activation; past events are not backfilled; auto-off on persistent errors; human-in-the-loop steps. |

### 3.1 The web-first evidence

The products that do this well all let a person schedule and receive results without installing anything:

- **Claude web** has a Scheduled page at `claude.ai/scheduled-task`, reached from the sidebar. Scheduled tasks
  run remotely by default, so they fire when the computer is asleep or the app is closed. Only tasks that need
  a local folder, browser or screen require the computer; the page says so.
  [Claude Help Center](https://support.claude.com/en/articles/13854387-schedule-recurring-tasks-in-claude-cowork)
- **Claude Code cloud** has `claude.ai/code/routines`: a saved prompt + repos + connectors, with schedule,
  API and GitHub triggers; it runs on Anthropic-managed infrastructure or an org's self-hosted environment.
  [docs](https://code.claude.com/docs/en/routines)
- **ChatGPT web** creates scheduled tasks in `Scheduled`; web tasks can use uploaded context, connected tools,
  skills and plugins but cannot work directly in a local folder. Event triggers are web/mobile only.
  [docs](https://help.openai.com/en/articles/10291617-scheduled-tasks-in-chatgpt)
- **Cursor cloud agents** run in a cloud sandbox from the web dashboard; the user's machine is not required.
  [docs](https://cursor.com/docs/cloud-agent/automations)
- **Gemini** schedules actions in the Gemini web app and notifies there/mobile.
  [docs](https://support.google.com/gemini/answer/16316416)

The pattern is consistent: **the web page is the product; the cloud is the executor; local is an exception for
local files.**

### 3.2 What the infrastructure products contribute

These define the reliability vocabulary the user-facing products are built on.

- **Trigger.dev** scheduled tasks: IANA timezone, `lastTimestamp`, `upcoming` next 10 runs, multiple schedules
  per task, TTL for stale runs when the previous run is still executing.
  [docs](https://trigger.dev/docs/tasks/scheduled)
- **Inngest** cron: timezone prefix, jitter (1 second to 5 minutes), explicit DST warning, fan-out, pause after
  20 consecutive failures.
  [docs](https://www.inngest.com/docs/durable-execution/guides-and-advanced/events-and-triggers/schedules-and-delayed-starts)
- **Temporal** schedules: durable, replayable, server-owned; the "heartbeat" that keeps an ambient agent alive
  across worker restarts, with a deterministic audit trail.
  [article](https://temporal.io/blog/orchestrating-ambient-agents-with-temporal)
- **Vercel Workflows**: durable `workflow`/`step`, `sleep` for minutes to months, hooks for external events,
  deterministic replay across deploys.
  [docs](https://vercel.com/docs/workflows)
- **Vercel Cron Jobs / Schedules**: legacy Cron Hobby is once per day with up to a 59-minute window; the newer
  Schedules product is a public beta (Nov 2026) with per-schedule timezones, private function targets and
  runtime-created schedules. Hobby Schedules are at most every 15 minutes; Pro/Enterprise default jitter is
  1 minute. Function duration limits still apply.
  [cron limits](https://vercel.com/docs/schedules/cron-jobs/usage-and-pricing),
  [migration/Schedules](https://vercel.com/docs/schedules/migrate-from-cron-jobs),
  [changelog](https://vercel.com/changelog/vercel-schedules-is-now-in-public-beta)
- **Railway cron**: starts a service on a crontab, UTC, minimum 5 minutes between runs, and skips a run if the
  previous execution is still active. The service must exit.
  [docs](https://docs.railway.com/cron-jobs)
- **GitHub Actions `on.schedule`**: POSIX cron, UTC by default (optional IANA `timezone`), shortest interval
  5 minutes, runs from the default branch, can be delayed or dropped under load.
  [docs](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#onschedule)
- **Supabase Cron / `pg_cron`**: every second to once a year, SQL or HTTP; the docs recommend no more than 8
  concurrent jobs and no job longer than 10 minutes.
  [docs](https://supabase.com/docs/guides/cron)
- **Upstash QStash**: cron schedules with `CRON_TZ=` IANA timezones, retries, callbacks, failure callbacks.
  [docs](https://upstash.com/docs/qstash/features/schedules)
- **BullMQ**: job schedulers with `tz`, `upsertJobScheduler` for idempotent schedule changes; one delayed job per
  scheduler.
  [docs](https://docs.bullmq.io/guide/job-schedulers/repeat-options)
- **Postgres `FOR UPDATE SKIP LOCKED`**: a single atomic claim query, a partial index on pending rows, and a
  status flip in the same statement; the practical no-Redis queue for this scale.
  [article](https://www.prisma.io/blog/you-dont-need-a-job-queue-postgres-already-has-skip-locked)
- **OWASP Webhook Security**: HMAC over the raw body, timestamp tolerance, constant-time compare, replay cache,
  persisted event ids, idempotent side effects, no in-order assumption.
  [cheat sheet](https://cheatsheetseries.owasp.org/cheatsheets/Webhook_Security_Cheat_Sheet.html)
- **OpenAI Agents SDK human-in-the-loop**: `needs_approval` on tools, `interruptions`, serializable `RunState`,
  resume after approve/reject.
  [docs](https://openai.github.io/openai-agents-python/human_in_the_loop/)
- **MCP Tasks extension**: long-running MCP calls return a durable task handle with `working`,
  `input_required`, `completed`, `failed`, `cancelled`; clients poll, provide input, and resume after a
  disconnect.
  [docs](https://modelcontextprotocol.io/extensions/tasks/overview)

### 3.3 Cloud execution is a solved pattern, but not a trivial one

A cloud coding agent needs a control plane and a data plane. The useful reference is OpenInspect: the control
plane manages session state, WebSocket streams, GitHub integration, encrypted repo secrets and sandbox
lifecycle; the data plane starts a sandbox, restores/clones the repo, launches OpenCode, and streams tool calls,
logs, screenshots and artifacts back. Work can start from the web app, Slack, GitHub, Linear, cron, Sentry or a
webhook, and all of them attach to the same session model.
[OpenInspect](https://murraycole.com/projects/openinspect)

Sandbox providers differ in isolation, cold start, pause/resume, session caps and cost; E2B, Modal, Daytona,
Cloudflare Sandbox, Vercel Sandbox and others all fit different constraints. E2B uses Firecracker microVMs and
memory-preserving pause; Modal uses gVisor and GPU sandboxes; Daytona offers long-lived containers/VMs; Vercel
Sandbox has 45-minute Hobby / 24-hour Pro sessions; Cloudflare Sandbox runs a container in its own VM and can
sleep after idle.
[E2B comparison](https://e2b.dev/resources/best-code-execution-sandboxes-for-ai-agents),
[Cloudflare coding-agent runner](https://developers.cloudflare.com/sandbox/get-started/build-a-coding-agent-runner/)

The important safety pattern from cloud coding products:

- Each run gets a fresh sandbox, deleted when the run ends.
- The clone token is used to clone and then removed from the repo settings inside the sandbox before the agent
  starts; the push happens from a separate sandbox the agent never touched.
- The agent cannot merge; it opens a PR for review.
- Network access is blocked or allowlisted; commands run as an unprivileged user; a time limit applies.
- If the diff contains a secret value, nothing is pushed.
- Every command is shown in the run log.

This is the level of care a web-only coding executor needs. A backend API process running an agent with the
repo token and full network access is not an acceptable shortcut.

---

## 4. What "SOTA expected" means

This is the checklist to design against. "MUST" is table stakes for a credible v1; "SHOULD" is what users will
compare against Claude/Cursor/ChatGPT; "LATER" is real but can wait.

### 4.1 Web-first expectations

| | Expectation | Why |
|---|---|---|
| MUST | Create, edit, pause, resume, delete, run now and review history entirely in the browser. No install, no CLI. | This is the user's explicit requirement and what Claude web/ChatGPT web/Cursor cloud do. |
| MUST | Runs continue when the user's laptop is closed/asleep. | The point of cloud scheduling; local tasks are the exception. |
| MUST | Results are visible in a web inbox and can be delivered to email/Slack/room/web push. | A result that only exists in a desktop app is not web-first. |
| MUST | Account-scoped definitions, org scoping, auth and billing on the server. | The browser cannot be the source of truth. |
| MUST | The creation flow shows where the task will run (cloud vs local) and what it can reach (repo, connectors, network). | Users must understand the blast radius and why a local-folder task needs the desktop app. |
| SHOULD | Mobile-friendly list/detail/approve, and share links/templates. | ChatGPT/Gemini/Claude all meet users on mobile. |
| SHOULD | A "needs local files" fallback: explain that this task requires the desktop app or a cloud project, rather than failing at run time. | Claude is explicit that local-folder tasks only run locally. |

### 4.2 Creation and management

| | Expectation | Why |
|---|---|---|
| MUST | Create from plain English ("every weekday at 9, review new PRs") **and** a manual form. | Every leading product does this. The natural-language path must show the parsed schedule and require confirmation; never create silently from model output. |
| MUST | Presets (hourly/daily/weekdays/weekly/monthly) plus custom cron; RRULE for "first Monday", "last working day", "every other Tuesday". | Cron's day-of-month/day-of-week OR semantics make "first Monday" wrong; RRULE is the correct tool. |
| MUST | Timezone picker, defaulting to the account/device timezone; store the IANA name, not an offset. | Travel and DST break offset-only schedules. |
| MUST | Next 5 runs preview, with a DST warning when an occurrence is skipped or repeated. | Users need to see what the machine will actually do. |
| MUST | Edit, duplicate, pause/resume, delete, Run now. | Basic control. Deleting should archive runs, not erase the audit trail. |
| MUST | Active/paused state visible in the list; next run and last run/outcome at a glance. | The list is the product's home screen. |
| SHOULD | Templates ("daily briefing", "inbox triage", "weekly review", "daily PR review", "dependency audit", "failing CI triage", "docs drift", "release notes", "flaky test hunter"). | Claude web ships starter templates; Cursor and Lindy lean on them. |
| SHOULD | Dry run: real reads, real model calls if needed, no writes/sends/posts. | Lindy's dry run is the trust-building feature; it catches bad prompts before unattended runs. |
| SHOULD | Share/export/import a definition (link or `.kell/automations/<slug>.md`). | ChatGPT shares task links; a repo-native file fits kell's `.kell` model. |
| LATER | Calendar view, bulk actions, folders/tags, search. | Useful at scale, not needed for v1. |

### 4.3 Schedule semantics

| | Expectation | Why |
|---|---|---|
| MUST | Cron (5-field, optional seconds where supported), RRULE, one-off timestamp. | Covers simple and complex cadences. |
| MUST | Explicit DST policy: skip nonexistent local times; run ambiguous times once (first occurrence); store the canonical UTC occurrence. | Every scheduler eventually hits this; hiding it produces duplicate or missing runs. |
| MUST | Catch-up policy per trigger: `skip`, `once` (most recent missed only), `bounded` (last N), `all` (rare, ledger-like). Default `once` for daily/weekly, `skip` for frequent tasks. | Claude catches up once in 7 days; Trigger.dev uses TTL; Railway skips. There is no universal default, so make it explicit. |
| MUST | Overlap policy per trigger: `skip`, `queue`, `cancel`, `parallel` (with a cap). Default `skip` for code-changing tasks. | A second run must not edit the same worktree as the first. |
| MUST | Maximum runtime and a stale-run TTL; cancel and mark `timed_out` when exceeded. | Unattended agents can hang on a prompt, a network call or a permission ask. |
| MUST | Deterministic jitter (for example hash(automation_id) modulo a window), capped at a few minutes; never start before the scheduled time. | Avoids a thundering herd at 9:00 and keeps tests reproducible. Claude uses a deterministic offset; Inngest uses a random window. |
| MUST | Minimum interval per executor/plan; reject or warn on schedules that are too frequent. | ChatGPT free is daily; Claude cloud is hourly; Railway is 5 minutes; Vercel Hobby is daily/15 minutes. |
| SHOULD | Natural-language parsing via a real parser (`chrono-node`, `dateparser`, or similar), not only an LLM. Show the normalized cron/RRULE and the next runs. | LLM-only parsing is unpredictable and hard to edit. |
| SHOULD | Pause on repeated failure (for example 5-20 consecutive), notify, and require a human to resume. | Inngest pauses after 20; Relay turns the workflow off and emails. Silent infinite retries are worse. |
| LATER | Business-day calendars, holiday exclusions, "last working day", time-of-day windows. | Needed for finance/ops tasks; not v1. |

### 4.4 Event triggers

| | Expectation | Why |
|---|---|---|
| MUST | Generic inbound webhook per trigger, with a per-trigger secret, HMAC signature, timestamp tolerance, event-id dedupe, and a fast 202 before processing. | This is the universal integration point and the security boundary. |
| MUST | First-class GitHub triggers (PR opened/pushed/merged, review submitted, comment, CI completed, workflow run completed) via a GitHub App, with event filters. | The highest-value coding use case; Cursor and Claude both lead with it. |
| MUST | Room mention trigger: when `@kell` or a named agent is mentioned in a room, start a run with the room context. | kell already has rooms, mentions and a room agent; this is a natural differentiator. |
| SHOULD | Slack (message/reaction), Linear (issue created/status changed), Sentry (issue created/updated), PagerDuty (incident triggered/acknowledged/resolved). | These are the common "wake the agent" sources; Cursor supports all of them. |
| SHOULD | Event filters (field equals/contains/regex, labels, branch, author) and a "test with sample payload" flow. | Without filters, every event becomes an expensive run. |
| SHOULD | Event coalescing: batch a burst into one run, or drop with a reason when over a per-trigger rate limit. | Event storms are the classic failure mode; ChatGPT groups multiple events. |
| SHOULD | Treat the event payload as untrusted **data**, not instructions. Wrap it, label it, and never let it change tools, permissions or approval state. | Claude's API trigger explicitly wraps fire text as untrusted; prompt injection through a webhook is the obvious attack. |
| LATER | Email/Gmail, calendar, file changes, database changes, local Git hooks, custom app connectors. | Higher integration cost and privacy surface. File/Git triggers are useful in the desktop app but must be opt-in. |

### 4.5 Execution and reliability

| | Expectation | Why |
|---|---|---|
| MUST | Durable run ledger in Postgres, not in memory. | Restarts, deploys and worker crashes must not lose schedules or runs. |
| MUST | At-least-once execution with idempotent side effects. Exactly-once is not achievable across a model, a shell and a third-party API. | The honest contract; use idempotency keys for emails, PR comments, room posts, charges. |
| MUST | Unique occurrence identity `(automation_id, trigger_id, scheduled_for)` and a unique index; duplicate ticks create zero extra runs. | The core correctness invariant. |
| MUST | Atomic claim with `FOR UPDATE SKIP LOCKED` (or a lease) and a fencing token; a stale worker cannot finalize a newer run. | Multiple Railway instances / retries must not double-run or overwrite. |
| MUST | Retry with exponential backoff, max attempts, dead-letter state, and manual retry. | Transient provider/network failures are normal. |
| MUST | Heartbeat/lease extension for long runs; reconciliation sweep for stuck `running` rows. | A killed process leaves rows behind; someone must clean them. |
| MUST | Per-run and per-automation budget caps, plus a per-org cap; stop before the next model call when exhausted. | Unattended agents can spend real money; kell already has wallet/cost governor primitives. |
| SHOULD | Step checkpoints / durable execution for multi-step agent runs; resume after crash. | Trigger.dev/Inngest/Temporal/Vercel Workflows all sell this; kell's `execution_runs` has a retry/resume pattern to reuse. |
| SHOULD | Run status names aligned with MCP Tasks: `queued`, `running`, `needs_approval`, `succeeded`, `failed`, `cancelled`, `skipped`, `expired`. | One vocabulary across API, UI and MCP. |
| SHOULD | Outbox for notifications/deliveries in the same transaction as the run result. | Otherwise a crash loses the "it finished" message. |
| LATER | Multi-region scheduling, leader election, queue broker (Redis/SQS), sharded schedulers. | Postgres is enough until run volume is large. |

### 4.6 Permissions, safety and approvals

| | Expectation | Why |
|---|---|---|
| MUST | Per-automation permission mode: read-only, workspace-write, full access; tool allowlist; connector allowlist. | Claude/Cursor both make this per-task; unattended runs must not inherit "allow everything". |
| MUST | Unattended runs never hang forever on a permission prompt. Pause the run in `needs_approval`, notify, and resume after a decision (or expire). | Claude notes that MCP tools marked `requiresUserInteraction` stall every run; design for it. |
| MUST | Default deny for writes/network in unattended mode unless the task explicitly grants them; "always allow" is per tool and revocable. | The blast radius of a bad prompt is the tool permissions. |
| MUST | Approval is per tool call (or per side effect), with approve/reject/always, and the run resumes from the same step. | OpenAI's HITL `RunState` is the reference model. |
| MUST | Secrets live in the secret store, referenced by name; never in `.kell`, the prompt, the repo or logs. | `.kell` is shared/committed; prompts are copied/shared. |
| MUST | Webhook secrets are per trigger, stored hashed, shown once, rotatable; one leaked token cannot fire every automation. | Claude gives each task its own bearer token; Cursor generates a per-automation API key. |
| MUST | SSRF protection for any outbound URL/tool: block private ranges, metadata endpoints and DNS rebinding; allowlist where possible. | "Fetch this URL" is a standard agent tool and a standard SSRF hole. |
| MUST | Tenant isolation on every read/write; return 404 rather than 403 for another user's id; RLS on, anon/authenticated revoked, backend-only tables. | Follow `db/155_delegate_relay.sql` and `db/157_rooms.sql`. |
| MUST | Cloud sandbox isolation: fresh sandbox per run, unprivileged user, network allowlist, clone token removed before the agent starts, no push to the base branch. | A web-only coding executor runs untrusted model output and repo code; the host must not be exposed. |
| SHOULD | Org admin controls: enable/disable automations, allowed event providers, max concurrency, require approval for cloud writes, audit export. | Claude has an org toggle; Cursor has team visibility/service accounts. |
| SHOULD | "Run as" identity: me vs a service account, so an automation does not stop when an employee leaves. | Cursor does this per automation; important for team automations. |
| SHOULD | Audit every create/edit/pause/resume/delete/run-now/approve/secret-rotate with actor, time and redacted before/after. | Compliance and debugging. |
| LATER | E2E encryption for local-only automation definitions; private-room execution; BYO cloud runner. | Depends on the private-rooms/MLS work. |

### 4.7 Observability, results and notifications

| | Expectation | Why |
|---|---|---|
| MUST | Run history: status, trigger, scheduled time, start/end, duration, attempt, model, tokens, cost, session id, trace id, error code/message, skipped reason. | This is the first debugging surface. |
| MUST | Show skipped runs and why: budget exceeded, paused, approval pending, worker unavailable, rate limited, missed. | Claude shows skipped reasons; hiding skips makes the product look broken. |
| MUST | A result inbox: unread results, failures, approvals; "nothing to report" runs can auto-archive. | Codex's Triage is the pattern; not every run deserves a notification. |
| MUST | Notifications on failure, approval needed, and a result the user asked for; digest the rest. | Notification fatigue kills trust. |
| SHOULD | Per-run log with the tool calls, diffs, artifacts, and a link to the agent session. | Users need to see what happened, not just a status. |
| SHOULD | OTel spans + metrics + Sentry with redaction; correlate `automation_run_id` with the agent session and the model trace. | kell already has OTel/Sentry; extend the existing conventions. |
| SHOULD | Alerts: failure streak, scheduler lag, approval pending too long, budget 80%/100%, webhook rejection spike. | The scheduler itself needs an SLO. |
| LATER | Dashboards per org, cost per automation, success-rate trends, export to SIEM. | Team/enterprise features. |

### 4.8 Billing, quotas and plans

| | Expectation | Why |
|---|---|---|
| MUST | Record cost/tokens per run; show an estimate before saving; enforce a per-run and per-month cap. | Every commercial product bills automations; kell has a wallet and org keys. |
| MUST | Plan limits on active automations, minimum interval, runs/hour, event runs/hour and concurrency. | ChatGPT limits active tasks (3/5/10/15) and event runs (30/hour, 720/day); Gemini limits active schedules (10). |
| SHOULD | Auto-pause when the wallet/plan is exhausted; notify with the reason. | A silent failure looks like a bug; a surprise bill is worse. |
| SHOULD | Org pooling: team automations bill to the org, not a person. | Cursor's service-account model. |
| LATER | Overage pricing, prepaid packs, per-automation budgets, department chargeback. | Pricing work. |

### 4.9 Agent-specific expectations

| | Expectation | Why |
|---|---|---|
| MUST | New session per run by default, with an option to continue the same session ("thread automation"). | Codex distinguishes standalone vs thread; Claude starts a fresh session per task. Polling loops want continuity; code reviews want isolation. |
| MUST | Worktree/branch isolation for code-changing runs; show the diff; apply/merge is a human action. | Claude/Codex/Cursor all isolate; it keeps unattended edits away from unfinished work and never pushes to the base branch. |
| MUST | Per-automation model and effort (cheap vs strong, reasoning level) and a cost preview. | kell's cheap/strong lane is a differentiator; don't hide it. |
| MUST | Per-automation tools/connectors; no implicit "all MCP servers". | Cursor connects MCP per automation; Claude includes all connectors by default and tells you to remove them. |
| SHOULD | Optional memory (`MEMORIES.md` or similar) across runs, viewable/editable/deletable, treated as untrusted. | Cursor's memories improve recurring work but warn about poisoning. |
| SHOULD | Result destinations: inbox, room, Slack, email, PR comment, file. | Lindy's "destination" is part of the mental model. |
| SHOULD | Skills/templates and explicit skill invocation (for example `$review-pr`) so a run does not depend on implicit tool selection. | ChatGPT/Codex recommend this for maintainability. |
| LATER | Computer use, multi-repo runs, agent-to-agent delegation, self-created automations. | High risk/complexity; gate behind explicit permissions. |

### 4.10 Local is the exception, not the default

| | Expectation | Why |
|---|---|---|
| MUST | A task that needs local files must say so before creation; offer the desktop app or a cloud project. | Claude is explicit: local-folder tasks only run locally. |
| MUST | Local tasks run only while the desktop app is open and the machine is awake; catch up according to policy and notify. | The current desktop app quits on `window-all-closed`; that must change if local tasks are supported. |
| SHOULD | Tray/background mode, "Keep computer awake" setting, clear skipped reasons. | Claude/ChatGPT both expose this. |
| LATER | OS-level wake when the app is closed, with explicit install/uninstall. | Higher risk; cloud tasks are the better answer for always-on. |

---

## 5. Recommended architecture for kell

### 5.1 Principles

1. **Web-first.** A signed-in person can create, run and manage a task in the browser. The desktop app is an
   optional executor for local files, never a prerequisite.
2. **Cloud-first.** The default executor is a backend worker/sandbox that keeps running when the laptop is
   closed. Local is an explicit choice for local files/tools.
3. **One definition, several executors.** An automation is a saved prompt + triggers + policy. `executor`
   decides where it runs. Do not build a separate "chat task" and "code automation" product; ChatGPT has two
   systems and the docs spend paragraphs explaining the difference.
4. **Control plane in the backend, data plane in the executor.** The backend owns definitions, triggers, the
   run ledger, event dedupe, approvals, notifications and billing. The executor owns the sandbox/process and
   streams events back.
5. **Postgres is the queue until proven otherwise.** `FOR UPDATE SKIP LOCKED` + a partial index + a lease is
   enough for kell's scale; it avoids adding Redis/Celery/Temporal now. Revisit when run volume, multi-region or
   long-lived sleeps demand it.
6. **At-least-once, idempotent effects.** Every run has a stable occurrence/event key. Every external side effect
   carries the run id. Duplicates are expected and harmless.
7. **The event payload is data, not instructions.** Wrap it, label it, and never let it grant permissions or
   approve actions.
8. **A web-only coding executor needs a real sandbox.** Do not run model-generated code in the API process with
   the repo token and full network access. Fresh sandbox, unprivileged user, network allowlist, clone token
   removed, push from a separate sandbox, PR-only.

### 5.2 Topology

```
Browser (routing_kell_frontend on Vercel)
  /scheduled            list / detail / create / run history / approvals / inbox
        |
        | HTTPS + Supabase session
        v
Backend (FastAPI on Railway)
  /v1/automations       CRUD, triggers, runs, approvals
  automation_scheduler  asyncio loop or dedicated tick service
  Postgres/Neon         automations, triggers, runs, events, approvals, outbox
  event gateway         POST /v1/automation-hooks/{trigger_id}
  cloud executors       sandbox / github_actions / delegate / http
  notifications         web push, email, Slack, room, in-app inbox
        |
        v
Cloud data plane (one of)
  managed sandbox (E2B / Modal / Daytona / Cloudflare Sandbox / Vercel Sandbox)
  Railway worker container (only with a real sandbox boundary)
  GitHub Actions (repository_dispatch)
  a registered agent via the delegate relay
  an external HTTP agent

Optional: Desktop (Electron) as a local executor
  local scheduler -> opencode serve -> local files / local MCP
  shows cloud tasks read-only or manages both
```

The browser talks only to the backend. It never holds the schedule or the run state. The desktop app is a
separate client that can register as a `local` executor and show cloud tasks, but nothing in the web path
depends on it.

### 5.3 Cloud execution options

This is the central build decision. The interface (`Executor.start/poll/cancel/approve`) should hide the
choice, so kell can start with one and add another without rewriting the scheduler.

| Option | Isolation | Best for | Trade-offs |
|---|---|---|---|
| **Backend model/tool loop** | Process-level, not a code sandbox | Non-code tasks: summaries, inbox triage, monitoring, reports, room answers. | Cannot safely run arbitrary repo code or shell commands. |
| **Managed sandbox** (E2B, Modal, Daytona, Cloudflare Sandbox, Vercel Sandbox) | MicroVM/container, fresh per run, network policy, pause/resume | The default for cloud coding tasks: clone repo, run OpenCode/Claude Code, stream logs, open PR. | New vendor, cost, cold start, secrets/network setup, sandbox lifecycle. |
| **Railway worker container** | Container, but you own the boundary | Early MVP if the task does not run untrusted code; or as the orchestrator that calls a sandbox. | Not a security boundary for arbitrary code; do not let it run model-generated shell commands with repo tokens. |
| **GitHub Actions** via `repository_dispatch` | GitHub-hosted runner, repo-scoped | Repo-only tasks (tests, dependency audit, PR review) with no new sandbox vendor. | Requires a workflow in the repo; 5-minute minimum; UTC; can be delayed; secrets in GitHub; less seamless than a sandbox. |
| **Delegate relay** | The registered agent's machine | Bring-your-own agent (a hosted agent, a colleague's runner, a CLI). | Not always-on unless the agent is online; not a true "web user without a machine" solution. |
| **External HTTP agent** | The vendor's | Integrations with an existing agent platform. | Trust, auth, data residency, observability. |

Recommendation:

- **Non-code tasks:** start with the backend model/tool loop; it reuses `room_agent`/`chat` and needs no
  sandbox.
- **Coding tasks:** the target is a managed sandbox with a repo clone and PR flow. Pick one provider after a
  spike; E2B or Cloudflare Sandbox are good general starting points, Modal if GPU/ML work matters, Vercel
  Sandbox if the work stays on Vercel.
- **Zero-infrastructure fallback:** GitHub Actions `repository_dispatch` for repo-only tasks. It is web-only
  and requires no desktop, but it is not the default UX because it needs a workflow file and has a 5-minute
  floor.
- **Do not** use the delegate relay as the only cloud path. It is useful for bring-your-own-agent, but a web
  user should not need a machine online to use a scheduled task.

### 5.4 Where definitions live

| Kind | Location | Sharing |
|---|---|---|
| Personal cloud automation (default) | Backend `automations` table, owner = Supabase subject. | Across the user's devices; managed in the browser. |
| Team automation | Backend, `org_id` + `run_as` service account, or a committed `.kell/automations/<slug>.md`. | Org members; opt-in import for repo files. |
| Personal local automation (optional) | Desktop `userData` (SQLite or versioned JSON). | This device only; never required for a web task. |
| Ephemeral session loop | Session state (like Claude `/loop`). | Not saved; ends with the session. |

A repo-native definition can be a Markdown file with YAML front matter, in the spirit of `.kell/` and `SKILL.md`:

```markdown
---
name: Daily PR review
description: Review new pull requests against the team checklist.
schedule: "0 9 * * 1-5"
timezone: America/New_York
executor: cloud
model: cheap
permissions: workspace-write
deliver: inbox
---

Review the pull requests opened since the last run. Leave inline comments for
security, performance and style. If there is nothing to review, say so in one line.
```

Rules for this file: no secrets, no absolute paths, no machine-specific settings; import requires a confirmation
because a committed schedule could otherwise start running on every teammate's account. The backend/desktop
should store a `source: repo` marker and an explicit opt-in.

### 5.5 Data model (backend)

Migration: the next free number is `186` (highest existing is `185_zdr_provider_policies.sql`). Follow the
RLS/revoke pattern from `155_delegate_relay.sql` and `157_rooms.sql`: RLS on, `anon`/`authenticated` revoked,
backend-only, every row scoped by `owner_sub` and optional `org_id`.

```sql
-- 186_automations.sql (sketch, not final DDL)

CREATE TABLE automations (
    id                uuid PRIMARY KEY,
    owner_sub         text NOT NULL,
    org_id            uuid,
    name              text NOT NULL,
    description       text,
    prompt            text NOT NULL,
    prompt_hash       text NOT NULL,
    version           integer NOT NULL DEFAULT 1,
    executor          text NOT NULL CHECK (executor IN
                      ('cloud','sandbox','github_actions','delegate','http','local')),
    environment_id    uuid,                                  -- cloud env: image, network, secrets
    model             text,
    effort            text,
    permission_mode   text NOT NULL DEFAULT 'workspace-write'
                      CHECK (permission_mode IN ('read-only','workspace-write','full')),
    tools             jsonb NOT NULL DEFAULT '[]'::jsonb,   -- allowlist; empty = default safe set
    connectors        jsonb NOT NULL DEFAULT '[]'::jsonb,
    repo_provider     text,                                  -- github | gitlab | none
    repo_owner        text,
    repo_name         text,
    repo_ref          text,
    worktree          boolean NOT NULL DEFAULT true,
    session_mode      text NOT NULL DEFAULT 'new' CHECK (session_mode IN ('new','continue')),
    deliver           jsonb NOT NULL DEFAULT '{"channel":"inbox"}'::jsonb,
    budget_usd        numeric(10,4),
    max_runtime_s     integer NOT NULL DEFAULT 1800,
    status            text NOT NULL DEFAULT 'active' CHECK (status IN ('active','paused','archived')),
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE automation_triggers (
    id                uuid PRIMARY KEY,
    automation_id     uuid NOT NULL REFERENCES automations(id) ON DELETE CASCADE,
    kind              text NOT NULL CHECK (kind IN
                      ('schedule','webhook','github','slack','linear','sentry','pagerduty','room','file','git','manual')),
    enabled           boolean NOT NULL DEFAULT true,
    cron              text,
    rrule             text,
    timezone          text NOT NULL DEFAULT 'UTC',           -- IANA name
    once_at           timestamptz,
    event_source      text,
    event_filter      jsonb NOT NULL DEFAULT '{}'::jsonb,
    secret_hash       text,
    secret_hint       text,
    catchup_policy    text NOT NULL DEFAULT 'once'
                      CHECK (catchup_policy IN ('skip','once','bounded','all')),
    catchup_limit     integer NOT NULL DEFAULT 1,
    overlap_policy    text NOT NULL DEFAULT 'skip'
                      CHECK (overlap_policy IN ('skip','queue','cancel','parallel')),
    max_parallel      integer NOT NULL DEFAULT 1,
    jitter_seconds    integer NOT NULL DEFAULT 0,
    min_interval_s    integer,
    next_run_at       timestamptz,
    last_run_at       timestamptz,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE automation_runs (
    id                uuid PRIMARY KEY,
    automation_id     uuid NOT NULL REFERENCES automations(id) ON DELETE CASCADE,
    trigger_id        uuid REFERENCES automation_triggers(id) ON DELETE SET NULL,
    occurrence_key    text,                                  -- automation:trigger:UTC instant, or event id
    scheduled_for     timestamptz,
    status            text NOT NULL DEFAULT 'queued'
                      CHECK (status IN ('queued','running','needs_approval','succeeded','failed',
                                        'cancelled','skipped','expired','timed_out')),
    attempt           integer NOT NULL DEFAULT 0,
    max_attempts      integer NOT NULL DEFAULT 3,
    run_after         timestamptz NOT NULL DEFAULT now(),
    locked_by         text,
    lease_until       timestamptz,
    fencing_token     bigint NOT NULL DEFAULT 0,
    started_at        timestamptz,
    finished_at       timestamptz,
    session_id        text,                                  -- agent session, if any
    execution_run_id  uuid,                                  -- existing durable execution run, if any
    sandbox_id        text,                                  -- provider sandbox id, if any
    trace_id          text,
    cost_usd          numeric(10,4),
    tokens_in         integer,
    tokens_out        integer,
    error_code        text,
    error_message     text,
    result_summary    text,
    skipped_reason    text,
    created_at        timestamptz NOT NULL DEFAULT now()
);

-- The core correctness constraint: one run per logical occurrence.
CREATE UNIQUE INDEX automation_runs_occurrence
    ON automation_runs (automation_id, occurrence_key)
    WHERE occurrence_key IS NOT NULL;

CREATE INDEX automation_runs_due
    ON automation_runs (run_after)
    WHERE status = 'queued';

CREATE TABLE automation_events (
    id                uuid PRIMARY KEY,
    source            text NOT NULL,                         -- github | slack | linear | sentry | pagerduty | webhook | room
    event_id          text NOT NULL,
    trigger_id        uuid REFERENCES automation_triggers(id) ON DELETE SET NULL,
    received_at       timestamptz NOT NULL DEFAULT now(),
    payload_hash      text NOT NULL,
    payload_preview   jsonb,
    status            text NOT NULL DEFAULT 'received'
                      CHECK (status IN ('received','accepted','duplicate','rejected','processed')),
    run_id            uuid REFERENCES automation_runs(id) ON DELETE SET NULL,
    UNIQUE (source, event_id)
);

CREATE TABLE automation_approvals (
    id                uuid PRIMARY KEY,
    run_id            uuid NOT NULL REFERENCES automation_runs(id) ON DELETE CASCADE,
    tool_name         text NOT NULL,
    tool_input        jsonb NOT NULL,
    status            text NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending','approved','rejected','expired')),
    decided_by        text,
    decided_at        timestamptz,
    expires_at        timestamptz,
    created_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE automation_outbox (
    id                uuid PRIMARY KEY,
    run_id            uuid NOT NULL REFERENCES automation_runs(id) ON DELETE CASCADE,
    channel           text NOT NULL,                         -- inbox | email | slack | room | push
    payload           jsonb NOT NULL,
    status            text NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending','sent','failed')),
    attempts          integer NOT NULL DEFAULT 0,
    next_attempt_at   timestamptz NOT NULL DEFAULT now()
);
```

Design notes:

- `occurrence_key` is the idempotency key. For a schedule use the canonical UTC instant (not the local wall
  time), for example `sched:<trigger_id>:2026-11-09T14:00:00Z`. For an event use `<source>:<event_id>`.
- `fencing_token` increments on every claim; a worker finalizes only if its token still matches. This prevents a
  stale worker from overwriting a newer run.
- `prompt_hash` and `version` freeze what a run executed. Editing an automation increments the version; old runs
  keep their snapshot.
- `payload_preview` is redacted and capped; the raw payload should be stored only if needed, with a short TTL.
- `environment_id` points to a cloud environment (image, setup script, network policy, secret references). Do
  not put secret values in the automation row.
- `deliver` is a small policy object, not free-form: `{"channel":"inbox"}`,
  `{"channel":"slack","target":"#eng","thread":true}`, `{"channel":"room","room":"#ops"}`.

### 5.6 Scheduler semantics

**Due calculation.** Store `next_run_at` in UTC. Compute the next occurrence in the trigger's IANA zone with a
library that handles DST (`croner`/`cron-parser` in Node, `croniter`/`dateutil.rrule` in Python). Store the
canonical UTC instant in `occurrence_key`. Never use the app process clock as the source of truth for "is it
due"; use the database's `now()` when claiming.

**Catch-up.** On a tick, if `next_run_at` is older than the grace window:

| Policy | Behaviour | Use |
|---|---|---|
| `skip` | Advance to the next future occurrence; write a `skipped` run with `skipped_reason='missed'`. | Frequent tasks, cache refreshes. |
| `once` | Create one run for the most recent missed occurrence; skip older ones. | Daily/weekly summaries (Claude's default). |
| `bounded` | Create the last N missed occurrences (N small). | Maintenance tasks that must catch up a little. |
| `all` | Create every missed occurrence. | Ledger-like work; rare and dangerous after long downtime. |

**Overlap.** Before starting, check for an active run of the same automation:

| Policy | Behaviour | Use |
|---|---|---|
| `skip` | Write a `skipped` run with `skipped_reason='previous_run_active'`. | Code changes, PR reviews. |
| `queue` | Leave the new run `queued`; cap the queue. | Read-only summaries. |
| `cancel` | Cancel the active run and start the new one. | "Latest state wins" polling. |
| `parallel` | Allow up to `max_parallel`. | Independent shards; use sparingly. |

**Jitter.** Compute a deterministic offset from the automation/trigger id, for example
`hash(id) % jitter_seconds`, and add it to the scheduled time. Never run before the scheduled time. Keep the
jitter small (0-5 minutes) and show the effective next run in the UI.

**Minimum interval.** Validate per executor: local can do 1 minute; cloud tasks should default to 15 minutes or
hourly; event runs need a per-hour cap. Reject with a clear message, not a silent adjustment.

**One-off.** `once_at` creates a single occurrence and then marks the trigger `completed` (or disables it). Do
not delete the definition; users may want to re-run it.

**Timezone travel.** Store the IANA zone. Offer "follow this account's timezone" (default) and "fixed timezone"
(for team tasks). If the user changes zones, recompute the next run and show it.

**DST examples to test.**

- `0 2 * * *` in `America/New_York` on 2026-03-08: 02:00 does not exist. Policy: skip to the next valid time
  (03:00) or skip the day; document the choice.
- `0 1 * * *` in `America/New_York` on 2026-11-01: 01:00 occurs twice. Policy: run once at the first occurrence.
- A schedule at 02:30 in a zone with a 30-minute DST shift (for example Lord Howe) is a good extra test.

**"First Monday" is not a cron expression.** In standard cron, restricting both day-of-month and day-of-week
uses OR semantics (`0 9 1-7 * 1` runs every Monday and every 1st-7th, not the first Monday). Use RRULE
(`FREQ=MONTHLY;BYDAY=1MO`) or a library extension. `node-cron` uses AND semantics for the two fields, so a
pattern moved between libraries can run on different days. Document which parser is authoritative.

### 5.7 Scheduler implementation

**Backend (primary).**

- Use an in-process `asyncio` loop in the FastAPI lifespan, like `ingestion_scheduler.start(app)`; it is already
  the house pattern and needs no new infrastructure. Unlike the ingestion loop, do not assume a single Railway
  instance: claim with `FOR UPDATE SKIP LOCKED` and a lease so N replicas are safe.
- A dedicated Railway cron service (minimum 5 minutes, UTC) is a valid tick if you prefer a separate process.
  It must exit; Railway skips a run if the previous one is still active.
- Do not use Vercel Cron/Schedules to run agents. Use Vercel only to call a backend tick endpoint if you must;
  Vercel function duration, Hobby limits (once per day for Cron, 15 minutes for Schedules) and UTC/jitter make it
  the wrong execution plane.
- Postgres claim:

```sql
UPDATE automation_runs
SET status = 'running',
    locked_by = $1,
    lease_until = now() + interval '5 minutes',
    fencing_token = fencing_token + 1,
    started_at = now(),
    attempt = attempt + 1
WHERE id = (
    SELECT id
    FROM automation_runs
    WHERE status = 'queued' AND run_after <= now()
    ORDER BY run_after
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
RETURNING *;
```

- Heartbeat every minute while running; extend `lease_until`. On finish, update only if `locked_by` and
  `fencing_token` still match. A reconciliation sweep marks runs whose lease expired as `failed`
  (`lease_expired`) or `timed_out`, and creates skipped rows for missed occurrences according to policy.
- For multi-step agent work, link `automation_runs.execution_run_id` to the existing durable execution run
  (`app/execution/durable_run.py`) and reuse its retry/resume semantics rather than inventing a second state
  machine.
- If the backend runs multiple replicas, ensure only one scheduler tick is active per shard via the claim query,
  not an in-memory lock. The ingestion loop's single-instance assumption is not safe for automations.

**Desktop (optional, later).**

- Put the timer in the Electron main process, not the renderer: it survives page reloads, has `powerMonitor`,
  `Notification`, tray and single-instance lock (`desktop/electron/main.ts:38`).
- Persist definitions and runs in `app.getPath("userData")`; SQLite is the right long-term store, a versioned
  JSON file with atomic replace is acceptable for the first slice.
- `powerMonitor.on("suspend")` records the suspend time; `powerMonitor.on("resume")` and app start run the
  catch-up policy. `powerMonitor.on("shutdown")` marks running runs as interrupted.
- Add a tray/background mode and change `window-all-closed` so closing the window does not kill schedules. Make
  this an explicit setting: "Keep running in the background" with a tray icon and a quit item.
- Worktree isolation: create a sibling `git worktree add` for the run, start the engine there, show the diff in
  the Changes panel, and let the user apply/merge. Do not auto-merge.
- Permission asks: a scheduled run must not block forever. On `permission.asked`, write an `automation_approvals`
  row, set the run to `needs_approval`, notify, and stop pumping until the person answers. Reuse
  `desktop/app/api/permission/route.ts` for the reply path.
- Sign-in/expense: `engineStart()` calls `freshSignIn()`; an expired token can open a browser flow that an
  unattended run cannot complete. Detect this, pause the automation with `needs_signin`, and notify. Check
  `walletRemaining()` before a personal-lane run and skip with `budget` if zero.

**When to move to a durable engine.** If kell needs multi-region scheduling, sleeps of days/weeks, high fan-out,
or a managed UI for retries, evaluate Trigger.dev/Inngest (managed), Temporal (self-hosted/Cloud, heavier), or
Vercel Workflows (if the work stays in TypeScript on Vercel). Do not add one now; Postgres + a loop is enough
and keeps the operational surface small.

### 5.8 Execution adapters

Define one interface so the scheduler does not care where a run happens:

```python
class Executor(Protocol):
    async def start(self, run: Run, automation: Automation) -> RunHandle: ...
    async def poll(self, handle: RunHandle) -> RunStatus: ...
    async def cancel(self, handle: RunHandle) -> None: ...
    async def approve(self, handle: RunHandle, approval: Approval) -> None: ...
```

| Executor | What it does | Reuse |
|---|---|---|
| `cloud` | Backend model/tool loop for non-code tasks: summarize, triage, monitor, report, answer in a room. | `app/services/chat.py`, `room_agent.py`, `chat_completions.py`, existing tool router. |
| `sandbox` | Fresh managed sandbox: clone repo, run setup, run OpenCode/Claude Code, stream events, open a PR. | New adapter; `app/services/sandbox.py`/`sandbox_executor.py` for local primitives, `app/execution/` for run state. |
| `github_actions` | `repository_dispatch` to a workflow that checks out the repo and runs the agent/script. | GitHub App or installation token; `POST /repos/{owner}/{repo}/dispatches`. |
| `delegate` | Create a task in the existing delegate relay; a registered agent pulls it and posts the result. | `backend/app/services/delegate.py`, `db/155_delegate_relay.sql`, `delegate_inbox`/`delegate_result` MCP tools. |
| `http` | POST the prompt/payload to an external endpoint and record the response. | New, small adapter; useful for "bring your own agent". |
| `local` | Desktop starts an OpenCode session in the chosen folder/worktree, streams events, records cost, handles permission asks. | `desktop/lib/engine.ts`, `app/api/prompt/route.ts`, `app/api/permission/route.ts`, `lib/snapshots.ts`. |

For the `sandbox` executor, the run flow is:

1. Create a fresh sandbox with the automation's environment (image, setup script, network policy, secret
   references).
2. Clone the repo with a short-lived token; remove the token from the repo config before the agent starts.
3. Start the agent (OpenCode/Claude Code/Codex) with the saved prompt; stream logs/events back to the run.
4. On completion, compute the diff; scan for secrets; push a branch from a separate sandbox; open a PR.
5. Destroy the sandbox; store artifacts and the run summary.

Do not let the agent merge, push to the base branch, or read the long-lived clone token.

### 5.9 Event ingestion

**Generic webhook.**

- `POST /v1/automation-hooks/{trigger_id}`.
- Auth: per-trigger bearer token or HMAC signature. For third-party providers, verify the provider's own
  signature instead. For generic callers, use `X-Kell-Signature: t=<unix>,v1=<hex>` over `<t>.<raw body>`.
- Read the raw body before JSON parsing; constant-time compare; reject a timestamp more than 5 minutes from
  the server clock; cache authenticated event ids for at least twice the tolerance.
- Deduplicate by `(source, event_id)`; return `202` for a new event and `200` for an authenticated duplicate
  without repeating side effects.
- Cap body size (for example 1 MB), rate-limit per trigger, and store only a redacted preview.
- Enqueue a run; do not call the model in the request.

**GitHub.** Use a GitHub App (not a personal OAuth token) so webhooks are delivered reliably and the app can be
installed per repo/org. Verify `X-Hub-Signature-256`; dedupe on `X-GitHub-Delivery`; support the events Cursor
and Claude expose: PR opened/pushed/merged, review submitted, review comment, issue comment, label changed, CI
completed, workflow run completed. Do not run fork PR code with the base repo's permissions; Cursor explicitly
refuses fork PR triggers for this reason.

**Slack.** Use the Events API with the signing secret (`X-Slack-Signature` + `X-Slack-Request-Timestamp`), answer
the URL verification challenge, and subscribe to the narrowest events (`message.channels`, `reaction_added`).
Only public channels unless the app is granted more; say so in the UI.

**Linear / Sentry / PagerDuty.** Verify each provider's signature header, dedupe on its delivery id, and map the
event to a small normalized envelope (`source`, `type`, `action`, `resource`, `url`, `actor`, `payload`). Keep
provider-specific parsing in adapters, not in the scheduler.

**Rooms.** The rooms event envelope already carries `mentions` and `kind` (`msg`, `task`, `task_update`). Add a
trigger kind `room` with filters: room id, mention handle, event kind, sender kind. On a match, create a run with
the recent room context (bounded) and post the result back to the room if `deliver` says so. This is the most
kell-specific trigger and the easiest to demo.

**Local file/git (desktop only).** Use `chokidar` or `fs.watch` with debounce and an ignore list (`.git`,
`node_modules`, build output); allowlist paths; resolve symlinks; never follow outside the project. For git,
prefer polling `git log`/`git status` over installing a hook; if a hook is offered, make it explicit and
removable. These triggers only work while the app is open.

**Email/calendar.** Later. Gmail push needs a Pub/Sub setup and OAuth verification; calendar polling is simpler
but still needs OAuth scopes and privacy review. Do not put this in v1.

**Event filters.** Store a small JSON filter and evaluate it in the adapter:
`{"action":["opened","ready_for_review"],"labels":{"contains":"bug"},"branch":{"regex":"^release/"}}`.
Provide a sample payload tester and show the normalized event in the run detail.

**Event storms.** Per-trigger rate limit, coalescing window, and a max queue depth. If a burst exceeds the limit,
write a `skipped` run with `skipped_reason='rate_limited'` and notify once, not per event. ChatGPT groups
multiple events into one task; Cursor's filters are the first line of defense.

### 5.10 Approvals and permissions

There are three different approval mechanisms in the repo today; do not conflate them:

1. **Desktop engine permission asks** (`permission.asked` → `desktop/app/api/permission/route.ts`) for tool calls.
2. **Delegate relay `needs_approval`** (`backend/app/services/delegate.py`) for tasks from another person.
3. **Knowledge debate approvals** (`backend/app/api/approval.py`) for publishing knowledge changes.

For automations, add a fourth, narrow concept: `automation_approvals`, tied to a run and a tool call. It should
reuse the UI patterns but not the knowledge-debate state machine.

Policy:

- Default permission mode for unattended runs is `workspace-write`; `full` requires an explicit confirmation
  and is org-gated.
- Writes outside the workspace, network access, package installs, `git push`, PR creation, email/Slack sends and
  payments are approval-required by default.
- A run that hits an approval moves to `needs_approval`, writes an approval row, sends a notification, and
  stops consuming compute. It resumes from the same step after approve/reject. Expire after a configurable
  window (for example 24 hours) with `expired`.
- "Always allow" is per automation + tool, revocable from the automation detail page. It is not global.
- MCP tools that declare `requiresUserInteraction` should be disabled for unattended runs or routed to
  approval; Claude's docs note they prompt every time and stall runs.
- The prompt/tool policy is not enforcement. Enforce with the engine's permission configuration, the tool
  allowlist, the sandbox, and network policy. `TOOL_RULES` in `desktop/lib/tools.ts` is guidance, not a security
  boundary.

### 5.11 Observability, notifications and audit

**Run log.** For each run, store the event stream (tool calls, permission asks, model steps, cost) as JSONL or
rows. The UI shows a timeline, the final result, the diff/artifacts, and links to the agent session. Local logs
stay on the device by default; cloud logs are encrypted at rest and redacted.

**Metrics.** Extend the existing OTel setup with:

- `automation_runs_total{status,executor}`
- `automation_run_duration_seconds{executor}`
- `automation_schedule_lag_seconds`
- `automation_failures_total{error_code}`
- `automation_cost_usd_total{owner,org}`
- `automation_approvals_pending`
- `automation_webhook_rejected_total{reason}`
- `automation_queue_depth`

**Alerts.** Failure streak, lag over 2 minutes, approval pending over 1 hour, budget at 80%/100%, webhook
rejection spike, and a dead scheduler heartbeat. Send them through the existing alerting path, not a new one.

**Notifications.** In-app inbox is the default. Add per-automation delivery: web push, email, Slack, room post,
desktop notification (for local tasks). Immediate for failures and approvals; digest for successes. "Nothing to
report" runs can auto-archive (Codex pattern) so the inbox stays useful.

**Audit.** Reuse `backend/app/services/audit.py`; record create/update/pause/resume/delete/run-now/approve/
reject/secret-rotate/import with actor, IP, user agent and a redacted diff. Retention should match the existing
audit policy.

### 5.12 Billing and quotas

- Record `cost_usd`, `tokens_in`, `tokens_out`, `model` per run. Show an estimate in the create flow based on the
  chosen lane and the last similar run.
- Enforce a per-run cap and a per-month cap before each model call; pause with `budget` when exhausted. Reuse
  the wallet/cost governor; do not create a second ledger.
- Suggested starting limits (product decision, not final):
  - Free: 3 active automations, daily minimum interval, no event triggers.
  - Paid: 25 active, hourly minimum for cloud, 1-minute local, event runs capped per hour/day.
  - Org: pooled budget, concurrency cap, admin approval for cloud writes.
- Auto-pause after repeated failures and after prolonged inactivity (with a notification), matching ChatGPT/
  Gemini/Inngest behavior.

### 5.13 UX surfaces

**Web (primary).**

- Add a **Scheduled** page at `/scheduled` (or `/console/automations` if console is the dashboard), linked from
  the header/account menu/sidebar. It is the home for cloud automations.
- List: name, status, next run, last outcome, executor badge (Cloud/Local), owner/team, cost this month.
- Detail: instructions, triggers, schedule preview, model/effort, permission mode, environment/repo, delivery,
  budget, run history (including skipped reasons), approvals, "Run now", pause/resume, edit, delete.
- Create flow: a single box ("Describe what should happen and when") plus "Set up manually"; parse and show the
  normalized schedule and next runs; require confirmation. Offer starter templates: daily briefing, inbox
  triage, weekly review, daily PR review, dependency audit, failing CI triage, docs drift, release notes,
  flaky test hunter.
- "Create with Claude" path: the assistant asks clarifying questions, then shows the task name, schedule and
  prompt; the person must click "Schedule". Never create silently.
- Event trigger setup: connect GitHub/Slack/Linear/Sentry/PagerDuty, pick events and filters, show a sample
  payload, "Send test event", and delivery logs.
- Webhook setup: URL, secret (shown once), rotate, disable, sample `curl`, recent deliveries, replay button.
- Approvals inbox: pending tool calls with arguments, approve/reject/always, a link to the run.
- Run detail: timeline, cost, artifacts, diff/PR link, logs, "Open in chat"/"Open in room", retry/cancel.
- Share/import: a link or `.kell/automations/<slug>.md`; import requires confirmation and shows the schedule.
- Docs: update `routing_kell_frontend/app/docs/page.tsx` and `README.md`; add a "Scheduled tasks" section with
  the cloud/local trade-off and the security model.

**Chat integration.**

- "Schedule this" from a chat message: parse the intent, show a confirmation card with the prompt and next runs,
  create on confirm. Never create silently.
- A `@kell` room mention can create/run an automation if the sender has permission; otherwise it is a normal
  chat turn.
- MCP tools: `automation_create`, `automation_list`, `automation_update`, `automation_cancel`,
  `automation_run_now`, `automation_runs`. Gate them on `automation:write`/`automation:run`; rate-limit; audit;
  require confirmation for create/delete. Cursor's `/automate` and Claude's `/schedule` show this is expected,
  but self-created automations are a loop/abuse surface.

**Desktop (optional).**

- Add a **Scheduled** item to the left sidebar. It can show cloud tasks read-only (or create them via the Rooms
  session later) and manage local tasks.
- Local create flow: "Only on this computer" toggle, folder picker, worktree option, permission mode, model.
  Show the same "runs only while kell is open and the computer is awake" copy as Claude/ChatGPT.
- A "Keep running in the background" setting with a tray icon; a "Keep computer awake" setting (explicit, not
  default).
- Empty state with three example automations and a link to docs.

### 5.14 File-by-file change map

**Backend (primary)**

| File | Change |
|---|---|
| `backend/db/186_automations.sql` (new) | Tables, indexes, RLS/revokes, unique occurrence index. |
| `backend/app/api/automations.py` (new) | CRUD, run now, pause/resume, runs, approvals, webhook endpoint. |
| `backend/app/services/automations.py` (new) | Definition validation, next-run computation, claim/finalize, policy. |
| `backend/app/services/automation_scheduler.py` (new) | `asyncio` loop, lease/heartbeat, reconciliation, metrics. |
| `backend/app/services/automation_executors.py` (new) | `cloud`, `sandbox`, `github_actions`, `delegate`, `http`, `local` adapters. |
| `backend/app/services/automation_events.py` (new) | Provider signature verification, normalization, filters, dedupe, coalescing. |
| `backend/app/services/automation_sandbox.py` (new) | Sandbox provider client, repo clone, setup, run, diff, PR, cleanup. |
| `backend/app/main.py` | Include the router; start/stop the scheduler in the lifespan. |
| `backend/app/config.py` | Settings: enabled, tick interval, lease, retention, max concurrency, plan limits, sandbox provider. |
| `backend/app/services/authn.py` / `auth_context.py` | Scopes: `automation:read`, `automation:write`, `automation:run`, `automation:admin`. |
| `backend/app/services/audit.py` | Automation actions and redacted diffs. |
| `backend/app/services/telemetry.py` / `observability.py` | Spans/metrics for tick, trigger, run, approval. |
| `backend/app/services/delegate.py` | Optional executor adapter; do not fork the task status model. |
| `backend/app/services/rooms.py` | Room trigger source and result destination. |
| `backend/app/services/execution.py` / `sandbox.py` | Cloud executor hooks; link `execution_run_id`. |
| `backend/requirements.txt` / `pyproject.toml` | `croniter` and/or `python-dateutil` if not already present; avoid a scheduler framework. |

**Web (primary)**

| File | Change |
|---|---|
| `routing_kell_frontend/app/scheduled/page.tsx` (new) | List/detail/create; cloud-first. |
| `routing_kell_frontend/components/automations/*` (new) | `AutomationList`, `AutomationDetail`, `TriggerEditor`, `SchedulePreview`, `RunHistory`, `ApprovalsInbox`, `WebhookPanel`, `TemplatePicker`. |
| `routing_kell_frontend/lib/automations.ts` (new) | Typed API client and shared types. |
| `routing_kell_frontend/lib/api.ts` / `sse.ts` / `session.ts` | Reuse; add auth/error handling for automation routes. |
| `routing_kell_frontend/components/Header.tsx` / account menu | Link to `/scheduled` for signed-in users. |
| `routing_kell_frontend/app/chat/page.tsx` / `components/chat/RoomView.tsx` | "Schedule this" action; room mention trigger hints. |
| `routing_kell_frontend/app/docs/page.tsx` | User-facing docs. |
| `routing_kell_frontend/README.md` | Routes/decisions update. |

**Desktop (optional, later)**

| File | Change |
|---|---|
| `desktop/electron/automations.ts` (new) | Local store, due calculation, claim/lock, run loop, catch-up/overlap, notifications. |
| `desktop/electron/main.ts` | Start/stop the scheduler; tray/background mode; `powerMonitor` suspend/resume/shutdown; IPC handlers; keep single-instance lock. |
| `desktop/electron/preload.ts` | Typed `window.kellDesktop.automations` bridge. No raw `ipcRenderer`. |
| `desktop/lib/automations.ts` (new) | Shared types, cron/RRULE parsing, next-run preview, timezone/DST helpers, validation. |
| `desktop/app/api/automations/run/route.ts` (new) | Server-side run function: start/reuse engine, create session, subscribe to events, record log/cost, handle permission asks. |
| `desktop/app/page.tsx` | Scheduled sidebar/list/detail/create; run history; approvals; Local/Cloud badge. |
| `desktop/app/api/permission/route.ts` | Route decisions into the automation approval row when the session belongs to a run. |
| `desktop/lib/worktree.ts` | Add `createWorktree`/`removeWorktree` helpers; keep the existing change reader. |
| `desktop/package.json` | Add `croner`/`rrule` (and `chrono-node` if natural-language creation is in v1). |
| `docs/desktop.md` | Document the optional local scheduler and background mode. |

---

## 6. Phased delivery

### Phase 0: decisions and shared vocabulary

- Decide the first cloud executor (sandbox provider vs GitHub Actions vs model-only), repo provider (GitHub
  first), environment/secrets model, plan limits and retention.
- Freeze the automation/trigger/run/approval vocabulary and status names.
- Write the acceptance criteria (section 7) before code.

### Phase 1: web/backend scheduled tasks (MVP, no desktop required)

Scope:

- `186_automations.sql`, API, scheduler loop, run ledger, approvals table, outbox.
- `/scheduled` UI: create with Claude/manual, templates, list/detail, run now, pause/resume, history, inbox,
  web push/email notifications.
- One cloud executor that can actually do the product's core task:
  - non-code tasks: backend model/tool loop;
  - coding tasks: a managed sandbox with repo clone and PR, or GitHub Actions as the zero-infrastructure
    fallback.
- Auth/scopes, owner/org isolation, audit, cost recording, quotas.

Acceptance: a signed-in person can create "every weekday at 9, review new PRs" in the browser, close the
laptop, and see a run with a diff/PR and a cost in the web inbox. No desktop app is installed. A second backend
instance does not duplicate the run.

### Phase 2: event triggers

Scope:

- Generic webhook with HMAC/replay protection and delivery logs.
- GitHub App triggers (PR/push/review/CI) with filters.
- Room mention trigger and result delivery.
- Slack/Linear/Sentry/PagerDuty adapters as time allows.
- Event coalescing, rate limits, test payloads.

Acceptance: a bad signature is rejected; a replayed event has no second side effect; a GitHub PR event starts
exactly one run; a room mention starts a run and posts the result back.

### Phase 3: cloud code execution hardening

Scope:

- Sandbox lifecycle, repo clone, setup script, network allowlist, secret injection/removal, diff/secret scan,
  separate push sandbox, PR creation, cleanup.
- Multi-repo/environment support if needed; GitHub first, GitLab later.
- Cost/runtime caps per sandbox; cold-start optimization; artifact storage.

Acceptance: an untrusted prompt cannot reach the host, the long-lived clone token, or the base branch; a diff
with a secret is not pushed; every command is in the run log; the sandbox is destroyed after the run.

### Phase 4: durability, approvals and governance

Scope:

- Move tick to a dedicated service/queue if needed; multi-instance leases/fencing; reconciliation.
- Durable multi-step runs with checkpoints; retry/dead-letter UI.
- Org controls, service accounts, sharing/import, templates, natural-language creation, MCP tools.
- Observability dashboards, SLOs, retention/export/delete.
- Optional email/calendar triggers.

Acceptance: 1,000 due occurrences across 10 workers produce exactly 1,000 runs; a killed worker recovers; an
org admin can disable automations and export the audit log.

### Phase 5: desktop-local executor (optional)

Scope:

- Local scheduler in Electron main; tray/background mode; `powerMonitor` catch-up.
- Local folder/worktree runs; local permission mode; local notifications.
- Desktop can show cloud tasks and manage both; local-only tasks are clearly marked.

Acceptance: a user who needs local files can create a local task and see it run while the app is open, with a
clear skipped reason when it is not; a web user never needs this.

---

## 7. Test plan

### Unit

- Cron/RRULE next-run computation across timezones, including `America/New_York`, `Europe/London`,
  `Asia/Kolkata`, `Australia/Lord_Howe`.
- DST spring-forward: nonexistent local time follows the documented policy.
- DST fall-back: ambiguous local time runs once (first occurrence).
- "First Monday" via RRULE; verify naive cron gives the wrong answer.
- Catch-up policies: skip, once, bounded, all; bounded after a long downtime.
- Overlap policies: skip, queue, cancel, parallel with cap.
- Jitter is deterministic and never negative.
- Idempotency key generation: schedule vs event; same input -> same key.
- HMAC verification: valid, invalid, old timestamp, wrong scheme, multiple signatures during rotation.
- Event filter matching: equals, contains, regex, missing field, wrong type.
- Prompt hash/version snapshot.

### Integration (Postgres)

- N concurrent workers claim M due runs; exactly M runs, no duplicates.
- Duplicate tick with the same occurrence key creates zero extra rows.
- Lease expiry and fencing: a stale worker cannot finalize; a new worker can.
- Retry/backoff: attempts increment, `run_after` moves, dead-letter after max.
- Reconciliation: stuck `running` rows become `failed`/`timed_out`.
- Approval: run pauses, approval row created, approve resumes, reject fails the run, expire works.
- Budget: run stops before the next call at the cap; status `skipped`/`failed` with `budget`.
- Tenant isolation: user A cannot read/update/delete user B's automation or run; 404 not 403.
- RLS/revokes: anon/authenticated cannot read the tables directly.
- Outbox: notification is written in the same transaction; a crash does not lose it.

### End-to-end (web/cloud, primary)

- Create a cloud task in the browser; the scheduler starts it; the run appears in history; result in inbox.
- Close the laptop/browser; the run still happens.
- Pause/resume; no run while paused; resume does not fire all missed occurrences.
- Run now; cost recorded; plan quota enforced.
- GitHub PR event starts exactly one run; a second delivery is a duplicate.
- Webhook bad signature -> 401; replay -> 200 with no side effect; oversized body -> 413.
- Room mention -> run + room result.
- Approval inbox -> approve/reject -> run resumes/fails.
- Org admin disables automations -> existing triggers stop, members cannot create.
- Share/import -> schedule and timezone preserved; import requires confirmation.
- A task that needs local files explains the desktop fallback instead of failing silently.

### End-to-end (sandbox/coding)

- Fresh sandbox per run; repo cloned; setup script cached/run; agent starts.
- Clone token removed before the agent starts; push from a separate sandbox; base branch untouched.
- Network allowlist blocks metadata/private ranges; commands run as unprivileged user.
- Diff with a secret value is not pushed; run fails with a clear reason.
- PR opened for review; agent cannot merge.
- Sandbox destroyed on success/failure/timeout; logs/artifacts retained per policy.

### End-to-end (desktop, optional)

- Create a local schedule, see next runs, run now, see the diff/cost.
- Quit and reopen the app; the schedule persists; a missed run follows the catch-up policy.
- Sleep/resume; a run due during sleep is caught up once and the skipped reason is visible.
- Close the window with background mode on; the scheduler keeps running and the tray shows it.
- Close the window with background mode off; the scheduler stops and the UI says so.
- Permission ask: run pauses, notification appears, approve resumes, reject fails; no infinite hang.
- Worktree: run edits are isolated; the main working tree is untouched; apply/merge is manual.
- Expired sign-in: run pauses with `needs_signin`, notification, no browser pop-up storm.
- Zero wallet: run skips with `budget`, no model call.

### Load/chaos

- 1,000 due occurrences, 10 workers, 1-minute tick; no duplicates, lag bounded.
- Provider 429/timeout: backoff, retry, dead-letter, alert.
- DB fail/restart: scheduler reconnects; no lost occurrences.
- Clock skew: use DB time; a worker with a wrong clock cannot claim/finalize incorrectly.
- Event burst: coalescing/rate limit; one skipped row, one notification.
- Sandbox provider outage: retry with backoff; surface a clear error; do not lose the run.

### Security

- Prompt injection via webhook payload, room message, PR title/body, file content: payload is labeled data;
  tools/permissions cannot be changed by it; writes require approval.
- SSRF: `http://169.254.169.254`, `localhost`, private ranges, DNS rebinding blocked.
- Secret leakage: tokens/passwords absent from logs, run history, error messages, prompts, `.kell` files.
- Cross-tenant access and ID enumeration.
- Webhook token rotation invalidates the old token.
- Agent-created automation loop: depth/rate limit; no infinite create chain.
- Sandbox escape/abuse: no host mount, no docker socket, no repo token after clone, no push to base branch.

---

## 8. Pitfalls and anti-patterns

- **Requiring the desktop app for a web task.** This is the main thing to avoid. If a task does not need local
  files, it must run in the cloud from the browser.
- **`setInterval` in a serverless function.** It dies with the invocation. Use a durable tick or a backend
  process.
- **In-memory schedules.** A restart loses them. Persist definitions and runs.
- **Client clock.** Use the database/server clock for due checks; store UTC; compute local times with a real
  timezone library.
- **Naive cron for "first Monday".** Standard cron ORs day-of-month and day-of-week; use RRULE.
- **One global webhook token.** One leak fires everything. Use per-trigger secrets, hashed, rotatable.
- **Signature over parsed JSON.** Re-serializing changes bytes and breaks HMAC. Verify the raw body.
- **No replay protection.** A captured valid request can be replayed. Timestamp + event-id cache.
- **Assuming event order.** Events arrive late/out of order; fetch current state or use a sequence when the
  provider documents one.
- **Retrying non-idempotent actions.** Use a run id/idempotency key for emails, PR comments, room posts,
  charges.
- **Auto-approving tools because the run is unattended.** That is how an agent deletes a repo. Pause and ask,
  or default deny.
- **Letting a permission ask hang.** The run occupies a slot forever. Expire and notify.
- **Running model-generated code in the API process.** Use a sandbox; remove the clone token; push from a
  separate sandbox; never push to the base branch.
- **Unbounded runs and budgets.** Cap runtime, steps, tokens and dollars; stop before the next call.
- **Silent skips.** Show why a run did not happen; otherwise users think the product is broken.
- **Notification spam.** Digest successes; immediate only for failures/approvals/findings.
- **Secrets in `.kell` or prompts.** `.kell` is committed; prompts are shared/copied.
- **Pretending local tasks run when the app is closed.** Be honest; offer cloud tasks instead, or an explicit
  OS-level opt-in.
- **Installing OS scheduled tasks silently.** It is a security and trust decision; make it visible and
  uninstallable.
- **Treating event payloads as instructions.** Wrap and label them; never let them approve or change tools.
- **A separate "agents" silo with no triggers.** Zapier's own migration folded Agents into the workflow
  platform for this reason. Put triggers in the product.
- **Two systems for chat tasks and code automations.** ChatGPT has this split and its docs explain the
  difference at length. One model, different executors.
- **No versioning.** Editing a prompt must not reinterpret past runs; snapshot `prompt_hash`/`version`.
- **No audit.** "Who created this automation that spent $400?" must be answerable.

---

## 9. Open questions

1. **First cloud executor.** Managed sandbox (E2B/Modal/Cloudflare Sandbox/Vercel Sandbox/Daytona) vs GitHub
   Actions vs a Railway worker. Recommendation: non-code tasks on the backend loop; coding tasks on a managed
   sandbox; GitHub Actions as a repo-only fallback. Pick one provider with a short spike before building the
   adapter.
2. **Repo/PR flow.** GitHub App first; clone with a short-lived token; remove it before the agent starts; push
   from a separate sandbox; open a PR; never merge. Confirm the exact provider permissions and branch policy.
3. **Environment/secrets model.** Per-automation environment with image, setup script, network allowlist and
   secret references. Do not put secret values in the automation row or prompt. Confirm the secret store and
   rotation path.
4. **First event providers.** Recommendation: generic webhook + GitHub + room mentions. Add Slack/Linear/
   Sentry/PagerDuty after the security and dedupe path is proven.
5. **Repo-shared definitions.** Do we want `.kell/automations/*.md` as a first-class, committed artifact?
   Recommendation: yes, opt-in on import; personal cloud tasks stay in the backend.
6. **Local fallback.** When a web task needs local files, do we show "install desktop" or "use a cloud project"?
   Recommendation: explain both, default to cloud; never silently fail.
7. **RRULE vs cron-only.** Recommendation: both; cron for simple, RRULE for monthly/nth-weekday/business-day
   patterns. Store both and show the normalized description.
8. **Natural language.** Recommendation: yes, but always parse to a concrete schedule and require confirmation;
   do not let the model silently create or edit a schedule.
9. **Plan limits.** Recommendation: start with the ChatGPT/Gemini shape (active count, minimum interval, event
   runs/hour/day, concurrency) and tune with real usage; do not publish final numbers until pricing is decided.
10. **Identity for team automations.** Recommendation: add `run_as` (me vs service account) before org sharing;
    otherwise a departing employee breaks the team's automations.
11. **Private data.** Do automations need E2E encryption? Cloud runs need server-readable prompts and tool
    output; local-only definitions can be encrypted at rest. This depends on the private-rooms/MLS direction.
12. **MCP self-scheduling.** Should agents be allowed to create automations? Recommendation: yes, behind a
    scope, confirmation, rate limit and audit; it is a SOTA expectation (Claude `/schedule`, Cursor `/automate`),
    but it is also a loop/abuse surface.
13. **Run retention.** Recommendation: 30 days for logs/results, 7 days for event payload previews, configurable
    per org; export/delete on request.
14. **macOS/Linux.** The desktop app is Windows-first. The web/cloud path is cross-platform by construction; the
    optional desktop executor can follow later.

---

## 10. Sources

Product docs and engineering references used for this note (checked 2026-11-09):

- Claude Help Center, schedule recurring tasks in Claude Cowork (web Scheduled page, remote by default):
  https://support.claude.com/en/articles/13854387-schedule-recurring-tasks-in-claude-cowork
- Claude Code, cloud scheduled tasks (schedule/API/GitHub triggers):
  https://code.claude.com/docs/en/routines
- Claude Code, run prompts on a schedule (session `/loop`, cron tools):
  https://code.claude.com/docs/en/scheduled-tasks
- Claude Code, schedule recurring tasks in Desktop (optional local executor):
  https://code.claude.com/docs/en/desktop-scheduled-tasks
- Cursor, Automations (schedules, source control, Slack, webhooks, Linear, Sentry, PagerDuty):
  https://cursor.com/docs/cloud-agent/automations
- ChatGPT, Scheduled tasks:
  https://help.openai.com/en/articles/10291617-scheduled-tasks-in-chatgpt
- ChatGPT/Codex, Scheduled tasks (web tasks, desktop local projects, RRULE, sandbox):
  https://learn.chatgpt.com/docs/automations
- Gemini, Schedule actions:
  https://support.google.com/gemini/answer/16316416
- Lindy, Routines:
  https://docs.lindy.ai/teammate/routines
- Relay.app, Scheduled trigger:
  https://docs.relay.app/triggers/scheduled-trigger
- Zapier, Set up your agent's trigger:
  https://help.zapier.com/hc/en-us/articles/45394909914381-Set-up-your-agent-s-trigger
- n8n, Build and manage agents (schedules, sessions):
  https://docs.n8n.io/build/build-and-manage-agents
- Trigger.dev, Scheduled tasks:
  https://trigger.dev/docs/tasks/scheduled
- Inngest, Schedules and delayed starts:
  https://www.inngest.com/docs/durable-execution/guides-and-advanced/events-and-triggers/schedules-and-delayed-starts
- Temporal, Orchestrating ambient agents:
  https://temporal.io/blog/orchestrating-ambient-agents-with-temporal
- Vercel Workflows:
  https://vercel.com/docs/workflows
- Vercel Cron Jobs, usage and pricing:
  https://vercel.com/docs/schedules/cron-jobs/usage-and-pricing
- Vercel, migrating from Cron Jobs to Schedules:
  https://vercel.com/docs/schedules/migrate-from-cron-jobs
- Vercel Schedules public beta changelog:
  https://vercel.com/changelog/vercel-schedules-is-now-in-public-beta
- Railway, Cron jobs:
  https://docs.railway.com/cron-jobs
- GitHub Actions, `on.schedule`:
  https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#onschedule
- GitHub Actions, trigger a workflow (`repository_dispatch`):
  https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow
- Supabase, Cron:
  https://supabase.com/docs/guides/cron
- Upstash QStash, Schedules:
  https://upstash.com/docs/qstash/features/schedules
- BullMQ, Repeat options:
  https://docs.bullmq.io/guide/job-schedulers/repeat-options
- Postgres `FOR UPDATE SKIP LOCKED` job queue:
  https://www.prisma.io/blog/you-dont-need-a-job-queue-postgres-already-has-skip-locked
- OWASP, Webhook Security Cheat Sheet:
  https://cheatsheetseries.owasp.org/cheatsheets/Webhook_Security_Cheat_Sheet.html
- OpenAI Agents SDK, Human-in-the-loop:
  https://openai.github.io/openai-agents-python/human_in_the_loop/
- MCP Tasks extension:
  https://modelcontextprotocol.io/extensions/tasks/overview
- Electron `powerMonitor` (optional local executor):
  https://www.electronjs.org/docs/latest/api/power-monitor/
- Electron notifications (optional local executor):
  https://www.electronjs.org/docs/latest/tutorial/notifications
- OpenInspect, control plane/data plane for cloud coding agents:
  https://murraycole.com/projects/openinspect
- E2B, code execution sandboxes comparison:
  https://e2b.dev/resources/best-code-execution-sandboxes-for-ai-agents
- Cloudflare Sandbox, build a coding agent runner:
  https://developers.cloudflare.com/sandbox/get-started/build-a-coding-agent-runner/
- NeonCodex cloud agents (sandbox/PR safety model):
  https://neoncodex.io/docs/web-app/cloud-agents
- RFC 5545 (iCalendar RRULE):
  https://datatracker.ietf.org/doc/html/rfc5545
- crontab(5), day-of-month/day-of-week OR semantics:
  https://man7.org/linux/man-pages/man5/crontab.5.html
