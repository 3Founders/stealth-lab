# Claude Code hook schema

Type: research
Status: resolved
Blocked by:

## Question

What is the *actual* current Claude Code ingestion surface, as documented by primary sources — not as assumed by spec.md?

spec.md lists hook events including `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `PostToolBatch`, `SubagentStart`, `SubagentStop`, `TaskCreated`, `TaskCompleted`, `PreCompact`, `PostCompact`, `SessionEnd` — and then says to use the actual current schema after inspecting official documentation. Several of those names look speculative.

Establish, against official Anthropic documentation:

1. **The real hook event list.** Which of the above exist, under what exact names, and what else exists that spec.md omits.
2. **Per-event payload fields.** For each event, the exact JSON the hook receives on stdin: session id, transcript path, cwd, tool name, tool input, tool response, permission decision, error information, and anything else.
3. **Configuration mechanism.** How hooks are registered in `settings.json` — matchers, command form, the `$CLAUDE_PROJECT_DIR` variable, project vs user vs local scope — and what the hook process can and cannot return to influence the session.
4. **Delivery guarantees.** Is a hook guaranteed to fire? What happens on timeout, on non-zero exit, on a crashed session? Is ordering guaranteed? This directly determines how much the ingestion pipeline must defend against missing and late events.
5. **The transcript file.** Claude Code writes session transcripts as JSONL. Where do they live, what is one line's shape, and what do they contain that hooks do not? spec.md requires that memory correctness must **not** depend on hooks firing perfectly — so the transcript is the candidate backstop, and its completeness relative to hooks is the key fact to establish.
6. **Subagent and Task semantics.** How subagent invocations appear in hooks and in the transcript, and whether parent/child relationships are recoverable.
7. **Versioning.** Is the hook schema versioned or announced anywhere, such that a provider version change can be detected rather than silently mis-normalized?

Deliverable: a cited Markdown findings file. Every claim carries a link to official documentation. Where documentation is silent, say so explicitly and mark it as an empirical question rather than guessing — a wrong assumption here propagates into the trace model and the whole ingestion pipeline.

The `claude-code-guide` agent is available and is the right first stop; corroborate against the public docs.

## Answer

Full cited findings: [research/claude-code-hook-schema.md](../research/claude-code-hook-schema.md).

Sourced against `https://code.claude.com/docs/en/hooks`, `https://code.claude.com/docs/en/sessions`,
and `https://code.claude.com/docs/en/sub-agents` (fetched 2026-08-17), corroborated via a
`claude-code-guide` pass plus independent web search.

1. **Event list**: spec.md's 13 named events are all real, exact hook-event names — not
   speculative. The official surface is larger: **31 documented events total**, adding families
   spec.md never scoped (`Notification`, `PermissionRequest`/`PermissionDenied`, `ConfigChange`,
   `CwdChanged`, `DirectoryAdded`, `FileChanged`, `WorktreeCreate`/`WorktreeRemove`,
   `Elicitation`/`ElicitationResult`, `TeammateIdle`, `Setup`, `InstructionsLoaded`,
   `UserPromptExpansion`, `MessageDisplay`, `StopFailure`).
2. **Payload fields**: common fields (`session_id`, `prompt_id`, `transcript_path`, `cwd`,
   `permission_mode`, `hook_event_name`, `agent_id`/`agent_type`) are documented for every
   event; several event-specific fields are documented (`tool_name`/`tool_input` on
   `PreToolUse`, `prompt` on `UserPromptSubmit`, `source` on `SessionStart`, etc.), but a full
   field-by-field schema is **not** published for every event (e.g. `CwdChanged`,
   `Elicitation*`, `TeammateIdle`) — flagged as open/empirical.
3. **Configuration**: matches spec.md's assumption — `settings.json` (`user`/project/
   `project.local`/managed/plugin/subagent-frontmatter scopes), `matcher` + `hooks[].command`,
   `${CLAUDE_PROJECT_DIR}`. Hooks return control via exit code (0/2/other) and JSON stdout
   (`continue`, `hookSpecificOutput.permissionDecision`, `updatedInput`, `decision`, etc.).
4. **Delivery guarantees**: the weakest documented area — hooks are explicitly **best-effort**,
   with no retry, no crash semantics, and non-deterministic ordering when multiple hooks touch
   the same event concurrently. This directly confirms spec.md's design instinct that memory
   correctness must not depend on hooks firing perfectly.
5. **Transcript**: JSONL at `~/.claude/projects/<project>/<session-id>.jsonl`, written
   continuously. Docs explicitly state the per-line entry format is **internal and can change
   between releases** — official guidance is to not parse it directly (use `/export` or the
   documented script interfaces instead). Whether it's a strict superset of hook data is not
   stated in docs and remains an open empirical question requiring direct capture/diffing.
6. **Subagents/Tasks**: `SubagentStart`/`SubagentStop` (matcher = subagent name) are distinct
   from `TaskCreated`/`TaskCompleted` (around the Task-tool/task lifecycle); subagent
   transcripts are stored separately and nested under the parent session's path. No documented
   `parent_session_id`/`parent_agent_id` field exists on hook payloads or transcript entries —
   parent/child linkage is structurally inferable at best, not a confirmed explicit field.
7. **Versioning**: confirmed absent. No schema-version field anywhere, no deprecation policy;
   the only stated mitigation is defensive, forward-tolerant parsing plus tracking Claude Code
   release notes.
