import type { Metadata } from "next";
import Link from "next/link";
import AnimatedHeading from "@/components/AnimatedHeading";

export const metadata: Metadata = {
  title: "Evidence",
  description: "What we have measured, on which tasks, and what that does not show.",
};

// Source: the phase-1 A/B in docs/HANDOFF_abtest_2026-10-11.md section 2.2. Same tasks in both arms. "Opus alone" is
// Claude Code on Opus; "keळ" is cheap models first with Opus as the fallback. Cost change is keळ against Opus alone at
// API prices. Do not add a total or a headline saving: the newest runs are mixed (see the page text).
const rows = [
  ["BoolQ", "Yes/no questions over a passage", "label", "10", "10", "10", "−89%", "10 of 10"],
  ["IFEval", "Writing with format rules", "rule checkers", "20", "20", "20", "−62%", "15 of 20"],
  ["BIRD SQL", "Question to SQL on a database", "result match", "48", "45", "44", "−50%", "32 of 48"],
  ["DS-1000", "Data-science Python", "tests", "30", "29", "27", "−43%", "19 of 30"],
  ["OpenAPI", "Contract-driven code", "property tests", "20", "16", "13", "−1%", "3 of 20"],
];

export default function Evidence() {
  return (
    <>
      <section className="page-hero frame grid">
        <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>EVIDENCE</b></div>
        <h1 className="display"><AnimatedHeading>Evidence</AnimatedHeading></h1>
        <p className="lead">What we have measured, on which tasks, and what it does not show. Read the limits before the numbers.</p>
      </section>

      <div className="frame grid" style={{ paddingBottom: 120 }}>
        <div className="doc-sec" style={{ gridColumn: "1 / span 12" }}>
          <h2>First measurements, on public benchmarks</h2>
          <p>
            The same tasks were run two ways: Claude Code on Opus alone, and keळ sending each task to cheaper models first, with Opus as the
            fallback. The last column is how many tasks the cheaper models delivered without the fallback.
          </p>
          <div style={{ overflowX: "auto" }}>
            <table>
              <thead>
                <tr>
                  <th>Suite</th><th>What it is</th><th>Checked by</th><th>Tasks</th>
                  <th>Opus alone solved</th><th>keळ solved</th><th>Cost change vs Opus alone</th><th>Answered by cheaper models</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r[0]}>{r.map((c, i) => <td key={i}>{c}</td>)}</tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="small dim">Cost change is at API prices for Claude Code. Each suite is measured separately; there is no overall figure on purpose.</p>

          <h3>What this does not show</h3>
          <ul>
            <li><b>Not your work.</b> These are public benchmarks. We have no result yet on presentations or free-form question answering, and nothing here tests them.</li>
            <li><b>Small samples.</b> Ten of ten correct is consistent with a true rate as low as about 72%; twenty of twenty, about 84%. &ldquo;Matched Opus&rdquo; is what we saw, not a proof of equivalence.</li>
            <li><b>Automatic graders.</b> Every suite above has a label or a rule checker. Work with no ground truth has no such grader.</li>
            <li><b>Wrong answers that pass the checks exist.</b> Four of keळ&rsquo;s misses were wrong cheaper-model answers that passed both the visible check and the tests written first. That is why verification is part of the offer, and why it is not complete.</li>
            <li><b>The saving is against API prices.</b> On a flat per-seat subscription, inside its included usage, the dollar saving can be zero even when routing works.</li>
            <li><b>It is slower.</b> Median time was 56 seconds against 33 for Opus alone across all 128 tasks.</li>
            <li><b>The newest runs are mixed.</b> On one recent issue the cost fell by 32–63%; on another it cost more than Opus alone. Treat any single saving figure, including those above, as specific to its task.</li>
          </ul>

          <h3>What we would measure on your work</h3>
          <p>
            Your own tasks, your own accepted answers, and a number fixed in advance. See <Link href="/pilot" style={{ textDecoration: "underline" }}>how a pilot starts</Link>.
          </p>
        </div>
      </div>
    </>
  );
}
