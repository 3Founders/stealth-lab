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
import { configPath, readConfig, readPackage, resolveSettings, writeConfig } from "../lib/config.mjs";
import fs from "node:fs";

// Everything else is imported by the command that needs it. A hook is a fresh node process on EVERY prompt, and each module
// costs 5-50 ms to load (measured), so a hook now loads only its own path instead of the installer, the OAuth flow and the
// relay as well.
const lazy = {
  clients: () => import("../lib/clients.mjs"),
  proxy: () => import("../lib/proxy.mjs"),
  hook: () => import("../lib/hook.mjs"),
  oauth: () => import("../lib/oauth.mjs"),
  claudeExec: () => import("../lib/claude_exec.mjs"),
  subagent: () => import("../lib/subagent_hook.mjs"),
  capture: () => import("../lib/capture_hook.mjs"),
  instructions: () => import("../lib/instructions.mjs"),
  cursorHooks: () => import("../lib/cursor_hooks.mjs"),
  setup: () => import("../lib/setup.mjs"),
  join: () => import("../lib/join.mjs"),
  store: () => import("../lib/exec/store.mjs"),
};
// kept in step with clients() by a test (test/setup.test.mjs); written out so --help does not load the installer
const CLIENT_IDS = "claude-code, cursor, vscode, windsurf, cline, codex, claude-desktop";
// freshToken() returns the saved token unless a refresh token is stored, so the OAuth module is loaded only for a signed-in session
async function renewToken(settings) {
  if (!readConfig().refresh_token) return settings.token;
  return (await lazy.oauth()).freshToken(settings);
}

const pkg = readPackage();
const UA = `stealthlab-mcp/${pkg.version} node/${process.versions.node}`;
const out = (m = "") => process.stderr.write(m + "\n");

const HELP = `stealthlab-mcp ${pkg.version} -- connect your coding agent to StealthLab

Usage:
  npx -y stealthlab-mcp setup [options]      the whole first-time install in one command: register your agents,
                                             write exec.json, remember the Cursor model, then check what is still
                                             missing and say how to fix it (exit 0 = ready, 3 = not ready yet)
  npx -y stealthlab-mcp join <invite> --url <https://mcp-host/mcp> --api <https://api-host>
                                             your company already connected its provider key: sign in, join the
                                             organization, write the model list, test one call through the gateway
                                             (no provider key on this machine; exit 0 = ready, 3 = not ready)
  npx -y stealthlab-mcp install [options]    register StealthLab with your agents
  stealthlab-mcp uninstall [--client <id>]   remove it again
  stealthlab-mcp doctor                      check the hosted server is reachable
  stealthlab-mcp undo [<id>] [--list] [--force]
                                             put back what a dispatched cheap answer changed in this repository
                                             (the latest, or <id>); refuses if you edited those files since, unless --force
  stealthlab-mcp instructions [--dir <repo>] [--client cursor] [--remove] [--dry-run]
                                             tell the agents in a repo when to use StealthLab: a marked block in
                                             AGENTS.md (Cursor, Codex, opencode read it) and, for Cursor,
                                             .cursor/rules/stealthlab.mdc. Edit or remove it any time.
  stealthlab-mcp login [--no-browser]        sign in through your browser (OAuth); the hooks then keep their token
                                             fresh on their own. Add --token <token> to save a token you already have
                                             instead (it is not refreshed).
  stealthlab-mcp logout                      forget the saved token
  stealthlab-mcp config                      print the saved config (token masked)
  stealthlab-mcp hook-prompt                 Claude Code UserPromptSubmit hook (installed by "install"):
                                             looks each task up with find_ways and adds what Kel knows,
                                             with a model plan for this session's model (STEALTHLAB_HOOK_ROUTING=off
                                             skips it; STEALTHLAB_HOOK_CANDIDATES="model|scaffold,..." sets the list)
  stealthlab-mcp hook capture-tool           Claude Code PostToolUse (Bash) / Stop hooks (installed with
  stealthlab-mcp hook capture-stop           hook-prompt; active only with a saved token): read test verdicts
                                             and report one outcome per prompt: report_result on the
                                             prompt's model plan when there is one, else report_model_run
                                             (ids, model, pass/fail only; STEALTHLAB_CAPTURE=off disables)
  stealthlab-mcp hook route-subagent        Claude Code PreToolUse (Agent|Task) / PostToolUse (report_result) hooks
  stealthlab-mcp hook route-report           (installed with hook-prompt): hand delegated work to the model plan's
                                             model -- refuse a subagent call that asks for another, follow next_model
                                             (STEALTHLAB_MODEL_GUARD=deny|rewrite|off)
  stealthlab-mcp hook cursor-session        Cursor hooks (installed with Cursor; --no-hooks skips them):
  stealthlab-mcp hook cursor-prompt          sessionStart, beforeSubmitPrompt, postToolUse, stop -- look each
  stealthlab-mcp hook cursor-tool            task up in the background, hand what Kel knows to the agent at
  stealthlab-mcp hook cursor-stop            its first tool call, and report one outcome per prompt; with
                                             --with-exec the prompt hook runs dispatch first (cheap models)
  stealthlab-mcp exec apply <run_id>        apply a VERIFIED executor run to its checkout (refuses otherwise)
  stealthlab-mcp exec                       run the local executor MCP server (stdio; installed only by
                                             "install --with-exec"): drives YOUR locally installed agents
                                             in git worktrees and verifies their work with your checks
  stealthlab-mcp hook subagent-start         Claude Code SubagentStart / SubagentStop hooks (installed by
  stealthlab-mcp hook subagent-stop          "install --with-exec"): re-run a plan node's check= after a
                                             subagent and record the outcome with report_model_run
  stealthlab-mcp survey [path] [options]     survey this repository locally (uploads nothing): units, repo identity,
                                             cited facts in .stealth/claims*.md, a worklist for the agent, and
                                             library.md from past fix commits. --validate re-checks facts only.
                                             Options: --validate --touch <path> --max-units <n> --no-history
                                             --history-max <n> --verify-commands --allow-public-name --full --json
  stealthlab-mcp library <command>           keep .stealth/library.md (this repo's solved problems), its
                                             indexes, SUMMARY.md and routing.md up to date; local only
                                             ("stealthlab-mcp library help" for the commands)
  stealthlab-mcp plan validate [--root <repo>] check .stealth/procedures.md and .stealth/run.md (the plan_and_run
                                             format): ids, step/claim/dep references, no cycles, concrete checks
  stealthlab-mcp [serve] [--url <url>]       run the stdio relay (what Claude Desktop launches)
  stealthlab-mcp help | --help | --version

Setup options (setup also takes the install options below):
  --token-file <f>   read the bearer token from a file (no pasting a long token onto a command line)
  --key-env <NAME>   the environment variable that holds YOUR provider key (default: GENERAL_COMPUTE_API_KEY)
  --cursor-model <m> the model you picked in Cursor (it is not "auto"); saved in config.json
  --no-exec          connect only: no cheap-model dispatch, nothing blocks a prompt
  --guided           go one step at a time: say what the next step changes and wait for Enter (q quits)

Join options (join also takes --client, --no-hooks, --no-exec, --dry-run):
  --api <url>        your company's StealthLab API address (the invite page shows the whole command)
  --cursor-model <m> the model you picked in Cursor (it is not "auto"); saved in config.json

Install options:
  --client <id>   only these clients (repeatable, or "all"): ${CLIENT_IDS}
                  default: every client detected on this machine
  --url <url>     MCP endpoint (default: $STEALTHLAB_MCP_URL, saved config, or the built-in URL)
  --token <tok>   bearer token to send (optional; reads are anonymous)
  --dry-run       show what would change, change nothing
  --no-hooks      Claude Code / Cursor: register the MCP server only, without the hooks
  --with-exec     Claude Code, opt-in: the prompt hook runs cheap models (~/.stealthlab/exec.json) before
                  your session's model and applies a verified answer. Hooks only: no MCP server, agents or
                  model guard in the session. With --no-hooks: the old executor agents instead.
  --keep-mcp      with --with-exec: keep the StealthLab MCP server registered as well, so the session can call
                  StealthLab's tools too. Hooks-only is the default.

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

async function pickClients(requested) {
  const { clients } = await lazy.clients();
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
  const { chosen } = await pickClients(v.client);
  const { launchSpec, SERVER_NAME } = await lazy.clients();
  const ctx = { url, token: settings.token, stdio: { ...launchSpec(), args: [...launchSpec().args, "--url", url] },
                hooks: !v["no-hooks"], dispatch: Boolean(v["with-exec"]), keepMcp: Boolean(v["keep-mcp"]), launch: launchSpec(), hookCommand: { ...launchSpec(), args: [...launchSpec().args, "hook-prompt"] } };

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
      out(`  would configure ${c.label.padEnd(15)} ${c.id === "claude-code" && v["with-exec"] && !v["no-hooks"]
        ? (v["keep-mcp"] ? "hooks (prompt hook with dispatch + capture hooks) and the MCP server; no agents or model guard"
                         : "hooks only (prompt hook with dispatch + capture hooks; no MCP server, agents or model guard)") : c.where()}`);
      continue;
    }
    try {
      const where = c.install(ctx);
      out(`  ok    ${c.label.padEnd(15)} ${typeof where === "string" ? where : c.where()}`);
    } catch (err) {
      failed++;
      out(`  FAIL  ${c.label.padEnd(15)} ${err.message}`);
    }
  }
  if (v["with-exec"] && !v["no-hooks"] && chosen.some((c) => c.id === "claude-code")) {
    // Claude Code took the hooks-only dispatch install (clients.mjs installClaudeDispatch): no agents to write
  } else if (v["with-exec"]) {
    if (!chosen.some((c) => c.id === "claude-code")) {
      if (chosen.some((c) => c.id === "cursor") && !v["no-hooks"]) {
        out("\n--with-exec: Cursor's prompt hook runs dispatch (the open models in ~/.stealthlab/exec.json try each task");
        out("  first; set STEALTHLAB_DISPATCH_CHECK to the project's test command). See docs/cursor_dispatch.md.");
      } else {
        out("\n--with-exec: Claude Code not selected, so no agents or hooks were written.");
      }
      out("  Other MCP clients can run the executor server as a stdio command: stealthlab-mcp exec");
    } else if (v["dry-run"]) {
      const { describeExec } = await lazy.claudeExec();
      for (const f of describeExec()) out(`  would write  ${f}`);
    } else {
      try {
        const { installExec } = await lazy.claudeExec();
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

// One command for the first-time install. Order matters: the token and URL are saved by the install, exec.json is written
// only if absent, and the checklist at the end tests what a program cannot do for the user (server, key, model).
async function cmdSetup(v) {
  const { checklist, DEFAULT_KEY_ENV, ensureExecConfig, keyIsSetAsync, probeServer } = await lazy.setup();
  const { readExecConfig } = await lazy.store();
  // --guided: before each step say what it changes and wait for Enter. Without a terminal there is nobody to ask, so it runs straight through.
  const guided = Boolean(v.guided) && process.stdin.isTTY && !v["dry-run"];
  const step = async (title, changes) => {
    if (!guided) return;
    const rl = (await import("node:readline/promises")).createInterface({ input: process.stdin, output: process.stderr });
    const a = (await rl.question(`\nNext: ${title}\n  ${changes}\n  Enter to continue, q to stop: `)).trim().toLowerCase();
    rl.close();
    if (a === "q" || a === "quit") { out("Stopped. Nothing after this step was done; run setup again any time."); process.exit(0); }
  };
  if (v["token-file"]) {
    try { v.token = fs.readFileSync(v["token-file"], "utf8").trim(); } catch (err) { die(`cannot read --token-file: ${err.message}`, 2); }
    if (!v.token) die("--token-file is empty", 2);
  }
  const keyEnv = v["key-env"] || DEFAULT_KEY_ENV;
  const dispatch = !v["no-exec"];
  if (v["dry-run"]) {
    out(`setup (dry run): would install${dispatch ? " with dispatch" : " connect-only"}, ${dispatch ? `write exec.json if absent (key variable ${keyEnv}), ` : ""}save the Cursor model, then check readiness`);
    return cmdInstall({ ...v, "with-exec": dispatch });
  }
  await step("register your agents", `adds the StealthLab server and hooks to ${(await pickClients(v.client)).chosen.map((c) => c.label).join(", ") || "no detected agent"} (the hooks run in every workspace), and saves the URL${v.token ? " and token" : ""} in ~/.stealthlab/config.json. Undo: stealthlab-mcp uninstall.`);
  await cmdInstall({ ...v, "with-exec": dispatch });
  if (process.exitCode) return;
  await step("model list and Cursor model", dispatch ? `writes ~/.stealthlab/exec.json (cheap open models; your key is read from $${keyEnv}, never stored) if there is none${v["cursor-model"] ? `, and saves the Cursor model ${v["cursor-model"]}` : ""}.` : "saves the Cursor model only (connect-only mode).");        // the install failed for a client: nothing to check yet
  if (v["cursor-model"]) writeConfig({ cursor_model: String(v["cursor-model"]).trim() });
  let created = false;
  if (dispatch) {
    try { created = ensureExecConfig({ keyEnv }).created; } catch (err) { die(err.message, 2); }
  }
  await step("check readiness", "makes one request to the server and looks for your key variable; it spends nothing and changes nothing.");
  const settings = resolveSettings(v);
  // the server probe (network) and the key lookup (a child process on Windows) wait on different things: run them together
  const keyNameFirst = (() => { try { return Object.values(readExecConfig().profiles)[0]?.key_env || keyEnv; } catch { return keyEnv; } })();
  const [server, key] = await Promise.all([probeServer(settings.url, settings.token), keyIsSetAsync(keyNameFirst)]);
  let execOk = true, execDetail = "", models = [];
  if (dispatch) {
    try {
      const cfg = readExecConfig();
      models = Object.values(cfg.executors).flatMap((e) => (Array.isArray(e?.models) ? e.models : []).map(String));
      if (!models.length) { execOk = false; execDetail = "no models listed under executors"; }
    } catch (err) { execOk = false; execDetail = err.message; }
  }
  // the key variable the first profile names (the template names one for all three); a hand-edited file may differ
  let keyName = keyEnv;
  try { keyName = Object.values(readExecConfig().profiles)[0]?.key_env || keyEnv; } catch { /* reported above */ }
  const chosenIds = (await pickClients(v.client)).chosen.map((c) => c.id);
  const cfgNow = readConfig();
  const res = checklist({ server, hasToken: Boolean(settings.token), execOk, execDetail, models, keyEnv: keyName,
                          key: keyName === keyNameFirst ? key : await keyIsSetAsync(keyName), dispatch, cursorModel: cfgNow.cursor_model || process.env.STEALTHLAB_CURSOR_MODEL,
                          wantsCursor: chosenIds.includes("cursor"),
                          central: (() => { try { const ps = Object.values(readExecConfig().profiles); return ps.length > 0 && ps.every((p) => p.key_from); } catch { return false; } })() });
  out(created ? `\nWrote ${"~/.stealthlab/exec.json"} (open models on General Compute; your key stays in $${keyName}).` : "");
  out("\nReadiness:");
  for (const i of res.items) out(`  ${i.ok ? "ok     " : "MISSING"} ${i.name.padEnd(16)} ${i.detail}`);
  if (res.ready) { out("\nReady. Restart your agent (Cursor: close every window), then send a small task with a test."); return; }
  out("\nStill to do:");
  for (const i of res.items.filter((x) => !x.ok && x.fix)) out(`  - ${i.name}: ${i.fix}`);
  out("\nRun `stealthlab-mcp setup` again to re-check; it changes nothing that is already in place.");
  process.exitCode = 3;
}

// A developer's whole setup when the company's admin has already connected the provider key (docs/setup.md).
async function cmdJoin(code, v) {
  const J = await lazy.join();
  let invite, api;
  try {
    invite = J.parseInvite(code);
    api = J.normalizeApi(v.api || readConfig().api_url);
  } catch (err) { die(err.message, 2); }
  const settings = resolveSettings(v);
  requireUrl(settings);
  if (v["dry-run"]) {
    out(`join (dry run): would sign in at ${settings.url}, redeem the invite at ${api}/v1/join, write ~/.stealthlab/exec.json (organization gateway, no provider key),`);
    out("  register your agents with dispatch, then make one test call through the gateway");
    return;
  }
  let token = settings.token;
  if (!token) {
    try {
      const { login: oauthLogin } = await lazy.oauth();
      await oauthLogin({ url: settings.url, noBrowser: Boolean(v["no-browser"]), print: out });
    } catch (err) { die(`sign-in failed: ${err.message}`); }
    token = resolveSettings(v).token;
  } else {
    token = await renewToken(settings);
  }
  let answer;
  try { answer = await J.redeem({ api, token, code: invite }); } catch (err) { die(err.message); }
  const exec = J.writeExec(answer);
  writeConfig({ url: settings.url, api_url: api, organization_id: answer.organization_id, organization_name: answer.name, dispatch: !v["no-exec"],
                ...(v["cursor-model"] ? { cursor_model: String(v["cursor-model"]).trim() } : {}) });
  out(`Joined ${answer.name}. Models: ${exec.models.join(", ")}${exec.movedTo ? `\n  (your own exec.json was kept as ${exec.movedTo})` : ""}`);
  process.exitCode = 0;
  await cmdInstall({ ...v, token: undefined, "with-exec": !v["no-exec"] });
  const hooksOk = !process.exitCode;
  process.exitCode = 0;
  const test = await J.selfTest({ gateway: answer.gateway, token, orgId: answer.organization_id, profile: J.cheapest(answer.profiles) });
  const chosenIds = (await pickClients(v.client)).chosen.map((c) => c.id);
  const res = J.joinChecklist({ org: answer.name, models: exec.models, selfTest: test, hooksOk, wantsCursor: chosenIds.includes("cursor"),
                                cursorModel: readConfig().cursor_model || process.env.STEALTHLAB_CURSOR_MODEL });
  out("\nReadiness:");
  for (const i of res.items) out(`  ${i.ok ? "ok     " : "MISSING"} ${i.name.padEnd(16)} ${i.detail}`);
  if (res.ready) { out("\nReady. Restart your agent (Cursor: close every window), then send a small task with a test."); return; }
  out("\nStill to do:");
  for (const i of res.items.filter((x) => !x.ok && x.fix)) out(`  - ${i.name}: ${i.fix}`);
  out("\nRun the same command again to re-check; it changes nothing that is already in place.");
  process.exitCode = 3;
}

async function cmdUninstall(v) {
  const { chosen } = await pickClients(v.client?.length ? v.client : ["all"]);
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
      const { uninstallExec } = await lazy.claudeExec();
      if (uninstallExec().changed) out("  removed  Executor layer (agents + SubagentStart/SubagentStop hooks)");
      if (readConfig().dispatch) { writeConfig({ dispatch: null }); out("  removed  Dispatch (hooks-only install)"); }
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

async function cmdSurvey(dir, argv) {
  let v;
  try {
    ({ values: v } = parseArgs({
      args: argv,
      options: {
        validate: { type: "boolean" }, touch: { type: "string", multiple: true }, "max-units": { type: "string" },
        "no-history": { type: "boolean" }, "history-max": { type: "string" }, since: { type: "string" },
        "verify-commands": { type: "boolean" }, "allow-public-name": { type: "boolean" }, full: { type: "boolean" },
        json: { type: "boolean" }, "dry-run": { type: "boolean" }, fill: { type: "boolean" },
      },
      strict: true,
    }));
  } catch (err) {
    die(`${err.message}\n\n${HELP}`, 2);
  }
  // Imported lazily: only `survey` loads the scanner.
  const { runSurvey } = await import("../lib/survey/survey.mjs");
  const r = runSurvey(dir || process.cwd(), {
    validateOnly: v.validate, touch: v.touch, maxUnits: v["max-units"] ? Number(v["max-units"]) : undefined,
    history: !v["no-history"], historyMax: v["history-max"] ? Number(v["history-max"]) : undefined, since: v.since,
    verifyCommands: v["verify-commands"], allowPublicName: v["allow-public-name"] ? true : undefined, full: v.full, dryRun: v["dry-run"],
  });
  // --fill: a cheap model works the worklist (lib/survey/fill.mjs); the validator then keeps only facts the code shows
  if (v.fill && !v.validate && !v["dry-run"]) {
    const { fillWorklist } = await import("../lib/survey/fill.mjs");
    const f = await fillWorklist({ root: r.root });
    if (f.added) {
      const again = runSurvey(r.root, { validateOnly: true });
      r.fill = { asked: f.asked, written: f.added, rejected: again.validation?.rejected || 0 };
    } else r.fill = { asked: f.asked, written: 0, rejected: 0 };
  }
  if (v.json) return process.stdout.write(JSON.stringify(r, null, 2) + "\n");
  const val = r.validation;
  out(`survey   : ${r.root} (${r.listing.files} files via ${r.listing.source}${r.listing.sparse ? `, ${r.listing.sparse} sparse` : ""})`);
  out(`identity : ${r.identity.repo_id} (${r.identity.strength}, from ${r.identity.source})`);
  out(`units    : ${r.units.length}${r.lazy ? " (lazy: root + touched units now)" : ""}; zones ${r.zones.length}; aux ${r.aux}`);
  out(`facts    : +${r.merge.added} ~${r.merge.updated} -${r.merge.removed}; checked ok ${val.ok || 0}, stamped ${val.stamped || 0}, relocated ${val.relocated || 0}, stale ${val.stale || 0}, unverifiable ${val.unverifiable || 0}, rejected ${val.rejected || 0}`);
  for (const x of r.rejected.slice(0, 10)) out(`  rejected ${x.page} ${x.id}: ${x.reason}`);
  if (r.history) out(`library  : +${r.history.added || 0} past fixes (${r.history.scanned || 0} commits scanned)`);
  out(`worklist : ${r.worklist.length} unit(s) for the agent, ~${r.estTokens} tokens -> .stealth/survey/worklist.md`);
  if (r.fill) out(`fill     : ${r.fill.written} facts written by a cheap model from ${r.fill.asked} worklist unit(s); ${r.fill.rejected} rejected by the validator`);
  out(`time     : ${r.timing.total_ms} ms`);
}

async function main() {
  const argv = process.argv.slice(2);
  const cmd = argv[0] && !argv[0].startsWith("-") ? argv.shift() : undefined;
  if (cmd === "library") {
    // its own flags and positionals (lib/library_cli.mjs); imported lazily like exec
    const { runLibraryCli } = await import("../lib/library_cli.mjs");
    return runLibraryCli(argv);
  }
  if (cmd === "plan") {
    const { runPlanCli } = await import("../lib/plan_check.mjs");
    process.exitCode = runPlanCli(argv);
    return;
  }
  if (cmd === "dag") {
    // after doing your part of a task handed over in parts: check it, then the cheap models do the parts after it
    const { runDagCli } = await import("../lib/dag_run.mjs");
    process.exitCode = await runDagCli(argv);
    return;
  }
  if (cmd === "undo") {
    // put back what a dispatched answer changed (lib/undo.mjs); its own flags and one optional id
    const { runUndoCli } = await import("../lib/undo.mjs");
    process.exitCode = runUndoCli(argv);
    return;
  }
  if (cmd === "exec" && argv[0] === "apply") {
    // Apply a VERIFIED executor run to the checkout. The executor's MCP tools live only inside the delegator
    // subagent (which may not apply), so the main agent applies from the shell; same checks as apply_run.
    const runId = argv[1];
    if (!runId) die("usage: stealthlab-mcp exec apply <run_id>", 2);
    const { ExecRuntime } = await import("../lib/exec/runtime.mjs");
    try {
      const { applyWithUndo } = await import("../lib/undo.mjs");
      return out(JSON.stringify(await applyWithUndo(new ExecRuntime({}), runId)));
    } catch (err) {
      die(err.message, 1);
    }
  }
  // `hook <event>` and `survey [path]` take one positional; everything else is flags only.
  const sub = (cmd === "hook" || cmd === "survey" || cmd === "join") && argv[0] && !argv[0].startsWith("-") ? argv.shift() : undefined;
  if (cmd === "survey") return cmdSurvey(sub, argv);
  let v;
  try {
    ({ values: v } = parseArgs({
      args: argv,
      options: {
        client: { type: "string", multiple: true },
        url: { type: "string" },
        token: { type: "string" },
        "no-browser": { type: "boolean" },
        "dry-run": { type: "boolean" },
        dir: { type: "string" },
        remove: { type: "boolean" },
        "no-hooks": { type: "boolean" },
        "with-exec": { type: "boolean" },
        "keep-mcp": { type: "boolean" },
        "token-file": { type: "string" },
        api: { type: "string" },
        "key-env": { type: "string" },
        "cursor-model": { type: "string" },
        "no-exec": { type: "boolean" },
        guided: { type: "boolean" },
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
    case "join":
      return cmdJoin(sub, v);
    case "setup":
      return cmdSetup(v);
    case "install":
      return cmdInstall(v);
    case "uninstall":
      return cmdUninstall(v);
    case "doctor":
      return cmdDoctor(v);
    case "instructions": {
      const dir = v.dir || process.cwd();
      const { applyInstructions } = await lazy.instructions();
      for (const r of applyInstructions(dir, { clients: v.client || [], remove: !!v.remove, dryRun: !!v["dry-run"] })) {
        out(`${r.result.padEnd(12)} ${r.file}`);
      }
      return;
    }
    case "login": {
      if (v.token) {
        // A token you already hold: saved as is. Any earlier sign-in's refresh data goes, so it can't replace this one.
        writeConfig({ token: v.token, refresh_token: undefined, expires_at: undefined, oauth: undefined });
        return out(`token saved to ${configPath()}`);
      }
      const settings = resolveSettings(v);
      if (!settings.url) die("no server URL: pass --url <https://host/mcp> or run `install` first", 2);
      try {
        const { login: oauthLogin } = await lazy.oauth();
        await oauthLogin({ url: settings.url, noBrowser: Boolean(v["no-browser"]), print: out });
      } catch (err) {
        die(`sign-in failed: ${err.message}`);
      }
      return out(`signed in; token saved to ${configPath()} (the hooks refresh it themselves)`);
    }
    case "logout":
      (await lazy.oauth()).logoutConfig();
      return out("signed out: token and refresh token removed");
    case "hook-prompt": {
      const chunks = [];
      for await (const c of process.stdin) chunks.push(c);
      const settings = resolveSettings(v);
      const { runPromptHook } = await lazy.hook();
      const { logHook } = await lazy.subagent();
      settings.token = await renewToken(settings);          // a signed-in token is renewed shortly before it expires
      return runPromptHook({
        stdinText: Buffer.concat(chunks).toString("utf8"), settings, userAgent: UA,
        write: (s) => process.stdout.write(s + "\n"),
        // also to ~/.stealthlab/hooks.log: Claude Code keeps no hook stderr, and dispatch's decisions need a record
        log: (m) => { out(m); try { logHook(process.env, "prompt", m); } catch { /* best effort */ } },
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
        if (sub?.startsWith("cursor-")) {
          // Cursor reads one JSON object from stdout. The lookup worker is detached and prints nothing.
          if (sub === "cursor-lookup") {
            if (process.env.STEALTHLAB_CURSOR_JOB) {
              const { runCursorLookupWorker } = await lazy.cursorHooks();
              const settings = resolveSettings({});
              settings.token = await renewToken(settings);
              await runCursorLookupWorker(process.env.STEALTHLAB_CURSOR_JOB, { settings, userAgent: UA });
            }
          } else {
            const chunks = [];
            for await (const c of process.stdin) chunks.push(c);
            const settings = resolveSettings({});
            const { runCursorHook } = await lazy.cursorHooks();
            // the prompt hook may call the server itself (dispatch): a signed-in token is renewed first
            if (sub === "cursor-prompt") { try { settings.token = await renewToken(settings); } catch { /* as saved */ } }
            const r = await runCursorHook(sub.slice("cursor-".length), {
              stdinText: Buffer.concat(chunks).toString("utf8"), settings, userAgent: UA, hasToken: Boolean(settings.token),
            });
            process.stdout.write(JSON.stringify(r || {}) + "\n");
          }
        } else if (sub === "capture-stop" && process.env.STEALTHLAB_CAPTURE_JOB) {
          await (await lazy.capture()).runCaptureWorker(process.env.STEALTHLAB_CAPTURE_JOB);
        } else if (sub === "route-subagent" || sub === "route-report") {
          const chunks = [];
          for await (const c of process.stdin) chunks.push(c);
          const { runRouteHook } = await import("../lib/model_guard.mjs");
          const r = runRouteHook(sub, { stdinText: Buffer.concat(chunks).toString("utf8") });
          if (r) process.stdout.write(JSON.stringify(r) + "\n");
        } else if (sub === "capture-tool" || sub === "capture-stop") {
          const chunks = [];
          for await (const c of process.stdin) chunks.push(c);
          const { runCaptureHook } = await lazy.capture();
          await runCaptureHook(sub, {
            stdinText: Buffer.concat(chunks).toString("utf8"), hasToken: Boolean(resolveSettings({}).token),
          });
        } else if (sub === "subagent-stop" && process.env.STEALTHLAB_HOOK_WORKER_FILE) {
          await (await lazy.subagent()).runStopWorker(process.env.STEALTHLAB_HOOK_WORKER_FILE);
        } else {
          const chunks = [];
          for await (const c of process.stdin) chunks.push(c);
          const { runSubagentHook } = await lazy.subagent();
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
      const { runStdioRelay } = await lazy.proxy();
      return runStdioRelay({ url: requireUrl(settings), token: settings.token, userAgent: UA });
    }
    default:
      die(`unknown command "${cmd}"\n\n${HELP}`, 2);
  }
}

main().catch((err) => die(err.message));
