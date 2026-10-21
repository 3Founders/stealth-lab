"use client";
import { useEffect, useRef, useState } from "react";

// The homepage demo: a replay of what the developer sees in their terminal for three example tasks, with what each
// costs beside it and what the repository's .stealth folder remembers afterwards. It shows outcomes only (who did the
// task, whether it passed the project's checks, what it cost), never how keळ decides. Everything here is made up.

type Kind = "prompt" | "kel" | "diff-del" | "diff-add" | "file" | "run" | "ok" | "fail" | "model" | "done";
type Line = { at: number; kind: Kind; text: string };
type Scenario = {
  label: string;
  agent: string;
  lines: Line[];
  // keळ's cost as a share of "your model alone", as [time in ms, percent] points; linear in between.
  cost: [number, number][];
  outcome: string;
  // What .stealth already holds from earlier tasks, beyond stealthBase, and what it gains at the end of the run.
  earlier?: { file: string; entry: string };
  remembered: { file: string; entry: string };
};

const PROMPT_MS = 1600; // the prompt types out over this long; every other line appears at its `at`
const DONE_PAD = 900;

const scenarios: Scenario[] = [
  {
    label: "A bug fix",
    agent: "Claude Code",
    lines: [
      { at: 0, kind: "prompt", text: "CSV export drops the last row when the file has no trailing newline. Fix it." },
      { at: 2100, kind: "kel", text: "keळ is on it, starting from 4 notes in .stealth" },
      { at: 3300, kind: "file", text: "src/export/csv.py" },
      { at: 3500, kind: "diff-del", text: '-    rows = data.split("\\n")[:-1]' },
      { at: 3700, kind: "diff-add", text: "+    rows = data.splitlines()" },
      { at: 4600, kind: "run", text: "running your tests: pytest" },
      { at: 6000, kind: "ok", text: "212 passed in 8.4s" },
      { at: 6800, kind: "done", text: "✓ applied by keळ · undo any time: stealthlab-mcp undo" },
    ],
    cost: [[0, 0], [2100, 2], [6800, 18]],
    outcome: "Done for a fraction of the cost.",
    remembered: { file: "library.md", entry: "CSV export dropped its last row → splitlines(); checked by pytest" },
  },
  {
    label: "A harder task",
    agent: "Claude Code",
    lines: [
      { at: 0, kind: "prompt", text: "Add rate limiting to the login endpoint: 5 attempts per minute per IP." },
      { at: 2100, kind: "kel", text: "keळ is on it, starting from 2 notes in .stealth" },
      { at: 3600, kind: "run", text: "running your tests: pytest" },
      { at: 5000, kind: "fail", text: "2 failed, 38 passed" },
      { at: 5800, kind: "kel", text: "keळ: not good enough, so nothing is applied. Over to your model." },
      { at: 6900, kind: "model", text: "I'll add a per-IP limiter to the login route and a test for it." },
      { at: 8100, kind: "file", text: "src/auth/routes.py  src/auth/limits.py  tests/test_login_limit.py" },
      { at: 9000, kind: "run", text: "running your tests: pytest" },
      { at: 10300, kind: "ok", text: "41 passed in 9.1s" },
      { at: 11000, kind: "done", text: "✓ done by your model, exactly as without keळ" },
    ],
    cost: [[0, 0], [2100, 2], [5800, 9], [11000, 108]],
    outcome: "Same result as without keळ, for slightly more.",
    remembered: { file: "routing.md", entry: "auth changes in this repo → leave to your model" },
  },
  {
    label: "Next week",
    agent: "Cursor",
    lines: [
      { at: 0, kind: "prompt", text: "The JSON export has the same problem with the last record." },
      { at: 2000, kind: "kel", text: "keळ is on it, starting from last week's CSV fix in .stealth" },
      { at: 2900, kind: "file", text: "src/export/json.py" },
      { at: 3100, kind: "diff-del", text: '-    records = body.split("\\n")[:-1]' },
      { at: 3300, kind: "diff-add", text: "+    records = body.splitlines()" },
      { at: 4000, kind: "run", text: "running your tests: pytest" },
      { at: 5300, kind: "ok", text: "214 passed in 8.9s" },
      { at: 6000, kind: "done", text: "✓ applied by keळ · added to .stealth for next time" },
    ],
    cost: [[0, 0], [2000, 1], [6000, 9]],
    outcome: "Cheaper each time a task repeats.",
    earlier: { file: "library.md", entry: "CSV export dropped its last row → splitlines(); checked by pytest" },
    remembered: { file: "library.md", entry: "JSON export dropped its last record → splitlines(); checked by pytest" },
  },
];

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

  return (
    <div className="demo" ref={root}>
      <div className="demo-tabs" role="tablist" aria-label="Example tasks">
        {scenarios.map((s, k) => (
          <button key={s.label} role="tab" aria-selected={k === si} onClick={() => pick(k)}>
            <span>{String(k + 1).padStart(2, "0")}</span>{s.label}
          </button>
        ))}
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
              const added = finished && sc.remembered.file === f.file;
              return (
                <div key={f.file} className="stealth-file">
                  <b>{f.file}</b>
                  {f.lines.map((l) => <span key={l}>{l}</span>)}
                  {sc.earlier?.file === f.file && <span>{sc.earlier.entry}</span>}
                  {added && <span className="stealth-new">+ {sc.remembered.entry}</span>}
                  {!f.lines.length && !added && sc.earlier?.file !== f.file && <span className="dim">nothing yet</span>}
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
