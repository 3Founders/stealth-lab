// Executor adapter registry. Each adapter drives one locally installed coding
// agent headlessly; see common.mjs for the shared launch rules (no shell, .cmd
// shims unwrapped, STEALTHLAB_* stripped from the child env) and the ToS boundary
// (only the user's own installed agents, under the user's own logins).
//
// Interface (fixed by .scratch/prompts/executor_layer_split.md):
//   { id, VERIFIED_WITH: {version,date,source}|null,
//     detect({env}) -> {installed, version, bin},
//     buildCommand({task, model, worktree, timeoutS, env}) -> {cmd, args, env, stdinText},
//     parseOutput({stdout, stderr, exitCode}) -> {finalMessage, learned[], tokens|null, costUsd|null},
//     health({env}) -> {healthy, detail} }
// Extras every adapter also carries: bin (the PATH name), DOCS_SOURCE, AUTO_APPROVE_FLAG,
// and buildCommand accepts an optional `bin` (a path from detect()) to skip the PATH lookup.
//
// Gate: runnable(adapter, detected) / assertRunnable(...) are the one rule the
// runtime applies before spawning: VERIFIED_WITH must be set and the installed
// major version must equal the verified one; otherwise the adapter refuses.
import opencode from "./opencode.mjs";
import codex from "./codex.mjs";
import claude from "./claude.mjs";
import gemini from "./gemini.mjs";
import openhands from "./openhands.mjs";
import cline from "./cline.mjs";
import stealth from "./stealth.mjs";
import fake from "./fake.mjs";

export { runnable, assertRunnable, resolveBin, parseCmdShim, scrubEnv, extractLearned } from "./common.mjs";

export const ADAPTERS = Object.freeze({ opencode, codex, claude, gemini, openhands, cline, stealth, fake });

export function fakeAllowed(env = process.env) {
  return (env || {}).STEALTHLAB_EXEC_ALLOW_FAKE === "1";
}

export function getAdapter(id, { env = process.env } = {}) {
  if (typeof id !== "string" || !Object.prototype.hasOwnProperty.call(ADAPTERS, id)) {
    throw new Error(`unknown executor "${id}" (known: ${Object.keys(ADAPTERS).filter((k) => k !== "fake").join(", ")})`);
  }
  if (id === "fake" && !fakeAllowed(env)) {
    throw new Error('executor "fake" is test-only; set STEALTHLAB_EXEC_ALLOW_FAKE=1 to use it');
  }
  return ADAPTERS[id];
}

// The adapters a caller may select under this env: every real one, plus fake when allowed.
export function listAdapters({ env = process.env } = {}) {
  return Object.values(ADAPTERS).filter((a) => a.id !== "fake" || fakeAllowed(env));
}
