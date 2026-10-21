// A task in parts: the plan comes from the closest problem this repository already solved (library.md + its diff), as a
// DAG of nodes (lib/dag.mjs), and each node -- one file's change, with that file's piece of the past fix as its worked
// example -- goes to the cheap models on its own. Small, well-scoped units are where cheap models win: on a real issue
// the whole task failed cheap on turn 1 while the small follow-up was won by flash for $0.006 (Modal, 2026-10-15).
//
//   plan    the library entry whose diff touched the located files (or that the lookup matched), with >= 2 source steps
//   models  per node: a short past piece -> every cheap rung; a long one -> the strong rung first; an area where the
//           cheap models keep losing (areas.md) -> the session's model (the node is left to it)
//   run     wave by wave; a node starts from the accepted changes of the nodes before it (the executor's extra_patches);
//           each node is checked by "no new failures" (the project's check, judged against the unchanged code); a node
//           that failed in parallel gets one more try after its wave, with the others' changes in
//   final   all parts together, checked by the full check (with the tests written for the task) in a fresh worktree;
//           a failure there gets one repair try; then the change is applied with an undo point
//   partial the accepted parts are applied (undo point), run.md marks the rest ready, and the session's model is told
//           which parts are done and which are its own
// STEALTHLAB_DISPATCH_DAG=off: the task goes to the cheap models whole, as before.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { buildDag } from "./dag.mjs";
import { diffPiece, readStepExtras, splitDiff, stepsFromDiff } from "./way_extract.mjs";
import { loadLibrary } from "./library.mjs";
import { locate } from "./locate.mjs";
import { readArea, recordArea } from "./areas.mjs";
import { createWorktree, git, headCommit, removeWorktree, repoTopLevel, workingTreeDiff } from "./exec/worktree.mjs";

export const dagEnabled = (env = process.env) => (env.STEALTHLAB_DISPATCH_DAG || "on").toLowerCase() !== "off";
const TERMINAL = new Set(["verified", "failed", "timed_out", "cancelled", "error"]);
const TASK_MAX = 12000;
const BIG_STEP = 15;                    // added lines in the past piece above which a node goes to the strong rung first

function readSolution(root, entry) {
  const sol = (entry.procs || [])[0]?.solution;
  if (!sol) return "";
  try { return fs.readFileSync(path.join(root, ".stealth", "library", sol), "utf8"); } catch { return ""; }
}

// -> {entry, diff, steps, dag} | null
// When this session started: the first timestamp in its transcript (ISO), or null.
export function sessionStartedAt(transcriptPath) {
  if (!transcriptPath) return null;
  try {
    const fd = fs.openSync(transcriptPath, "r");
    const buf = Buffer.alloc(65536);
    const n = fs.readSync(fd, buf, 0, buf.length, 0);
    fs.closeSync(fd);
    for (const line of buf.toString("utf8", 0, n).split(/\r?\n/)) {
      try { const t = JSON.parse(line)?.timestamp; if (t && !Number.isNaN(Date.parse(t))) return new Date(t).toISOString(); } catch { /* next line */ }
    }
  } catch { /* no transcript */ }
  return null;
}

// since: the session's start (sessionStartedAt) -- an entry recorded after it is this session's own work
export function planFromLibrary({ root, reply, loc, since = null }) {
  let lib;
  try { lib = loadLibrary(root); } catch { return null; }
  // only a past fix the lookup's judge says MATCHES this task: touching the same file is not the same task (in a real
  // repository most tasks touch the same few files; an unrelated task in parser.py was planned as the parts of an old
  // parser.py fix). No judgement (no server, no judge): the task goes whole.
  const matched = new Set((reply?.library_matches || []).filter((m) => m?.id && m.relation === "matches").map((m) => m.id));
  const located = new Set(loc?.files || []);
  let best = null;
  for (const e of lib.entries || []) {
    if (e.outcome === "fail") continue;
    if (!matched.has(e.id)) continue;
    // this session's own fix of this very task is not a plan for its follow-up (sqlglot-4051: turn 2 was split from the
    // entry turn 1 had recorded 12 s earlier, ct-series-v4)
    if (since && String(e.verified_at || "") >= since) continue;
    const diff = readSolution(root, e);
    if (!diff) continue;
    const steps = stepsFromDiff(diff);
    if (steps.length < 2) continue;
    // a past fix shapes the plan only when it changed the code THIS task is located in (one of the top two located
    // files): a lookup that merely calls it related is not enough -- a "related" regexp_extract fix planned an unrelated
    // T-SQL issue as 5 parts in files it never needed, and the cheap models never saw the real task (Modal, sqlglot-4067)
    const top = new Set((loc?.files || []).slice(0, 2));
    const overlapTop = steps.filter((s) => s.at.some((f) => top.has(f))).length;
    if (overlapTop < 1) continue;
    const overlap = steps.filter((s) => s.at.some((f) => located.has(f))).length;
    const score = overlap;
    if (!best || score > best.score || (score === best.score && String(e.verified_at || "") > String(best.entry.verified_at || ""))) {
      best = { entry: e, diff, steps, score };
    }
  }
  if (!best) return null;
  // the Way's general wording of each step and the parameters of that past fix (lib/way_describe.mjs), where it has them
  const p = best.entry.procs?.[0] || {};
  const words = new Map();
  for (const s of p.steps || []) {
    const at = readStepExtras(s.extra).at[0];
    if (at && s.do && !/^In \S+: change /.test(s.do)) words.set(at, s.do);
  }
  for (const s of best.steps) if (words.has(s.at[0])) s.desc = words.get(s.at[0]);
  const slots = ((p.extra || []).find((f) => f.startsWith("slots=")) || "").slice(6).split(",").filter(Boolean)
    .map((x) => x.split(":").map((v) => decodeURIComponent(v.replace(/%(?![0-9A-Fa-f]{2})/g, "%25"))));
  return { entry: best.entry, diff: best.diff, steps: best.steps, slots, way: p.name, dag: buildDag(best.steps) };
}

// No similar fix here, but the request asks for several things (the brief's parts, lib/brief.mjs): one node per part,
// each located on its own (its likely file); parts in different files are independent, the rest in order (lib/dag.mjs).
// There is no worked example: the part's own words and the located code. -> plan | null
export function planFromParts({ parts = [], root, env = process.env }) {
  const ps = (parts || []).map((p) => String(p || "").trim()).filter(Boolean).slice(0, 6);
  if (ps.length < 2) return null;
  const steps = ps.map((p, i) => {
    const l = locate({ prompt: p, root, env });
    return { order: i + 1, kind: "action", at: l.files.slice(0, 1), gives: [], needs: [], added: 0, desc: p };
  });
  return { entry: { id: "request", title: "the request's own parts" }, diff: "", steps, slots: [], way: null, dag: buildDag(steps) };
}

// the node's task: this part of the task, with the past fix's piece for the same file as the worked example
export function nodeTask({ task, node, total, entry, diff, where = "", done = [], slots = [] }) {
  const s = node.step;
  const head = `Part ${node.id.slice(2)} of ${total} of this task (the other parts are done separately; change only what this part needs):\n`;
  const ask = String(task || "").trim();
  const where_ = s.at.length ? ` -- in ${s.at.join(", ")}` : "";
  const said = s.desc ? `${s.desc}${diff ? where_ : s.at.length ? ` (likely in ${s.at.join(", ")})` : ""}` : `the change in ${s.at.join(", ")}`;
  const part = `\nThis part: ${said}${s.gives.length ? ` (in the past fix: ${s.gives.slice(0, 5).join(", ")})` : ""}.` +
    (slots.length ? ` In that fix ${slots.map(([k, v]) => `{${k}} was ${v}`).join(", ")}; find their values for this task.` : "") +
    (done.length ? ` Already done and in the code: ${done.join("; ")}.` : "");
  const tail = where ? `\n\n${where}` : "";
  const exHead = diff ? `\nWorked example -- this part in a similar fix already made in this repository ("${String(entry?.title || "").slice(0, 120)}"); adapt it, do not copy it:\n` : "";
  const room = TASK_MAX - head.length - part.length - tail.length - exHead.length - 40;
  const askCut = ask.length <= Math.max(300, Math.floor(room / 2)) ? ask : `${ask.slice(0, Math.max(300, Math.floor(room / 2)) - 10)} [...]`;
  const exRoom = TASK_MAX - head.length - askCut.length - part.length - tail.length - exHead.length - 10;
  const example = diff && exRoom > 200 ? exHead + diffPiece(diff, s.at, exRoom) : "";
  return `${head}${askCut}${part}${example}${tail}`.slice(0, TASK_MAX);
}

// which rungs try a node: "session" leaves it to the session's model
export function nodeModels({ node, rungs, strongSet, root, areaSkipAfter = 3 }) {
  for (const f of node.step.at) {
    const a = readArea(root, f);
    if (a && a.losses >= areaSkipAfter && a.wins === 0) return { rungs: [], who: "session" };
  }
  const strong = rungs.filter((r) => strongSet.has(r.model));
  if ((node.step.added || 0) > BIG_STEP && strong.length) return { rungs: strong, who: strong[0].model };
  return { rungs, who: rungs.map((r) => r.model).join("/") };
}

// one node on its rungs: up to `width` at once, the first verified answer wins -> {winner, tried}
async function runNode({ rt, repo, task, check, scope, rungs, extraPatches, limitS, width, instanceKey, log }) {
  const tried = [];
  const queue = [...rungs];
  const live = new Map();                 // unit -> {run_id, promise}
  let winner = null;
  const start = (r) => {
    const p = (async () => {
      try {
        const st = await rt.achieve({ repo_path: repo, task, checks: [check], scope, executor: r.executor, model: r.model, base: "working-tree",
          extra_patches: extraPatches, ...(instanceKey ? { instance_key: instanceKey } : {}) });
        live.get(r.unit).run_id = st.run_id;
        let res;
        do { res = await rt.runResult(st.run_id, 55); } while (!TERMINAL.has(res?.state));
        return { r, res };
      } catch (err) {
        return { r, err };
      }
    })();
    live.set(r.unit, { run_id: null, promise: p });
  };
  let timer;
  const deadline = new Promise((res) => { timer = setTimeout(() => res({ timeout: true }), limitS * 1000); });   // awaited, cleared below
  while (!winner && (queue.length || live.size)) {
    while (queue.length && live.size < width) start(queue.shift());
    const done = await Promise.race([...[...live.values()].map((v) => v.promise), deadline]);
    if (done.timeout) break;
    live.delete(done.r.unit);
    const files = done.res?.diff?.files || [];
    const ok = done.res?.state === "verified" && files.length > 0 && done.res?.diff?.patch_path;
    tried.push({ unit: done.r.unit, state: done.err ? "error" : done.res?.state, run_id: done.res?.run_id ?? null, cost_usd: done.res?.cost_usd ?? null });
    // each part's try has its own line, as a whole-task try does (a part's flash try had none, ct-smoke-final6)
    if (!ok) log(`dag ${done.r.unit}: no pass (${done.err ? `error: ${String(done.err.message).slice(0, 120)}` : done.res?.state})`);
    if (ok) {
      let patch = "";
      try { patch = fs.readFileSync(done.res.diff.patch_path, "utf8"); } catch { /* unreadable: not a win */ }
      if (patch.trim()) winner = { unit: done.r.unit, model: done.r.model, run_id: done.res.run_id, patch, files, cost_usd: done.res.cost_usd ?? null };
    }
  }
  clearTimeout(timer);
  for (const [unit, v] of live) {
    if (v.run_id) try { await rt.cancelRun(v.run_id); } catch { /* best effort */ }
    tried.push({ unit, state: winner ? "cancelled" : "timed out", run_id: v.run_id });
  }
  if (!winner) log(`dag: ${task.split("\n")[0].slice(0, 60)}: no cheap pass`);
  return { winner, tried };
}

// a part's tries into the route's counts: a pass is a win, a failed answer a loss; a cancelled or timed-out run is
// neither (it says nothing about the model), as for a whole-task try
export function countTries(tried, obs) {
  for (const t of tried) {
    const [model, executor] = String(t.unit || "").split("|");
    if (!model) continue;
    if (t.state === "verified") obs(model, executor || "stealth", true);
    else if (t.state === "failed") obs(model, executor || "stealth", false);
  }
}

// the full check on base + every accepted part, in a fresh worktree -> {ok, tail}
async function finalCheck({ repo, patches, check, env, runShell }) {
  const dir = path.join(os.tmpdir(), `stealth-final-${Date.now()}-${Math.random().toString(16).slice(2, 6)}`);
  try {
    await createWorktree({ repo, commit: await headCommit(repo, env), dir, patch: await workingTreeDiff(repo, env), extraPatches: patches, env });
    const { code, out } = await runShell(check, { cwd: dir, timeoutMs: 600000 });
    return { ok: code === 0, tail: String(out || "").slice(-1500) };
  } catch (err) {
    return { ok: false, tail: String(err.message).slice(0, 500) };
  } finally {
    try { await removeWorktree({ repo, dir, env }); } catch { /* best effort */ }
  }
}

// apply accepted parts to the checkout, in order, with an undo point -> files
async function applyParts({ repo, patches, task, models, env, undo }) {
  const files = [...new Set(patches.flatMap((p) => splitDiff(p).map((f) => f.file)))];
  const point = undo?.enabled ? undo.create({ repo, files, model: models, task }) : null;
  try {
    for (const p of patches) await git(["-C", repo, "apply", "--binary", "--whitespace=nowarn", "-"], { env, input: p });
  } catch (err) {
    if (point) undo.discard(point, { repo });
    throw err;
  }
  if (point) undo.finish(point, { repo });
  return { files, undone: !!point };
}

// -> the dispatch result ({handled: true, ...} or {handled: false, partial, ...}) or null (no plan: the caller goes on)
export async function runDag(ctx) {
  const { rt, root, payload, rawPayload, reply, loc, where, rungs, strongSet, policy, interCheck, fullCheck, specDone, env, log,
          runShell, undo, instanceKey, obs = () => {}, goalId = null } = ctx;
  if (!dagEnabled(env)) return null;
  const plan = planFromLibrary({ root, reply, loc, since: sessionStartedAt(payload?.transcript_path) }) || planFromParts({ parts: ctx.parts, root, env });
  if (!plan || plan.dag.nodes.length < 2) return null;
  const repo = await repoTopLevel(payload.cwd || root, env);
  const nodes = plan.dag.nodes;
  log(`dag: ${nodes.length} parts from ${plan.entry.id} ("${String(plan.entry.title).slice(0, 60)}"): ` +
      plan.dag.waves.map((w) => w.map((id) => `${id} ${nodes.find((n) => n.id === id).step.at[0]}`).join(" + ")).join(" -> "));
  const accepted = new Map();            // node id -> winner
  const tried = [];
  const yours = new Set();               // parts for the session's model
  const waiting = new Set();             // cheap parts after one of yours: `stealthlab-mcp dag continue` does them
  const left = { get size() { return yours.size + waiting.size; } };
  const order = plan.dag.waves.flat();
  const patchesBefore = (n) => order.filter((id) => accepted.has(id) && id !== n.id).map((id) => accepted.get(id).patch);
  const doneText = () => order.filter((id) => accepted.has(id)).map((id) => `${id} in ${nodes.find((n) => n.id === id).step.at.join(", ")}`);
  const runOne = async (n) => {
    const m = nodeModels({ node: n, rungs, strongSet, root, areaSkipAfter: policy.areaSkipAfter });
    if (!m.rungs.length) { yours.add(n.id); n.who = "session"; return; }
    if (n.deps.some((d) => yours.has(d) || waiting.has(d))) { waiting.add(n.id); n.who = m.who; n.rungUnits = m.rungs.map((r) => r.unit); return; }
    n.who = m.who;
    const task = nodeTask({ task: rawPayload.prompt, node: n, total: nodes.length, entry: plan.entry, diff: plan.diff, where, done: doneText(), slots: plan.slots });
    const r = await runNode({ rt, repo, task, check: interCheck, scope: policy.scope, rungs: m.rungs, extraPatches: patchesBefore(n),
                              limitS: policy.timeoutS, width: policy.maxParallel, instanceKey, log });
    tried.push(...r.tried.map((t) => ({ ...t, node: n.id })));
    countTries(r.tried, obs);
    for (const f of n.step.at) recordArea(root, f, !!r.winner);
    if (r.winner) { accepted.set(n.id, r.winner); n.who = r.winner.model; }
    return r.winner;
  };
  for (const wave of plan.dag.waves) {
    const ns = wave.map((id) => nodes.find((n) => n.id === id));
    const results = await Promise.all(ns.map((n) => runOne(n)));
    // a node that failed beside others gets one more try with their accepted changes in (they may have been what it needed)
    if (ns.length > 1) for (const [i, n] of ns.entries()) if (!results[i] && !yours.has(n.id) && !waiting.has(n.id) && accepted.size) { await runOne(n); }
    // a part the cheap models tried and could not do is the session's
    for (const n of ns) if (!accepted.has(n.id) && !waiting.has(n.id)) { yours.add(n.id); n.who = "session"; }
  }
  const patches = order.filter((id) => accepted.has(id)).map((id) => accepted.get(id).patch);
  const models = [...new Set([...accepted.values()].map((w) => w.model))].join(", ");
  if (!left.size) {
    await specDone;                                  // the full check runs the tests written for the task
    let fin = await finalCheck({ repo, patches, check: fullCheck, env, runShell });
    if (!fin.ok) {
      log(`dag: all parts passed but the full check fails; one repair try`);
      const repair = await runNode({ rt, repo, check: fullCheck, scope: policy.scope, rungs, extraPatches: patches, limitS: policy.timeoutS,
        width: policy.maxParallel, instanceKey, log,
        task: `${String(rawPayload.prompt).slice(0, 1200)}\n\nEvery part of this task is already in the code, but the check fails:\n${fin.tail.slice(-600)}\nFix what makes it fail.` });
      tried.push(...repair.tried.map((t) => ({ ...t, node: "repair" })));
      // the repair passed the full check in its own worktree; the parts plus it are checked together once more
      if (repair.winner) {
        const withRepair = [...patches, repair.winner.patch];
        fin = await finalCheck({ repo, patches: withRepair, check: fullCheck, env, runShell });
        if (fin.ok) patches.push(repair.winner.patch);
      }
    }
    if (fin.ok) {
      const applied = await applyParts({ repo, patches, task: rawPayload.prompt, models, env, undo });
      return { handled: true, dag: { entry: plan.entry.id, nodes: nodes.map((n) => ({ id: n.id, at: n.step.at, deps: n.deps, who: n.who, status: "done" })) },
               files: applied.files, tried, undone: applied.undone, models, cost_usd: [...accepted.values()].reduce((s, w) => s + (w.cost_usd || 0), 0) };
    }
    for (const n of nodes) { yours.add(n.id); n.who = "session"; }   // the parts do not add up: nothing applied, all the session's
    accepted.clear();
  }
  // partial: the accepted parts are applied (they passed "no new failures"); the rest is the session's
  let applied = { files: [], undone: false };
  if (accepted.size && policy.dagPartialApply) {
    try { applied = await applyParts({ repo, patches: order.filter((id) => accepted.has(id)).map((id) => accepted.get(id).patch), task: rawPayload.prompt, models, env, undo }); }
    catch (err) { log(`dag: the accepted parts could not be applied (${err.message})`); accepted.clear(); }
  }
  const status = (n) => (accepted.has(n.id) ? "done" : waiting.has(n.id) ? "waiting" : "ready");
  // the cheap parts after yours: saved so `stealthlab-mcp dag continue` can do them once your part is in
  const resumable = waiting.size > 0 && saveDagState(root, {
    task: rawPayload.prompt, entry: plan.entry.id, slots: plan.slots, where, interCheck, fullCheck, instanceKey, goalId,
    policy: { timeoutS: policy.timeoutS, maxParallel: policy.maxParallel, scope: policy.scope },
    rungs: rungs.map((r) => ({ unit: r.unit, model: r.model, executor: r.executor })),
    nodes: nodes.map((n) => ({ id: n.id, deps: n.deps, step: n.step, who: n.who || "session", status: status(n), rungUnits: n.rungUnits || null })),
  });
  return { handled: false, tried, partial: {
    entry: plan.entry.id, applied: applied.files, undone: applied.undone, resumable,
    nodes: nodes.map((n) => ({ id: n.id, at: n.step.at, deps: n.deps, who: n.who || "session", status: status(n), gives: n.step.gives })) } };
}

// ------------------------------------------------------------------ after the session's part: the rest, cheap

const STATE = "dag_state.json";
export function saveDagState(root, state) {
  try { fs.writeFileSync(path.join(root, ".stealth", STATE), JSON.stringify({ ...state, saved_at: new Date().toISOString() }, null, 1)); return true; }
  catch { return false; }
}
export function loadDagState(root) {
  try { return JSON.parse(fs.readFileSync(path.join(root, ".stealth", STATE), "utf8")); } catch { return null; }
}

// `stealthlab-mcp dag continue`: the session has done its part(s) of the plan in the working tree. Checks them ("no new
// failures"), then runs the waiting parts on cheap models wave by wave (each applied as it passes, with an undo
// point), then the full check over the whole task. -> {ok, lines: [what happened], state}
export async function continueDag({ root, env = process.env, rt, log = () => {}, runShell, undo, fresh = false }) {
  const st = loadDagState(root);
  const out = [];
  if (!st) return { ok: false, lines: ["No plan to continue here (.stealth/dag_state.json): nothing was handed over in parts."] };
  if (st.awaiting_plan) return { ok: false, lines: [`This task waits for your plan: \`${"stealthlab-mcp"} dag plan\` first (see its usage).`] };
  const repo = await repoTopLevel(root, env);
  // fresh: a plan just made -- the session's parts are not done yet, so only the cheap parts that need none of them run
  const yours = fresh ? [] : st.nodes.filter((n) => n.status === "ready");
  if (yours.length) {
    const c = await runShell(st.interCheck, { cwd: repo, timeoutMs: 600000 });
    if (c.code !== 0) {
      return { ok: false, lines: [`Your part (${yours.map((n) => n.id).join(", ")}) does not pass the check yet: nothing else was run.`,
                                 String(c.out || "").slice(-1200)] };
    }
    for (const n of yours) { n.status = "done"; n.who = "session"; }
    out.push(`Your part (${yours.map((n) => n.id).join(", ")}) passes the check (no new failures).`);
  }
  const byUnit = new Map(st.rungs.map((r) => [r.unit, r]));
  const total = st.nodes.length;
  const entry = { title: st.entry };
  let diff = "";
  try {
    const e = loadLibrary(root).entries.find((x) => x.id === st.entry);
    if (e) { entry.title = e.title; diff = readSolution(root, e); }
  } catch { /* no example then */ }
  for (let progress = true; progress;) {
    progress = false;
    const ready = st.nodes.filter((n) => n.status === "waiting" && n.deps.every((d) => st.nodes.find((x) => x.id === d)?.status === "done"));
    for (const n of ready) {
      const rungs = (n.rungUnits || st.rungs.map((r) => r.unit)).map((u) => byUnit.get(u)).filter(Boolean);
      const done = st.nodes.filter((x) => x.status === "done").map((x) => `${x.id} in ${x.step.at.join(", ")}`);
      const task = nodeTask({ task: st.task, node: n, total, entry, diff, where: st.where, done, slots: st.slots || [] });
      const r = await runNode({ rt, repo, task, check: st.interCheck, scope: st.policy.scope, rungs, extraPatches: [],
                                limitS: st.policy.timeoutS, width: st.policy.maxParallel, instanceKey: st.instanceKey, log });
      for (const f of n.step.at) recordArea(root, f, !!r.winner);
      if (st.goalId) {
        const { ensureRoute, recordObs } = await import("./library.mjs");
        countTries(r.tried, (m, ex, ok) => { try { recordObs(root, ensureRoute(root, st.goalId), m, ex, ok); } catch { /* best effort */ } });
      }
      if (r.winner) {
        await applyParts({ repo, patches: [r.winner.patch], task: st.task, models: r.winner.model, env, undo });
        n.status = "done"; n.who = r.winner.model;
        out.push(`${n.id} (${n.step.at.join(", ")}): done by ${r.winner.model} and applied.`);
        progress = true;
      } else {
        n.status = "ready"; n.who = "session";
        out.push(`${n.id} (${n.step.at.join(", ")}): the cheap models could not do it; it is yours.`);
      }
    }
  }
  const open = st.nodes.filter((n) => n.status !== "done");
  if (!open.length) {
    const fin = await runShell(st.fullCheck, { cwd: repo, timeoutMs: 600000 });
    out.push(fin.code === 0 ? "All parts are in; the full check passes." : `All parts are in, but the full check fails:\n${String(fin.out || "").slice(-1200)}`);
    try { fs.unlinkSync(path.join(root, ".stealth", STATE)); } catch { /* */ }
    return { ok: fin.code === 0, lines: out, state: st };
  }
  saveDagState(root, st);
  out.push(`Still yours: ${open.map((n) => `${n.id} (${n.step.at.join(", ")})`).join(", ")}. Run this again after them.`);
  return { ok: false, lines: out, state: st };
}

// ------------------------------------------------------------------ a large task: the session's model plans, cheap models build

// A large change (the brief's size=large) that the strong cheap model could not do whole goes back to the session's
// model -- not to do it all, but to plan it: a few parts, each with its files, the names it gives and needs, and who
// does it ("me": a part that needs a design decision; "cheap": one that follows from the plan). Planning is a small
// share of a large change's tokens; the parts that follow from it go to cheap models (`dag plan`, then `dag continue`
// after the session's own parts). dispatch saves the task's checks and models first (awaiting_plan).
// STEALTHLAB_DISPATCH_SESSION_PLAN=off: the large task is handed back whole, as before.
export const sessionPlanEnabled = (env = process.env) => (env.STEALTHLAB_DISPATCH_SESSION_PLAN || "on").toLowerCase() !== "off";

export const PLAN_USAGE = 'stealthlab-mcp dag plan < plan.json   with {"parts": [{"desc": "what this part does", ' +
  '"files": ["path/a.py"], "gives": ["names it adds"], "needs": ["names it uses from other parts"], "who": "me" | "cheap"}]}';

// -> {ok, steps, error}: the session's plan, checked (2-8 parts, each with a description; files must exist or be new
// under an existing folder)
export function readPlan(text, root) {
  let o;
  try { o = JSON.parse(String(text || "")); } catch { return { ok: false, error: "the plan is not JSON" }; }
  const parts = Array.isArray(o?.parts) ? o.parts : [];
  if (parts.length < 2 || parts.length > 8) return { ok: false, error: `a plan has 2-8 parts (got ${parts.length}); a smaller task is done whole` };
  const list = (v) => (Array.isArray(v) ? v : []).map((x) => String(x || "").trim()).filter(Boolean).slice(0, 12);
  const steps = [];
  for (const [i, p] of parts.entries()) {
    const desc = String(p?.desc || "").trim();
    if (!desc) return { ok: false, error: `part ${i + 1} has no desc` };
    const files = list(p.files).map((f) => f.replace(/\\/g, "/").replace(/^\.\//, ""));
    const bad = files.find((f) => f.startsWith("/") || f.includes("..") || !fs.existsSync(path.join(root, path.dirname(f))));
    if (bad) return { ok: false, error: `part ${i + 1}: ${bad} is not a path in this repository` };
    steps.push({ order: i + 1, kind: "action", at: files, gives: list(p.gives), needs: list(p.needs), added: 0, desc: desc.slice(0, 400),
                 who: String(p.who || "cheap").toLowerCase() === "me" ? "session" : "cheap" });
  }
  return { ok: true, steps };
}

// `stealthlab-mcp dag plan`: the session's plan -> the DAG (lib/dag.mjs: data flow, same file, unsure -> in order); its
// own parts are "ready" (yours), the rest "waiting"; the cheap parts that need none of the session's run at once.
export async function planDag({ root, text, env = process.env, rt, log = () => {}, runShell, undo }) {
  const st = loadDagState(root);
  if (!st?.awaiting_plan) return { ok: false, lines: ["No large task is waiting for a plan here."] };
  const p = readPlan(text, root);
  if (!p.ok) return { ok: false, lines: [`The plan was not taken: ${p.error}.`, `Usage: ${PLAN_USAGE}`] };
  const dag = buildDag(p.steps);
  st.nodes = dag.nodes.map((n) => ({ id: n.id, deps: n.deps, step: n.step, who: n.step.who,
    status: n.step.who === "session" ? "ready" : "waiting", rungUnits: null }));
  st.awaiting_plan = false;
  saveDagState(root, st);
  log(`dag plan: ${st.nodes.length} parts, yours: ${st.nodes.filter((n) => n.status === "ready").map((n) => n.id).join(", ") || "none"}`);
  const r = await continueDag({ root, env, rt, log, runShell, undo, fresh: true });
  return { ...r, lines: [`Plan taken: ${st.nodes.length} parts (${dag.waves.map((w) => w.join(" + ")).join(" -> ")}).`, ...r.lines] };
}

export async function runDagCli(argv, { cwd = process.cwd(), env = process.env, print = (s) => process.stdout.write(`${s}\n`), stdin = null } = {}) {
  if (argv[0] !== "continue" && argv[0] !== "plan") {
    print("usage: stealthlab-mcp dag continue   (after doing your part of a plan handed over in parts)");
    print(`       ${PLAN_USAGE}`);
    return 2;
  }
  const { findStealthRoot } = await import("./library.mjs");
  const root = findStealthRoot(cwd);
  if (!root) { print("No .stealth/ here."); return 1; }
  const { ExecRuntime } = await import("./exec/runtime.mjs");
  const { runShellCapture } = await import("./dispatch.mjs");
  const { createUndoPoint, finishUndoPoint, discardUndoPoint, undoEnabled } = await import("./undo.mjs");
  const ctx = { root, env, rt: new ExecRuntime({ env }), runShell: runShellCapture, log: () => {},
    undo: { enabled: undoEnabled(env), create: (o) => createUndoPoint({ ...o, env }), finish: (p, o) => finishUndoPoint(p, { ...o, env }),
            discard: (p, o) => discardUndoPoint(p, { ...o, env }) } };
  let r;
  if (argv[0] === "plan") {
    // the plan: a file named after `plan`, else stdin
    let text = "";
    try { text = argv[1] ? fs.readFileSync(path.resolve(cwd, argv[1]), "utf8") : (stdin ?? fs.readFileSync(0, "utf8")); }
    catch (err) { print(`The plan could not be read: ${err.message}`); return 2; }
    r = await planDag({ ...ctx, text });
  } else {
    r = await continueDag(ctx);
  }
  for (const l of r.lines) print(l);
  try {
    if (r.state) {
      const { writeDagRun } = await import("./dispatch_run.mjs");
      writeDagRun(root, { task: r.state.task, entry: r.state.entry, interCheck: r.state.interCheck, fullCheck: r.state.fullCheck,
        nodes: r.state.nodes.map((n) => ({ id: n.id, at: n.step.at, deps: n.deps, who: n.who, status: n.status, gives: n.step.gives })) });
    }
  } catch { /* run.md is a record */ }
  return r.ok ? 0 : 1;
}
