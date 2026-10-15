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
import { diffPiece, splitDiff, stepsFromDiff } from "./way_extract.mjs";
import { loadLibrary } from "./library.mjs";
import { readArea, recordArea } from "./areas.mjs";
import { createWorktree, git, headCommit, removeWorktree, repoTopLevel, workingTreeDiff } from "./exec/worktree.mjs";

export const dagEnabled = (env = process.env) => (env.STEALTHLAB_DISPATCH_DAG || "on").toLowerCase() !== "off";
const TERMINAL = new Set(["verified", "failed", "timed_out", "cancelled", "error"]);
const TASK_MAX = 2000;
const BIG_STEP = 15;                    // added lines in the past piece above which a node goes to the strong rung first

function readSolution(root, entry) {
  const sol = (entry.procs || [])[0]?.solution;
  if (!sol) return "";
  try { return fs.readFileSync(path.join(root, ".stealth", "library", sol), "utf8"); } catch { return ""; }
}

// -> {entry, diff, steps, dag} | null
export function planFromLibrary({ root, reply, loc }) {
  let lib;
  try { lib = loadLibrary(root); } catch { return null; }
  const matched = new Set((reply?.library_matches || []).filter((m) => m?.id && m.relation !== "unrelated").map((m) => m.id));
  const located = new Set(loc?.files || []);
  let best = null;
  for (const e of lib.entries || []) {
    if (e.outcome === "fail") continue;
    const diff = readSolution(root, e);
    if (!diff) continue;
    const steps = stepsFromDiff(diff);
    if (steps.length < 2) continue;
    const overlap = steps.filter((s) => s.at.some((f) => located.has(f))).length;
    const score = overlap + (matched.has(e.id) ? 2 : 0);
    if (score < 1) continue;                        // nothing ties this past fix to the task
    if (!best || score > best.score || (score === best.score && String(e.verified_at || "") > String(best.entry.verified_at || ""))) {
      best = { entry: e, diff, steps, score };
    }
  }
  if (!best) return null;
  return { entry: best.entry, diff: best.diff, steps: best.steps, dag: buildDag(best.steps) };
}

// the node's task: this part of the task, with the past fix's piece for the same file as the worked example
export function nodeTask({ task, node, total, entry, diff, where = "", done = [] }) {
  const s = node.step;
  const head = `Part ${node.id.slice(2)} of ${total} of this task (the other parts are done separately; change only what this part needs):\n`;
  const ask = String(task || "").trim();
  const part = `\nThis part: the change in ${s.at.join(", ")}${s.gives.length ? ` (in the past fix: ${s.gives.slice(0, 5).join(", ")})` : ""}.` +
    (done.length ? ` Already done and in the code: ${done.join("; ")}.` : "");
  const tail = where ? `\n\n${where}` : "";
  const exHead = `\nWorked example -- this part in a similar fix already made in this repository ("${String(entry.title).slice(0, 120)}"); adapt it, do not copy it:\n`;
  const room = TASK_MAX - head.length - part.length - tail.length - exHead.length - 40;
  const askCut = ask.length <= Math.max(300, Math.floor(room / 2)) ? ask : `${ask.slice(0, Math.max(300, Math.floor(room / 2)) - 10)} [...]`;
  const exRoom = TASK_MAX - head.length - askCut.length - part.length - tail.length - exHead.length - 10;
  const example = exRoom > 200 ? exHead + diffPiece(diff, s.at, exRoom) : "";
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
  const deadline = new Promise((res) => { timer = setTimeout(() => res({ timeout: true }), limitS * 1000); timer.unref?.(); });
  while (!winner && (queue.length || live.size)) {
    while (queue.length && live.size < width) start(queue.shift());
    const done = await Promise.race([...[...live.values()].map((v) => v.promise), deadline]);
    if (done.timeout) break;
    live.delete(done.r.unit);
    const files = done.res?.diff?.files || [];
    const ok = done.res?.state === "verified" && files.length > 0 && done.res?.diff?.patch_path;
    tried.push({ unit: done.r.unit, state: done.err ? "error" : done.res?.state, run_id: done.res?.run_id ?? null, cost_usd: done.res?.cost_usd ?? null });
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
          runShell, undo, instanceKey } = ctx;
  if (!dagEnabled(env)) return null;
  const plan = planFromLibrary({ root, reply, loc });
  if (!plan || plan.dag.nodes.length < 2) return null;
  const repo = await repoTopLevel(payload.cwd || root, env);
  const nodes = plan.dag.nodes;
  log(`dag: ${nodes.length} parts from ${plan.entry.id} ("${String(plan.entry.title).slice(0, 60)}"): ` +
      plan.dag.waves.map((w) => w.map((id) => `${id} ${nodes.find((n) => n.id === id).step.at[0]}`).join(" + ")).join(" -> "));
  const accepted = new Map();            // node id -> winner
  const tried = [];
  const left = new Set();                // nodes for the session's model
  const order = plan.dag.waves.flat();
  const patchesBefore = (n) => order.filter((id) => accepted.has(id) && id !== n.id).map((id) => accepted.get(id).patch);
  const doneText = () => order.filter((id) => accepted.has(id)).map((id) => `${id} in ${nodes.find((n) => n.id === id).step.at.join(", ")}`);
  const runOne = async (n) => {
    const m = nodeModels({ node: n, rungs, strongSet, root, areaSkipAfter: policy.areaSkipAfter });
    if (!m.rungs.length || n.deps.some((d) => left.has(d))) { left.add(n.id); n.who = "session"; return; }
    n.who = m.who;
    const task = nodeTask({ task: rawPayload.prompt, node: n, total: nodes.length, entry: plan.entry, diff: plan.diff, where, done: doneText() });
    const r = await runNode({ rt, repo, task, check: interCheck, scope: policy.scope, rungs: m.rungs, extraPatches: patchesBefore(n),
                              limitS: policy.timeoutS, width: policy.maxParallel, instanceKey, log });
    tried.push(...r.tried.map((t) => ({ ...t, node: n.id })));
    for (const f of n.step.at) recordArea(root, f, !!r.winner);
    if (r.winner) { accepted.set(n.id, r.winner); n.who = r.winner.model; }
    return r.winner;
  };
  for (const wave of plan.dag.waves) {
    const ns = wave.map((id) => nodes.find((n) => n.id === id));
    const results = await Promise.all(ns.map((n) => runOne(n)));
    // a node that failed beside others gets one more try with their accepted changes in (they may have been what it needed)
    if (ns.length > 1) for (const [i, n] of ns.entries()) if (!results[i] && !left.has(n.id) && accepted.size) { await runOne(n); }
    for (const n of ns) if (!accepted.has(n.id)) left.add(n.id);
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
      if (repair.winner) { patches.push(repair.winner.patch); fin = { ok: true }; }
    }
    if (fin.ok) {
      const applied = await applyParts({ repo, patches, task: rawPayload.prompt, models, env, undo });
      return { handled: true, dag: { entry: plan.entry.id, nodes: nodes.map((n) => ({ id: n.id, at: n.step.at, deps: n.deps, who: n.who, status: "done" })) },
               files: applied.files, tried, undone: applied.undone, models, cost_usd: [...accepted.values()].reduce((s, w) => s + (w.cost_usd || 0), 0) };
    }
    for (const id of order) left.add(id);           // the parts do not add up: nothing is applied, the session's model does it
    accepted.clear();
  }
  // partial: the accepted parts are applied (they passed "no new failures"); the rest is the session's
  let applied = { files: [], undone: false };
  if (accepted.size && policy.dagPartialApply) {
    try { applied = await applyParts({ repo, patches: order.filter((id) => accepted.has(id)).map((id) => accepted.get(id).patch), task: rawPayload.prompt, models, env, undo }); }
    catch (err) { log(`dag: the accepted parts could not be applied (${err.message})`); accepted.clear(); }
  }
  return { handled: false, tried, partial: {
    entry: plan.entry.id, applied: applied.files, undone: applied.undone,
    nodes: nodes.map((n) => ({ id: n.id, at: n.step.at, deps: n.deps, who: n.who || "session", status: accepted.has(n.id) ? "done" : "ready",
                               gives: n.step.gives })) } };
}
