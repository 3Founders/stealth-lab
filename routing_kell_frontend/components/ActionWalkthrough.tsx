"use client";
import { useEffect, useRef, useState } from "react";

// The homepage demo: a replay of what the developer sees in their terminal for four example tasks, with what each
// costs beside it and what the repository's .stealth folder remembers afterwards. It shows outcomes only (who did the
// task, whether it passed the project's checks, what it cost), never how keळ decides. Everything here is made up.

type Kind = "prompt" | "kel" | "diff-del" | "diff-add" | "file" | "run" | "ok" | "fail" | "model" | "done";
// The headline's words plus the check: the strip above the terminal lights each one as the replay reaches it.
type Word = "recall" | "reuse" | "route" | "tests";
type Line = { at: number; kind: Kind; text: string; r?: Word[] };
type Entry = { file: string; entry: string };
type Scenario = {
  label: string;
  agent: string;
  lines: Line[];
  // keळ's cost as a share of "your model alone", as [time in ms, percent] points; linear in between.
  cost: [number, number][];
  outcome: string;
  // What .stealth already holds from earlier tasks, beyond stealthBase, and what it gains at the end of the run.
  earlier?: Entry[];
  remembered?: Entry;
};

const PROMPT_MS = 1600; // the prompt types out over this long; every other line appears at its `at`
const DONE_PAD = 900;

const CSV_FIX = "CSV export dropped its last row → splitlines(); checked by pytest";
const AUTH_ROUTE = "auth changes in this repo → leave to your model";

const scenarios: Scenario[] = [
  {
    label: "A bug fix",
    agent: "Claude Code",
    lines: [
      { at: 0, kind: "prompt", text: "CSV export drops the last row when the file has no trailing newline. Fix it." },
      { at: 2000, kind: "kel", text: "from .stealth: exports live in src/export/; tests run with pytest from the repo root", r: ["recall"] },
      { at: 2900, kind: "kel", text: "→ handed to a cheaper model", r: ["route"] },
      { at: 3700, kind: "file", text: "src/export/csv.py" },
      { at: 3900, kind: "diff-del", text: '-    rows = data.split("\\n")[:-1]' },
      { at: 4100, kind: "diff-add", text: "+    rows = data.splitlines()" },
      { at: 4800, kind: "run", text: "running your tests: pytest, from the repo root" },
      { at: 6200, kind: "ok", text: "212 passed in 8.4s", r: ["tests"] },
      { at: 7000, kind: "done", text: "✓ applied by keळ · undo any time: stealthlab-mcp undo" },
    ],
    cost: [[0, 0], [2900, 2], [7000, 18]],
    outcome: "Done by a cheaper model, checked by your tests.",
    remembered: { file: "library.md", entry: CSV_FIX },
  },
  {
    label: "A harder task",
    agent: "Claude Code",
    lines: [
      { at: 0, kind: "prompt", text: "Add rate limiting to the login endpoint: 5 attempts per minute per IP." },
      { at: 2000, kind: "kel", text: "from .stealth: auth lives in src/auth/; tests run with pytest", r: ["recall"] },
      { at: 2900, kind: "kel", text: "→ handed to a cheaper model", r: ["route"] },
      { at: 4200, kind: "run", text: "running your tests: pytest" },
      { at: 5600, kind: "fail", text: "2 failed, 38 passed", r: ["tests"] },
      { at: 6300, kind: "kel", text: "keळ: the checks failed, so nothing is applied → over to your model", r: ["route"] },
      { at: 7300, kind: "model", text: "I'll add a per-IP limiter to the login route and a test for it." },
      { at: 8500, kind: "file", text: "src/auth/routes.py  src/auth/limits.py  tests/test_login_limit.py" },
      { at: 9300, kind: "run", text: "running your tests: pytest" },
      { at: 10600, kind: "ok", text: "41 passed in 9.1s" },
      { at: 11300, kind: "done", text: "✓ done by your model, exactly as without keळ" },
    ],
    cost: [[0, 0], [2900, 2], [6300, 9], [11300, 108]],
    outcome: "Same result as without keळ, for slightly more. keळ remembers to skip this kind of task.",
    remembered: { file: "routing.md", entry: AUTH_ROUTE },
  },
  {
    label: "Next week",
    agent: "Cursor",
    lines: [
      { at: 0, kind: "prompt", text: "The JSON export has the same problem with the last record." },
      { at: 2000, kind: "kel", text: "from .stealth: last week's CSV fix, and the test that proved it", r: ["recall", "reuse"] },
      { at: 2800, kind: "kel", text: "→ handed to a cheaper model", r: ["route"] },
      { at: 3500, kind: "file", text: "src/export/json.py" },
      { at: 3700, kind: "diff-del", text: '-    records = body.split("\\n")[:-1]' },
      { at: 3900, kind: "diff-add", text: "+    records = body.splitlines()" },
      { at: 4600, kind: "run", text: "running your tests: pytest" },
      { at: 5900, kind: "ok", text: "214 passed in 8.9s", r: ["tests"] },
      { at: 6600, kind: "done", text: "✓ applied by keळ · added to .stealth for next time" },
    ],
    cost: [[0, 0], [2800, 1], [6600, 9]],
    outcome: "Cheaper each time a task repeats.",
    earlier: [{ file: "library.md", entry: CSV_FIX }],
    remembered: { file: "library.md", entry: "JSON export dropped its last record → splitlines(); checked by pytest" },
  },
  {
    label: "Auth again",
    agent: "Claude Code",
    lines: [
      { at: 0, kind: "prompt", text: "Lock an account for 15 minutes after 10 failed logins." },
      { at: 2000, kind: "kel", text: "from .stealth: auth changes in this repo are better left to your model", r: ["recall"] },
      { at: 2700, kind: "kel", text: "→ straight to your model: no cheaper attempt, nothing wasted", r: ["route"] },
      { at: 3700, kind: "model", text: "I'll add a lockout counter next to the rate limiter, with a test." },
      { at: 5000, kind: "file", text: "src/auth/limits.py  tests/test_lockout.py" },
      { at: 5900, kind: "run", text: "running your tests: pytest" },
      { at: 7200, kind: "ok", text: "43 passed in 9.3s", r: ["tests"] },
      { at: 7900, kind: "done", text: "✓ done by your model, with no detour" },
    ],
    cost: [[0, 0], [2700, 1], [7900, 101]],
    outcome: "Sent straight to your model: no failed attempt to pay for.",
    earlier: [{ file: "routing.md", entry: AUTH_ROUTE }],
  },
];

const WORDS: [Word | "reduce", string][] = [["recall", "Recall"], ["reuse", "Reuse"], ["route", "Route"], ["reduce", "Reduce"], ["tests", "Tests decide"]];

// What .stealth already holds before the demo's task (the same made-up repository throughout).
const stealthBase: { file: string; lines: string[] }[] = [
  { file: "claims.md", lines: ["Exports live in src/export/ (csv.py, json.py)", "Tests: pytest, run from the repository root"] },
  { file: "library.md", lines: ["Date filter off by one day → inclusive end date"] },
  { file: "routing.md", lines: [] },
];

const endOf = (s: Scenario) => s.lines[s.lines.length - 1].at + DONE_PAD;
const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function costAt(s: Scenario, t: number): number {
  const pts = s.cost;
  if (t <= pts[0][0]) return pts[0][1];
  for (let k = 1; k < pts.length; k++) {
    const [t1, c1] = pts[k];
    const [t0, c0] = pts[k - 1];
    if (t <= t1) return c0 + ((c1 - c0) * (t - t0)) / (t1 - t0);
  }
  return pts[pts.length - 1][1];
}

export default function ActionWalkthrough() {
  const [si, setSi] = useState(0);
  const [t, setT] = useState(0);
  const [playing, setPlaying] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const started = useRef(false);
  const sc = scenarios[si];
  const end = endOf(sc);
  const finished = t >= end;

  // Start the first time the demo scrolls into view; with reduced motion, show each run's end state at once.
  useEffect(() => {
    if (reducedMotion()) { setT(Number.MAX_SAFE_INTEGER); return; }
    const el = root.current;
    if (!el) return;
    const io = new IntersectionObserver(([e]) => {
      if (e.isIntersecting && !started.current) { started.current = true; setPlaying(true); }
    }, { threshold: 0.35 });
    io.observe(el);
    return () => io.disconnect();
  }, []);

  useEffect(() => {
    if (!playing) return;
    let last = performance.now();
    let raf = requestAnimationFrame(function tick(now) {
      setT((x) => x + (now - last));
      last = now;
      raf = requestAnimationFrame(tick);
    });
    return () => cancelAnimationFrame(raf);
  }, [playing]);

  useEffect(() => { if (finished) setPlaying(false); }, [finished]);

  const pick = (k: number) => {
    setSi(k);
    const reduced = reducedMotion();
    setT(reduced ? Number.MAX_SAFE_INTEGER : 0);
    setPlaying(!reduced);
  };
  const replay = () => { setT(0); setPlaying(true); };

  const shown = sc.lines.filter((l) => l.at <= t);
  const prompt = sc.lines[0].text;
  const typed = prompt.slice(0, Math.round((Math.min(t, PROMPT_MS) / PROMPT_MS) * prompt.length));
  const alone = Math.min(t / end, 1) * 100;
  const withKel = costAt(sc, Math.min(t, end));
  const busy = !finished && t > PROMPT_MS;
  const lit = new Set(shown.flatMap((l) => l.r || []));

  return (
    <div className="demo" ref={root}>
      <div className="demo-tabs" role="tablist" aria-label="Example tasks">
        {scenarios.map((s, k) => (
          <button key={s.label} role="tab" aria-selected={k === si} onClick={() => pick(k)}>
            <span>{String(k + 1).padStart(2, "0")}</span>{s.label}
          </button>
        ))}
      </div>

      <div className="rstrip" aria-label="What this task used">
        {WORDS.map(([w, label]) => {
          const on = w === "reduce" ? finished && withKel < 100 : lit.has(w);
          return <span key={w} data-on={on} data-word={w}>{w === "tests" ? "✓ " : ""}{label}</span>;
        })}
      </div>

      <div className="demo-body">
        <div className="term" aria-label={`${sc.agent} terminal, replay`}>
          <div className="term-bar">
            <i /><i /><i />
            <span>{sc.agent} · your-repo</span>
          </div>
          <div className="term-screen" aria-live="polite">
            <div className="term-line" data-kind="prompt"><span className="term-p">&gt;</span> {finished ? prompt : typed}{t < PROMPT_MS && <span className="term-caret" />}</div>
            {shown.slice(1).map((l) => (
              <div key={l.at} className="term-line" data-kind={l.kind}>
                {(l.kind === "kel" || l.kind === "run") && <span className="term-p">·</span>}
                {l.kind === "model" && <span className="term-p">your model:</span>}
                {l.text}
              </div>
            ))}
            {busy && <div className="term-line"><span className="term-spin" aria-hidden="true" /></div>}
          </div>
          <div className="term-controls">
            {finished ? (
              <button type="button" onClick={replay}>Replay</button>
            ) : (
              <button type="button" onClick={() => setPlaying(!playing)}>{playing ? "Pause" : "Play"}</button>
            )}
            {finished && si < scenarios.length - 1 && <button type="button" onClick={() => pick(si + 1)}>Next task →</button>}
          </div>
        </div>

        <div className="demo-side">
          <div className="demo-cost" aria-label="What this task costs">
            <small className="caption dim">What this task costs</small>
            <div><span>Your model alone</span><i style={{ width: `${alone / 1.1}%` }} /><b>{Math.round(alone)}%</b></div>
            <div data-k><span>With keळ</span><i style={{ width: `${withKel / 1.1}%` }} /><b>{Math.round(withKel)}%</b></div>
            <p className="small" style={{ minHeight: "1.5em" }}>{finished ? sc.outcome : " "}</p>
          </div>

          <div className="stealth-peek" aria-label="Your repository's .stealth folder">
            <small className="caption dim">your-repo/.stealth</small>
            {stealthBase.map((f) => {
              const added = finished && sc.remembered?.file === f.file;
              const before = (sc.earlier || []).filter((e) => e.file === f.file);
              return (
                <div key={f.file} className="stealth-file">
                  <b>{f.file}</b>
                  {f.lines.map((l) => <span key={l}>{l}</span>)}
                  {before.map((e) => <span key={e.entry}>{e.entry}</span>)}
                  {added && <span className="stealth-new">+ {sc.remembered!.entry}</span>}
                  {!f.lines.length && !added && !before.length && <span className="dim">nothing yet</span>}
                </div>
              );
            })}
          </div>
        </div>
      </div>

      <p className="action-note">Illustrative replay, not a live run. The repository, files, test counts and costs are made up for the demo.</p>
    </div>
  );
}
