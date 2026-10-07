#!/usr/bin/env node
// stealthlab-mcp -- connect your coding agent to the hosted StealthLab MCP.
//
// Nothing StealthLab runs on your machine: no database, no Python, no
// server. `install` writes one entry into each agent's MCP config pointing
// at the hosted endpoint and exits. The only thing that ever runs locally is
// the tiny stdio relay (`stealthlab-mcp` with no args), and only for Claude
// Desktop, whose config file cannot hold a remote URL.
//
// Opt-in exception: `install --with-exec` adds the local executor layer
// (`stealthlab-mcp exec`, an MCP stdio server that runs the user's OWN locally
// installed coding agents in git worktrees and verifies their work) plus two
// Claude Code agent files and SubagentStart/SubagentStop hooks. Without
// --with-exec nothing of it is installed or run.
import { parseArgs } from "node:util";
import { clients, launchSpec, SERVER_NAME } from "../lib/clients.mjs";
import { configPath, readConfig, readPackage, resolveSettings, writeConfig } from "../lib/config.mjs";
import { runStdioRelay } from "../lib/proxy.mjs";
import { runPromptHook } from "../lib/hook.mjs";
import { describeExec, installExec, uninstallExec } from "../lib/claude_exec.mjs";
import { runStopWorker, runSubagentHook } from "../lib/subagent_hook.mjs";
import { runCaptureHook, runCaptureWorker } from "../lib/capture_hook.mjs";
import { applyInstructions } from "../lib/instructions.mjs";

const pkg = readPackage();
const UA = `stealthlab-mcp/${pkg.version} node/${process.versions.node}`;
const out = (m = "") => process.stderr.write(m + "\n");

const HELP = `stealthlab-mcp ${pkg.version} -- connect your coding agent to StealthLab

Usage:
  npx -y stealthlab-mcp install [options]    register StealthLab with your agents
  stealthlab-mcp uninstall [--client <id>]   remove it again
  stealthlab-mcp doctor                      check the hosted server is reachable
  stealthlab-mcp instructions [--dir <repo>] [--client cursor] [--remove] [--dry-run]
                                             tell the agents in a repo when to use StealthLab: a marked block in
                                             AGENTS.md (Cursor, Codex, opencode read it) and, for Cursor,
                                             .cursor/rules/stealthlab.mdc. Edit or remove it any time.
  stealthlab-mcp login --token <token>       save a token (only report_discovery needs one)
  stealthlab-mcp logout                      forget the saved token
  stealthlab-mcp config                      print the saved config (token masked)
  stealthlab-mcp hook-prompt                 Claude Code UserPromptSubmit hook (installed by "install"):
                                             looks each task up with find_ways and adds what Kel knows
  stealthlab-mcp hook capture-tool           Claude Code PostToolUse (Bash) / Stop hooks (installed with
  stealthlab-mcp hook capture-stop           hook-prompt; active only with a saved token): read test verdicts
                                             and report one outcome per prompt with report_model_run
                                             (ids, model, pass/fail only; STEALTHLAB_CAPTURE=off disables)
  stealthlab-mcp exec                       run the local executor MCP server (stdio; installed only by
                                             "install --with-exec"): drives YOUR locally installed agents
                                             in git worktrees and verifies their work with your checks
  stealthlab-mcp hook subagent-start         Claude Code SubagentStart / SubagentStop hooks (installed by
  stealthlab-mcp hook subagent-stop          "install --with-exec"): re-run a plan node's check= after a
                                             subagent and record the outcome with report_model_run
  stealthlab-mcp library <command>           keep .stealth/library.md (this repo's solved problems), its
                                             indexes, SUMMARY.md and routing.md up to date; local only
                                             ("stealthlab-mcp library help" for the commands)
  stealthlab-mcp [serve] [--url <url>]       run the stdio relay (what Claude Desktop launches)
  stealthlab-mcp help | --help | --version

Install options:
  --client <id>   only these clients (repeatable, or "all"): ${clients().map((c) => c.id).join(", ")}
                  default: every client detected on this machine
  --url <url>     MCP endpoint (default: $STEALTHLAB_MCP_URL, saved config, or the built-in URL)
  --token <tok>   bearer token to send (optional; reads are anonymous)
  --dry-run       show what would change, change nothing
  --no-hooks      Claude Code: register the MCP server only, without the knowledge hook
  --with-exec     Claude Code, opt-in: also install the local executor layer (agents
                  stealth-executor + stealth-delegator in ~/.claude/agents, and the
                  SubagentStart/SubagentStop hooks). Only your own local agents and logins.

Config: ${configPath()}
`;

function die(msg, code = 1) {
  out(`error: ${msg}`);
  process.exit(code);
}

function requireUrl(settings) {
  if (!settings.url) {
    die("no MCP URL configured -- pass --url https://<host>/mcp or set STEALTHLAB_MCP_URL", 2);
  }
  return settings.url;
}

function pickClients(requested) {
  const all = clients();
  if (!requested?.length) return { chosen: all.filter((c) => c.detect()), explicit: false };
  if (requested.includes("all")) return { chosen: all, explicit: true };
  const unknown = requested.filter((id) => !all.some((c) => c.id === id));
  if (unknown.length) die(`unknown client(s): ${unknown.join(", ")} (known: ${all.map((c) => c.id).join(", ")})`, 2);
  return { chosen: all.filter((c) => requested.includes(c.id)), explicit: true };
}

async function cmdInstall(v) {
  const settings = resolveSettings(v);
  const url = requireUrl(settings);
  const { chosen } = pickClients(v.client);
  const ctx = { url, token: settings.token, stdio: { ...launchSpec(), args: [...launchSpec().args, "--url", url] },
                hooks: !v["no-hooks"], hookCommand: { ...launchSpec(), args: [...launchSpec().args, "hook-prompt"] } };

  if (!v["dry-run"]) writeConfig({ url, ...(v.token ? { token: v.token } : {}) });

  out(`StealthLab MCP -> ${url}${settings.token ? " (with token)" : " (anonymous reads)"}`);
  if (!chosen.length) {
    out("\nNo supported agent detected. Add it by hand, e.g.:");
    out(`  claude mcp add --scope user --transport http ${SERVER_NAME} ${url}`);
    out(`  or any MCP client: { "url": "${url}" }`);
    return;
  }
  let failed = 0;
  for (const c of chosen) {
    if (v["dry-run"]) {
      out(`  would configure ${c.label.padEnd(15)} ${c.where()}`);
      continue;
    }
    try {
      c.install(ctx);
      out(`  ok    ${c.label.padEnd(15)} ${c.where()}`);
    } catch (err) {
      failed++;
      out(`  FAIL  ${c.label.padEnd(15)} ${err.message}`);
    }
  }
  if (v["with-exec"]) {
    if (!chosen.some((c) => c.id === "claude-code")) {
      out("\n--with-exec: Claude Code not selected, so no agents or hooks were written.");
      out("  Other MCP clients can run the executor server as a stdio command: stealthlab-mcp exec");
    } else if (v["dry-run"]) {
      for (const f of describeExec()) out(`  would write  ${f}`);
    } else {
      try {
        const r = installExec({ launch: launchSpec() });
        out(`  ok    ${"Executor layer".padEnd(15)} ${[...r.agents, r.settings].join(", ")}`);
      } catch (err) {
        failed++;
        out(`  FAIL  ${"Executor layer".padEnd(15)} ${err.message}`);
      }
    }
  }
  out("\nRestart your agent (or reload its MCP servers) to pick up StealthLab.");
  if (failed) process.exitCode = 1;
}

async function cmdUninstall(v) {
  const { chosen } = pickClients(v.client?.length ? v.client : ["all"]);
  for (const c of chosen) {
    try {
      const removed = c.uninstall();
      if (removed) out(`  removed  ${c.label}`);
    } catch (err) {
      out(`  skip     ${c.label}: ${err.message}`);
    }
  }
  if (chosen.some((c) => c.id === "claude-code")) {
    try {
      if (uninstallExec().changed) out("  removed  Executor layer (agents + SubagentStart/SubagentStop hooks)");
    } catch (err) {
      out(`  skip     Executor layer: ${err.message}`);
    }
  }
}

async function cmdDoctor(v) {
  const settings = resolveSettings(v);
  const url = requireUrl(settings);
  out(`endpoint : ${url}`);
  out(`token    : ${settings.token ? "set" : "none (anonymous reads)"}`);
  const headers = { accept: "application/json, text/event-stream", "content-type": "application/json", "user-agent": UA };
  if (settings.token) headers.authorization = `Bearer ${settings.token}`;
  const init = {
    jsonrpc: "2.0", id: 1, method: "initialize",
    params: { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "stealthlab-mcp-doctor", version: pkg.version } },
  };
  let res;
  try {
    res = await fetch(url, { method: "POST", headers, body: JSON.stringify(init), signal: AbortSignal.timeout(15000) });
  } catch (err) {
    die(`unreachable: ${err.cause?.message || err.message}`);
  }
  const text = await res.text();
  if (!res.ok) die(`HTTP ${res.status}: ${text.slice(0, 300)}`);
  const json = text.trim().startsWith("{")
    ? JSON.parse(text)
    : JSON.parse(text.split(/\r?\n/).filter((l) => l.startsWith("data:")).map((l) => l.slice(5).trim()).find(Boolean) || "{}");
  if (json.error) die(`initialize failed: ${json.error.message}`);
  const info = json.result?.serverInfo || {};
  out(`server   : ${info.name || "?"} ${info.version || ""} (protocol ${json.result?.protocolVersion || "?"})`);
  const sid = res.headers.get("mcp-session-id");
  if (sid) {
    await fetch(url, { method: "DELETE", headers: { ...headers, "mcp-session-id": sid } }).catch(() => {});
  }
  out("ok");
}

async function main() {
  const argv = process.argv.slice(2);
  const cmd = argv[0] && !argv[0].startsWith("-") ? argv.shift() : undefined;
  if (cmd === "library") {
    // its own flags and positionals (lib/library_cli.mjs); imported lazily like exec
    const { runLibraryCli } = await import("../lib/library_cli.mjs");
    return runLibraryCli(argv);
  }
  // `hook <event>` takes one positional; everything else is flags only.
  const sub = cmd === "hook" && argv[0] && !argv[0].startsWith("-") ? argv.shift() : undefined;
  let v;
  try {
    ({ values: v } = parseArgs({
      args: argv,
      options: {
        client: { type: "string", multiple: true },
        url: { type: "string" },
        token: { type: "string" },
        "dry-run": { type: "boolean" },
        dir: { type: "string" },
        remove: { type: "boolean" },
        "no-hooks": { type: "boolean" },
        "with-exec": { type: "boolean" },
        help: { type: "boolean", short: "h" },
        version: { type: "boolean", short: "v" },
      },
      strict: true,
    }));
  } catch (err) {
    die(`${err.message}\n\n${HELP}`, 2);
  }
  if (v.client) v.client = v.client.flatMap((s) => s.split(",")).map((s) => s.trim()).filter(Boolean);

  if (v.version) return out(pkg.version);
  if (v.help || cmd === "help") return out(HELP);

  switch (cmd) {
    case "install":
      return cmdInstall(v);
    case "uninstall":
      return cmdUninstall(v);
    case "doctor":
      return cmdDoctor(v);
    case "instructions": {
      const dir = v.dir || process.cwd();
      for (const r of applyInstructions(dir, { clients: v.client || [], remove: !!v.remove, dryRun: !!v["dry-run"] })) {
        out(`${r.result.padEnd(12)} ${r.file}`);
      }
      return;
    }
    case "login":
      if (!v.token) die("pass --token <token>", 2);
      writeConfig({ token: v.token });
      return out(`token saved to ${configPath()}`);
    case "logout":
      writeConfig({ token: undefined });
      return out("token removed");
    case "hook-prompt": {
      const chunks = [];
      for await (const c of process.stdin) chunks.push(c);
      return runPromptHook({
        stdinText: Buffer.concat(chunks).toString("utf8"), settings: resolveSettings(v), userAgent: UA,
        write: (s) => process.stdout.write(s + "\n"), log: out,
      });
    }
    case "exec": {
      // Imported lazily: the executor runtime is opt-in and never loaded by the other commands.
      let mod;
      try {
        mod = await import("../lib/exec/server.mjs");
      } catch (err) {
        if (err.code === "ERR_MODULE_NOT_FOUND") die("the executor runtime (lib/exec/server.mjs) is not in this build");
        throw err;
      }
      return mod.runExecServer({});
    }
    case "hook": {
      // Always exit 0: a non-zero exit shows as an error in Claude Code, and 2 would block the subagent.
      try {
        if (sub === "capture-stop" && process.env.STEALTHLAB_CAPTURE_JOB) {
          await runCaptureWorker(process.env.STEALTHLAB_CAPTURE_JOB);
        } else if (sub === "capture-tool" || sub === "capture-stop") {
          const chunks = [];
          for await (const c of process.stdin) chunks.push(c);
          await runCaptureHook(sub, {
            stdinText: Buffer.concat(chunks).toString("utf8"), hasToken: Boolean(resolveSettings({}).token),
          });
        } else if (sub === "subagent-stop" && process.env.STEALTHLAB_HOOK_WORKER_FILE) {
          await runStopWorker(process.env.STEALTHLAB_HOOK_WORKER_FILE);
        } else {
          const chunks = [];
          for await (const c of process.stdin) chunks.push(c);
          await runSubagentHook(sub, { stdinText: Buffer.concat(chunks).toString("utf8") });
        }
      } catch { /* logged inside; never fail the hook */ }
      process.exit(0);
    }
    case "config":
      return out(JSON.stringify({ ...readConfig(), token: readConfig().token ? "<set>" : undefined }, null, 2));
    case undefined:
    case "serve": {
      if (!cmd && process.stdin.isTTY) return out(HELP); // a human typed it; agents pipe stdin
      const settings = resolveSettings(v);
      return runStdioRelay({ url: requireUrl(settings), token: settings.token, userAgent: UA });
    }
    default:
      die(`unknown command "${cmd}"\n\n${HELP}`, 2);
  }
}

main().catch((err) => die(err.message));
