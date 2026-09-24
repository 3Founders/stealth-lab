#!/usr/bin/env node
// stealthlab-mcp -- connect your coding agent to the hosted StealthLab MCP.
//
// Nothing StealthLab runs on your machine: no database, no Python, no
// server. `install` writes one entry into each agent's MCP config pointing
// at the hosted endpoint and exits. The only thing that ever runs locally is
// the tiny stdio relay (`stealthlab-mcp` with no args), and only for Claude
// Desktop, whose config file cannot hold a remote URL.
import { parseArgs } from "node:util";
import { clients, launchSpec, SERVER_NAME } from "../lib/clients.mjs";
import { configPath, readConfig, readPackage, resolveSettings, writeConfig } from "../lib/config.mjs";
import { runStdioRelay } from "../lib/proxy.mjs";

const pkg = readPackage();
const UA = `stealthlab-mcp/${pkg.version} node/${process.versions.node}`;
const out = (m = "") => process.stderr.write(m + "\n");

const HELP = `stealthlab-mcp ${pkg.version} -- connect your coding agent to StealthLab

Usage:
  npx -y stealthlab-mcp install [options]    register StealthLab with your agents
  stealthlab-mcp uninstall [--client <id>]   remove it again
  stealthlab-mcp doctor                      check the hosted server is reachable
  stealthlab-mcp login --token <token>       save a token (only report_discovery needs one)
  stealthlab-mcp logout                      forget the saved token
  stealthlab-mcp [--url <url>]               run the stdio relay (what Claude Desktop launches)

Install options:
  --client <id>   only these clients (repeatable, or "all"): ${clients().map((c) => c.id).join(", ")}
                  default: every client detected on this machine
  --url <url>     MCP endpoint (default: $STEALTHLAB_MCP_URL, saved config, or the built-in URL)
  --token <tok>   bearer token to send (optional; reads are anonymous)
  --dry-run       show what would change, change nothing

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
  const ctx = { url, token: settings.token, stdio: { ...launchSpec(), args: [...launchSpec().args, "--url", url] } };

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
  let v;
  try {
    ({ values: v } = parseArgs({
      args: argv,
      options: {
        client: { type: "string", multiple: true },
        url: { type: "string" },
        token: { type: "string" },
        "dry-run": { type: "boolean" },
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
    case "login":
      if (!v.token) die("pass --token <token>", 2);
      writeConfig({ token: v.token });
      return out(`token saved to ${configPath()}`);
    case "logout":
      writeConfig({ token: undefined });
      return out("token removed");
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
