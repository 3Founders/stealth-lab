// `npm run mcp` (from console/): the MCP and its console, as one thing.
//
//   * Starts the local MCP server (the same command backend/scripts/start_mcp_local.ps1 runs), then the console helper that
//     serves this page (`kell console --ui-dir console/out`), and prints both addresses.
//   * They are independent: stopping one does not stop the other. Each says when the other closes (this program prints
//     it, and the console page shows it live). An MCP already running on the port is left alone. Ctrl+C stops what
//     this program started.
//   * `--no-mcp` runs the console alone. `--mcp-cmd "<shell command>"` replaces how the MCP is started (the command gets the
//     port in MCP_PORT).
//
// This file only starts and watches processes; it changes nothing in backend/ or packaging/.
import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const CONSOLE_DIR = path.resolve(HERE, "..");
export const ROOT = path.resolve(CONSOLE_DIR, "..");

export const HELP = `Usage: npm run mcp -- [options]
  Starts the local MCP server and its console together. Each keeps running if the other stops, and says so.

  --mcp-port <n>       MCP port (default 8765). A server already listening there is reused, not restarted.
  --console-port <n>   console port (default: a free one)
  --no-mcp             run the console alone
  --mcp-cmd "<cmd>"    start the MCP with this shell command instead (it receives the port in MCP_PORT)
  --no-build           do not build the console page if it is missing
  --help`;

export function parseArgs(argv) {
  const o = { mcpPort: 8765, consolePort: 0, noMcp: false, mcpCmd: null, build: true, help: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    const val = () => { const v = argv[++i]; if (v === undefined) throw new Error(`${a} needs a value`); return v; };
    if (a === "--help" || a === "-h") o.help = true;
    else if (a === "--no-mcp") o.noMcp = true;
    else if (a === "--no-build") o.build = false;
    else if (a === "--mcp-port") o.mcpPort = port(val(), a);
    else if (a === "--console-port") o.consolePort = port(val(), a, true);
    else if (a === "--mcp-cmd") o.mcpCmd = val();
    else throw new Error(`unknown option ${a}`);
  }
  return o;
}
function port(v, flag, allowZero = false) {
  const n = Number(v);
  if (!Number.isInteger(n) || n < (allowZero ? 0 : 1) || n > 65535) throw new Error(`${flag} needs a port number`);
  return n;
}

/** How to start the MCP: -> { cmd, args, cwd, env, shell }. Pure, so the choice is testable on any platform. */
export function mcpCommand({ platform = process.platform, root = ROOT, port: p = 8765, mcpCmd = null, exists = fs.existsSync } = {}) {
  const env = { MCP_PORT: String(p) };
  if (mcpCmd) return { cmd: mcpCmd, args: [], cwd: root, env, shell: true };
  const backend = path.join(root, "backend");
  if (platform === "win32") {
    return { cmd: "powershell.exe", args: ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path.join(backend, "scripts", "start_mcp_local.ps1"), "-Port", String(p)], cwd: backend, env, shell: false };
  }
  const venv = path.join(backend, ".venv", "bin", "python");
  return { cmd: exists(venv) ? venv : "python3", args: ["-m", "uvicorn", "app.mcp_server.server:app", "--host", "127.0.0.1", "--port", String(p), "--workers", "1"],
           cwd: backend, env: { ...env, DEPLOYMENT_MODE: "single_user" }, shell: false };
}

export function consoleCommand({ root = ROOT, uiDir = path.join(CONSOLE_DIR, "out"), port: p = 0 } = {}) {
  return { cmd: process.execPath, args: [path.join(root, "packaging", "npm", "bin", "kell.mjs"), "console", "--ui-dir", uiDir, ...(p ? ["--port", String(p)] : [])] };
}

/** Is something accepting connections on this loopback port? */
export function isListening(p, host = "127.0.0.1", timeoutMs = 600) {
  return new Promise((resolve) => {
    const s = net.connect({ port: p, host });
    const done = (v) => { s.destroy(); resolve(v); };
    s.setTimeout(timeoutMs, () => done(false));
    s.once("connect", () => done(true));
    s.once("error", () => done(false));
  });
}

export function consoleUrl(text) {
  const m = /http:\/\/127\.0\.0\.1:\d+\/\?t=[a-f0-9]+/.exec(text);
  return m ? m[0] : null;
}

function killTree(child) {
  if (!child || child.exitCode !== null) return;
  if (process.platform === "win32") spawnSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], { stdio: "ignore" });
  else { try { process.kill(-child.pid, "SIGTERM"); } catch { child.kill("SIGTERM"); } }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function waitFor(fn, ms, step = 400) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) { if (await fn()) return true; await sleep(step); }
  return false;
}

export async function main(argv = process.argv.slice(2), { log = (m) => process.stdout.write(m + "\n") } = {}) {
  let o;
  try { o = parseArgs(argv); } catch (e) { process.stderr.write(`${e.message}\n\n${HELP}\n`); return 2; }
  if (o.help) { log(HELP); return 0; }

  const out = path.join(CONSOLE_DIR, "out");
  if (!fs.existsSync(path.join(out, "index.html"))) {
    if (!o.build) { process.stderr.write("The console page is not built. Run `npm run build` in console/ first.\n"); return 1; }
    log("Building the console page (once)...");
    const b = spawnSync("npm", ["run", "build"], { cwd: CONSOLE_DIR, stdio: "inherit", shell: true });
    if (b.status !== 0) { process.stderr.write("The console build failed.\n"); return 1; }
  }

  let mcp = null, startedMcp = false;
  if (!o.noMcp) {
    if (await isListening(o.mcpPort)) {
      log(`MCP already running on 127.0.0.1:${o.mcpPort}: leaving it alone. The console will show when it closes.`);
    } else {
      const c = mcpCommand({ port: o.mcpPort, mcpCmd: o.mcpCmd });
      log(`Starting the MCP on 127.0.0.1:${o.mcpPort}...`);
      mcp = spawn(c.cmd, c.args, { cwd: c.cwd, env: { ...process.env, ...c.env }, stdio: ["ignore", "inherit", "inherit"], shell: c.shell, detached: process.platform !== "win32", windowsHide: true });
      startedMcp = true;
      let exited = null;
      mcp.once("exit", (code) => { exited = code ?? 1; });
      const up = await waitFor(async () => exited !== null || (await isListening(o.mcpPort)), 90000);
      if (exited !== null || !up) {
        process.stderr.write(exited !== null ? `The MCP stopped right away (exit ${exited}). Starting the console anyway: it will show the MCP as closed.\n` : "The MCP did not start listening within 90 seconds. Starting the console anyway: it will show the MCP as closed.\n");
        if (exited === null) killTree(mcp);
      }
    }
  }

  const cc = consoleCommand({ port: o.consolePort });
  const helper = spawn(cc.cmd, cc.args, { cwd: ROOT, env: process.env, stdio: ["ignore", "pipe", "pipe"], detached: process.platform !== "win32", windowsHide: true });
  let helperOut = "";
  const url = await new Promise((resolve) => {
    // the helper prints its address to stderr; read both so a change of stream does not break this
    for (const stream of [helper.stdout, helper.stderr]) stream.on("data", (d) => { helperOut += d; const u = consoleUrl(helperOut); if (u) resolve(u); });
    helper.once("exit", () => resolve(null));
    setTimeout(() => resolve(null), 20000);
  });
  if (!url) { process.stderr.write("The console did not start.\n"); killTree(helper); if (startedMcp) killTree(mcp); return 1; }

  log("");
  if (!o.noMcp) log(`  MCP      http://127.0.0.1:${o.mcpPort}/mcp`);
  log(`  Console  ${url}`);
  log(o.noMcp ? "\nCtrl+C stops the console." : "\nStopping one does not stop the other; each says when the other closes. Ctrl+C stops what was started here.");

  let mcpUp = o.noMcp ? null : await isListening(o.mcpPort);
  return await new Promise((resolve) => {
    let closing = false, helperUp = true, mcpAlive = Boolean(mcp) && mcp.exitCode === null;
    const done = () => { if (closing) return; closing = true; clearInterval(timer); setTimeout(() => resolve(0), 300); };
    const checkEnd = () => { if (!helperUp && !mcpAlive) { log("\nBoth are closed."); done(); } };
    const timer = setInterval(async () => {
      if (o.noMcp) return;
      const now = await isListening(o.mcpPort);
      if (now !== mcpUp) { mcpUp = now; log(now ? "MCP is running again." : "MCP closed. The console is still running and shows it as closed."); }
    }, 2000);
    helper.once("exit", () => { helperUp = false; log(o.noMcp ? "Console closed." : "Console closed. The MCP is still running."); checkEnd(); });
    if (mcp) mcp.once("exit", () => { mcpAlive = false; checkEnd(); });
    const interrupt = () => { if (closing) return; log("\nStopping what was started here."); killTree(helper); if (startedMcp) killTree(mcp); done(); };
    for (const sig of ["SIGINT", "SIGTERM"]) process.once(sig, interrupt);
  });
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().then((code) => process.exit(code), (e) => { process.stderr.write(`${e?.stack || e}\n`); process.exit(1); });
}
