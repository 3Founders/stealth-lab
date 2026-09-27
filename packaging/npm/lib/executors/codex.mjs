// Codex CLI adapter. Drives the user's own locally installed `codex` under the
// user's own ChatGPT/API login (ToS boundary: see common.mjs). The write sandbox
// is only granted because the runtime spawns this inside a throwaway git
// worktree (spec constraint 4).
//
// Verified 2026-09-27 against the installed binary (codex-cli 0.153.4,
// %APPDATA%\npm\codex.cmd -> node_modules\@openai\codex\bin\codex.js),
// `codex exec --help`, excerpt:
//   Usage: codex exec [OPTIONS] [PROMPT]
//   [PROMPT] Initial instructions for the agent. If not provided as an argument (or if `-` is
//            used), instructions are read from stdin.
//   -m, --model <MODEL>          Model the agent should use
//   -s, --sandbox <SANDBOX_MODE> [possible values: read-only, workspace-write, danger-full-access]
//   -C, --cd <DIR>               Tell the agent to use the specified directory as its working root
//       --color <COLOR>          [possible values: always, never, auto]
//       --json                   Print events to stdout as JSONL
// Docs (https://developers.openai.com/codex/noninteractive -> learn.chatgpt.com/docs/non-interactive-mode):
//   "By default, `codex exec` runs in a read-only sandbox." ... "Prefer the explicit
//   `--sandbox workspace-write` flag in new scripts" (over the deprecated --full-auto).
// We deliberately do NOT use --dangerously-bypass-approvals-and-sandbox.
//
// The task goes on stdin (PROMPT = "-"), so it never touches an argv parser.
//
// JSON output (--json), per the docs example; the event names thread.started,
// item.completed, turn.completed, turn.failed, agent_message and
// cached_input_tokens were also confirmed present in the installed codex.exe:
//   {"type":"thread.started","thread_id":"..."}
//   {"type":"item.completed","item":{"id":"item_3","type":"agent_message","text":"..."}}
//   {"type":"turn.completed","usage":{"input_tokens":24763,"cached_input_tokens":24448,"output_tokens":122,"reasoning_output_tokens":0}}
//   {"type":"turn.failed","error":{"message":"..."}} / {"type":"error","message":"..."}
import {
  VERIFIED_DATE, detectBin, healthOf, jsonLines, launchOrThrow, num, plainResult, scrubEnv, tailText, extractLearned,
} from "./common.mjs";

const BIN = "codex";

const adapter = {
  id: "codex",
  bin: BIN,
  VERIFIED_WITH: { version: "0.153.4", date: VERIFIED_DATE, source: "codex exec --help (codex-cli 0.153.4, installed binary); https://learn.chatgpt.com/docs/non-interactive-mode" },
  DOCS_SOURCE: "https://learn.chatgpt.com/docs/non-interactive-mode",
  AUTO_APPROVE_FLAG: "--sandbox workspace-write",

  detect({ env = process.env } = {}) {
    return detectBin(BIN, { env });
  },

  buildCommand({ task, model, worktree, timeoutS, env = process.env, bin } = {}) {
    const launch = launchOrThrow(BIN, { env, bin });
    const text = String(task ?? "").trim();
    if (!text) throw new Error("task is empty");
    const args = [...launch.prefix, "exec", "--json", "--color", "never", "--sandbox", "workspace-write"];
    if (worktree) args.push("--cd", worktree);
    if (model) args.push("--model", String(model));
    args.push("-");
    void timeoutS; // no timeout flag in `codex exec --help`; the runtime enforces it
    return { cmd: launch.cmd, args, env: scrubEnv(env), stdinText: text };
  },

  parseOutput({ stdout = "", stderr = "" } = {}) {
    const events = jsonLines(stdout).filter((e) => typeof e.type === "string");
    if (!events.length) return plainResult(stdout, stderr);
    let finalMessage = "";
    let tin = 0, tout = 0, sawTokens = false, err = "";
    for (const e of events) {
      if (e.type === "item.completed" && e.item && e.item.type === "agent_message" && typeof e.item.text === "string") finalMessage = e.item.text;
      if (e.type === "turn.completed" && e.usage) {
        sawTokens = true;
        tin += num(e.usage.input_tokens) || 0;
        tout += (num(e.usage.output_tokens) || 0) + (num(e.usage.reasoning_output_tokens) || 0);
      }
      if (e.type === "turn.failed") err = String((e.error && e.error.message) || "turn failed");
      if (e.type === "error" && e.message) err = String(e.message);
    }
    if (!finalMessage && err) finalMessage = `error: ${err}`;
    finalMessage = tailText(finalMessage);
    return { finalMessage, learned: extractLearned(finalMessage), tokens: sawTokens ? { in: tin, out: tout } : null, costUsd: null };
  },

  health({ env = process.env } = {}) {
    return healthOf(adapter, { env });
  },
};

export default adapter;
