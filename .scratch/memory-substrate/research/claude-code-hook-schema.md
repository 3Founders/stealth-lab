# Claude Code hook ingestion surface

Research for [ticket 07](../issues/07-claude-code-hook-schema.md). Evidence-gathering only — no
ingestion design proposed here.

**Primary sources used** (fetched 2026-08-17, official Anthropic/Claude Code docs):

- Hooks reference — `https://code.claude.com/docs/en/hooks` (fetched as `hooks.md`)
- Sessions / transcript storage — `https://code.claude.com/docs/en/sessions`
- Subagents — `https://code.claude.com/docs/en/sub-agents`
- Corroborating web search over third-party summaries of the same official page (used only to
  cross-check the primary fetch, never as a standalone source): search results independently
  listing the same ~31 event names and pointing at `https://code.claude.com/docs/en/hooks`.
- A `claude-code-guide` subagent pass (this environment's built-in Claude Code documentation
  agent) was run first as a sanity check; every claim it made was independently re-verified
  against a direct `WebFetch` of the primary doc page before being included below. Its raw
  output is **not** cited on its own anywhere in this document — only the doc pages are.

Docs explicitly warn that hook and transcript formats are internal and can change between
Claude Code releases (see §7). Everything below reflects the documentation as fetched on
2026-08-17; treat exact field lists as a snapshot, not a permanent contract.

---

## 1. The real hook event list

spec.md's list (`SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`,
`PostToolUseFailure`, `PostToolBatch`, `SubagentStart`, `SubagentStop`, `TaskCreated`,
`TaskCompleted`, `PreCompact`, `PostCompact`, `SessionEnd`) is **not speculative** — every one
of those 13 names is a real, currently-documented hook event
(`https://code.claude.com/docs/en/hooks`). spec.md was more accurate than the ticket's framing
assumed.

What spec.md omits — the official reference lists **31 events in total** as of this fetch,
roughly double spec.md's list:

- **Lifecycle**: `SessionStart`, `SessionEnd`, `Setup` (fires on `--init-only`/maintenance
  mode), `InstructionsLoaded` (CLAUDE.md / `.claude/rules/*.md` load)
- **Prompt handling**: `UserPromptSubmit`, `UserPromptExpansion` (a typed slash-style command
  expanding into a prompt), `MessageDisplay` (while assistant text is streamed)
- **Tool calls**: `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `PostToolBatch` (after a
  full batch of parallel tool calls resolves)
- **Permissions**: `PermissionRequest`, `PermissionDenied`
- **Compaction**: `PreCompact`, `PostCompact`
- **Turn end**: `Stop`, `StopFailure` (turn ends due to an API error — matcher values include
  `rate_limit`, `overloaded`, `authentication_failed`, `billing_error`)
- **Subagents**: `SubagentStart`, `SubagentStop`
- **Tasks**: `TaskCreated`, `TaskCompleted`
- **Notifications**: `Notification`
- **Agent teams**: `TeammateIdle`
- **Environment/config**: `ConfigChange`, `CwdChanged`, `DirectoryAdded`, `FileChanged`
- **Worktrees**: `WorktreeCreate`, `WorktreeRemove`
- **MCP elicitation**: `Elicitation`, `ElicitationResult`

Source: `https://code.claude.com/docs/en/hooks`, event list section.

**Implication for spec.md**: the 13-event list is a valid subset, not wrong, but it silently
omits event families spec.md's downstream trace model may need to decide about explicitly —
notably `PostToolUseFailure`/`PostToolBatch`/`TaskCreated`/`TaskCompleted` are already in
spec.md, but `Notification`, `ConfigChange`, `CwdChanged`, `FileChanged`, `WorktreeCreate/
Remove`, `Elicitation*`, `TeammateIdle`, `Setup`, `InstructionsLoaded`, `UserPromptExpansion`,
`MessageDisplay`, `PermissionRequest`, `PermissionDenied`, `StopFailure` are not mentioned at
all and represent ingestion-surface area spec.md hasn't scoped in or out.

---

## 2. Per-event payload fields

**Common fields on every hook invocation** (stdin JSON for `command` hooks, POST body for
`http` hooks) — `https://code.claude.com/docs/en/hooks`:

```json
{
  "session_id": "abc123",
  "prompt_id": "550e8400-...",
  "transcript_path": "/home/user/.claude/projects/.../transcript.jsonl",
  "cwd": "/home/user/my-project",
  "permission_mode": "default|plan|acceptEdits|auto|dontAsk|bypassPermissions",
  "effort": { "level": "low|medium|high|xhigh|max" },
  "hook_event_name": "PreToolUse",
  "agent_id": "unique-id-if-in-subagent",
  "agent_type": "agent-name-if-in-subagent"
}
```

Event-specific fields documented on the same page:

- **`PreToolUse`**: `tool_name`, `tool_input` (tool-specific — e.g. `command` for Bash)
- **`PostToolUse`**: `tool_name`, `tool_input`, plus the tool's result (`tool_output`/
  `tool_response`-shaped; the page's JSON examples show tool-specific result payloads rather
  than one fixed field name)
- **`PostToolUseFailure`**: same tool identity fields plus error information from the failed
  call
- **`UserPromptSubmit`**: `prompt` (submitted text)
- **`SessionStart`**: `source` (`startup|resume|clear|compact|fork`)
- **`SessionEnd`**: matcher/reason values `clear|resume|logout|prompt_input_exit|
  bypass_permissions_disabled|other`
- **`SubagentStart`/`SubagentStop`**: `agent_id`, `agent_type` (matches agent name from
  frontmatter, or built-in names `general-purpose`/`Explore`/`Plan`)
- **`ConfigChange`**: matcher values `user_settings|project_settings|local_settings|
  policy_settings|skills`
- **`CwdChanged`**: old/new working directory context (page shows this as a matcher-plus-input
  event; exact field names for old/new path are not spelled out beyond the matcher enum)
- **`FileChanged`**: matcher is a literal filename to watch
- **`StopFailure`**: matcher values `rate_limit|overloaded|authentication_failed|
  billing_error`, etc.
- **`InstructionsLoaded`**: matcher values `session_start|nested_traversal|path_glob_match|
  include|compact`

**Open empirical question, explicitly**: the reference page does not give a complete,
field-by-field JSON schema for every one of the 31 events (e.g. exact field names for
`CwdChanged`'s old/new cwd, `FileChanged`'s payload beyond the matcher, `Elicitation`/
`ElicitationResult` payload shape, `TeammateIdle` payload). Anything not explicitly listed
above should be treated as **undocumented** and verified empirically (capture a real hook
invocation) before an ingestion pipeline depends on it.

---

## 3. Configuration mechanism

Source: `https://code.claude.com/docs/en/hooks`.

**Scopes**, in ascending precedence-adjacent layers (docs list them, precedence order across
project/user/local is not explicitly stated as a numbered order on this page):

- `~/.claude/settings.json` — user, all projects
- `.claude/settings.json` — project, shareable/versioned
- `.claude/settings.local.json` — project, local-only, gitignored
- Managed/organization policy settings
- Plugin `hooks/hooks.json` — active only while the plugin is enabled
- Skill/subagent frontmatter — scoped to that component's own execution (e.g. a subagent's
  frontmatter `PreToolUse`/`PostToolUse`/`Stop`, where `Stop` is converted to `SubagentStop` at
  runtime per `https://code.claude.com/docs/en/sub-agents`)

**Registration shape**:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "${CLAUDE_PROJECT_DIR}/.claude/hooks/block-rm.sh",
            "timeout": 600,
            "if": "Bash(rm *)"
          }
        ]
      }
    ]
  },
  "disableAllHooks": false,
  "allowedHttpHookUrls": ["https://example.com/hooks/*"]
}
```

Hook `type` can be `command`, `http`, `mcp_tool`, `prompt`, or `agent` — not just shell
commands. Path placeholders: `${CLAUDE_PROJECT_DIR}` (project root), `${CLAUDE_PLUGIN_ROOT}`,
`${CLAUDE_PLUGIN_DATA}`.

**Matchers**: exact tool/event name (`Bash`, `Edit|Write`), regex (`^Notebook`, `mcp__.*`), or
wildcard (`*`/omitted = match all). Several events use non-tool-name matcher vocabularies
instead (`SessionStart`'s `startup|resume|clear|compact|fork`, `SubagentStart`/`SubagentStop`'s
agent-type names, etc. — see §1/§2).

**What a hook can return to influence the session**:

| Exit code | Effect |
|---|---|
| `0` | Success; stdout parsed as JSON if present. Plain-text stdout is normally only visible in the debug log, except for `UserPromptSubmit`, `UserPromptExpansion`, `SessionStart` where Claude sees it directly. |
| `2` | Blocking error — blocks the action (tool call, permission, prompt, etc.) on events that support blocking; stderr becomes the blocking reason. |
| other | Non-blocking error — the action proceeds; JSON stdout is still read if valid. |

JSON stdout can set: `continue` (bool — stop the whole turn), `stopReason`, `systemMessage`,
`terminalSequence`, and an event-specific `hookSpecificOutput` object, e.g.
`permissionDecision: allow|deny|escalate` and `permissionDecisionReason` for `PreToolUse`,
`additionalContext` for `UserPromptSubmit`/`PostToolUse`/`Stop`/`SubagentStop`,
`updatedInput` (rewrite tool arguments) for `PreToolUse`, `decision: allow|deny` for
`TaskCreated`/`TaskCompleted`. `SubagentStop` returning exit code 2 prevents the subagent from
stopping; `TaskCreated`/`TaskCompleted` returning exit code 2 rolls back the creation/
completion.

---

## 4. Delivery guarantees

Source: `https://code.claude.com/docs/en/hooks`. This is the area the docs are explicitly
weakest on, which matters directly for spec.md's requirement that memory correctness must not
depend on hooks firing perfectly.

- **No stated delivery SLA.** The docs describe hooks as running "at best effort" —
  there is no documented guarantee that a configured hook fires for every occurrence of its
  event.
- **Timeouts**: `command`/`http`/`mcp_tool` hooks default to 600s (10 min), except
  `UserPromptSubmit` (30s) and `MessageDisplay` (10s); `prompt` hooks 30s; `agent` hooks 60s;
  `SessionEnd` hooks share a 1.5s budget across all matching hooks (raised to 60s if a
  per-hook timeout is explicitly configured longer). On timeout, a command/HTTP/MCP-tool hook
  is canceled and its output discarded — execution continues (non-blocking), i.e. a slow hook
  is silently dropped, not retried.
- **Non-zero exit**: does not block the action unless the exit code is exactly `2` (or the
  hook's JSON output explicitly overrides). No retry is documented.
- **Session crash**: not addressed on this page at all. No stated guarantee that
  already-queued or in-flight hooks complete, or that partial writes are rolled back.
- **Ordering**: "all matching hooks finish before Claude Code merges results" when multiple
  hooks match one event, but the docs explicitly flag that ordering across hooks that both try
  to set `updatedInput` on the same event is **non-deterministic**.

**Explicit open empirical question**: there is no documented answer for (a) whether a hook is
retried after a transient failure, (b) whether hook failures are surfaced anywhere durable
(vs. only a debug log / user-facing notice), or (c) exact behavior when the whole Claude Code
process is killed mid-hook. Given this, any ingestion pipeline built on hooks must independently
assume **at-most-once, best-effort delivery with no ordering guarantee across concurrent
matches**, and needs a reconciliation source — see §5.

---

## 5. The transcript file

Source: `https://code.claude.com/docs/en/sessions` (§"Export and locate session data" /
§"Where transcripts are stored").

- **Location**: `~/.claude/projects/<project>/<session-id>.jsonl`, where `<project>` is the
  working-directory path with non-alphanumeric characters replaced by `-`; names over 200
  characters are truncated to 200 with a hash of the full path appended. Location is
  configurable via `CLAUDE_CONFIG_DIR`; retention via `cleanupPeriodDays` in `settings.json`
  (default 30 days); writes can be suppressed via `CLAUDE_CODE_SKIP_PROMPT_HISTORY` or, for a
  single non-interactive run, `--no-session-persistence`.
- **Format**: JSONL. "Each line is a JSON object for a message, tool use, or metadata entry."
- **Explicitly undocumented and unstable**: "The entry format is internal to Claude Code and
  changes between versions, so scripts that parse these files directly can break on any
  release." The docs actively steer consumers away from parsing it directly: "To build on
  session data, use `/export` or the script interfaces instead" — where "script interfaces"
  means `claude -p --output-format json/stream-json`, `claude -p --resume <id>`, the
  `transcript_path` field hooks/statusline already receive, or the Agent SDK.
- **Sessions are saved continuously** as you work (not just at end), so a `SessionEnd` hook (or
  any later read) can archive a transcript that already contains everything up to that point.
- **Relative completeness vs. hooks**: the docs do not make an explicit "transcript is a
  superset of hooks" claim. What can be established: the transcript captures full conversation
  history including tool calls and results (used to reconstruct a resumed session's state), and
  a `SessionEnd` hook is specifically documented as a way to "archive the transcript when a
  session ends" — implying the transcript is treated as the durable record and hooks as a
  reactive/side-channel signal. But because the entry schema itself is explicitly
  undocumented, **whether the transcript captures every field an ingestion pipeline would want
  (e.g. permission decisions, exact hookSpecificOutput content) is an open empirical
  question**, not a documented fact. This is the single most consequential unknown for
  spec.md's "hooks aren't the source of truth" requirement — it needs to be answered by
  capturing a real transcript and diffing its fields against hook payloads, not from docs
  alone.

---

## 6. Subagent and Task semantics

Sources: `https://code.claude.com/docs/en/sub-agents`, cross-referenced with
`https://code.claude.com/docs/en/hooks`.

- **Hooks**: `SubagentStart`/`SubagentStop` fire around a subagent's lifetime; the matcher is
  the subagent's `name` from frontmatter (or built-in names `general-purpose`/`Explore`/
  `Plan`; plugin-scoped subagents match as `plugin:name`). A subagent's own frontmatter can
  additionally declare `PreToolUse`/`PostToolUse` (fire for that subagent's own tool calls) and
  `Stop`, which "is converted to `SubagentStop` at runtime." Inside a subagent's execution, the
  common hook-input fields carry `agent_id`/`agent_type` so a hook handler can tell it's
  running inside a subagent rather than the main session.
- **`TaskCreated`/`TaskCompleted`**: distinct from `SubagentStart`/`SubagentStop` — these fire
  around the `TaskCreate` tool / task completion, not subagent spawn/exit. The reference page
  does not state whether the underlying Task-tool invocation itself also fires a generic
  `PreToolUse`/`PostToolUse` pair in addition to `TaskCreated`/`TaskCompleted` — **left
  unresolved by the docs, flagged as an open question**.
- **Isolation model**: subagents run in isolated context windows and do not inherit the
  parent's conversation history (a `--fork-session`/`/branch`-style fork is the documented
  exception, which inherits the full parent conversation instead). A subagent's fresh context
  is limited to: system prompt, the task message from the parent, CLAUDE.md files, a git-status
  snapshot, and preloaded skills.
- **Transcript organization**: subagent transcripts are stored **separately** from the main
  conversation, are not affected by main-conversation compaction, and persist/expire under the
  same `cleanupPeriodDays` retention as the main transcript. The main conversation's transcript
  sees only the subagent's completion notification/summary, not its verbose intermediate
  activity — full subagent detail lives only in the subagent's own transcript file.
- **Parent/child recoverability**: a subagent can be given a name/ID that makes it addressable
  later (resume by name/ID via `SendMessage`), and the subagent's `agent_id` is present in its
  own hook payloads. However, **no explicit `parent_session_id`/`parent_agent_id` field is
  documented** on either the hook payload or in the transcript description. Practically, the
  parent/child link is inferable structurally (the subagent's transcript is stored nested under
  the parent session's project/session path, and the parent conversation records the
  `Task`/`Agent` tool call that spawned it, including the name/ID assigned), but there is no
  documented explicit foreign-key-style field connecting a subagent transcript entry back to a
  specific parent-session turn. Treat exact reconstruction mechanics as an **open empirical
  question** requiring inspection of a real nested transcript, not something confirmed by docs
  text alone.

---

## 7. Versioning

Sources: `https://code.claude.com/docs/en/hooks`, `https://code.claude.com/docs/en/sessions`.

- **No schema version field exists** anywhere in the documented hook input/output JSON, and
  none is mentioned for the transcript's per-line JSON either.
- **No changelog-driven contract**: the sessions page states plainly that the transcript "entry
  format is internal to Claude Code and changes between versions, so scripts that parse these
  files directly can break on any release," and offers no deprecation policy or migration
  notice mechanism — the only stated mitigation is to not depend on the raw format at all
  (use `/export` or the script interfaces in §5 instead).
- **Hook schema** is likewise not versioned in the reference page; changes would only surface
  via Claude Code's own release notes (not fetched as part of this pass — the release-notes
  page was out of scope for this ticket's 7 sub-questions, but is the only documented channel
  that could carry such an announcement).
- **Consequence for the ingestion pipeline**: there is no structural way to detect a schema
  change at ingest time other than defensive parsing (unknown-field tolerance, required-field
  validation with graceful degradation, and treating any parse failure as a signal to
  fall back to raw storage rather than reject the event). This should be treated as a hard
  design constraint, not an incidental detail.

---

## Summary of confirmed vs. open items

| # | Question | Status |
|---|---|---|
| 1 | Event list | Confirmed — 31 documented events; spec.md's 13 are a valid subset |
| 2 | Payload fields | Partially confirmed — common fields + several event-specific fields documented; many events (esp. environment/worktree/elicitation family) lack a full field-by-field schema in the docs |
| 3 | Configuration | Confirmed — settings.json structure, matchers, `$CLAUDE_PROJECT_DIR`, exit-code/JSON-output contract all documented |
| 4 | Delivery guarantees | Explicitly weak/undocumented — best-effort only, no retry/crash semantics documented; treat as open |
| 5 | Transcript file | Location/format confirmed; per-line schema explicitly undocumented and stated to be unstable across releases; completeness vs. hooks is an open empirical question |
| 6 | Subagent/Task semantics | Hook events and isolation model confirmed; explicit parent/child linking field not documented — open empirical question |
| 7 | Versioning | Confirmed absence — no schema version field or deprecation policy exists anywhere in the documented surface |
