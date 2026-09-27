// Cline CLI adapter. Drives the user's own locally installed `cline` under the
// user's own provider login (ToS boundary: see common.mjs). Auto-approval is only
// used because the runtime spawns this inside a throwaway git worktree (spec
// constraint 4). A hang has been reported on Linux ARM64: the runtime's hang
// detector and health() (a bounded `--version`) are the guard.
//
// Verified 2026-09-27 against the installed binary (cline 3.0.61,
// %APPDATA%\npm\cline.cmd -> node_modules\cline\bin\cline (node script that
// execs the bundled cline.exe)), `cline --help`, excerpt:
//   Usage: cline [options] [command] [prompt]
//   prompt                        Your prompt. Default to start in act mode with auto-approve enabled.
//   --json                        Output messages as JSON instead of styled text
//   --auto-approve <boolean>      Set tool auto-approval for all tools (default: true)
//   -c, --cwd <path>              Working directory
//   -m, --model <model-id>        Model to use for the session with the selected provider
//   -t, --timeout <seconds>       Optional timeout in seconds (default: 0 for no timeout)
// Docs: https://docs.cline.bot/cli/cli-reference (same flags).
//
// JSON output (--json): the docs page still shows the older {"type":"say"|"ask",
// "text",...} NDJSON shape; the installed 3.0.61 binary instead writes, per its
// compiled source, one line per event: {"type":"agent_event","event":{...}} where
// event.type in iteration_start|iteration_end|content_start|content_end|usage|done|notice,
//   content_end {contentType:"text", text}
//   usage {inputTokens, outputTokens, totalInputTokens, totalOutputTokens, totalCost, ...}
//   done {reason:"completed"|"error"|..., text, iterations, usage}
// We parse the verified 3.x shape and also accept the documented say/ask shape.
import {
  VERIFIED_DATE, detectBin, healthOf, jsonLines, launchOrThrow, num, plainResult, safeTaskArg, scrubEnv, tailText, extractLearned,
} from "./common.mjs";

const BIN = "cline";

const adapter = {
  id: "cline",
  bin: BIN,
  VERIFIED_WITH: { version: "3.0.61", date: VERIFIED_DATE, source: "cline --help (3.0.61, installed binary); https://docs.cline.bot/cli/cli-reference" },
  DOCS_SOURCE: "https://docs.cline.bot/cli/cli-reference",
  AUTO_APPROVE_FLAG: "--auto-approve true",

  detect({ env = process.env } = {}) {
    return detectBin(BIN, { env });
  },

  buildCommand({ task, model, worktree, timeoutS, env = process.env, bin } = {}) {
    const launch = launchOrThrow(BIN, { env, bin });
    const args = [...launch.prefix, "--json", "--auto-approve", "true"];
    if (worktree) args.push("--cwd", worktree);
    if (model) args.push("--model", String(model));
    if (Number.isFinite(timeoutS) && timeoutS > 0) args.push("--timeout", String(Math.ceil(timeoutS)));
    args.push(safeTaskArg(task));
    return { cmd: launch.cmd, args, env: scrubEnv(env), stdinText: undefined };
  },

  parseOutput({ stdout = "", stderr = "" } = {}) {
    const lines = jsonLines(stdout);
    const agent = lines.filter((l) => l.type === "agent_event" && l.event && typeof l.event === "object").map((l) => l.event);
    const legacy = lines.filter((l) => (l.type === "say" || l.type === "ask") && typeof l.text === "string");
    if (!agent.length && !legacy.length) return plainResult(stdout, stderr);
    let finalMessage = "";
    let tokens = null, costUsd = null;
    for (const e of agent) {
      if (e.type === "content_end" && e.contentType === "text" && typeof e.text === "string" && e.text.trim()) finalMessage = e.text;
      if (e.type === "usage") {
        const i = num(e.totalInputTokens), o = num(e.totalOutputTokens);
        if (i !== null || o !== null) tokens = { in: i || 0, out: o || 0 };
        if (num(e.totalCost) !== null) costUsd = e.totalCost;
      }
      if (e.type === "done") {
        if (typeof e.text === "string" && e.text.trim()) finalMessage = e.text;
        else if (!finalMessage && e.reason && e.reason !== "completed") finalMessage = `error: ${e.reason}`;
        const u = e.usage;
        if (!tokens && u && (num(u.inputTokens) !== null || num(u.outputTokens) !== null)) tokens = { in: num(u.inputTokens) || 0, out: num(u.outputTokens) || 0 };
      }
    }
    if (!agent.length) {
      const said = legacy.filter((l) => l.type === "say" && !l.partial && ["completion_result", "text"].includes(l.say));
      const done = said.filter((l) => l.say === "completion_result");
      const pick = (done.length ? done : said).pop();
      if (pick) finalMessage = pick.text;
    }
    finalMessage = tailText(finalMessage);
    return { finalMessage, learned: extractLearned(finalMessage), tokens, costUsd };
  },

  health({ env = process.env } = {}) {
    return healthOf(adapter, { env });
  },
};

export default adapter;
