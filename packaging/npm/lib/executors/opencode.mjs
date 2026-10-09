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
import { FOREIGN_CREDENTIAL_VARS, KEY_ENV_FOR_CHILD, profileKey } from "./profile.mjs";

const BIN = "opencode";
export const PROFILE_PROVIDER_ID = "stealth-open";

// Open-model mode (a profile with openai_base_url): the provider is declared inline through OPENCODE_CONFIG_CONTENT
// (https://opencode.ai/docs/config/ lists it as "inline config ... runtime overrides"), so nothing is written into
// the worktree or the user's own opencode.json. `@ai-sdk/openai-compatible` is OpenCode's documented package for
// any OpenAI-compatible API. The key is referenced as {env:OPEN_MODEL_API_KEY}, never embedded in the config text.
// Field names under "provider" follow OpenCode's published provider docs; the exact schema is NOT checked against
// the installed binary here (no run spends tokens in the test suite), so treat the first live run as the check.
export function openModelConfig(profile) {
  // request_extras (e.g. OpenRouter's provider.zdr) cannot be passed through OpenCode's provider options as far as
  // its docs say. Dropping them silently would let a profile claim zero retention that the requests do not ask for,
  // so a profile that needs them is refused here; the stealth executor sends them.
  if (Object.keys(profile.request_extras).length) {
    throw new Error(`profile ${profile.name} sets request_extras, which the opencode executor cannot send; use the stealth executor for it`);
  }
  const options = { baseURL: profile.openai_base_url, apiKey: `{env:${KEY_ENV_FOR_CHILD}}` };
  return {
    $schema: "https://opencode.ai/config.json",
    provider: {
      [PROFILE_PROVIDER_ID]: {
        npm: "@ai-sdk/openai-compatible",
        name: "StealthLab open model",
        options,
        models: { [profile.model_id]: { name: profile.model_id } },
      },
    },
  };
}

export function openModelEnv(profile, env = process.env) {
  const out = {};
  for (const k of Object.keys(env)) if (FOREIGN_CREDENTIAL_VARS.includes(k.toUpperCase())) out[k] = undefined;
  return { ...out, [KEY_ENV_FOR_CHILD]: profileKey(profile, env), OPENCODE_CONFIG_CONTENT: JSON.stringify(openModelConfig(profile)) };
}

const adapter = {
  id: "opencode",
  bin: BIN,
  VERIFIED_WITH: { version: "1.18.32", date: VERIFIED_DATE, source: "opencode run --help (1.18.32, installed binary); https://opencode.ai/docs/cli/" },
  DOCS_SOURCE: "https://opencode.ai/docs/cli/",
  AUTO_APPROVE_FLAG: "--auto",

  detect({ env = process.env } = {}) {
    return detectBin(BIN, { env });
  },

  buildCommand({ task, model, worktree, timeoutS, env = process.env, bin, profile } = {}) {
    const launch = launchOrThrow(BIN, { env, bin });
    if (profile && !profile.openai_base_url) {
      throw new Error(`profile ${profile.name} has no openai_base_url, so the opencode executor cannot reach it (use claude for its anthropic_base_url)`);
    }
    const args = [...launch.prefix, "run", "--format", "json", "--auto"];
    if (worktree) args.push("--dir", worktree);
    if (profile) args.push("--model", `${PROFILE_PROVIDER_ID}/${profile.model_id}`);
    else if (model) args.push("--model", String(model));
    args.push(safeTaskArg(task));
    void timeoutS; // no timeout flag in `opencode run --help`; the runtime enforces it
    return { cmd: launch.cmd, args, env: profile ? { ...scrubEnv(env), ...openModelEnv(profile, env) } : scrubEnv(env), stdinText: undefined };
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
