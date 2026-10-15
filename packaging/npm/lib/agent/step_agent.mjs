// StealthLab's own small coding agent: a bounded tool loop (read, list, search, edit, write, shell) over ONE
// OpenAI-compatible chat/completions endpoint. It is the "stealth" executor's child process, so the runtime gives it
// a throwaway git worktree as cwd and verifies the result with the node's checks; the agent never decides success.
//
// Why it exists: the harnesses we drive (claude, opencode, ...) are someone else's code with their own prompts,
// data flows and cost behaviour. This one is ours: zero dependencies, one endpoint, every request body visible in
// this file, a hard step / cost budget, and the provider key kept out of everything the model can see or run.
//
// Honest scope limits:
//  * Quality is not claimed. The tool set and prompt follow the loop tuned in backend/app/execution/coding_agent.py
//    (numbered reads, bounded output, a step budget), but this is a re-implementation and has not been benchmarked
//    against it; the three-arm harness is the place to find out.
//  * `run_shell` runs whatever the model asks, in the worktree, as the user. The worktree is throwaway and the key is
//    withheld from the shell's env, but a command can still touch the network and the user's files outside cwd. The
//    runtime's worktree + scope check is the containment; this is not a sandbox.
//  * No streaming, no parallel tool calls beyond running a returned batch in order, no image input.
//
// Input (stdin, JSON): { task, base_url, model_id, input_per_mtok?, output_per_mtok?, request_extras?, no_system_role?, max_steps?,
//   max_cost_usd?, max_tokens?, hang_s?, early_stop?, early_stop_steps?, early_stop_errors?, block_installs? }. The provider key
//   arrives in env OPEN_MODEL_API_KEY only. early_stop (default true): a run that is going round in circles stops at once
//   (lib/agent/stall.mjs). block_installs (default true): while the worktree's node_modules / venv are links to the project's
//   own, package installs in run_shell are refused (lib/agent/installs.mjs).
// Output (stdout): one progress line per step (so the runtime's hang detector sees life), then one final JSON line:
//   {"type":"final","message","learned":[],"tokens":{"in","out"},"costUsd","steps","stop","stalled"?}.
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { exec, execFileSync } from "node:child_process";
import { pathToFileURL } from "node:url";
import { StallDetector } from "./stall.mjs";
import { installRefusal } from "./installs.mjs";

export const KEY_ENV = "OPEN_MODEL_API_KEY";
const PROTECTED = new Set(["model", "messages", "stream", "stream_options", "tools", "tool_choice", "functions",
  "function_call", "max_tokens", "max_completion_tokens", "temperature"]);
const SKIP_DIRS = new Set([".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".next", ".tox"]);
const MAX_READ_CHARS = 12000;
const MAX_LIST = 300;
const MAX_MATCHES = 60;
const MAX_FILE_BYTES = 1_000_000;
const SHELL_TIMEOUT_MS = 120_000;
const SHELL_TAIL = 6000;
// ~40k tokens: every step resends the whole history, so a big context multiplies tokens (and rate-limit hits)
const CONTEXT_CHAR_LIMIT = 160_000;

const SYSTEM = `You are a coding agent working inside one git repository (your current directory). Complete the task with the \
smallest correct change.
- Find the code first with search / list_files, then read only what you need with read_file (it returns numbered lines).
- Change files with edit_file (exact text replacement; old_text must match once) or write_file for new files.
- Check your work by running the project's tests or a quick command with run_shell when one exists.
- You have at most {max_steps} tool calls. Spend them locating the right code, then editing, then checking.
- When done, call finish with a short summary. Add a line "Learned: <one fact worth keeping>" only if you found something surprising.
- Do not touch files outside the task, do not install packages, and never print or look for credentials.`;

const TOOLS = [
  fn("read_file", "Read a file with line numbers.", { path: str("File path relative to the repo root"),
    start_line: int("First line, 1-based (default 1)"), num_lines: int("How many lines (default 200)") }, ["path"]),
  fn("list_files", "List files under a directory (recursive, skips .git and dependency folders).",
    { path: str("Directory, default ."), pattern: str("Substring the path must contain") }, []),
  fn("search", "Search file contents for text (case-insensitive substring, or a regex when regex=true).",
    { query: str("What to look for"), path: str("Directory or file to search, default ."), regex: { type: "boolean" } }, ["query"]),
  fn("edit_file", "Replace text in a file. old_text must appear exactly once.", { path: str("File path"),
    old_text: str("Exact text to replace"), new_text: str("Replacement text") }, ["path", "old_text", "new_text"]),
  fn("write_file", "Create or overwrite a file with the given content.", { path: str("File path"), content: str("Full file content") }, ["path", "content"]),
  fn("run_shell", "Run a shell command in the repo root (tests, builds). Output is truncated.", { command: str("The command") }, ["command"]),
  fn("finish", "Finish the task with a summary.", { summary: str("What you changed and how you checked it") }, ["summary"]),
];

function str(description) { return { type: "string", description }; }
function int(description) { return { type: "integer", description }; }
function fn(name, description, properties, required) {
  return { type: "function", function: { name, description, parameters: { type: "object", properties, required } } };
}

// ---------------------------------------------------------------- sandbox: path and tool helpers

function realRoot(cwd) { return fs.realpathSync(cwd); }

// Resolve `p` inside cwd or throw. A path that escapes (.. , absolute, or through a symlink that leaves) is refused.
export function safePath(cwd, p) {
  if (typeof p !== "string" || !p.trim() || p.includes("\0")) throw new Error("path is required");
  const root = realRoot(cwd);
  const abs = path.resolve(root, p);
  const rel = path.relative(root, abs);
  if (rel.startsWith("..") || path.isAbsolute(rel)) throw new Error(`path ${p} is outside the repository`);
  // follow symlinks of the deepest existing ancestor
  let probe = abs;
  while (!fs.existsSync(probe) && probe !== root) probe = path.dirname(probe);
  const realProbe = fs.realpathSync(probe);
  const back = path.relative(root, realProbe);
  if (back.startsWith("..") || path.isAbsolute(back)) throw new Error(`path ${p} resolves outside the repository`);
  return { abs, rel: rel.split(path.sep).join("/") };
}

function refuseGit(rel) {
  if (rel === ".git" || rel.startsWith(".git/")) throw new Error("the .git directory is off limits");
}

function* walk(root, dirAbs, limit) {
  const stack = [dirAbs];
  let n = 0;
  while (stack.length && n < limit) {
    const d = stack.pop();
    let entries;
    try { entries = fs.readdirSync(d, { withFileTypes: true }); } catch { continue; }
    entries.sort((a, b) => a.name.localeCompare(b.name));
    for (const e of entries) {
      if (e.isSymbolicLink()) continue;
      const full = path.join(d, e.name);
      if (e.isDirectory()) { if (!SKIP_DIRS.has(e.name)) stack.push(full); continue; }
      yield path.relative(root, full).split(path.sep).join("/");
      if (++n >= limit) return;
    }
  }
}

function toolReadFile(cwd, a) {
  const { abs, rel } = safePath(cwd, a.path);
  refuseGit(rel);
  const st = fs.statSync(abs);
  if (!st.isFile()) throw new Error(`${rel} is not a file`);
  if (st.size > 5_000_000) throw new Error(`${rel} is too large to read (${st.size} bytes); search for the part you need`);
  const lines = fs.readFileSync(abs, "utf8").split(/\r?\n/);
  const start = Math.max(1, Number(a.start_line) || 1);
  const count = Math.min(400, Math.max(1, Number(a.num_lines) || 200));
  let out = "";
  for (let i = start; i < start + count && i <= lines.length; i++) {
    const line = `${i}\t${lines[i - 1]}\n`;
    if (out.length + line.length > MAX_READ_CHARS) { out += `... (truncated at ${MAX_READ_CHARS} characters; continue from line ${i})\n`; break; }
    out += line;
  }
  return out || `${rel}: no lines in range (file has ${lines.length})`;
}

function toolListFiles(cwd, a) {
  const { abs, rel } = safePath(cwd, a.path || ".");
  refuseGit(rel);
  const root = realRoot(cwd);
  const want = a.pattern ? String(a.pattern) : "";
  const out = [];
  for (const f of walk(root, abs, 5000)) { if (!want || f.includes(want)) out.push(f); if (out.length >= MAX_LIST) break; }
  return out.length ? out.join("\n") + (out.length >= MAX_LIST ? "\n... (list truncated)" : "") : "(no files)";
}

function toolSearch(cwd, a) {
  const { abs, rel } = safePath(cwd, a.path || ".");
  refuseGit(rel);
  const root = realRoot(cwd);
  let test;
  if (a.regex) { try { const re = new RegExp(String(a.query), "i"); test = (s) => re.test(s); } catch (e) { throw new Error(`bad regex: ${e.message}`); } }
  else { const q = String(a.query).toLowerCase(); test = (s) => s.toLowerCase().includes(q); }
  const files = fs.statSync(abs).isFile() ? [path.relative(root, abs).split(path.sep).join("/")] : [...walk(root, abs, 8000)];
  const hits = [];
  for (const f of files) {
    const full = path.join(root, f);
    let st;
    try { st = fs.statSync(full); } catch { continue; }
    if (st.size > MAX_FILE_BYTES) continue;
    let text;
    try { text = fs.readFileSync(full, "utf8"); } catch { continue; }
    if (text.includes("\0")) continue;
    const lines = text.split(/\r?\n/);
    for (let i = 0; i < lines.length; i++) {
      if (test(lines[i])) { hits.push(`${f}:${i + 1}: ${lines[i].trim().slice(0, 200)}`); if (hits.length >= MAX_MATCHES) return hits.join("\n") + "\n... (more matches; narrow the search)"; }
    }
  }
  return hits.length ? hits.join("\n") : "(no matches)";
}

function toolEditFile(cwd, a) {
  const { abs, rel } = safePath(cwd, a.path);
  refuseGit(rel);
  if (typeof a.old_text !== "string" || typeof a.new_text !== "string" || !a.old_text) throw new Error("old_text and new_text are required");
  const text = fs.readFileSync(abs, "utf8");
  // a Windows (CRLF) file: the model copies lines with "\n", so match and write in the file's own line endings
  if (text.includes("\r\n") && !text.includes(a.old_text) && a.old_text.includes("\n")) {
    const crlf = (s) => s.replace(/\r?\n/g, "\r\n");
    a = { ...a, old_text: crlf(a.old_text), new_text: crlf(a.new_text) };
  }
  const count = text.split(a.old_text).length - 1;
  if (count === 0) throw new Error(`old_text not found in ${rel}; read the file again and copy the text exactly`);
  if (count > 1) throw new Error(`old_text matches ${count} places in ${rel}; include more surrounding lines so it matches once`);
  fs.writeFileSync(abs, text.replace(a.old_text, () => a.new_text));
  return `edited ${rel}`;
}

function toolWriteFile(cwd, a) {
  const { abs, rel } = safePath(cwd, a.path);
  refuseGit(rel);
  if (typeof a.content !== "string") throw new Error("content is required");
  fs.mkdirSync(path.dirname(abs), { recursive: true });
  fs.writeFileSync(abs, a.content);
  return `wrote ${rel} (${a.content.length} characters)`;
}

function shellEnv(env) {
  // the shell sees the user's environment minus the provider key and StealthLab's own variables
  const out = {};
  for (const [k, v] of Object.entries(env)) {
    if (v === undefined || k === KEY_ENV || /^STEALTHLAB_/i.test(k)) continue;
    out[k] = v;
  }
  return out;
}

function toolRunShell(cwd, a, env, blockInstalls = true) {
  if (typeof a.command !== "string" || !a.command.trim()) throw new Error("command is required");
  // the worktree's node_modules / venv are links to the project's own (lib/exec/deps.mjs): an install would change the real ones
  const refused = installRefusal(cwd, a.command, { enabled: blockInstalls });
  if (refused) throw new Error(refused);
  return new Promise((resolve) => {
    exec(a.command, { cwd, env: shellEnv(env), timeout: SHELL_TIMEOUT_MS, windowsHide: true, maxBuffer: 4 << 20 }, (err, stdout, stderr) => {
      const text = `${stdout || ""}${stderr || ""}`;
      const tail = text.length > SHELL_TAIL ? "... (earlier output cut)\n" + text.slice(-SHELL_TAIL) : text;
      const code = err ? (err.killed ? "timed out" : `exit ${err.code ?? 1}`) : "exit 0";
      resolve(`[${code}]\n${tail}`.trimEnd());
    });
  });
}

export async function runTool(name, args, { cwd, env, blockInstalls = true }) {
  switch (name) {
    case "read_file": return toolReadFile(cwd, args);
    case "list_files": return toolListFiles(cwd, args);
    case "search": return toolSearch(cwd, args);
    case "edit_file": return toolEditFile(cwd, args);
    case "write_file": return toolWriteFile(cwd, args);
    case "run_shell": return toolRunShell(cwd, args, env, blockInstalls);
    default: throw new Error(`unknown tool ${name}`);
  }
}

// ---------------------------------------------------------------- model calls

function redactKey(text, key) { return key ? String(text).split(key).join("[redacted]") : String(text); }

const sleepReal = (ms) => new Promise((r) => setTimeout(r, ms));
// one model call that never answers (seen with minimax on General Compute: no reply for the whole time limit) is cut
// here and retried like a transport error, instead of holding the run until dispatch's limit
const CALL_TIMEOUT_MS = Math.max(5000, Number(process.env.STEALTHLAB_AGENT_CALL_TIMEOUT_S || 120) * 1000);

async function chat({ baseUrl, key, body, fetchImpl, sleep, log }) {
  const url = `${baseUrl.replace(/\/+$/, "")}/chat/completions`;
  let lastErr;
  for (let attempt = 0; attempt < 6; attempt++) {
    let res = null;
    let waitMs = 1000 * 2 ** Math.min(attempt, 3);
    try {
      res = await fetchImpl(url, { method: "POST", redirect: "error", signal: AbortSignal.timeout(CALL_TIMEOUT_MS), headers: { "content-type": "application/json", authorization: `Bearer ${key}`, connection: "close" }, body: JSON.stringify(body) });
    } catch (err) {
      lastErr = new Error(`request failed (${err && err.name === "TimeoutError" ? "no reply in time" : err && err.name})`); // worth another try
    }
    let text = "";
    if (res) {
      try { text = await res.text(); } catch (err) {     // the body can time out too
        lastErr = new Error(`request failed (${err && err.name === "TimeoutError" ? "no reply in time" : err && err.name})`);
        res = null;
      }
    }
    if (res) {
      if (res.status === 429) {
        // a tokens-per-minute limit frees up within the minute: wait what the provider asks (or 10-40 s), not 1-8 s
        lastErr = new Error(`HTTP 429: ${redactKey(text, key).slice(0, 200)}`);
        const after = Number(res.headers?.get?.("retry-after"));
        waitMs = Number.isFinite(after) && after > 0 ? Math.min(60000, after * 1000) : Math.min(40000, 10000 * 2 ** Math.min(attempt, 2));
      } else if (/did not complete|parse tool call|tool call.*pars|invalid tool call/i.test(text) && (res.status === 400 || res.status >= 500)) {
        // the PROVIDER could not parse the model's tool call (General Compute + gpt-oss, deep into a run): the model
        // sampled a malformed call, and at temperature 0 the same request returns the same one -- so ask again with
        // a little sampling variation instead of ending the run
        lastErr = new Error(`HTTP 400: ${redactKey(text, key).slice(0, 200)}`);
        body = { ...body, temperature: 0.4 + 0.2 * attempt };
      } else if (res.status >= 500) lastErr = new Error(`HTTP ${res.status}: ${redactKey(text, key).slice(0, 200)}`);
      else if (res.status >= 300) throw new Error(`HTTP ${res.status}: ${redactKey(text, key).slice(0, 300)}`);   // 4xx: retrying will not help
      else {
        try { return JSON.parse(text); } catch { throw new Error("the endpoint did not return JSON"); }
      }
    }
    if (attempt < 5) { log(`retrying after ${lastErr.message}`); await sleep(waitMs); }
  }
  throw new Error(`the endpoint kept failing: ${lastErr.message}`);
}

function compact(messages) {
  let total = messages.reduce((n, m) => n + (typeof m.content === "string" ? m.content.length : 0), 0);
  if (total <= CONTEXT_CHAR_LIMIT) return;
  // elide the oldest tool outputs, keeping the system prompt, the task and the last 8 messages
  for (let i = 2; i < messages.length - 8 && total > CONTEXT_CHAR_LIMIT; i++) {
    const m = messages[i];
    if (m.role === "tool" && typeof m.content === "string" && m.content.length > 200) {
      total -= m.content.length - 40;
      m.content = "[older tool output elided to save context]";
    }
  }
}

function costOf(cfg, tin, tout) {
  if (typeof cfg.input_per_mtok !== "number" || typeof cfg.output_per_mtok !== "number") return null;
  return (tin * cfg.input_per_mtok + tout * cfg.output_per_mtok) / 1e6;
}

function parseLearned(text) {
  const out = [];
  for (const m of String(text || "").matchAll(/^\s*Learned:\s*(.+)$/gim)) out.push(m[1].trim().slice(0, 300));
  return out.slice(0, 5);
}

// ---------------------------------------------------------------- the loop

const sha = (buf) => crypto.createHash("sha1").update(buf).digest("hex");

// A file's content hash, "absent" when it does not exist or cannot be read (the stall detector compares these).
function fileHash(cwd, rel) {
  try { return sha(fs.readFileSync(safePath(cwd, rel).abs)); } catch { return "absent"; }
}

// The worktree as a git sees it: the status, the diff against HEAD and the content of untracked files. null when this is
// not a git worktree (the run then has no "no change for N steps" rule; the others still apply).
export function worktreeFingerprint(cwd) {
  try {
    const git = (args) => execFileSync("git", ["-C", cwd, ...args], { encoding: "buffer", maxBuffer: 32 * 1024 * 1024, stdio: ["ignore", "pipe", "ignore"], windowsHide: true });
    const status = git(["status", "--porcelain=v1", "--untracked-files=all", "-z"]);
    const diff = git(["diff", "HEAD", "--no-ext-diff"]);
    const h = crypto.createHash("sha1").update(status).update(diff);
    for (const entry of status.toString("utf8").split("\0")) {
      if (!entry.startsWith("?? ")) continue;
      try { const f = path.join(cwd, entry.slice(3)); if (fs.statSync(f).size <= MAX_FILE_BYTES) h.update(entry).update(fs.readFileSync(f)); } catch { /* gone */ }
    }
    return h.digest("hex");
  } catch {
    return null;
  }
}

// Files the task names (up to PRELOAD_FILES, inside cwd, text only), as read_file would show them -> the text appended to
// the task, or "". Never throws: a file that is missing, outside cwd or not text is simply left out.
const PRELOAD_FILES = 3;
const NAMED = /(?:^|[\s`'"(])((?:[\w.-]+\/)*[\w.-]+\.(?:md|txt|json|ya?ml|toml|py|js|mjs|cjs|ts|tsx|jsx|sql|csv|html|css|go|rs|java|rb|sh))(?=$|[\s`'".,;:)!?])/g;
// path:start-end in the task (StealthLab's "likely place" line, lib/locate.mjs): those lines, not the file's first 200
const RANGED = /(?:^|[\s`'"(,])((?:[\w.-]+\/)*[\w.-]+\.(?:py|js|mjs|cjs|ts|tsx|jsx|go|rs|java|rb|sql|sh)):(\d+)-(\d+)(?=$|[\s`'".,;:)!?])/g;
export function preloadNamedFiles(task, cwd) {
  const parts = [];
  const seen = new Set();
  for (const m of String(task || "").matchAll(RANGED)) {
    if (parts.length >= PRELOAD_FILES) break;
    const [name, a, b] = [m[1], Number(m[2]), Number(m[3])];
    seen.add(name);
    try {
      const text = toolReadFile(cwd, { path: name, start_line: a, num_lines: Math.max(1, b - a + 1) });
      if (!text.includes("\u0000")) parts.push(`--- ${name} (lines ${a}-${b}) ---\n${text}`);
    } catch { /* not there, outside cwd, or not a file */ }
  }
  for (const m of String(task || "").matchAll(NAMED)) {
    const name = m[1];
    if (seen.has(name) || parts.length >= PRELOAD_FILES) continue;
    seen.add(name);
    try {
      const text = toolReadFile(cwd, { path: name });
      if (!text.includes("\u0000")) parts.push(`--- ${name} ---\n${text}`);
    } catch { /* not there, outside cwd, or not a file */ }
  }
  return parts.length ? `\n\nThe files the task names, already read for you (read_file output, numbered lines):\n\n${parts.join("\n")}` : "";
}

// The images the task names that exist inside cwd (at most 2, 4 MB each), as OpenAI-style image_url parts.
const IMAGE_NAMED = /(?:^|[\s`'"(])((?:[\w.-]+\/)*[\w.-]+\.(png|jpe?g|gif|webp))(?=$|[\s`'".,;:)!?])/gi;
export function namedImageParts(task, cwd) {
  const parts = [];
  for (const m of String(task || "").matchAll(IMAGE_NAMED)) {
    if (parts.length >= 2) break;
    try {
      const { abs } = safePath(cwd, m[1]);
      const st = fs.statSync(abs);
      if (!st.isFile() || st.size > 4_000_000) continue;
      const ext = m[2].toLowerCase() === "jpg" ? "jpeg" : m[2].toLowerCase();
      parts.push({ type: "image_url", image_url: { url: `data:image/${ext};base64,${fs.readFileSync(abs).toString("base64")}` } });
    } catch { /* missing or outside cwd */ }
  }
  return parts;
}

export async function runAgent(cfg, { cwd = process.cwd(), env = process.env, fetchImpl = globalThis.fetch, sleep = sleepReal, log = () => {}, fingerprint = worktreeFingerprint } = {}) {
  const key = env[KEY_ENV];
  if (!key) throw new Error(`${KEY_ENV} is not set`);
  if (!cfg || typeof cfg.task !== "string" || !cfg.task.trim()) throw new Error("task is required");
  if (!cfg.base_url || !cfg.model_id) throw new Error("base_url and model_id are required");
  const extras = {};
  for (const [k, v] of Object.entries(cfg.request_extras || {})) { if (!PROTECTED.has(k)) extras[k] = v; }
  const maxSteps = Math.min(100, Math.max(1, Number(cfg.max_steps) || 30));
  const maxCost = typeof cfg.max_cost_usd === "number" ? cfg.max_cost_usd : null;
  const system = SYSTEM.replace("{max_steps}", String(maxSteps));
  // Some endpoints reject a system turn (General Compute's Gemma 4 answers 400 "invalid base64 image data",
  // checked 2026-10-09); for them the instructions open the first user turn instead.
  // the files the task names are read up front: a model otherwise spends its first 2-3 steps listing and reading them
  const taskText = cfg.task.trim() + (cfg.preload === false ? "" : preloadNamedFiles(cfg.task, cwd));
  const messages = cfg.no_system_role
    ? [{ role: "user", content: `${system}\n\n---\n\nTask:\n${taskText}` }]
    : [{ role: "system", content: system }, { role: "user", content: taskText }];
  if (cfg.vision) {
    // a model that can see gets the images the task names (screenshot.png) as image parts of its first turn
    const parts = namedImageParts(cfg.task, cwd);
    if (parts.length) {
      const first = messages[messages.length - 1];
      first.content = [{ type: "text", text: first.content }, ...parts];
    }
  }
  let tin = 0, tout = 0, step = 0, stop = "max_steps", finalMessage = "";
  // stop a stuck run early (lib/agent/stall.mjs); cfg.early_stop === false turns it off
  const detector = cfg.early_stop === false ? null
    : new StallDetector({ noChangeSteps: Number(cfg.early_stop_steps) || 6, errorRun: Number(cfg.early_stop_errors) || 3 });
  detector?.start(fingerprint(cwd));
  let stalled = null;

  const seen = new Map();          // tool call signature -> its last output, for repeated reads
  while (step < maxSteps) {
    compact(messages);
    log(`step ${step + 1}: asking ${cfg.model_id}`);
    let reply;
    try {
      reply = await chat({
        baseUrl: cfg.base_url, key, fetchImpl, sleep, log,
        body: { ...extras, model: cfg.model_id, messages, tools: TOOLS, tool_choice: "auto", temperature: 0, max_tokens: Number(cfg.max_tokens) || 16000 },
      });
    } catch (err) {
      // a provider error mid-run: the tokens already spent are real cost, so they leave with the error
      err.spent = { tokens: { in: tin, out: tout }, costUsd: costOf(cfg, tin, tout), steps: step };
      throw err;
    }
    const usage = reply.usage || {};
    tin += Number(usage.prompt_tokens) || 0;
    tout += Number(usage.completion_tokens) || 0;
    const msg = reply.choices && reply.choices[0] && reply.choices[0].message;
    if (!msg) throw new Error("unexpected reply shape from the endpoint");
    const calls = Array.isArray(msg.tool_calls) ? msg.tool_calls : [];
    messages.push({ role: "assistant", content: msg.content ?? null, ...(calls.length ? { tool_calls: calls } : {}) });
    if (!calls.length) { finalMessage = String(msg.content || "").trim(); stop = "answered"; break; }

    let finished = false;
    for (const call of calls) {
      step++;
      const name = call.function && call.function.name;
      let args = {};
      let result;
      try {
        args = call.function && call.function.arguments ? JSON.parse(call.function.arguments) : {};
        if (name === "finish") { finalMessage = String(args.summary || "").trim(); finished = true; result = "done"; }
        else {
          if (detector && (name === "edit_file" || name === "write_file")) detector.seed(args.path, fileHash(cwd, args.path));
          result = await runTool(name, args, { cwd, env, blockInstalls: cfg.block_installs !== false });
        }
      } catch (err) {
        result = `error: ${redactKey(err.message, key)}`;
      }
      log(`step ${step}: ${name}${args && args.path ? " " + args.path : ""}${/^error:/.test(String(result)) ? " (error)" : ""}`);
      // the same read or search again, with the same output, while the first is still in context: say so instead of
      // resending it (agents re-read a big spec file over and over; each copy rides along on every later step)
      const sig = `${name}\u0000${JSON.stringify(args)}`;
      const body = redactKey(String(result), key);
      const prev = (name === "read_file" || name === "search" || name === "list_files") ? seen.get(sig) : null;
      if (prev && prev.text === body && messages[prev.index]?.content === body) {
        messages.push({ role: "tool", tool_call_id: call.id, content: `(unchanged: the same output as step ${prev.step}, still above)` });
      } else {
        messages.push({ role: "tool", tool_call_id: call.id, content: body });
        if (name === "read_file" || name === "search" || name === "list_files") seen.set(sig, { text: body, index: messages.length - 1, step });
        if (name === "edit_file" || name === "write_file") seen.clear();          // the files changed: earlier reads are stale
      }
      if (detector && name !== "finish") {
        const edit = (name === "edit_file" || name === "write_file") && !/^error:/.test(String(result));
        stalled = detector.observe({ name, args, result: String(result), hash: edit ? fileHash(cwd, args.path) : null,
                                     fingerprint: edit || name === "run_shell" ? fingerprint(cwd) : undefined });
        if (stalled) { log(`stalled (rule ${stalled.rule}): ${stalled.why}`); break; }
      }
      if (finished || step >= maxSteps) break;
    }
    if (stalled) { stop = "stalled"; break; }
    if (finished) { stop = "finished"; break; }
    const spent = costOf(cfg, tin, tout);
    if (maxCost !== null && spent !== null && spent >= maxCost) { stop = "cost_cap"; break; }
  }

  if (stalled) finalMessage = `stopped early (stalled, rule ${stalled.rule}): ${stalled.why}`;
  if (!finalMessage) finalMessage = stop === "max_steps" ? `stopped after ${maxSteps} tool calls without finishing` : stop === "cost_cap" ? "stopped: cost cap reached" : "";
  return { type: "final", message: finalMessage, learned: parseLearned(finalMessage), tokens: { in: tin, out: tout },
           costUsd: costOf(cfg, tin, tout), steps: step, stop, ...(stalled ? { stalled } : {}) };
}

// ---------------------------------------------------------------- CLI

async function readStdin() {
  let data = "";
  for await (const chunk of process.stdin) data += chunk;
  return data;
}

// Do not call process.exit() while stdin and the HTTP sockets are still closing: on Windows that aborts with a libuv
// assertion (exit code 0xC0000409), which the runtime would read as a failed run.
function finish(code) {
  process.exitCode = code;
  // Let the loop drain on its own; requests send `connection: close` so no socket keeps it alive. The unref'd timer
  // is only a backstop for a handle that never closes.
  setTimeout(() => process.exit(code), 5000).unref();
}

async function main() {
  let cfg;
  try { cfg = JSON.parse(await readStdin()); } catch { console.log(JSON.stringify({ type: "final", message: "error: stdin was not JSON", error: true })); return finish(2); }
  try {
    const result = await runAgent(cfg, { log: (s) => console.log(s) });
    console.log(JSON.stringify(result));
    finish(0);
  } catch (err) {
    const key = process.env[KEY_ENV];
    console.log(JSON.stringify({ type: "final", message: `error: ${redactKey(err.message, key)}`, error: true,
                                 tokens: err.spent?.tokens ?? null, costUsd: err.spent?.costUsd ?? null, steps: err.spent?.steps }));
    finish(1);
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) main();
