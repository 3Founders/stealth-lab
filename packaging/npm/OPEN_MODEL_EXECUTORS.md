# Open models in the local executors

Three executors can run an open model (GLM, DeepSeek, Kimi, ...) through `stealthlab-mcp exec`'s `achieve`:

| Executor | What runs | Endpoint it needs | Notes |
|---|---|---|---|
| `claude` | the user's own `claude -p`, headless | `anthropic_base_url` (an Anthropic-compatible route, e.g. Z.ai's) | same harness as the strong model, so only the model differs |
| `opencode` | the user's own `opencode run` | `openai_base_url` | provider is declared inline (`OPENCODE_CONFIG_CONTENT`); nothing written to the repo |
| `stealth` | our own small agent, `lib/agent/step_agent.mjs` | `openai_base_url` | no third-party binary; the only executor that sends `request_extras` (e.g. OpenRouter `provider.zdr`) |

## Configure
`~/.stealthlab/exec.json`:

```json
{
  "executors": { "stealth": { "models": ["glm-5.3"] } },
  "profiles": {
    "glm-5.3": {
      "model_id": "glm-5.3",
      "key_env": "ZAI_API_KEY",
      "anthropic_base_url": "https://api.z.ai/api/anthropic",
      "openai_base_url": "https://api.example.com/v1",
      "input_per_mtok": 0.6, "output_per_mtok": 2.2,
      "zdr": false, "no_training": true, "region": "us", "dpa_signed": false
    }
  }
}
```

`key_env` is the NAME of the environment variable holding the user's own provider key; the key is never stored here.
Compliance fields are declarations, absent = unknown. A profile is keyed by the model name a run asks for.

## How a model plan reaches them
- With the executor installed, every `executors.<id>.models` entry is offered to `find_ways` as a candidate
  `model|<id>` (`lib/hook.mjs` `execCandidates`).
- When the plan's step is one of them, the model guard (`lib/model_guard.mjs`) sends the work to the
  `stealth-delegator` subagent, with `executor`, `model` and the plan's `instance_key`.
- `achieve(instance_key=...)` reports each attempt with `report_result` (queued in the outbox when offline).
  `escalate` follows the reply's `next_model` while a local executor can run it, and `run_result.next_model` names
  the model after that. The guard then follows it, e.g. back to a Claude subagent.
- The plan text (`planPart`) tells the agent to use the delegator for such a step and not to report it again. A
  verified run is applied from the shell with `stealthlab-mcp exec apply <run_id>`, which makes the same checks
  as `apply_run`; the delegator's reply gives the exact command.
- Not yet run end to end: a live Claude Code session, the hosted `report_result` and a real provider key.

## What the code guarantees (and tests)
- The provider key goes only into the child that talks to that provider, never into argv, logs or the config text.
- `claude` and `stealth` children have the user's other vendors' credentials removed; `claude` also gets an empty
  `CLAUDE_CONFIG_DIR`, so the user's Claude login and hooks are not presented to a third party.
- A profile that sets `request_extras` is refused by `claude` and `opencode` (they cannot send them) rather than silently
  dropping a requirement such as zero retention.
- The step agent keeps file access inside the worktree, refuses `.git`, withholds the key from `run_shell`, and stops at
  its step and cost limits.

## What it does not guarantee
- Quality: the step agent is a re-implementation of the loop in `backend/app/execution/coding_agent.py` and is unmeasured.
- Containment: `run_shell` runs as the user inside a throwaway worktree; it is not a sandbox.
- The OpenCode inline provider block (`OPENCODE_CONFIG_CONTENT`, `@ai-sdk/openai-compatible`, `{env:...}` key) was run against the installed OpenCode 1.18.35 with a fake endpoint and honoured the baseURL and key (2026-10-09). Z.ai's endpoint was not called; a real run with a real key is still the check.

- Claude Code is the user's own install, used under their terms; we do not bundle it. Confirm Anthropic's terms for
  pointing it at a non-Anthropic endpoint before offering this as a feature.
