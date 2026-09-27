// OpenCode adapter. Drives the user's own locally installed `opencode` with the
// user's own provider logins (see common.mjs for the ToS boundary). Auto-approval
// (`--auto`) is only ever used because the runtime spawns this inside a
// throwaway git worktree (spec constraint 4); never point cwd at a user checkout.
//
// Verified 2026-09-27 against the installed binary (opencode 1.18.32,
// %APPDATA%\npm\opencode.cmd -> node_modules\opencode-ai\bin\opencode.exe),
// `opencode run --help`, excerpt:
//   opencode run [message..]
//   Positionals:  message  message to send                          [array] [default: []]
//   -m, --model   model to use in the format of provider/model      [string]
//       --format  format: default (formatted) or json (raw JSON events)
//                                  [string] [choices: "default", "json"] [default: "default"]
//       --dir     directory to run in, path on remote server if attaching   [string]
//       --auto    auto-approve permissions that are not explicitly denied (dangerous!)
//                                                                 [boolean] [default: false]
// Also matches https://opencode.ai/docs/cli/ (run flags table).
//
// JSON output (--format json), verified from the run command compiled into
// opencode.exe 1.18.32: one line per event,
//   JSON.stringify({type, timestamp, sessionID, ...data})
// with type in: text {part:{type:"text",text}}, step_start {part},
//   step_finish {part:{type:"step-finish",tokens:{input,output,reasoning,cache:{read,write}},cost}},
//   tool_use {part}, reasoning {part}, error {error:{name,data:{message}}}.
// No real run was captured (that would spend tokens); the fixture
// test/fixtures/executors/opencode.jsonl is constructed from this verified shape.
import {
  VERIFIED_DATE, detectBin, healthOf, jsonLines, launchOrThrow, num, plainResult, safeTaskArg, scrubEnv, tailText, extractLearned,
} from "./common.mjs";

const BIN = "opencode";

const adapter = {
  id: "opencode",
  bin: BIN,
  VERIFIED_WITH: { version: "1.18.32", date: VERIFIED_DATE, source: "opencode run --help (1.18.32, installed binary); https://opencode.ai/docs/cli/" },
  DOCS_SOURCE: "https://opencode.ai/docs/cli/",
  AUTO_APPROVE_FLAG: "--auto",

  detect({ env = process.env } = {}) {
    return detectBin(BIN, { env });
  },

  buildCommand({ task, model, worktree, timeoutS, env = process.env, bin } = {}) {
    const launch = launchOrThrow(BIN, { env, bin });
    const args = [...launch.prefix, "run", "--format", "json", "--auto"];
    if (worktree) args.push("--dir", worktree);
    if (model) args.push("--model", String(model));
    args.push(safeTaskArg(task));
    void timeoutS; // no timeout flag in `opencode run --help`; the runtime enforces it
    return { cmd: launch.cmd, args, env: scrubEnv(env), stdinText: undefined };
  },

  parseOutput({ stdout = "", stderr = "" } = {}) {
    const events = jsonLines(stdout).filter((e) => typeof e.type === "string" && ("part" in e || "error" in e));
    if (!events.length) return plainResult(stdout, stderr);
    let finalMessage = "";
    let tin = 0, tout = 0, cost = 0, sawTokens = false, sawCost = false;
    let err = "";
    for (const e of events) {
      if (e.type === "text" && e.part && typeof e.part.text === "string" && e.part.text.trim()) finalMessage = e.part.text;
      if (e.type === "step_finish" && e.part) {
        const t = e.part.tokens || {};
        if (num(t.input) !== null || num(t.output) !== null) {
          sawTokens = true;
          tin += num(t.input) || 0;
          tout += (num(t.output) || 0) + (num(t.reasoning) || 0);
        }
        if (num(e.part.cost) !== null) { sawCost = true; cost += e.part.cost; }
      }
      if (e.type === "error" && e.error) err = String((e.error.data && e.error.data.message) || e.error.message || e.error.name || "error");
    }
    if (!finalMessage && err) finalMessage = `error: ${err}`;
    finalMessage = tailText(finalMessage);
    return {
      finalMessage,
      learned: extractLearned(finalMessage),
      tokens: sawTokens ? { in: tin, out: tout } : null,
      costUsd: sawCost ? cost : null,
    };
  },

  health({ env = process.env } = {}) {
    return healthOf(adapter, { env });
  },
};

export default adapter;
