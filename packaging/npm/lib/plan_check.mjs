// `stealthlab-mcp plan validate`: checks the planner's own files, `.stealth/procedures.md` and `.stealth/run.md`,
// the way `survey --validate` checks claims. The agent writes both by hand (the `plan_and_run` prompt); nothing
// else reads them back, so a broken reference or a missing check would otherwise go unnoticed until a node is run.
//
// Grammar (backend/app/mcp_server/prompts.py, RUN_MD_FORMAT and plan_and_run step 4):
//   PROCEDURE|P-1|<procedure_id>|v<version>|<name>|goal=<goal_name>
//   STEP|P-1:<order>|<action|instruction|subgoal>|<do>|locator=<uri or ->|needs=<k=v,...>|check=<check or ->
//   NODE|N-3|<status>|<what to do>|step=P-1:3|claims=R-001..R-004|deps=N-2|check=<command, file or test>
// `check=` is always the last field and may itself contain `|` (a shell pipe), so it takes the rest of the line.
// Local only; reads files, writes nothing.
import fs from "node:fs";
import path from "node:path";

export const NODE_STATUS = new Set(["ready", "blocked", "running", "done", "failed", "skipped"]);
export const STEP_KINDS = new Set(["action", "instruction", "subgoal"]);

function readLines(file) {
  try {
    return fs.readFileSync(file, "utf8").split(/\r?\n/);
  } catch {
    return null;
  }
}

// Split a pipe line into its leading positional fields and its k=v fields; `check=` swallows the rest.
export function splitLine(line, nPositional) {
  const checkAt = line.indexOf("|check=");
  const head = checkAt >= 0 ? line.slice(0, checkAt) : line;
  const parts = head.split("|");
  const pos = parts.slice(0, nPositional);
  const kv = {};
  for (const p of parts.slice(nPositional)) {
    const eq = p.indexOf("=");
    if (eq > 0) kv[p.slice(0, eq)] = p.slice(eq + 1);
    else if (p.trim()) pos.push(p);           // free text that contained a `|`: an extra positional
  }
  if (checkAt >= 0) kv.check = line.slice(checkAt + "|check=".length);
  return { pos, kv };
}

// Every claim id on claims.md and claims/*.md, in page order (ranges are contiguous on one page).
export function claimIds(stealthDir) {
  const pages = [path.join(stealthDir, "claims.md")];
  try {
    for (const f of fs.readdirSync(path.join(stealthDir, "claims"))) {
      if (f.endsWith(".md")) pages.push(path.join(stealthDir, "claims", f));
    }
  } catch { /* no unit pages */ }
  const ids = new Map();                       // id -> {page, index}
  for (const page of pages) {
    const lines = readLines(page);
    if (!lines) continue;
    let i = 0;
    for (const line of lines) {
      if (!line.startsWith("CLAIM|")) continue;
      const id = line.split("|")[1];
      if (id && !ids.has(id)) ids.set(id, { page: path.relative(stealthDir, page), index: i++ });
    }
  }
  return ids;
}

export function parseProcedures(lines) {
  const procs = new Map();                     // P-1 -> {line, procedure_id}
  const steps = new Map();                     // P-1:3 -> {line, kind}
  const errors = [];
  lines.forEach((raw, i) => {
    const line = raw.trim();
    const at = { file: "procedures.md", line: i + 1 };
    if (line.startsWith("PROCEDURE|")) {
      const { pos, kv } = splitLine(line, 5);
      const [, id, procId, version, name] = pos;
      if (!/^P-\d+$/.test(id || "")) errors.push({ ...at, msg: `procedure id ${JSON.stringify(id)} is not P-<n>` });
      else if (procs.has(id)) errors.push({ ...at, msg: `procedure ${id} is declared twice` });
      else procs.set(id, { line: i + 1, procedure_id: procId });
      if (!procId) errors.push({ ...at, msg: "missing procedure_id" });
      if (!/^v\d+/.test(version || "")) errors.push({ ...at, msg: `version ${JSON.stringify(version)} is not v<n>` });
      if (!(name || "").trim()) errors.push({ ...at, msg: "missing procedure name" });
      if (!("goal" in kv)) errors.push({ ...at, msg: "missing goal=" });
    } else if (line.startsWith("STEP|")) {
      const { pos, kv } = splitLine(line, 4);
      const [, id, kind, todo] = pos;
      const m = /^(P-\d+):(\d+)$/.exec(id || "");
      if (!m) errors.push({ ...at, msg: `step id ${JSON.stringify(id)} is not P-<n>:<order>` });
      else if (!procs.has(m[1])) errors.push({ ...at, msg: `step ${id} comes before or without its PROCEDURE ${m[1]}` });
      else if (steps.has(id)) errors.push({ ...at, msg: `step ${id} is declared twice` });
      else steps.set(id, { line: i + 1, kind });
      if (!STEP_KINDS.has(kind)) errors.push({ ...at, msg: `step kind ${JSON.stringify(kind)} is not action|instruction|subgoal` });
      if (!(todo || "").trim()) errors.push({ ...at, msg: "missing what the step does" });
      if (!("check" in kv)) errors.push({ ...at, msg: "missing check= (write - when the Procedure gives none)" });
    } else if (line && !line.startsWith("#") && /^[A-Z]+\|/.test(line)) {
      errors.push({ ...at, msg: `unknown line kind ${line.split("|")[0]}` });
    }
  });
  return { procs, steps, errors };
}

function expandClaims(spec, ids) {
  // "R-001..R-004,R-009" -> problems; "-" or "" means none
  const problems = [];
  if (!spec || spec === "-") return problems;
  for (const part of spec.split(",").map((s) => s.trim()).filter(Boolean)) {
    const [a, b] = part.split("..");
    for (const id of b === undefined ? [a] : [a, b]) {
      if (!ids.has(id)) problems.push(`claim ${id} is not on claims.md or a claims/<unit>.md page`);
    }
    if (b !== undefined && ids.has(a) && ids.has(b)) {
      const ia = ids.get(a), ib = ids.get(b);
      if (ia.page !== ib.page) problems.push(`claim range ${part} spans two pages`);
      else if (ia.index > ib.index) problems.push(`claim range ${part} runs backwards`);
    }
  }
  return problems;
}

export function parseRun(lines) {
  const nodes = new Map();
  const errors = [];
  lines.forEach((raw, i) => {
    const line = raw.trim();
    const at = { file: "run.md", line: i + 1 };
    if (!line.startsWith("NODE|")) {
      if (line && !line.startsWith("#") && /^[A-Z]+\|/.test(line)) errors.push({ ...at, msg: `unknown line kind ${line.split("|")[0]}` });
      return;
    }
    const { pos, kv } = splitLine(line, 4);
    const [, id, status, what] = pos;
    if (!/^N-\d+$/.test(id || "")) { errors.push({ ...at, msg: `node id ${JSON.stringify(id)} is not N-<n>` }); return; }
    if (nodes.has(id)) { errors.push({ ...at, msg: `node ${id} is declared twice` }); return; }
    nodes.set(id, { line: i + 1, status, what, ...kv });
  });
  return { nodes, errors };
}

function findCycle(nodes) {
  const state = new Map();                      // 1 visiting, 2 done
  const stack = [];
  const visit = (id) => {
    if (state.get(id) === 2) return null;
    if (state.get(id) === 1) return [...stack.slice(stack.indexOf(id)), id];
    state.set(id, 1);
    stack.push(id);
    for (const d of depsOf(nodes.get(id))) {
      if (!nodes.has(d)) continue;
      const c = visit(d);
      if (c) return c;
    }
    stack.pop();
    state.set(id, 2);
    return null;
  };
  for (const id of nodes.keys()) {
    const c = visit(id);
    if (c) return c;
  }
  return null;
}

function depsOf(node) {
  const d = (node?.deps || "").trim();
  return !d || d === "-" ? [] : d.split(",").map((s) => s.trim()).filter(Boolean);
}

// The whole check. `root` is the repository (the folder holding .stealth/).
export function validatePlan(root) {
  const dir = path.join(root, ".stealth");
  const errors = [];
  const warnings = [];
  const runLines = readLines(path.join(dir, "run.md"));
  if (!runLines) return { ok: false, errors: [{ file: "run.md", line: 0, msg: "no .stealth/run.md" }], warnings, nodes: 0, steps: 0 };
  const procLines = readLines(path.join(dir, "procedures.md"));
  const proc = procLines ? parseProcedures(procLines) : { procs: new Map(), steps: new Map(), errors: [] };
  errors.push(...proc.errors);
  const run = parseRun(runLines);
  errors.push(...run.errors);
  const ids = claimIds(dir);

  for (const [id, n] of run.nodes) {
    const at = { file: "run.md", line: n.line };
    if (!NODE_STATUS.has(n.status)) errors.push({ ...at, msg: `${id}: status ${JSON.stringify(n.status)} is not one of ${[...NODE_STATUS].join("|")}` });
    if (!(n.what || "").trim()) errors.push({ ...at, msg: `${id}: missing what to do` });
    if (!("step" in n)) errors.push({ ...at, msg: `${id}: missing step= (write - for a node no Procedure step covers)` });
    else if (n.step !== "-") {
      if (!/^P-\d+:\d+$/.test(n.step)) errors.push({ ...at, msg: `${id}: step=${n.step} is not P-<n>:<order>` });
      else if (!procLines) errors.push({ ...at, msg: `${id}: step=${n.step} but there is no .stealth/procedures.md` });
      else if (!proc.steps.has(n.step)) errors.push({ ...at, msg: `${id}: step=${n.step} is not in procedures.md` });
    }
    if (!("claims" in n)) errors.push({ ...at, msg: `${id}: missing claims= (write - for none)` });
    else for (const p of expandClaims(n.claims, ids)) errors.push({ ...at, msg: `${id}: ${p}` });
    if (!("deps" in n)) errors.push({ ...at, msg: `${id}: missing deps= (write - for none)` });
    for (const d of depsOf(n)) {
      if (d === id) errors.push({ ...at, msg: `${id}: depends on itself` });
      else if (!run.nodes.has(d)) errors.push({ ...at, msg: `${id}: deps names ${d}, which is not a node` });
      else if (n.status === "done" && !["done", "skipped"].includes(run.nodes.get(d).status)) {
        warnings.push({ ...at, msg: `${id} is done but its dependency ${d} is ${run.nodes.get(d).status}` });
      }
    }
    const check = (n.check || "").trim();
    if (n.status !== "skipped" && (!check || check === "-")) {
      errors.push({ ...at, msg: `${id}: check= must be concrete (a command, a file that must exist, a test)` });
    }
    if (n.status === "skipped" && (!n.claims || n.claims === "-")) {
      warnings.push({ ...at, msg: `${id} is skipped without claims= naming the fact that shows the repo already has it` });
    }
  }
  const cycle = findCycle(run.nodes);
  if (cycle) errors.push({ file: "run.md", line: run.nodes.get(cycle[0]).line, msg: `dependency cycle: ${cycle.join(" -> ")}` });
  if (!run.nodes.size) warnings.push({ file: "run.md", line: 0, msg: "run.md has no NODE lines" });
  return { ok: errors.length === 0, errors, warnings, nodes: run.nodes.size, steps: proc.steps.size };
}

// The closest folder at or above `start` that holds .stealth/ (start itself when there is none).
export function nearestStealthRoot(start) {
  let dir = path.resolve(start);
  for (;;) {
    if (fs.existsSync(path.join(dir, ".stealth"))) return dir;
    const up = path.dirname(dir);
    if (up === dir) return path.resolve(start);
    dir = up;
  }
}

export const PLAN_HELP = `stealthlab-mcp plan validate [--root <repo>]   (local only; reads, never writes)

  Checks .stealth/procedures.md and .stealth/run.md (the plan_and_run format): line grammar, ids,
  statuses, that every step= is in procedures.md, every claims= id or range is on a claims page,
  every deps= node exists with no cycle, and every check= is concrete. Prints JSON; exit 1 on errors.
`;

export function runPlanCli(argv, { out = (s) => process.stdout.write(s + "\n"), err = (s) => process.stderr.write(s + "\n") } = {}) {
  const args = [...argv];
  const cmd = args[0] && !args[0].startsWith("-") ? args.shift() : "validate";
  if (cmd === "help" || args.includes("--help") || args.includes("-h")) { err(PLAN_HELP); return 0; }
  if (cmd !== "validate" && cmd !== "--validate") { err(`unknown plan command ${cmd}\n\n${PLAN_HELP}`); return 2; }
  const at = args.indexOf("--root");
  const root = at >= 0 && args[at + 1] ? path.resolve(args[at + 1]) : nearestStealthRoot(process.cwd());
  const result = validatePlan(root);
  out(JSON.stringify(result, null, 2));
  return result.ok ? 0 : 1;
}
