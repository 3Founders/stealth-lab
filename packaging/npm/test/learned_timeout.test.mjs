import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFileSync } from "node:child_process";

import { WINS_MAX, learnedLimitS, p90, parseRouting, recordObs, renderObsLine, routeWins } from "../lib/library.mjs";
import { dispatch, taskAllowanceS } from "../lib/dispatch.mjs";
import { replay, replayRuns } from "../scripts/replay_learned_timeout.mjs";

const tmp = (p) => fs.mkdtempSync(path.join(os.tmpdir(), p));
const ROUTE = "R-abc123";
const GOAL = "g1";
const routing = (root) => fs.readFileSync(path.join(root, ".stealth", "routing.md"), "utf8");
const obsLines = (root) => routing(root).split("\n").filter((l) => l.startsWith("OBS|"));

function stealthRoot(extra = "") {
  const root = tmp("lt-root-");
  fs.mkdirSync(path.join(root, ".stealth"));
  fs.writeFileSync(path.join(root, ".stealth", "routing.md"),
    `ROUTE|${ROUTE}|goal=-|g=${GOAL}|fit=-|basis=-|as_of=2026-10-12|step=*|ladder=-|whole=-\n${extra}`);
  return root;
}
const wins = (n, s) => Array.from({ length: n }, () => s);

// ---- recording ---------------------------------------------------------------------------------------------------------

test("a win's seconds join the route's list, p90s is recomputed, a failure adds nothing, and every OBS line of the route carries the same list", () => {
  const root = stealthRoot();
  recordObs(root, ROUTE, "gemma", "stealth", true, { winSeconds: 12.34 });
  recordObs(root, ROUTE, "minimax", "stealth", true, { winSeconds: 30 });
  recordObs(root, ROUTE, "gemma", "stealth", false, { winSeconds: 999 });         // not accepted: nothing recorded
  recordObs(root, ROUTE, "gemma", "stealth", true);                                  // accepted with no time: counted, no seconds
  const lines = obsLines(root);
  assert.equal(lines.length, 2);
  for (const l of lines) assert.match(l, /\|p90s=30\|wins=12\.3,30$/);
  assert.match(lines[0], /^OBS\|R-abc123\|gemma\|stealth\|n=3\|ok=2\|last=/);
  assert.match(lines[1], /^OBS\|R-abc123\|minimax\|stealth\|n=1\|ok=1\|last=/);
  assert.deepEqual(routeWins(root, GOAL), [12.3, 30]);
});

test("only the last 20 wins are kept; p90 is the nearest rank", () => {
  const root = stealthRoot();
  for (let i = 1; i <= WINS_MAX + 5; i++) recordObs(root, ROUTE, "gemma", "stealth", true, { winSeconds: i });
  const w = routeWins(root, GOAL);
  assert.equal(w.length, WINS_MAX);
  assert.equal(w[0], 6);
  assert.equal(w[WINS_MAX - 1], WINS_MAX + 5);
  assert.equal(p90([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]), 9);
  assert.equal(p90([5]), 5);
  assert.equal(p90([]), null);
});

test("old OBS lines without wins parse and render exactly as before; the new fields survive a parse/render round trip", () => {
  const old = `OBS|${ROUTE}|gemma|stealth|n=3|ok=2|last=2026-10-12`;
  const { obs } = parseRouting(`${old}\n`);
  assert.equal(obs[0].wins, undefined);
  assert.equal(renderObsLine(obs[0]), old);
  const withWins = `${old}|p90s=12.5|wins=10,12.5,9`;
  const parsed = parseRouting(`${withWins}\n`).obs[0];
  assert.deepEqual(parsed.wins, [10, 12.5, 9]);
  assert.equal(renderObsLine(parsed), withWins);
});

// ---- the limit -----------------------------------------------------------------------------------------------------------

test("learnedLimitS: fewer than 5 wins keep today's limit; otherwise clamp(2 x p90, 60 s, today's limit)", () => {
  assert.equal(learnedLimitS(wins(4, 10), { baseS: 150 }), null);
  assert.equal(learnedLimitS([], { baseS: 150 }), null);
  assert.equal(learnedLimitS(wins(5, 10), { baseS: 150 }), 60, "2 x 10 is below the 60 s floor");
  assert.equal(learnedLimitS(wins(5, 40), { baseS: 150 }), 80);
  assert.equal(learnedLimitS(wins(5, 100), { baseS: 150 }), 150, "never above today's limit");
  assert.equal(learnedLimitS(wins(5, 40), { baseS: 150, mult: 3 }), 120);
  assert.equal(learnedLimitS(wins(5, 10), { baseS: 50 }), 50, "a base below the floor still caps it");
});

// ---- through dispatch ---------------------------------------------------------------------------------------------------------

const PLAN = { status: "ok", instance_key: "g1.k1", goal_id: GOAL, ladder: ["gemma-4-31b-it|stealth", "claude-opus-5-5|claude-code"] };
const MINE = "claude-opus-5-5|claude-code";

function setup(obsExtra, envExtra = {}) {
  const home = tmp("lt-home-");
  fs.writeFileSync(path.join(home, "exec.json"), JSON.stringify({ executors: { stealth: { models: ["gemma-4-31b-it"] } } }));
  const root = stealthRoot(obsExtra);
  const g = (...a) => execFileSync("git", ["-C", root, ...a], { stdio: "pipe" });
  g("init", "-q"); g("config", "user.email", "t@e"); g("config", "user.name", "t");
  fs.writeFileSync(path.join(root, "calc.py"), "x = 1\n");
  g("add", "-A"); g("commit", "-q", "-m", "init");
  return { root, env: { STEALTHLAB_HOME: home, STEALTHLAB_DISPATCH_CHECK: "python -m pytest -q", STEALTHLAB_DISPATCH_TESTFIRST: "off",
                         STEALTHLAB_DISPATCH_CROSSCHECK: "off", STEALTHLAB_BRIEF: "off", ...envExtra } };
}
function stubRuntime(root, state = "verified") {
  return { async achieve() { return { run_id: "r-1" }; },
    async runResult() { return { state, cost_usd: 0.001, diff: { files: ["calc.py"] }, checks: [{ tail: "ok" }] }; },
    async applyRun() { fs.writeFileSync(path.join(root, "calc.py"), "x = 2\n"); return { files: ["calc.py"] }; },
    async cancelRun() { return {}; } };
}
const run = (s, state) => dispatch({ payload: { cwd: s.root, prompt: "Fix calc.py so x is 2" }, reply: { model_plan: PLAN }, root: s.root, mine: MINE,
                                     env: s.env, runtime: stubRuntime(s.root, state) });
const seed = (n, sec) => `OBS|${ROUTE}|gemma-4-31b-it|stealth|n=${n}|ok=${n}|last=2026-10-12|p90s=${sec}|wins=${wins(n, sec).join(",")}\n`;

test("dispatch: with 5+ wins on the route the cheap tries get the learned limit; with fewer, the default; the switch and the multiplier apply", async () => {
  const base = 150 + taskAllowanceS(setup("").root);
  assert.equal((await run(setup(seed(5, 10)))).limit_s, 60);
  assert.equal((await run(setup(seed(5, 40)))).limit_s, 80);
  assert.equal((await run(setup(seed(5, 40), { STEALTHLAB_DISPATCH_LEARNED_MULT: "3" }))).limit_s, 120);
  assert.equal((await run(setup(seed(4, 10)))).limit_s, base, "fewer than 5 wins: today's limit");
  assert.equal((await run(setup(""))).limit_s, base, "no wins at all");
  assert.equal((await run(setup(seed(5, 10), { STEALTHLAB_DISPATCH_LEARNED_TIMEOUT: "off" }))).limit_s, base, "switch off");
  assert.equal((await run(setup(seed(5, 100)))).limit_s, base, "never above the default");
});

test("dispatch: a cheap win records how long the tries took; a failed try records nothing", async () => {
  const s = setup("");
  const d = await run(s, "verified");
  assert.equal(d.handled, true);
  const [line] = obsLines(s.root);
  assert.match(line, /^OBS\|R-abc123\|gemma-4-31b-it\|stealth\|n=1\|ok=1\|last=\d{4}-\d\d-\d\d\|p90s=\d+(\.\d)?\|wins=\d+(\.\d)?$/);
  const f = setup("");
  const d2 = await run(f, "failed");
  assert.equal(d2.handled, false);
  assert.doesNotMatch(obsLines(f.root)[0], /wins=/);
});

// ---- the offline replays ---------------------------------------------------------------------------------------------------------

const row = (id, suite, at, seconds, win = true) => ({ task_id: id, suite, arm: "C", status: "done", started_at: at, seconds,
  turns: win ? 0 : 5, attempts: win ? [{ state: "verified", role: "attempt", model: "gemma" }] : [{ state: "failed", role: "attempt", model: "gemma" }] });

test("replay over result rows: a win slower than the limit its suite had learned is counted as cut; earlier wins and other suites are not", () => {
  const rows = [...[1, 2, 3, 4, 5].map((i) => row(`a${i}`, "s1", `2026-10-01T00:00:0${i}`, 20)),
    row("slow", "s1", "2026-10-01T00:01:00", 200),                  // limit is max(60, 2x20) = 60
    row("ok", "s1", "2026-10-01T00:02:00", 50),
    row("other", "s2", "2026-10-01T00:03:00", 500)];                // s2 has no wins yet: no limit
  const r = replay(rows);
  assert.equal(r.wins, 8);
  assert.equal(r.winsCut, 1);
  assert.match(r.cutWins[0], /^slow took 200 s, limit 60 s/);
  assert.equal(replay(rows, { mult: 9 }).winsCut, 1, "200 s is above 9 x 20");
  assert.equal(replay(rows, { mult: 10 }).winsCut, 0, "and not above 10 x 20");
});

test("replay over run records: wins and long non-wins per route (the instance key's goal), seconds saved on the long ones", () => {
  const dir = tmp("lt-runs-");
  const add = (i, key, attempts) => {
    fs.mkdirSync(path.join(dir, `r-${i}`), { recursive: true });
    fs.writeFileSync(path.join(dir, `r-${i}`, "run.json"), JSON.stringify({ run_id: `r-${i}`, instance_key: key, created_at: `2026-10-01T00:00:${String(i).padStart(2, "0")}Z`, attempts }));
  };
  for (let i = 1; i <= 5; i++) add(i, "gA.k1", [{ state: "verified", duration_s: 20 }]);
  add(6, "gA.k2", [{ state: "failed", duration_s: 150 }, { state: "cancelled", duration_s: 30 }]);      // limit 60: the failed one saves 90 s
  add(7, "gA.k3", [{ state: "verified", duration_s: 100 }]);                                             // a win slower than the limit: cut
  add(8, "gB.k1", [{ state: "failed", duration_s: 400 }]);                                               // route gB has no wins: no limit
  const r = replayRuns(dir);
  assert.deepEqual({ wins: r.wins, winsCut: r.winsCut, longNonWins: r.longNonWins, secondsSaved: r.secondsSaved }, { wins: 6, winsCut: 1, longNonWins: 1, secondsSaved: 90 });
});
