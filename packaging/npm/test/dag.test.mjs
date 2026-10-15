import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFileSync, spawnSync } from "node:child_process";
import { buildDag } from "../lib/dag.mjs";
import { diffPiece, readStepExtras, stepExtras, stepsFromDiff, wayStepsForEntry } from "../lib/way_extract.mjs";
import { nodeTask, planFromLibrary } from "../lib/dag_run.mjs";
import { addEntry, loadLibrary } from "../lib/library.mjs";
import { dispatch, triedNote } from "../lib/dispatch.mjs";

// a past fix in the sqlglot shape: the expression class, then the parser and the generator that use it
const PAST = `diff --git a/pkg/expressions.py b/pkg/expressions.py
--- a/pkg/expressions.py
+++ b/pkg/expressions.py
@@ -1,2 +1,5 @@ class Expression:
 class Expression:
     pass
+
+class ArrayFilter(Expression):
+    arg_types = {"this": True}
diff --git a/pkg/parser.py b/pkg/parser.py
--- a/pkg/parser.py
+++ b/pkg/parser.py
@@ -1,2 +1,3 @@ def parse(name):
 def parse(name):
-    return None
+    from pkg.expressions import ArrayFilter
+    return ArrayFilter() if name == "FILTER" else None
diff --git a/pkg/generator.py b/pkg/generator.py
--- a/pkg/generator.py
+++ b/pkg/generator.py
@@ -1,2 +1,3 @@ def generate(e):
 def generate(e):
-    return ""
+    from pkg.expressions import ArrayFilter
+    return "FILTER" if isinstance(e, ArrayFilter) else ""
diff --git a/tests/test_filter.py b/tests/test_filter.py
--- a/tests/test_filter.py
+++ b/tests/test_filter.py
@@ -0,0 +1,2 @@
+def test_x():
+    pass
`;

test("stepsFromDiff: one step per source file, what each gives and needs, needed steps first; tests are not steps", () => {
  const steps = stepsFromDiff(PAST);
  assert.deepEqual(steps.map((s) => s.at[0]), ["pkg/expressions.py", "pkg/parser.py", "pkg/generator.py"]);
  assert.ok(steps[0].gives.includes("ArrayFilter"));
  assert.deepEqual(steps[1].needs, ["ArrayFilter"]);
  assert.deepEqual(steps[2].needs, ["ArrayFilter"]);
  assert.equal(stepsFromDiff("diff --git a/README.md b/README.md\n+x").length, 0);
  const extras = stepExtras(steps[1]);
  assert.deepEqual(readStepExtras(extras).at, ["pkg/parser.py"]);
  assert.deepEqual(readStepExtras(extras).needs, ["ArrayFilter"]);
  assert.equal(wayStepsForEntry(PAST).length, 3);
  assert.match(diffPiece(PAST, ["pkg/parser.py"]), /ArrayFilter\(\) if name/);
  assert.doesNotMatch(diffPiece(PAST, ["pkg/parser.py"]), /isinstance/);
});

test("buildDag: data flow and same-file make edges; the rest runs in parallel; no locator -> in order", () => {
  const d = buildDag(stepsFromDiff(PAST));
  assert.deepEqual(d.waves, [["N-1"], ["N-2", "N-3"]], "expressions first, then parser and generator together");
  assert.deepEqual(d.nodes[1].deps, ["N-1"]);
  const same = buildDag([{ at: ["a.py"], gives: [], needs: [] }, { at: ["a.py"], gives: [], needs: [] }, { at: ["b.py"], gives: [], needs: [] }]);
  assert.deepEqual(same.waves, [["N-1", "N-3"], ["N-2"]], "one file: one after the other; another file: beside them");
  const unsure = buildDag([{ at: ["a.py"], gives: [], needs: [] }, { at: [], gives: [], needs: [] }]);
  assert.deepEqual(unsure.waves, [["N-1"], ["N-2"]]);
});

test("library: an entry's steps keep at= / gives= / needs=, and so does the Way made from them", () => {
  const repo = gitRepo({ "pkg/expressions.py": "class Expression:\n    pass\n" });
  const e = addEntry(repo, { title: "support FILTER", check: "true", observed: true, diff: PAST, steps: wayStepsForEntry(PAST) });
  const lib = loadLibrary(repo);
  const entry = lib.entries.find((x) => x.id === e.id);
  assert.deepEqual(readStepExtras(entry.procs[0].steps[1].extra).at, ["pkg/parser.py"]);
  const way = lib.ways.find((w) => w.id === entry.procs[0].way);
  assert.deepEqual(readStepExtras(way.steps[1].extra).needs, ["ArrayFilter"]);
  assert.match(fs.readFileSync(path.join(repo, ".stealth", "library.md"), "utf8"), /^S\|W-[0-9a-f]+:2\|action\|In pkg\/parser\.py[^\n]*\|at=pkg\/parser\.py\|gives=[^\n]*\|needs=ArrayFilter$/m);
});

test("nodeTask: this part, what is done, and the past fix's piece for the same file, within 2,000 characters", () => {
  const steps = stepsFromDiff(PAST);
  const d = buildDag(steps);
  const t = nodeTask({ task: "Support ARRAY_SLICE: parse and generate it. ".repeat(80), node: d.nodes[1], total: 3, entry: { title: "support FILTER" },
                       diff: PAST, where: "Likely place: pkg/parser.py:1-3 (parse).", done: ["N-1 in pkg/expressions.py"] });
  assert.ok(t.length <= 2000);
  assert.match(t, /^Part 2 of 3 of this task/);
  assert.match(t, /This part: the change in pkg\/parser\.py/);
  assert.match(t, /Already done and in the code: N-1 in pkg\/expressions\.py/);
  assert.match(t, /Worked example[\s\S]*ArrayFilter\(\) if name/);
  assert.match(t, /Likely place: pkg\/parser\.py:1-3/);
});

// ------------------------------------------------------------------ end to end, with a stand-in executor

function gitRepo(files) {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), "dag-repo-"));
  const g = (...a) => execFileSync("git", ["-C", repo, ...a], { stdio: "pipe" });
  g("init", "-q"); g("config", "user.email", "t@e"); g("config", "user.name", "t");
  for (const [f, text] of Object.entries(files)) { fs.mkdirSync(path.dirname(path.join(repo, f)), { recursive: true }); fs.writeFileSync(path.join(repo, f), text); }
  fs.mkdirSync(path.join(repo, ".stealth"), { recursive: true }); fs.writeFileSync(path.join(repo, ".stealth", ".keep"), "");
  g("add", "-A"); g("commit", "-q", "-m", "init");
  return repo;
}

// a repo where FILTER was already done (the library entry), and the new task is SLICE in the same files
function sliceRepo() {
  const repo = gitRepo({
    "pkg/__init__.py": "",
    "pkg/expressions.py": "class Expression:\n    pass\n",
    "pkg/parser.py": "def parse(name):\n    return None\n",
  });
  const past = `diff --git a/pkg/expressions.py b/pkg/expressions.py
--- a/pkg/expressions.py
+++ b/pkg/expressions.py
@@ -1,2 +1,4 @@
 class Expression:
     pass
+class ArrayFilter(Expression):
+    pass
diff --git a/pkg/parser.py b/pkg/parser.py
--- a/pkg/parser.py
+++ b/pkg/parser.py
@@ -1,2 +1,3 @@ def parse(name):
 def parse(name):
-    return None
+    from pkg.expressions import ArrayFilter
+    return ArrayFilter() if name == "FILTER" else None
`;
  addEntry(repo, { title: "support FILTER", check: "true", observed: true, diff: past, steps: wayStepsForEntry(past) });
  // the answers the stand-in models give for SLICE: the patch of each part, made against base (+ the part before it)
  const g = (...a) => execFileSync("git", ["-C", repo, ...a], { encoding: "utf8" });
  fs.writeFileSync(path.join(repo, "pkg", "expressions.py"), "class Expression:\n    pass\nclass ArraySlice(Expression):\n    pass\n");
  const p1 = g("diff", "--", "pkg/expressions.py");
  fs.writeFileSync(path.join(repo, "pkg", "parser.py"), "def parse(name):\n    from pkg.expressions import ArraySlice\n    return ArraySlice() if name == \"SLICE\" else None\n");
  const p2 = g("diff", "--", "pkg/parser.py");
  g("checkout", "--", "pkg");
  return { repo, p1, p2 };
}

function stubRt(answers, outcomes) {
  const calls = [];
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "dag-patches-"));
  return { calls,
    async achieve(input) { calls.push(input); return { run_id: `r-${calls.length}` }; },
    async runResult(id) {
      const input = calls[Number(id.slice(2)) - 1];
      const at = /This part:.*?(?:the change in |-- in |likely in )(pkg\/\w+\.py)/.exec(input.task)?.[1];
      const part = at === "pkg/expressions.py" ? "p1" : "p2";
      if (outcomes[part] !== "verified") return { run_id: id, state: "failed", cost_usd: 0.001, checks: [{ tail: "no" }] };
      const f = path.join(dir, `${id}.patch`);
      fs.writeFileSync(f, answers[part]);
      return { run_id: id, state: "verified", cost_usd: 0.002, diff: { files: [part === "p1" ? "pkg/expressions.py" : "pkg/parser.py"], patch_path: f } };
    },
    async cancelRun() {}, async applyRun() { throw new Error("a plan applies its parts itself"); } };
}

const PLAN = { status: "ok", instance_key: "g1.k1", goal_id: "g1", ladder: ["gemma-4-31b-it|stealth"] };
function env(home) {
  fs.writeFileSync(path.join(home, "exec.json"), JSON.stringify({ executors: { stealth: { models: ["gemma-4-31b-it"] } } }));
  return { STEALTHLAB_HOME: home, STEALTHLAB_DISPATCH_CHECK: "python -c \"import pkg.expressions, pkg.parser\"", STEALTHLAB_DISPATCH_TESTFIRST: "off",
           STEALTHLAB_DISPATCH_CROSSCHECK: "off", STEALTHLAB_BRIEF: "off", STEALTHLAB_EXPLAIN: "off", STEALTHLAB_DISPATCH_MIN_QUOTA_TOKENS: "0" };
}
const PROMPT = "Support ARRAY_SLICE like FILTER: add the expression in pkg/expressions.py and parse it in pkg/parser.py";

test("a task in parts: planned from the similar past fix, part 2 starts from part 1, all parts checked together, applied", async () => {
  const { repo, p1, p2 } = sliceRepo();
  const rt = stubRt({ p1, p2 }, { p1: "verified", p2: "verified" });
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "dag-home-"));
  assert.ok(planFromLibrary({ root: repo, reply: {}, loc: { files: ["pkg/parser.py"] } }), "the FILTER fix plans the task");
  const d = await dispatch({ payload: { cwd: repo, prompt: PROMPT }, reply: { model_plan: PLAN }, root: repo, mine: "claude-opus-5-5|claude-code",
                             env: env(home), runtime: rt });
  assert.equal(d.handled, true, JSON.stringify(d.tried));
  assert.equal(d.dag.nodes.length, 2);
  assert.match(rt.calls[0].task, /^Part 1 of 2[\s\S]*class ArrayFilter/);
  assert.deepEqual(rt.calls[0].extra_patches, []);
  assert.equal(rt.calls[1].extra_patches.length, 1, "part 2 starts from part 1's change");
  assert.match(fs.readFileSync(path.join(repo, "pkg", "parser.py"), "utf8"), /ArraySlice\(\) if name == "SLICE"/);
  assert.match(fs.readFileSync(path.join(repo, "pkg", "expressions.py"), "utf8"), /class ArraySlice/);
  assert.match(d.text, /^Done in 2 parts by gemma-4-31b-it/);
  const run = fs.readFileSync(path.join(repo, ".stealth", "run.md"), "utf8");
  assert.match(run, /^NODE\|N-2\|done\|The change in pkg\/parser\.py[^\n]*\|deps=N-1\|model=gemma-4-31b-it\|check=/m);
  const v = spawnSync(process.execPath, [path.resolve("bin/stealthlab-mcp.mjs"), "plan", "validate"], { cwd: repo, encoding: "utf8" });
  assert.equal(JSON.parse(v.stdout).ok, true, v.stdout);
  assert.ok(loadLibrary(repo).entries.length >= 2, "the new fix is in the library, with its steps, for the next one");
});

test("a part the cheap models cannot do: the done parts are applied, the session's model is told which parts are its own", async () => {
  const { repo, p1, p2 } = sliceRepo();
  const rt = stubRt({ p1, p2 }, { p1: "verified", p2: "failed" });
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "dag-home-"));
  const d = await dispatch({ payload: { cwd: repo, prompt: PROMPT }, reply: { model_plan: PLAN }, root: repo, mine: "claude-opus-5-5|claude-code",
                             env: { ...env(home), STEALTHLAB_DISPATCH_TIMEOUT_S: "10" }, runtime: rt });
  assert.equal(d.handled, false);
  assert.deepEqual(d.partial.nodes.map((n) => n.status), ["done", "ready"]);
  assert.match(fs.readFileSync(path.join(repo, "pkg", "expressions.py"), "utf8"), /class ArraySlice/, "part 1 applied");
  assert.doesNotMatch(fs.readFileSync(path.join(repo, "pkg", "parser.py"), "utf8"), /SLICE/);
  const note = triedNote(d);
  assert.match(note, /split this task into 2 parts/);
  assert.match(note, /applied: N-1 \(pkg\/expressions\.py\)/);
  assert.match(note, /Yours: N-2 \(pkg\/parser\.py\)/);
  const off = sliceRepo();
  const d2 = await dispatch({ payload: { cwd: off.repo, prompt: PROMPT }, reply: { model_plan: PLAN }, root: off.repo, mine: "claude-opus-5-5|claude-code",
    env: { ...env(fs.mkdtempSync(path.join(os.tmpdir(), "dag-home-"))), STEALTHLAB_DISPATCH_DAG: "off", STEALTHLAB_DISPATCH_TIMEOUT_S: "10" },
    runtime: stubRt({ p1: off.p1, p2: off.p2 }, { p1: "failed", p2: "failed" }) });
  assert.equal(d2.partial, undefined, "switch off: the task goes whole, as before");
});

// ------------------------------------------------------------------ general Goals and Ways (lib/way_describe.mjs)

import { describeEntry, parseDescription } from "../lib/way_describe.mjs";

test("parseDescription: one step per step given, slots and invariants clipped; anything else is no description", () => {
  const ok = parseDescription('sure: {"goal":"Support a new SQL function","way":"expression, parse, generate","slots":{"function":"ARRAY_FILTER"},' +
    '"steps":["Add the {function} expression class","Parse {function}","Generate {function}"],"invariants":["other dialects unchanged"]}', 3);
  assert.equal(ok.goal, "Support a new SQL function");
  assert.deepEqual(ok.slots, { function: "ARRAY_FILTER" });
  assert.equal(parseDescription('{"goal":"x","steps":["a"]}', 3), null, "wrong number of steps");
  assert.equal(parseDescription("not json", 1), null);
});

test("two fixes of the same kind, described: one general Goal, one Way with {slot} steps, each fix keeping its own values", async () => {
  const repo = gitRepo({ "pkg/expressions.py": "class Expression:\n    pass\n" });
  const reply = (fn) => async () => ({ ok: true, json: async () => ({ choices: [{ message: { content: JSON.stringify({
    goal: "Support a new SQL function", way: "Expression class, then parser and generator", slots: { function: fn },
    steps: ["Add the {function} expression class", "Parse {function} into its expression", "Generate SQL for {function}"],
    invariants: ["existing functions unchanged"] }) } }] }) });
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "dag-home-"));
  fs.writeFileSync(path.join(home, "exec.json"), JSON.stringify({ executors: {}, profiles: { "gpt-oss-120b": { openai_base_url: "https://example.test/v1", key_env: "K", model_id: "gpt-oss-120b" } } }));
  const env = { STEALTHLAB_HOME: home, K: "k" };
  const second = PAST.replace(/ArrayFilter/g, "ArraySlice").replace(/FILTER/g, "SLICE");
  for (const [diff, fn] of [[PAST, "ARRAY_FILTER"], [second, "ARRAY_SLICE"]]) {
    const e = addEntry(repo, { title: `support ${fn}`, check: "true", observed: true, diff, steps: wayStepsForEntry(diff) });
    assert.equal(await describeEntry({ root: repo, id: e.id, task: `support ${fn}`, diff, env, fetchImpl: reply(fn) }), true);
  }
  const lib = loadLibrary(repo);
  assert.equal(lib.goals.length, 1, "one general Goal for both");
  assert.equal(lib.goals[0].title, "Support a new SQL function");
  assert.equal(lib.ways.length, 1, "one Way: the same worded steps");
  assert.equal(lib.ways[0].steps[0].do, "Add the {function} expression class");
  assert.ok(lib.ways[0].extra.includes("slots=function") && lib.ways[0].extra.some((f) => f.startsWith("inv=")));
  assert.deepEqual(lib.entries.map((e) => e.procs[0].extra.find((f) => f.startsWith("slots="))).sort(), ["slots=function:ARRAY_FILTER", "slots=function:ARRAY_SLICE"]);
  assert.deepEqual(readStepExtras(lib.ways[0].steps[1].extra).needs, ["ArrayFilter"], "the steps keep where they are and what they need");
  // the plan words each part with the Way's step and names the parameters
  const plan = planFromLibrary({ root: repo, reply: {}, loc: { files: ["pkg/parser.py"] } });
  const t = nodeTask({ task: "support ARRAY_SORT", node: plan.dag.nodes[1], total: 3, entry: plan.entry, diff: plan.diff, slots: plan.slots });
  assert.match(t, /This part: Parse \{function\} into its expression -- in pkg\/parser\.py/);
  assert.match(t, /\{function\} was ARRAY_(FILTER|SLICE); find their values for this task/);
});

// ------------------------------------------------------------------ after the session's part (stealthlab-mcp dag continue)

import { continueDag, loadDagState } from "../lib/dag_run.mjs";
import { recordArea } from "../lib/areas.mjs";
import { runShellCapture } from "../lib/dispatch.mjs";

test("a part for the session, then cheap parts after it: handed over, then `dag continue` checks yours and does the rest", async () => {
  const { repo, p1, p2 } = sliceRepo();
  for (let i = 0; i < 3; i++) recordArea(repo, "pkg/expressions.py", false);       // cheap models keep losing there: yours
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "dag-home-"));
  const rt = stubRt({ p1, p2 }, { p1: "verified", p2: "verified" });
  const d = await dispatch({ payload: { cwd: repo, prompt: PROMPT }, reply: { model_plan: PLAN }, root: repo, mine: "claude-opus-5-5|claude-code",
                             env: { ...env(home), STEALTHLAB_DISPATCH_AREA_SKIP: "off" }, runtime: rt });
  assert.equal(d.handled, false);
  assert.deepEqual(d.partial.nodes.map((n) => [n.id, n.status]), [["N-1", "ready"], ["N-2", "waiting"]]);
  assert.equal(rt.calls.length, 0, "nothing ran yet: the first part is the session's, the second waits for it");
  const note = triedNote(d);
  assert.match(note, /Yours: N-1 \(pkg\/expressions\.py\)/);
  assert.match(note, /Do only your part; then run `node "[^`]*stealthlab-mcp\.mjs" dag continue`[^.]*has cheap models do N-2 \(pkg\/parser\.py\)/);
  assert.ok(loadDagState(repo));
  // the session does its part
  execFileSync("git", ["-C", repo, "apply", "-"], { input: p1 });
  const out = await continueDag({ root: repo, env: env(home), rt, runShell: runShellCapture, undo: { enabled: false } });
  assert.equal(out.ok, true, out.lines.join("\n"));
  assert.match(out.lines.join("\n"), /Your part \(N-1\) passes[\s\S]*N-2 \(pkg\/parser\.py\): done by gemma-4-31b-it and applied[\s\S]*full check passes/);
  assert.match(fs.readFileSync(path.join(repo, "pkg", "parser.py"), "utf8"), /ArraySlice\(\) if name == "SLICE"/);
  assert.equal(loadDagState(repo), null, "the plan is finished: its state is gone");
  assert.match(rt.calls[0].task, /Already done and in the code: N-1 in pkg\/expressions\.py/);
});

test("dag continue refuses to go on while the session's part does not pass", async () => {
  const { repo, p1, p2 } = sliceRepo();
  for (let i = 0; i < 3; i++) recordArea(repo, "pkg/expressions.py", false);
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "dag-home-"));
  const rt = stubRt({ p1, p2 }, { p1: "verified", p2: "verified" });
  await dispatch({ payload: { cwd: repo, prompt: PROMPT }, reply: { model_plan: PLAN }, root: repo, mine: "claude-opus-5-5|claude-code",
                   env: { ...env(home), STEALTHLAB_DISPATCH_AREA_SKIP: "off" }, runtime: rt });
  fs.writeFileSync(path.join(repo, "pkg", "expressions.py"), "class Expression(:\n");      // a broken part
  const out = await continueDag({ root: repo, env: env(home), rt, runShell: runShellCapture, undo: { enabled: false } });
  assert.equal(out.ok, false);
  assert.match(out.lines[0], /Your part \(N-1\) does not pass the check yet: nothing else was run/);
  assert.equal(rt.calls.length, 0);
});

test("each part's outcome counts for its model on the task's route (routing.md OBS), as a whole-task try does", async () => {
  const { repo, p1, p2 } = sliceRepo();
  const rt = stubRt({ p1, p2 }, { p1: "verified", p2: "verified" });
  const d = await dispatch({ payload: { cwd: repo, prompt: PROMPT }, reply: { model_plan: PLAN }, root: repo, mine: "claude-opus-5-5|claude-code",
                             env: env(fs.mkdtempSync(path.join(os.tmpdir(), "dag-home-"))), runtime: rt });
  assert.equal(d.handled, true);
  const routing = fs.readFileSync(path.join(repo, ".stealth", "routing.md"), "utf8");
  assert.match(routing, /^OBS\|R-[0-9a-f]+\|gemma-4-31b-it\|stealth\|n=2\|ok=2\|/m, routing);
});

// ------------------------------------------------------------------ the request's own parts (no similar fix yet)

test("no similar fix, but the request asks for two things: one part each, located on its own, run on cheap models", async () => {
  const repo = gitRepo({ "pkg/__init__.py": "", "pkg/expressions.py": "class Expression:\n    pass\n", "pkg/parser.py": "def parse(name):\n    return None\n" });
  const g = (...a) => execFileSync("git", ["-C", repo, ...a], { encoding: "utf8" });
  fs.writeFileSync(path.join(repo, "pkg", "expressions.py"), "class Expression:\n    pass\nclass ArraySlice(Expression):\n    pass\n");
  const p1 = g("diff", "--", "pkg/expressions.py");
  fs.writeFileSync(path.join(repo, "pkg", "parser.py"), "def parse(name):\n    from pkg.expressions import ArraySlice\n    return ArraySlice() if name == \"SLICE\" else None\n");
  const p2 = g("diff", "--", "pkg/parser.py");
  g("checkout", "--", "pkg");
  const parts = ["Add an ArraySlice class in pkg/expressions.py", "Make parse in pkg/parser.py return ArraySlice() for SLICE"];
  const brief = async () => ({ task: "ArraySlice", files_hinted: [], acceptance: "", parts, clarity: "clear", kind: "code", size: "medium" });
  const rt = stubRt({ p1, p2 }, { p1: "verified", p2: "verified" });
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "dag-home-"));
  const d = await dispatch({ payload: { cwd: repo, prompt: "add ArraySlice and parse SLICE into it" }, reply: { model_plan: PLAN }, root: repo,
    mine: "claude-opus-5-5|claude-code", env: { ...env(home), STEALTHLAB_BRIEF: "on" }, runtime: rt, briefImpl: brief });
  assert.equal(d.handled, true, JSON.stringify(d.tried || d));
  assert.equal(d.dag.entry, "request");
  assert.match(rt.calls[0].task, /This part: Add an ArraySlice class in pkg\/expressions\.py \(likely in pkg\/expressions\.py\)/);
  assert.doesNotMatch(rt.calls[0].task, /Worked example/, "no past fix: no example");
  assert.match(d.text, /split the request into the things it asks for/);
  assert.match(fs.readFileSync(path.join(repo, "pkg", "parser.py"), "utf8"), /SLICE/);
  assert.match(fs.readFileSync(path.join(repo, ".stealth", "run.md"), "utf8"), /# plan: the request's own parts/);
});

test("Claude Code's own messages in the prompt stream are not tasks: no lookup, no dispatch, no library title", async () => {
  const { shouldLookUp, isHarnessMessage } = await import("../lib/hook.mjs");
  const policy = { enabled: true, minWords: 3 };
  assert.equal(isHarnessMessage("<task-notification>\n<task-id>b1</task-id> completed</task-notification>"), true);
  assert.equal(shouldLookUp("<task-notification><task-id>b1</task-id><status>completed</status> the tests passed</task-notification>", policy), false);
  assert.equal(shouldLookUp("  <system-reminder>note</system-reminder> fix the parser please", policy), false);
  assert.equal(shouldLookUp("fix the <task-notification> handling in the parser", policy), true, "a real prompt that mentions the tag");
});

test("a past fix plans a task only when it changed the code the task is located in, whatever the lookup calls it", () => {
  const { repo } = sliceRepo();
  const reply = { library_matches: loadLibrary(repo).entries.map((e) => ({ id: e.id, relation: "related" })) };
  assert.equal(planFromLibrary({ root: repo, reply, loc: { files: ["pkg/tsql.py", "pkg/other.py"] } }), null, "located elsewhere: the task goes whole");
  assert.ok(planFromLibrary({ root: repo, reply, loc: { files: ["pkg/parser.py"] } }), "located in a file the past fix changed");
});
