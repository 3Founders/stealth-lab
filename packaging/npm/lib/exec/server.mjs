// stealthlab-exec: the local MCP stdio server (JSON-RPC 2.0, one message per line; stdout carries
// protocol only, diagnostics go to stderr) -- launched as `stealthlab-mcp exec`, opt-in via
// `install --with-exec`. This is the package's first LOCAL runtime component: it runs other coding
// agents that the user installed, with the user's own logins and keys, inside disposable git worktrees.
// It never proxies, pools or relays anyone's session or key: adapters are spawned as local processes
// only, and the only network egress is the user's own hosted StealthLab endpoint (recommend_models,
// report_model_run -- see hosted.mjs).
//
// Every tool returns in well under 2 s except run_result with wait_s (capped at 55 s so MCP clients
// don't time out). Requests are handled concurrently: a long run_result never blocks run_status.
import readline from "node:readline";
import { readPackage } from "../config.mjs";
import { flushOutbox } from "./evidence.mjs";
import { ExecRuntime } from "./runtime.mjs";

const PROTOCOLS = ["2025-06-18", "2025-03-26", "2024-11-05"];

const str = (description) => ({ type: "string", description });
export const TOOLS = [
  {
    name: "list_executors",
    description: "List the local coding agents StealthLab can drive as executors: installed?, version, whether " +
      "their headless flags were verified, health, and the models configured for each in ~/.stealthlab/exec.json.",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
  },
  {
    name: "achieve",
    description: "Start a run that achieves ONE small goal (e.g. one run.md NODE) in an isolated git worktree " +
      "with a locally installed agent, then verifies it with YOUR checks (each must exit 0) and the scope globs. " +
      "Returns immediately with a run_id; poll run_result(run_id, wait_s). The main checkout is never touched; " +
      "use apply_run to apply verified work.",
    inputSchema: {
      type: "object",
      required: ["repo_path", "task", "scope"],
      properties: {
        repo_path: str("Path to the git repository (any directory inside it)."),
        task: str("<= 2,000 chars: the objective + the NODE/STEP lines + claim lines."),
        checks: { type: "array", items: { type: "string" }, description: "Shell commands run in the worktree; each must exit 0. Defaults to the NODE lines' check= fields." },
        scope: { type: "array", items: { type: "string" }, description: "Globs (repo-relative, anchored) the run may modify." },
        goal_id: str("StealthLab Goal id (enables recommend_models and evidence)."),
        procedure_id: str("StealthLab Procedure id."),
        step_order: { type: "integer", description: "The Procedure step this node runs." },
        executor: str("Executor id (see list_executors). Default: chosen from evidence."),
        model: str("Model for the executor. Default: from exec.json / recommend_models."),
        timeout_s: { type: "integer", minimum: 1, maximum: 3600, description: "Hard timeout for the executor (default 900)." },
        hang_s: { type: "integer", minimum: 1, maximum: 3600, description: "Kill the executor after this many seconds without output (default 180)." },
        check_timeout_s: { type: "integer", minimum: 1, maximum: 3600, description: "Per-check timeout (default 600)." },
        race: { type: "integer", enum: [1, 2], description: "2 = run the top two candidates; the first verified wins." },
        base: { type: "string", enum: ["HEAD", "working-tree"], description: "working-tree copies your uncommitted diff (tracked files) into the worktree." },
      },
      additionalProperties: false,
    },
  },
  {
    name: "run_status",
    description: "State of a run: queued | running | verifying | verified | failed | timed_out | cancelled.",
    inputSchema: { type: "object", required: ["run_id"], properties: { run_id: str("From achieve.") }, additionalProperties: false },
  },
  {
    name: "run_result",
    description: "The compact result of a run (checks with redacted tails, scope violations, summary, diff stat, " +
      "evidence). Waits up to wait_s (<= 55) for a terminal state; if still running, returns its status with done=false.",
    inputSchema: {
      type: "object", required: ["run_id"],
      properties: { run_id: str("From achieve."), wait_s: { type: "integer", minimum: 0, maximum: 55 } },
      additionalProperties: false,
    },
  },
  {
    name: "apply_run",
    description: "Apply a VERIFIED run's changes to the main checkout. Refuses unless the run is verified and every " +
      "target file is unchanged since the run started (blob hashes). Returns the files applied.",
    inputSchema: { type: "object", required: ["run_id"], properties: { run_id: str("From achieve.") }, additionalProperties: false },
  },
  {
    name: "cancel_run",
    description: "Kill a run's process tree and remove its worktree(s).",
    inputSchema: { type: "object", required: ["run_id"], properties: { run_id: str("From achieve.") }, additionalProperties: false },
  },
];

export function createExecHandler({ env = process.env, adapters, fetchImpl, runtime } = {}) {
  const rt = runtime || new ExecRuntime({ env, adapters, fetchImpl });
  let version = "0";
  try { version = readPackage().version; } catch { /* keep */ }

  async function callTool(name, args = {}) {
    switch (name) {
      case "list_executors": return rt.listExecutors();
      case "achieve": return rt.achieve(args);
      case "run_status": return rt.runStatus(args.run_id);
      case "run_result": return rt.runResult(args.run_id, args.wait_s);
      case "apply_run": return rt.applyRun(args.run_id);
      case "cancel_run": return rt.cancelRun(args.run_id);
      default: {
        const e = new Error(`unknown tool: ${name}`);
        e.rpcCode = -32602;
        throw e;
      }
    }
  }

  // handle(msg) -> response object, or null for notifications / client responses.
  async function handle(msg) {
    if (!msg || typeof msg !== "object" || msg.jsonrpc !== "2.0") {
      return { jsonrpc: "2.0", id: msg?.id ?? null, error: { code: -32600, message: "Invalid Request" } };
    }
    const isRequest = msg.id !== undefined && msg.id !== null && typeof msg.method === "string";
    if (!isRequest) return null;
    const ok = (result) => ({ jsonrpc: "2.0", id: msg.id, result });
    switch (msg.method) {
      case "initialize": {
        const asked = msg.params?.protocolVersion;
        return ok({
          protocolVersion: PROTOCOLS.includes(asked) ? asked : PROTOCOLS[0],
          capabilities: { tools: { listChanged: false } },
          serverInfo: { name: "stealthlab-exec", version },
          instructions: "Local executor runtime: achieve() starts a verified run in a git worktree; poll run_result; " +
            "apply_run applies verified work only.",
        });
      }
      case "ping": return ok({});
      case "tools/list": return ok({ tools: TOOLS });
      case "tools/call": {
        const name = msg.params?.name;
        try {
          const result = await callTool(name, msg.params?.arguments || {});
          return ok({ content: [{ type: "text", text: JSON.stringify(result) }], structuredContent: Array.isArray(result) ? { executors: result } : result, isError: false });
        } catch (err) {
          if (err.rpcCode) return { jsonrpc: "2.0", id: msg.id, error: { code: err.rpcCode, message: err.message } };
          return ok({ content: [{ type: "text", text: `${name}: ${rt.red(err.message)}` }], isError: true });
        }
      }
      default:
        return { jsonrpc: "2.0", id: msg.id, error: { code: -32601, message: `Method not found: ${msg.method}` } };
    }
  }
  return { handle, runtime: rt };
}

export async function runExecServer({ stdin = process.stdin, stdout = process.stdout, stderr = process.stderr, env = process.env,
                                      adapters = undefined, fetchImpl = undefined } = {}) {
  const log = (m) => { try { stderr.write(`[stealthlab-exec] ${m}\n`); } catch { /* closed */ } };
  const { handle, runtime } = createExecHandler({ env, adapters, fetchImpl });
  const write = (obj) => stdout.write(JSON.stringify(obj) + "\n");
  runtime.gc().catch((err) => log(`gc: ${err.message}`));
  flushOutbox({ env, fetchImpl }).catch(() => {});
  const pending = new Set();
  const rl = readline.createInterface({ input: stdin, crlfDelay: Infinity });
  rl.on("line", (line) => {
    if (!line.trim()) return;
    let msg;
    try {
      msg = JSON.parse(line);
    } catch {
      write({ jsonrpc: "2.0", id: null, error: { code: -32700, message: "Parse error" } });
      return;
    }
    const batch = Array.isArray(msg) ? msg : [msg];
    for (const m of batch) {
      const p = handle(m).then((res) => { if (res) write(res); }).catch((err) => log(err.message));
      pending.add(p);
      p.finally(() => pending.delete(p));
    }
  });
  await new Promise((resolve) => rl.once("close", resolve));
  // The client went away: nobody can collect or apply these runs any more, so kill their trees and remove
  // their worktrees rather than leaving agents running unattended.
  const active = [...runtime.runs.values()].filter((r) => !runtime.isDone(r));
  for (const r of active) log(`client disconnected: cancelling ${r.id}`);
  await Promise.allSettled(active.map((r) => runtime.cancelRun(r.id)));
  await Promise.allSettled([...pending]);
  return runtime;
}
