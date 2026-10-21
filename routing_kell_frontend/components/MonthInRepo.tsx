"use client";
import { useState } from "react";

// "A month in your repo": drag through eight weeks and watch the .stealth folder fill, the share of tasks keळ takes
// grow, and the bill fall. One made-up team; the numbers are illustrative, not measured.
const weeks = [
  { handled: 15, entries: 4, bill: 92 },
  { handled: 22, entries: 9, bill: 86 },
  { handled: 30, entries: 15, bill: 79 },
  { handled: 36, entries: 22, bill: 73 },
  { handled: 41, entries: 28, bill: 69 },
  { handled: 45, entries: 33, bill: 66 },
  { handled: 48, entries: 38, bill: 63 },
  { handled: 50, entries: 42, bill: 61 },
];

export default function MonthInRepo() {
  const [w, setW] = useState(0);
  const now = weeks[w];
  return (
    <div className="month">
      <div className="month-head">
        <div>
          <small className="caption dim">Two months in one repository</small>
          <p className="lead" style={{ marginTop: 4 }}>The longer keळ works in a repository, the more it can take on.</p>
        </div>
        <label className="month-slider">
          <span className="caption">Week {w + 1}</span>
          <input
            type="range" min={0} max={weeks.length - 1} step={1} value={w}
            onChange={(e) => setW(Number(e.target.value))} aria-label="Week"
            aria-valuetext={`Week ${w + 1}`}
          />
        </label>
      </div>

      <div className="month-stats">
        <div><b>{now.entries}</b><span>entries in .stealth</span></div>
        <div><b>{now.handled}%</b><span>of tasks done by keळ</span></div>
        <div><b>{now.bill}%</b><span>of the bill without keळ</span></div>
      </div>

      <div className="month-bars" aria-label="Weekly bill, as a share of the bill without keळ">
        {weeks.map((x, k) => (
          <button
            key={k} type="button" data-on={k <= w} aria-current={k === w} onClick={() => setW(k)}
            aria-label={`Week ${k + 1}: ${x.bill}% of the bill without keळ`}
          >
            <i style={{ height: `${x.bill * 1.3}px` }} />
            <span>W{k + 1}</span>
          </button>
        ))}
      </div>
      <p className="action-note">Illustrative, not measured. How fast this happens depends on the repository, its tests and the kinds of tasks.</p>
    </div>
  );
}
