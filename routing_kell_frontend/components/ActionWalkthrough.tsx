"use client";
import { useEffect, useRef, useState } from "react";

// The homepage demo: a replay of what the developer sees in their terminal for four example tasks, with what each
// costs beside it and what the developer gets out of it. It shows outcomes only (who did the
// task, whether it passed the project's checks, what it cost), never how keळ decides. Everything here is made up.

type Kind = "prompt" | "kel" | "diff-del" | "diff-add" | "file" | "run" | "ok" | "fail" | "model" | "done";
// The headline's words plus the check: the strip above the terminal lights each one as the replay reaches it.
type Word = "recall" | "reuse" | "route" | "tests";
type Line = { at: number; kind: Kind; text: string; r?: Word[] };
type Scenario = {
  label: string;
  agent: string;
  lines: Line[];
  // keळ's cost as a share of "your model alone", as [time in ms, percent] points; linear in between.
  cost: [number, number][];
  outcome: string;
  // What the developer gets out of the task, shown beside the cost once it ends.
  doneBy: string;
  next: string;
};

const PROMPT_MS = 1600; // the prompt types out over this long; every other line appears at its `at`
const DONE_PAD = 900;


const scenarios: Scenario[] = [
  {
    label: "A bug fix",
    doneBy: "a cheaper model",
    next: "this fix comes back when a similar bug does",
    agent: "Claude Code",
    lines: [
      { at: 0, kind: "prompt", text: "CSV export drops the last row when the file has no trailing newline. Fix it." },
      { at: 2000, kind: "kel", text: "keळ already knows: exports live in src/export/; tests run with pytest from the repo root", r: ["recall"] },
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
  },
  {
    label: "A harder task",
    doneBy: "your model, after a cheaper try failed",
    next: "tasks like this go straight to your model",
    agent: "Claude Code",
    lines: [
      { at: 0, kind: "prompt", text: "Add rate limiting to the login endpoint: 5 attempts per minute per IP." },
      { at: 2000, kind: "kel", text: "keळ already knows: auth lives in src/auth/; tests run with pytest", r: ["recall"] },
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
  },
  {
    label: "A bigger task, in parts",
    doneBy: "your model for one part, cheaper models for the rest",
    next: "the whole change comes back for similar tasks",
    agent: "Claude Code",
    lines: [
      { at: 0, kind: "prompt", text: "Add rate limiting to the public API: 100 requests a minute per key." },
      { at: 2000, kind: "kel", text: "keळ: this looks like an earlier fix here (login throttling)", r: ["recall", "reuse"] },
      { at: 3000, kind: "kel", text: "3 parts: limiter.py first, then middleware.py and config.py side by side", r: ["route"] },
      { at: 3900, kind: "kel", text: "part 1, limiter.py → a cheaper model", r: ["route"] },
      { at: 4700, kind: "run", text: "running your tests: pytest tests/test_limiter.py" },
      { at: 5900, kind: "fail", text: "limit 100, let through 137 with 8 workers at once · nothing applied", r: ["tests"] },
      { at: 6700, kind: "kel", text: "part 1 → over to your model", r: ["route"] },
      { at: 7600, kind: "model", text: "I'll make the per-key count atomic, with a test for concurrent requests." },
      { at: 8800, kind: "kel", text: "parts 2 and 3 → cheaper models, side by side", r: ["route"] },
      { at: 9600, kind: "file", text: "src/api/middleware.py  src/api/config.py" },
      { at: 10400, kind: "run", text: "running your tests on all the parts together: pytest" },
      { at: 11800, kind: "ok", text: "58 passed in 7.2s", r: ["tests"] },
      { at: 12600, kind: "done", text: "✓ applied · remembered for next time" },
    ],
    cost: [[0, 0], [3900, 3], [6700, 10], [8800, 92], [11800, 108], [12600, 110]],
    outcome: "Your model did the hard part, so this one cost a little more. The rest still went to cheaper models.",
  },
  {
    label: "Next week",
    doneBy: "a cheaper model, reusing last week's fix",
    next: "cheaper again the next time",
    agent: "Cursor",
    lines: [
      { at: 0, kind: "prompt", text: "The JSON export has the same problem with the last record." },
      { at: 2000, kind: "kel", text: "keळ remembers: last week's CSV fix, and the test that proved it", r: ["recall", "reuse"] },
      { at: 2800, kind: "kel", text: "→ handed to a cheaper model", r: ["route"] },
      { at: 3500, kind: "file", text: "src/export/json.py" },
      { at: 3700, kind: "diff-del", text: '-    records = body.split("\\n")[:-1]' },
      { at: 3900, kind: "diff-add", text: "+    records = body.splitlines()" },
      { at: 4600, kind: "run", text: "running your tests: pytest" },
      { at: 5900, kind: "ok", text: "214 passed in 8.9s", r: ["tests"] },
      { at: 6600, kind: "done", text: "✓ applied by keळ · remembered for next time" },
    ],
    cost: [[0, 0], [2800, 1], [6600, 9]],
    outcome: "Cheaper each time a task repeats.",
  },
  {
    label: "Auth again",
    doneBy: "your model, straight away",
    next: "nothing wasted on a cheaper try",
    agent: "Claude Code",
    lines: [
      { at: 0, kind: "prompt", text: "Lock an account for 15 minutes after 10 failed logins." },
      { at: 2000, kind: "kel", text: "keळ remembers: auth changes in this repo go best to your model", r: ["recall"] },
      { at: 2700, kind: "kel", text: "→ straight to your model: no cheaper attempt, nothing wasted", r: ["route"] },
      { at: 3700, kind: "model", text: "I'll add a lockout counter next to the rate limiter, with a test." },
      { at: 5000, kind: "file", text: "src/auth/limits.py  tests/test_lockout.py" },
      { at: 5900, kind: "run", text: "running your tests: pytest" },
      { at: 7200, kind: "ok", text: "43 passed in 9.3s", r: ["tests"] },
      { at: 7900, kind: "done", text: "✓ done by your model, with no detour" },
    ],
    cost: [[0, 0], [2700, 1], [7900, 101]],
    outcome: "Sent straight to your model: no failed attempt to pay for.",
  },
];

const WORDS: [Word | "reduce", string][] = [["recall", "Recall"], ["reuse", "Reuse"], ["route", "Route"], ["reduce", "Reduce"], ["tests", "Tests decide"]];


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

          <div className="stealth-peek" aria-label="What this task came to">
            <small className="caption dim">For you</small>
            {[
              ["Done by", sc.doneBy],
              ["Your tests", finished ? "passed before anything was applied" : "running…"],
              ["Your prompt cache", "untouched"],
              ["Next time", sc.next],
            ].map(([k, v]) => (
              <div key={k} className="stealth-file">
                <b>{k}</b>
                <span className={finished || k === "Your prompt cache" ? "" : "dim"}>{finished || k === "Your prompt cache" || k === "Your tests" ? v : "…"}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

    </div>
  );
}
