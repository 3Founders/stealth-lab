import Link from "next/link";
import InstallCommand from "@/components/InstallCommand";
import Reveal from "@/components/Reveal";
import SectionRail from "@/components/SectionRail";
import StatusLabel, { type Status } from "@/components/StatusLabel";
import ActionWalkthrough from "@/components/ActionWalkthrough";

const rail = [
  { id: "top", label: "Introduction" },
  { id: "about", label: "About" },
  { id: "how", label: "How keळ works" },
  { id: "knowledge", label: "Knowledge" },
  { id: "action", label: "Knowledge in action" },
  { id: "execution", label: "Execution" },
  { id: "learning", label: "Learning" },
  { id: "use", label: "Use it" },
];

const Marker = ({ n, label }: { n: string; label: string }) => (
  <div className="marker caption"><b>{n}</b><span>/ {label}</span></div>
);

const ontology = [
  ["Goal", "What", "What someone wants to accomplish."],
  ["Procedure", "How", "A reusable method for accomplishing a Goal."],
  ["Implementation", "With what", "The concrete tool, API or system through which a Procedure can be executed."],
  ["Route", "Which way", "One candidate path: Goal → Procedure → Implementation. Several can exist; selection weighs applicability, constraints, evidence, performance and cost."],
  ["Run", "What happened", "An actual attempt. Distinct from a Procedure: it records what really occurred, including failure."],
  ["Claim", "What we hold", "Reusable general knowledge, independent of any one Procedure."],
  ["Evidence", "Why", "Support and provenance for Claims, Procedures and outcomes."],
  ["Benchmark", "Under what test", "A defined test under which candidates are evaluated. Results are contextual, never universal."],
  ["Submission", "Proposed", "A proposed new or improved Procedure, Implementation, or supporting knowledge."],
  ["Usage", "Reused", "Actual reuse of knowledge."],
  ["Value event", "Measured", "A measurable value event associated with usage."],
  ["Human intervention", "Your call", "An explicit point where a person supplies missing information, credentials, approval, an override, or verification. The human stays in control."],
];

const steps = [
  ["Goal", "State what you are trying to get done."],
  ["Known ways", "Recorded Procedures and the Implementations that can carry them."],
  ["Routes", "Candidate Goal → Procedure → Implementation paths, side by side."],
  ["Selection", "One Route is chosen against constraints, evidence and cost — with a reason."],
  ["Execution", "The Route is run. People are pulled in only where a decision is theirs."],
  ["Observation", "Events, artifacts, tool use and outputs are recorded as they happen."],
  ["Verification", "The outcome is checked against the Goal, not against the Run’s own report."],
  ["Reusable knowledge", "Evidence updates what is known — no more than the evidence supports."],
];

const trust: [string, string][] = [
  ["Unknown", "False"], ["Candidate", "Verified"], ["Source statement", "Established fact"],
  ["Successful execution", "Universally best"], ["High usage", "Proof of superiority"],
];
const states: Status[] = ["unknown", "candidate", "observed", "claimed", "evidenced", "verified", "executed", "failed", "successful"];

export default function Home() {
  return (
    <>
      <SectionRail ids={rail} />

      {/* HERO */}
      <section id="top" className="hero frame grid" aria-labelledby="h-hero">
        <h1 id="h-hero" className="display">
          <span className="ln" style={{ "--d": "0ms" } as React.CSSProperties}><span>Remember how</span></span>
          <span className="ln l2" style={{ "--d": "90ms" } as React.CSSProperties}><span>things <span className="hl">actually</span></span></span>
          <span className="ln l2" style={{ "--d": "180ms" } as React.CSSProperties}><span>get done.</span></span>
        </h1>
        <Reveal className="hero-sub" delay={250}>
          <p className="lead">keळ finds known ways to accomplish real-world goals, executes them, verifies what happened, and turns experience into reusable knowledge.</p>
        </Reveal>
        <Reveal className="hero-install" delay={350}><InstallCommand /></Reveal>
        <Reveal className="hero-legend" delay={450}>
          <div><b>Goal</b>what you want done</div>
          <div><b>Route</b>a known way to do it</div>
          <div><b>Run</b>what actually happened</div>
          <div><b>Evidence</b>why we believe it</div>
        </Reveal>
      </section>

      {/* 01 ABOUT */}
      <section id="about" className="section frame grid" aria-labelledby="h-about">
        <Marker n="01" label="ABOUT" />
        <Reveal className="statement">
          <h2 id="h-about" className="h1">Most systems remember data. <span className="dim">keळ remembers how things got done.</span></h2>
        </Reveal>
        <div className="col-a" style={{ marginTop: "clamp(32px,5vw,72px)" }}>
          <Reveal className="stack">
            <p className="lead">Documentation says how something should work. A tool says what it can do. Neither says what happened the last time someone tried.</p>
            <p className="lead">keळ keeps the missing layer: the known ways of getting a real goal done, with the record of when they worked, when they didn’t, and why.</p>
          </Reveal>
        </div>
        <div className="col-b" style={{ marginTop: "clamp(32px,5vw,72px)" }}>
          <Reveal className="stack" delay={120}>
            <p>It captures executable knowledge from what already exists and from what happens next. It does not treat any of it as truth on arrival: a source is a source, and every claim stays tied to what supports it.</p>
            <p className="dim small">keळ is not a chatbot, a workflow builder, a documentation system, or a generic agent platform. It is a system for finding, running, judging and accumulating known ways.</p>
          </Reveal>
        </div>
        <ul className="source-list" aria-label="What keळ learns from">
          {["Documented procedures", "Tools", "Implementations", "Previous executions", "Outcomes", "Evidence", "Benchmarks", "Failures", "Successful runs", "Human interventions"].map((t, i) => (
            <li key={t}><span>{String(i + 1).padStart(2, "0")}</span>{t}</li>
          ))}
        </ul>
      </section>

      {/* 02 HOW IT WORKS */}
      <section id="how" className="section frame grid" aria-labelledby="h-how">
        <Marker n="02" label="HOW IT WORKS" />
        <div className="how-title">
          <h2 id="h-how" className="h1">From a goal to a better-known way.</h2>
          <p className="lead dim" style={{ marginTop: 24 }}>Ask or discover. keळ works through the same loop each time — and every pass leaves the knowledge slightly better evidenced.</p>
        </div>
        <ol className="steps">
          {steps.map(([t, d], i) => (
            <Reveal as="li" key={t} delay={40}>
              <span className="n">{String(i + 1).padStart(2, "0")}</span>
              <div><h3 className="h3">{t}</h3><p>{d}</p></div>
            </Reveal>
          ))}
        </ol>
      </section>

      {/* 03 KNOWLEDGE */}
      <section id="knowledge" className="section frame grid" aria-labelledby="h-know">
        <Marker n="03" label="KNOWLEDGE" />
        <Reveal className="statement">
          <h2 id="h-know" className="h1">Twelve things, <span className="dim">never collapsed into “workflow.”</span></h2>
        </Reveal>
        <div className="cells">
          {ontology.map(([t, q, d], i) => (
            <Reveal key={t} className="cell" delay={(i % 4) * 60}>
              <div className="n"><span>{String(i + 1).padStart(2, "0")}</span></div>
              <div><span className="q">{q}</span><h3 className="h3">{t}</h3><p>{d}</p></div>
            </Reveal>
          ))}
        </div>

        <Reveal className="lineage">
          <h3 className="h2">Where knowledge comes from</h3>
          <ol>
            {[["Source", "not truth"], ["Artifact", ""], ["Observation", ""], ["Claim", ""], ["Procedure", ""], ["Implementation", ""], ["Execution", ""], ["Events · artifacts · outcome", ""], ["Observation · evidence", ""], ["Improved knowledge", ""]].map(([t, s]) => (
              <li key={t} className={t === "Source" ? "key" : ""}>{t}{s && <small>{s}</small>}</li>
            ))}
          </ol>
          <p className="note">This is a map of how things can relate, not a mandatory pipeline. A Source does not have to produce every object, and keळ does not manufacture objects to complete the picture. <b style={{ fontWeight: 400 }}>Source is not truth.</b></p>
        </Reveal>

        <Reveal className="lineage">
          <h3 className="h2">Epistemic discipline</h3>
        </Reveal>
        <div className="trust" style={{ marginTop: 28 }}>
          {trust.map(([a, b]) => (<div key={a}><span>{a}</span><i>≠</i><em>{b}</em></div>))}
        </div>
        <div className="states" aria-label="States knowledge can be in">
          {states.map((s) => <StatusLabel key={s} s={s} />)}
        </div>
      </section>

      {/* 04 ACTION */}
      <section id="action" className="section frame grid" aria-labelledby="h-action">
        <Marker n="04" label="KNOWLEDGE IN ACTION" />
        <Reveal className="statement">
          <h2 id="h-action" className="h1">One request, <span className="dim">followed all the way through.</span></h2>
        </Reveal>
        <ActionWalkthrough />
      </section>

      {/* 05 EXECUTION */}
      <section id="execution" className="section frame grid" aria-labelledby="h-exec">
        <Marker n="05" label="EXECUTION & EXPERIENCE" />
        <Reveal className="statement">
          <h2 id="h-exec" className="h1">keळ learns from what <span className="dim">actually happened.</span></h2>
        </Reveal>
        <div className="col-a" style={{ marginTop: "clamp(28px,4vw,56px)" }}>
          <Reveal><p className="lead">Every Run is preserved as a Run. It is never quietly promoted to a Procedure, and never assumed to have taught anything.</p></Reveal>
        </div>
        <div className="run-ledger" aria-label="What a Run can preserve">
          {[["Events", "what happened, in order"], ["Nodes", "the steps that ran"], ["Artifacts", "what was produced"], ["Cost & tool use", "what it took"], ["Outputs", "what came back"], ["Outcome", "how it ended"], ["Verification", "whether it held"], ["Human intervention", "where a person decided"]].map(([b, s]) => (
            <Reveal key={b}><b>{b}</b><span>{s}</span></Reveal>
          ))}
        </div>
        <div className="two-out">
          <Reveal className="out fail">
            <StatusLabel s="failed" />
            <h3>A failed Run</h3>
            <p>Stays failed. It can be useful evidence — that a route doesn’t apply, that a precondition was missing — but it can never become a Procedure by itself.</p>
          </Reveal>
          <Reveal className="out ok" delay={100}>
            <StatusLabel s="verified" />
            <h3>A verified Run</h3>
            <p>Stronger evidence. Still contextual: one success in one environment is not proof that a way is best everywhere. And not every Run produces reusable knowledge.</p>
          </Reveal>
        </div>
      </section>

      {/* 06 LEARNING */}
      <section id="learning" className="section frame grid" aria-labelledby="h-learn">
        <Marker n="06" label="LEARNING" />
        <Reveal className="statement">
          <h2 id="h-learn" className="h1">Better-known ways, <span className="dim">through evidence, execution and reuse.</span></h2>
        </Reveal>
        <ol className="loop">
          {["Use knowledge", "Execute", "Observe reality", "Verify outcome", "Capture evidence", "Improve knowledge", "Reuse"].map((t, i) => (
            <Reveal as="li" key={t} delay={i * 50}><span>{String(i + 1).padStart(2, "0")}</span>{t}</Reveal>
          ))}
        </ol>
        <p className="loop-back">Reuse is the next use. keळ doesn’t claim to always find the best solution — it accumulates better-known ways, and shows its evidence.</p>
      </section>

      {/* 07 USE IT */}
      <section id="use" className="section close frame grid" aria-labelledby="h-use">
        <Marker n="07" label="USE IT" />
        <Reveal className="statement">
          <h2 id="h-use" className="h1">Start with a goal.</h2>
        </Reveal>
        <div className="close-links">
          <Link href="/#install">Install keळ <span>from source</span></Link>
          <Link href="/problems">Explore problems <span>→</span></Link>
          <Link href="/search">Search knowledge <span>→</span></Link>
          <Link href="/docs">Read the docs <span>→</span></Link>
        </div>
      </section>
    </>
  );
}
