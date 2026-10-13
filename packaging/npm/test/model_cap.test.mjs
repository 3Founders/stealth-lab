import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFileSync } from "node:child_process";

import { WINS_MAX, median, modelCapS, modelSpeeds, modelWins, orderFastestFirst, p90, parseWinLines, recordModelWin, renderWinLine } from "../lib/model_speed.mjs";
import { parseRouting, recordObs, requestPayload, upsertRoutes } from "../lib/library.mjs";
import { dispatch } from "../lib/dispatch.mjs";
import { loadAttempts, replay } from "../scripts/replay_model_cap.mjs";

const tmp = (p) => fs.mkdtempSync(path.join(os.tmpdir(), p));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const routing = (root) => fs.readFileSync(path.join(root, ".stealth", "routing.md"), "utf8");
const winLines = (root) => routing(root).split("\n").filter((l) => l.startsWith("WIN|"));
const stealthRoot = () => { const r = tmp("mc-root-"); fs.mkdirSync(path.join(r, ".stealth")); return r; };

// ---- recording ------------------------------------------------------------------------------------------------------------

test("a win's seconds go on its own model's WIN line; two models, two lines; the last 20 only", () => {
  const root = stealthRoot();
  recordModelWin(root, "gemma", "stealth", 30.04);
  recordModelWin(root, "minimax", "stealth", 90);
  recordModelWin(root, "gemma", "stealth", 40);
  const lines = winLines(root);
  assert.equal(lines.length, 2);
  assert.match(lines[0], /^WIN\|gemma\|stealth\|n=2\|last=\d{4}-\d\d-\d\d\|p90s=40\|wins=30,40$/);
  assert.match(lines[1], /^WIN\|minimax\|stealth\|n=1\|last=\d{4}-\d\d-\d\d\|p90s=90\|wins=90$/);
  assert.deepEqual(modelWins(root, "gemma", "stealth").wins, [30, 40]);
  assert.equal(modelWins(root, "gemma", "claude"), null, "the scaffold is part of the unit");
  for (let i = 1; i <= WINS_MAX + 5; i++) recordModelWin(root, "gpt", "stealth", i);
  const g = modelWins(root, "gpt", "stealth");
  assert.equal(g.wins.length, WINS_MAX);
  assert.equal(g.wins[0], 6);
  assert.equal(g.n, WINS_MAX + 5, "n counts every win ever");
});

test("bad input records nothing and never throws; odd model names round-trip", () => {
  const root = stealthRoot();
  assert.equal(recordModelWin(root, "gemma", "stealth", NaN), false);
  assert.equal(recordModelWin(root, "gemma", "stealth", -1), false);
  assert.equal(recordModelWin(null, "gemma", "stealth", 5), false);
  assert.equal(recordModelWin("/no/such/dir/at/all", "gemma", "stealth", 5) === true || true, true);
  recordModelWin(root, "a|b", "stealth", 12);
  assert.deepEqual(modelWins(root, "a|b", "stealth").wins, [12]);
});

test("the WIN line coexists with the router's lines: ROUTE and OBS are untouched, parseRouting skips WIN, and it is NOT sent to the server", () => {
  const root = stealthRoot();
  upsertRoutes(root, ["ROUTE|R-abc123|goal=-|g=g1|fit=-|basis=-|as_of=2026-10-12|step=*|ladder=-|whole=-"]);
  recordObs(root, "R-abc123", "gemma", "stealth", true);
  recordModelWin(root, "gemma", "stealth", 25);
  recordObs(root, "R-abc123", "gemma", "stealth", true);              // rewriting OBS must keep the WIN line
  upsertRoutes(root, ["ROUTE|R-abc123|goal=-|g=g1|fit=-|basis=-|as_of=2026-10-13|step=*|ladder=-|whole=-"]);   // and so must a new plan
  assert.equal(winLines(root).length, 1);
  const parsed = parseRouting(routing(root));
  assert.equal(parsed.routes.length, 1);
  assert.deepEqual(parsed.obs.map((o) => [o.model, o.n, o.ok]), [["gemma", 2, 2]]);
  const sent = requestPayload(root).route_obs;
  assert.ok(sent.includes("ROUTE|") && sent.includes("OBS|"));
  assert.ok(!sent.includes("WIN|"), "durations stay on this machine");
});

test("parse and render round trip; malformed WIN lines are skipped", () => {
  const line = "WIN|gemma|stealth|n=7|last=2026-10-12|p90s=40|wins=30,40,35";
  const w = parseWinLines(`${line}\nWIN|bad\nWIN|x|y|n=1|wins=\nnot a win\n`).get("gemma|stealth");
  assert.deepEqual(w.wins, [30, 40, 35]);
  assert.equal(w.n, 7);
  assert.equal(renderWinLine({ model: "gemma", scaffold: "stealth", n: 7, wins: [30, 40, 35], last: "2026-10-12" }), line);
  assert.equal(parseWinLines("WIN|bad\nWIN|x|y|n=1|wins=\n").size, 0);
});

// ---- the cap and the order ------------------------------------------------------------------------------------------------------

test("modelCapS: fewer than 5 wins keep today's limit; otherwise clamp(2 x p90, 60 s, today's limit)", () => {
  assert.equal(modelCapS([10, 10, 10, 10], { baseS: 150 }), null);
  assert.equal(modelCapS(undefined, { baseS: 150 }), null);
  assert.equal(modelCapS(Array(5).fill(10), { baseS: 150 }), 60, "the floor");
  assert.equal(modelCapS(Array(5).fill(40), { baseS: 150 }), 80);
  assert.equal(modelCapS(Array(5).fill(100), { baseS: 150 }), 150, "never above today's limit");
  assert.equal(modelCapS(Array(5).fill(40), { baseS: 150, mult: 3 }), 120);
  assert.equal(modelCapS(Array(5).fill(10), { baseS: 150, floorS: 0.3 }), 20);
  assert.equal(p90([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]), 9);
  assert.equal(median([3, 1, 2]), 2);
  assert.equal(median([1, 2, 3, 4]), 2.5);
});

test("orderFastestFirst: known models by median, an unknown one as the median of the known, nothing known leaves the order", () => {
  const r = (m) => ({ unit: `${m}|stealth`, model: m, executor: "stealth" });
  const rungs = [r("minimax"), r("oss"), r("gemma")];
  const speeds = new Map([["gemma|stealth", 10], ["minimax|stealth", 50]]);
  assert.deepEqual(orderFastestFirst(rungs, speeds).map((x) => x.model), ["gemma", "oss", "minimax"], "oss (unknown) sits at the median 30");
  assert.deepEqual(orderFastestFirst(rungs, new Map()).map((x) => x.model), ["minimax", "oss", "gemma"]);
  assert.deepEqual(orderFastestFirst(rungs, new Map([["gemma|stealth", 10], ["oss|stealth", 10], ["minimax|stealth", 10]])).map((x) => x.model), ["minimax", "oss", "gemma"], "ties keep the order");
  const root = stealthRoot();
  for (let i = 0; i < 5; i++) recordModelWin(root, "gemma", "stealth", 10);
  for (let i = 0; i < 4; i++) recordModelWin(root, "minimax", "stealth", 50);
  assert.deepEqual([...modelSpeeds(root)], [["gemma|stealth", 10]], "only models with 5+ wins count");
});

// ---- through dispatch ---------------------------------------------------------------------------------------------------------------

const PLAN = { status: "ok", instance_key: "g1.k1", goal_id: "g1", ladder: ["minimax-m2-7|stealth", "gpt-oss-120b|stealth", "gemma-4-31b-it|stealth", "claude-opus-5-5|claude-code"] };
const MINE = "claude-opus-5-5|claude-code";

function setup(envExtra = {}) {
  const home = tmp("mc-home-");
  fs.writeFileSync(path.join(home, "exec.json"), JSON.stringify({ executors: { stealth: { models: ["minimax-m2-7", "gpt-oss-120b", "gemma-4-31b-it"] } } }));
  const root = tmp("mc-repo-");
  const g = (...a) => execFileSync("git", ["-C", root, ...a], { stdio: "pipe" });
  g("init", "-q"); g("config", "user.email", "t@e"); g("config", "user.name", "t");
  fs.mkdirSync(path.join(root, ".stealth"));
  fs.writeFileSync(path.join(root, "calc.py"), "x = 1\n");
  g("add", "-A"); g("commit", "-q", "-m", "init");
  return { root, env: { STEALTHLAB_HOME: home, STEALTHLAB_DISPATCH_CHECK: "python -m pytest -q", STEALTHLAB_DISPATCH_TESTFIRST: "off", STEALTHLAB_DISPATCH_CROSSCHECK: "off",
                        STEALTHLAB_BRIEF: "off", STEALTHLAB_DISPATCH_PARALLEL: "on", STEALTHLAB_DISPATCH_MAX_PARALLEL: "2", STEALTHLAB_DISPATCH_CAP_FLOOR_S: "0.3", ...envExtra } };
}
const seed = (root, model, secs, n = 5) => { for (let i = 0; i < n; i++) recordModelWin(root, model, "stealth", secs); };

// models: { name: { state, delay } }; a run ends after its delay or when cancelled
function runtime(root, models) {
  const runs = new Map(), starts = [], cancelled = [], wake = new Map();
  return { starts, cancelled,
    async achieve(i) { const id = `r-${i.model}`; runs.set(id, i.model); starts.push(i.model); return { run_id: id }; },
    async runResult(id) {
      const m = models[runs.get(id)];
      let wasCancelled = false;
      await Promise.race([sleep(m.delay || 0), new Promise((r) => wake.set(id, () => { wasCancelled = true; r(); }))]);
      return { run_id: id, state: wasCancelled ? "cancelled" : (m.state || "verified"), cost_usd: 0.001, diff: { files: ["calc.py"] }, checks: [{ tail: "no" }] };
    },
    async applyRun() { fs.writeFileSync(path.join(root, "calc.py"), "x = 2\n"); return { files: ["calc.py"] }; },
    async cancelRun(id) { cancelled.push(id); wake.get(id)?.(); return {}; } };
}
const run = (s, rt) => dispatch({ payload: { cwd: s.root, prompt: "Fix calc.py so x is 2" }, reply: { model_plan: PLAN }, root: s.root, mine: MINE, env: s.env, runtime: rt });

test("dispatch: the rungs start fastest model first, the winner releases its slower sibling (cancelled, no OBS), and its win joins its model's list", async () => {
  const s = setup();
  seed(s.root, "minimax-m2-7", 0.3);                                        // minimax usually wins in 0.3 s (times are kept to a tenth of a second)
  seed(s.root, "gemma-4-31b-it", 0.1);                                       // gemma is the faster model, so it is started first (cap 0.3 s: it wins in 0.15 s)
  const rt = runtime(s.root, { "minimax-m2-7": { delay: 5000 }, "gpt-oss-120b": { delay: 5000 }, "gemma-4-31b-it": { delay: 150 } });
  const t0 = Date.now();
  const d = await run(s, rt);
  assert.equal(d.handled, true);
  assert.ok(Date.now() - t0 < 4000, "nothing waited for the slow runs");
  assert.deepEqual(rt.starts.slice(0, 2), ["gemma-4-31b-it", "gpt-oss-120b"], "fastest first, then the unknown model at the median");
  assert.ok(rt.cancelled.includes("r-gpt-oss-120b"), "the sibling that was still going is cancelled when gemma passes");
  assert.ok(!rt.starts.includes("minimax-m2-7"), "the third rung was never needed");
  const obs = parseRouting(routing(s.root)).obs.map((o) => `${o.model}:${o.n}:${o.ok}`);
  assert.ok(!obs.some((x) => x.startsWith("gpt-oss")), "a cancelled run leaves no OBS line");
  assert.ok(winLines(s.root).some((l) => l.startsWith("WIN|gemma-4-31b-it|stealth|n=6|")), "gemma's win joined its list");
});

test("dispatch: the capped run is cancelled at its model's cap, not before, and not counted against the model", async () => {
  const s = setup({ STEALTHLAB_DISPATCH_MAX_PARALLEL: "1" });
  seed(s.root, "minimax-m2-7", 0.1);                                        // cap = 0.3 s
  const rt = runtime(s.root, { "minimax-m2-7": { delay: 5000 }, "gpt-oss-120b": { delay: 50 }, "gemma-4-31b-it": { delay: 50 } });
  const t0 = Date.now();
  const d = await run(s, rt);
  const took = Date.now() - t0;
  assert.equal(d.handled, true);
  assert.ok(took >= 280 && took < 3000, `${took} ms: capped near 0.3 s, then the next model`);
  const capped = d.tried.find((t) => /minimax/.test(t.unit));
  assert.equal(capped.state, "capped at 0.3 s");
  assert.ok(rt.cancelled.includes("r-minimax-m2-7"));
  assert.ok(!parseRouting(routing(s.root)).obs.some((o) => o.model === "minimax-m2-7"), "no OBS for the capped run: it is not a failure");
});

test("dispatch: a model with fewer than 5 wins has no cap and a slow run that wins still wins", async () => {
  const s = setup({ STEALTHLAB_DISPATCH_MAX_PARALLEL: "1" });
  seed(s.root, "minimax-m2-7", 0.1, 4);                                      // one win short
  const rt = runtime(s.root, { "minimax-m2-7": { delay: 700 }, "gpt-oss-120b": { delay: 50 }, "gemma-4-31b-it": { delay: 50 } });
  const d = await run(s, rt);
  assert.equal(d.handled, true);
  assert.equal(d.unit, "minimax-m2-7|stealth");
  assert.ok(!d.tried.some((t) => /capped/.test(t.state)));
});

test("dispatch: the switches. MODEL_CAP=off never caps; FASTEST_FIRST=off keeps the plan's order", async () => {
  const a = setup({ STEALTHLAB_DISPATCH_MAX_PARALLEL: "1", STEALTHLAB_DISPATCH_MODEL_CAP: "off" });
  seed(a.root, "minimax-m2-7", 0.1);
  const rtA = runtime(a.root, { "minimax-m2-7": { delay: 700 }, "gpt-oss-120b": { delay: 50 }, "gemma-4-31b-it": { delay: 50 } });
  assert.equal((await run(a, rtA)).unit, "minimax-m2-7|stealth");
  const b = setup({ STEALTHLAB_DISPATCH_MODEL_CAP: "off", STEALTHLAB_DISPATCH_FASTEST_FIRST: "off" });
  seed(b.root, "gemma-4-31b-it", 0.1);
  const rtB = runtime(b.root, { "minimax-m2-7": { delay: 300 }, "gpt-oss-120b": { delay: 300 }, "gemma-4-31b-it": { delay: 300 } });
  await run(b, rtB);
  assert.deepEqual(rtB.starts.slice(0, 2), ["minimax-m2-7", "gpt-oss-120b"], "the plan's order");
});

test("dispatch: a win records the seconds from ITS OWN start, per model, even when another model started earlier", async () => {
  const s = setup({ STEALTHLAB_DISPATCH_MAX_PARALLEL: "1" });
  const rt = runtime(s.root, { "minimax-m2-7": { state: "failed", delay: 400 }, "gpt-oss-120b": { delay: 200 }, "gemma-4-31b-it": { delay: 50 } });
  const d = await run(s, rt);
  assert.equal(d.unit, "gpt-oss-120b|stealth");
  const w = modelWins(s.root, "gpt-oss-120b", "stealth");
  assert.equal(w.wins.length, 1);
  assert.ok(w.wins[0] >= 0.15 && w.wins[0] < 1, `its own 0.2 s, not minimax's 0.4 s before it: ${w.wins[0]}`);
  assert.equal(modelWins(s.root, "minimax-m2-7", "stealth"), null, "a failure is not a win");
});

// ---- the offline replay ----------------------------------------------------------------------------------------------------------------

function runsDir(runs) {
  const dir = tmp("mc-runs-");
  runs.forEach((attempts, i) => {
    fs.mkdirSync(path.join(dir, `r-${i}`), { recursive: true });
    fs.writeFileSync(path.join(dir, `r-${i}`, "run.json"), JSON.stringify({ run_id: `r-${i}`, created_at: `2026-10-01T00:00:${String(i).padStart(2, "0")}Z`, attempts }));
  });
  return dir;
}
const att = (model, state, d) => ({ model, executor: "stealth", state, duration_s: d });

test("replay: per-model caps from each model's earlier wins; wins cut, seconds given back by slow non-wins, the 100 s table", () => {
  const dir = runsDir([
    ...Array.from({ length: 5 }, () => [att("gemma", "verified", 30), att("minimax", "verified", 90)]),
    [att("gemma", "verified", 33), att("minimax", "failed", 170)],                 // gemma cap 60 (floor); minimax 2 x 90 = 180 capped at the base 150: its 170 s failure gives back 20 s
    [att("gemma", "failed", 150), att("minimax", "cancelled", 400)],                // gemma's cap is now 66 (2 x p90 33): 84 s back; minimax's cancelled 400 s is cost only
    [att("gemma", "verified", 80), att("minimax", "verified", 100)],                // gemma's 80 s win is past its 66 s cap: cut; minimax's 100 s is under 150
  ]);
  const r = replay(loadAttempts(dir));
  assert.equal(r.wins, 13);
  assert.equal(r.winsCut, 1);
  assert.match(r.cutWins[0], /gemma\|stealth: 80 s, cap 66 s/);
  assert.equal(r.longNonWins, 2);
  assert.equal(r.secondsSaved, 104);
  assert.equal(r.longCancelled, 1);
  assert.equal(r.models["gemma|stealth"].median, 30);
  assert.equal(r.models["minimax|stealth"].over100, 2, "its 170 s and 400 s attempts (100 s itself is not over 100)");
  assert.equal(r.total100, 3);
  assert.equal(r.models["minimax|stealth"].shareOver100, 67);
});
