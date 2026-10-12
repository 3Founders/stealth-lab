// The agreement check: two cheap answers that both pass must also AGREE before either is applied.
//
// A cheap answer that passes the project's check and the test-first tests can still be wrong where those tests do not look (4 BIRD and
// 2 DS-1000 answers in phase 1: accepted, then failed the hidden grade). When a SECOND cheap answer passes too -- while the first is still
// being judged, or within 10 s of it -- the two are run against the same inputs and compared. Both pass the tests, so any difference means
// at least one of them is wrong somewhere the tests do not reach; dispatch then applies neither and the task goes up the ladder.
//
// The inputs: for every function the test-first file calls that the answers define (top-level functions in the files they changed), the
// literal arguments the tests use are the seeds; a `hypothesis` strategy is inferred from their types (integers, floats, text, booleans,
// lists, tuples, dicts) and a deterministic set of examples is drawn (`derandomize`, so both answers see the same ones). Each answer is
// run in its own worktree in its own process; the return value and the arguments after the call are compared, and so is the type of any
// exception. Python projects with a pytest check only. No model is called; nothing here costs money.
//
// "inconclusive" (the answer is accepted, as before) when there is nothing to compare: hypothesis is not installed, no function the tests
// call is defined by the answers, an argument whose type cannot be inferred, or either answer cannot be run. STEALTHLAB_DISPATCH_AGREE=off
// turns the check off; STEALTHLAB_DISPATCH_AGREE_WAIT_S (default 10) is how long a second pass is waited for;
// STEALTHLAB_DISPATCH_AGREE_EXAMPLES (default 40) is the examples per function.
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

export const agreeEnabled = (env = process.env) => (env.STEALTHLAB_DISPATCH_AGREE || "on").toLowerCase() !== "off";
export const agreeWaitS = (env = process.env) => Math.max(0, Number(env.STEALTHLAB_DISPATCH_AGREE_WAIT_S ?? 10));

export const AGREE_PY = String.raw`import ast, copy, importlib, importlib.util, json, math, os, sys

def top_functions(path):
    try:
        tree = ast.parse(open(path, encoding="utf-8").read())
    except Exception:
        return {}
    out = {}
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and not n.name.startswith("_"):
            a = n.args
            if a.vararg or a.kwarg or a.kwonlyargs:
                continue
            out[n.name] = len(a.args) - len(a.defaults), len(a.args)
    return out

def test_calls(path):
    tree = ast.parse(open(path, encoding="utf-8").read())
    calls = {}
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call) or n.keywords:
            continue
        f = n.func
        name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
        if not name:
            continue
        try:
            args = [ast.literal_eval(x) for x in n.args]
        except Exception:
            continue
        calls.setdefault(name, []).append(args)
    return calls

def strategy(st, values):
    vs = [v for v in values if v is not None]
    if not vs:
        return st.none()
    kinds = {type(v) for v in vs}
    if kinds == {bool}:
        s = st.booleans()
    elif kinds <= {int}:
        s = st.integers(-50, 50)
    elif kinds <= {int, float}:
        s = st.floats(-100, 100, allow_nan=False, allow_infinity=False)
    elif kinds == {str}:
        s = st.text(alphabet=st.characters(min_codepoint=32, max_codepoint=126), max_size=12)
    elif kinds == {list}:
        inner = [x for v in vs for x in v]
        s = st.lists(strategy(st, inner) if inner else st.integers(-50, 50), max_size=6)
    elif kinds == {tuple}:
        width = {len(v) for v in vs}
        if len(width) != 1:
            return None
        parts = [strategy(st, [v[i] for v in vs]) for i in range(width.pop())]
        if any(p is None for p in parts):
            return None
        s = st.tuples(*parts)
    elif kinds == {dict}:
        inner = [x for v in vs for x in v.values()]
        s = st.dictionaries(st.text(alphabet=st.characters(min_codepoint=97, max_codepoint=122), max_size=4),
                            strategy(st, inner) if inner else st.integers(-50, 50), max_size=4)
    else:
        return None
    if s is None:
        return None
    return st.one_of(st.none(), s) if len(vs) != len(values) else s

def gen(wt, files, spec, count, out):
    try:
        from hypothesis import HealthCheck, given, settings, strategies as st
    except Exception as e:
        json.dump({"error": "hypothesis is not installed"}, open(out, "w"))
        return
    defined = {}
    for f in files:
        defined.update(top_functions(os.path.join(wt, f)))
    seeds = test_calls(spec)
    targets = {}
    for name, calls in seeds.items():
        if name not in defined:
            continue
        lo, hi = defined[name]
        calls = [c for c in calls if lo <= len(c) <= hi]
        if not calls:
            continue
        arity = len(calls[0])
        calls = [c for c in calls if len(c) == arity]
        strats = [strategy(st, [c[i] for c in calls]) for i in range(arity)]
        if arity == 0 or any(s is None for s in strats):
            continue
        drawn = []
        @settings(max_examples=count, derandomize=True, database=None, deadline=None, suppress_health_check=list(HealthCheck))
        @given(st.tuples(*strats))
        def collect(args):
            drawn.append(list(args))
        try:
            collect()
        except Exception:
            continue
        seen, inputs = set(), []
        for a in calls + drawn:
            r = repr(a)
            if r not in seen:
                seen.add(r)
                inputs.append(r)
        targets[name] = inputs[: count + len(calls)]
    json.dump({"targets": targets}, open(out, "w"))

def norm(v):
    if isinstance(v, float):
        return "nan" if v != v else repr(round(v, 9) + 0.0)
    if isinstance(v, (list, tuple)):
        return type(v).__name__ + "[" + ",".join(norm(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{" + ",".join(sorted(norm(k) + ":" + norm(x) for k, x in v.items())) + "}"
    if isinstance(v, (set, frozenset)):
        return "set{" + ",".join(sorted(norm(x) for x in v)) + "}"
    return repr(v)

def load_function(wt, files, name):
    for f in files:
        rel = f[:-3].replace("\\", "/").replace("/", ".")
        try:
            mod = importlib.import_module(rel)
        except Exception:
            try:
                spec = importlib.util.spec_from_file_location(rel, os.path.join(wt, f))
                mod = importlib.util.module_from_spec(spec)
                sys.modules[rel] = mod
                spec.loader.exec_module(mod)
            except Exception:
                continue
        fn = getattr(mod, name, None)
        if callable(fn):
            return fn
    return None

def run(wt, files, inputs_path, out):
    os.chdir(wt)
    sys.path.insert(0, wt)
    plan = json.load(open(inputs_path))["targets"]
    result = {}
    for name, inputs in plan.items():
        fn = load_function(wt, files, name)
        if fn is None:
            result[name] = None
            continue
        rows = []
        for s in inputs:
            args = copy.deepcopy(ast.literal_eval(s))
            try:
                r = fn(*args)
                rows.append(["ok", norm(r), norm(args)])
            except BaseException as e:
                rows.append(["err", type(e).__name__])
        result[name] = rows
    json.dump(result, open(out, "w"))

if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "gen":
        gen(sys.argv[2], json.loads(sys.argv[3]), sys.argv[4], int(sys.argv[5]), sys.argv[6])
    else:
        run(sys.argv[2], json.loads(sys.argv[3]), sys.argv[4], sys.argv[5])
`;

export function writeAgreeScript(dir) {
  fs.mkdirSync(dir, { recursive: true });
  const file = path.join(dir, "agree.py");
  fs.writeFileSync(file, AGREE_PY);
  return file;
}

const py = (args, { cwd, timeoutMs }) => spawnSync("python", args, { cwd, encoding: "utf8", timeout: timeoutMs, windowsHide: true });

// Is there anything to compare? The answer changed a Python file that defines a top-level function. A SQL query, a code snippet that
// assigns a variable, a page or a JavaScript project has none, and then a second pass is not waited for at all.
export function definesFunctions({ worktree, files }) {
  for (const f of (files || []).filter((x) => /\.py$/.test(x))) {
    try { if (/^def [A-Za-z][A-Za-z0-9_]*\(/m.test(fs.readFileSync(path.join(worktree, f), "utf8"))) return true; } catch { /* unreadable */ }
  }
  return false;
}

// rows of two runs of the same inputs -> the first difference, or null
export function firstDifference(a, b) {
  for (const name of Object.keys(a)) {
    const x = a[name], y = b[name];
    if (!x || !y) continue;
    for (let i = 0; i < Math.min(x.length, y.length); i++) {
      if (JSON.stringify(x[i]) !== JSON.stringify(y[i])) return { function: name, index: i, a: x[i], b: y[i] };
    }
  }
  return null;
}

// -> { verdict: "agree" | "disagree" | "inconclusive", reason?, compared?, difference?: {function, input, a, b} }
//   a, b: { worktree, files }  (the .py files each answer changed); specFile: the test-first pytest file
export function agreeCheck({ a, b, specFile, env = process.env, timeoutMs = 120000 }) {
  try {
    const pyFiles = (x) => (x.files || []).filter((f) => /\.py$/.test(f));
    const filesA = pyFiles(a);
    if (!filesA.length || !pyFiles(b).length) return { verdict: "inconclusive", reason: "the answers changed no Python file" };
    if (!specFile || !fs.existsSync(specFile)) return { verdict: "inconclusive", reason: "no test-first file to take the calls from" };
    const dir = path.join(path.dirname(specFile), "agree");
    const script = writeAgreeScript(dir);
    const examples = Math.max(5, Number(env.STEALTHLAB_DISPATCH_AGREE_EXAMPLES) || 40);
    const inputs = path.join(dir, "inputs.json");
    let r = py([script, "gen", a.worktree, JSON.stringify(filesA), specFile, String(examples), inputs], { cwd: a.worktree, timeoutMs });
    if (r.status !== 0 || !fs.existsSync(inputs)) return { verdict: "inconclusive", reason: `could not make inputs (${String(r.stderr || "").trim().split("\n").pop()?.slice(0, 120) || "exit " + r.status})` };
    const plan = JSON.parse(fs.readFileSync(inputs, "utf8"));
    if (plan.error) return { verdict: "inconclusive", reason: plan.error };
    if (!Object.keys(plan.targets || {}).length) return { verdict: "inconclusive", reason: "no function the tests call is defined by the answers" };
    const outs = [];
    for (const [i, x] of [a, b].entries()) {
      const out = path.join(dir, `out-${i}.json`);
      r = py([script, "run", x.worktree, JSON.stringify(pyFiles(x)), inputs, out], { cwd: x.worktree, timeoutMs });
      if (r.status !== 0 || !fs.existsSync(out)) return { verdict: "inconclusive", reason: `an answer could not be run (${String(r.stderr || "").trim().split("\n").pop()?.slice(0, 120) || "exit " + r.status})` };
      outs.push(JSON.parse(fs.readFileSync(out, "utf8")));
    }
    const compared = Object.keys(plan.targets).reduce((n, k) => (outs[0][k] && outs[1][k] ? n + Math.min(outs[0][k].length, outs[1][k].length) : n), 0);
    if (compared < 5) return { verdict: "inconclusive", reason: `only ${compared} inputs could be compared` };
    const d = firstDifference(outs[0], outs[1]);
    if (!d) return { verdict: "agree", compared };
    return { verdict: "disagree", compared, difference: { function: d.function, input: plan.targets[d.function][d.index].slice(0, 200), a: d.a, b: d.b } };
  } catch (err) {
    return { verdict: "inconclusive", reason: `error: ${String(err.message).slice(0, 120)}` };
  }
}
