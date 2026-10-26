# Dispatch for Cursor

What this is: with `stealthlab-mcp install --client cursor --with-exec`, Cursor's prompt hook lets the cheap open
models in `~/.stealthlab/exec.json` try each task BEFORE Cursor's own model runs, the way the Claude Code hook does
(`packaging/npm/lib/dispatch.mjs`, unchanged). A verified change is applied to the checkout and the prompt is stopped with
a message; otherwise the prompt goes on to Cursor's model as without StealthLab.

Code: `packaging/npm/lib/cursor_dispatch.mjs` (the hook's dispatch step), `lib/cursor_hooks.mjs` (the hook entry and the
`hooks.json` writer), `lib/clients.mjs` (the install), `lib/instructions.mjs` (a note in the Cursor rule). Tests:
`test/cursor_dispatch.test.mjs`.

## What Cursor allows (read from https://cursor.com/docs/agent/hooks, 2026-10-12)

| Need | Cursor's answer | What we do |
|---|---|---|
| Stop a prompt before the model sees it | `beforeSubmitPrompt` runs "right after user hits send but before backend request" and returns `{"continue": false, "user_message": "..."}`; "Can prevent submission" | on a verified cheap change: `{"continue": false, "user_message": "Done by <model> ..."}` |
| Rewrite the prompt | not possible: `beforeSubmitPrompt` has no output field for the prompt text | so no `!opus` / `!cheap` prefix can be stripped; see below |
| Time the hook may take | per hook, `timeout` (seconds) in `hooks.json`; the default is a platform default (not stated) | dispatch installs the prompt hook with `timeout: 600`; the other hooks keep their short limits |
| The model and workspace | every hook gets `conversation_id`, `generation_id`, `model`, `workspace_roots`; `beforeSubmitPrompt` adds `prompt` and `attachments` | the plan is made against Cursor's model (`model`), the repo is `workspace_roots[0]` |
| Switch Cursor's model | no hook for it | nothing: the cheap models do the task or Cursor's own model does |

## The flow

1. `beforeSubmitPrompt` -> `hook cursor-prompt`. Without the executor installed (`dispatch: true` in the saved config and
   models in exec.json) it behaves as before: an instant `{"continue": true}` and a background lookup.
2. With it: find_ways with a model plan (`my_model` = Cursor's model, candidates = the open models), then the same
   `dispatch()` as Claude Code, with its check (`STEALTHLAB_DISPATCH_CHECK`, the matched library entry's check, or the check
   the project itself defines).
3. Verified and applied: `{"continue": false, "user_message": "Done by ..."}`. Cursor's model never runs. The Cursor rule
   (`.cursor/rules/stealthlab.mdc`) tells the agent how to recognise such a change when the user refers to one it did not make.
4. Not handled: `{"continue": true}`; what was tried and what Kel knows reach the agent at its first tool call
   (`postToolUse`), as without dispatch.
5. Any error: `{"continue": true}` (fail open).

## Limits you should know

- **"auto" is not a model.** Cursor's `model` for Auto mode is "auto"; the plan has nothing to compare against, so there is
  no dispatch unless `STEALTHLAB_CURSOR_MODEL` names the model to plan against.
- **No prompt rewriting**, so the Claude Code overrides (`!opus`, `!cheap`) cannot be stripped for the agent in Cursor; the
  prefix would be shown to Cursor's model as plain text.
- **No continuity line** for the next prompt in Cursor unless the Cursor rule is installed (it carries the note).
- The user sees the stop as a blocked prompt with our message, not as an answer in the chat.

## Switches

`STEALTHLAB_CURSOR_DISPATCH=off` (Cursor only), `STEALTHLAB_DISPATCH=off` (all clients), `STEALTHLAB_CURSOR_MODEL`, and every
`STEALTHLAB_DISPATCH_*` switch of the Claude Code dispatch.

## Not verified here

`cursor-agent` is not installed on the machine this was built on, so nothing was run against a real Cursor. The tests drive the
hook with Cursor's documented payloads and a stand-in executor. Proof pending: run `cursor-agent` (or Cursor) with a
repository that has `.kell/`, one cheap model in exec.json and a `STEALTHLAB_DISPATCH_CHECK`, send a task, and confirm the
prompt is stopped with "Done by ..." and the change is in the working tree; then a prompt the cheap model fails and confirm it
reaches Cursor's model with the "tried" note at its first tool call.
