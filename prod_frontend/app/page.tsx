import Link from "next/link";
import Reveal from "@/components/Reveal";
import SectionRail from "@/components/SectionRail";
import InteractiveLogo from "@/components/interactive-logo/InteractiveLogo";
import HeroThreshold from "@/components/HeroThreshold";

const rail = [
  { id: "top", label: "Introduction" },
  { id: "about", label: "What it does" },
  { id: "knowledge", label: "What you get" },
  { id: "evidence", label: "Evidence" },
  { id: "trust", label: "Trust" },
  { id: "use", label: "Start" },
];

const Marker = ({ n, label }: { n: string; label: string }) => (
  <div className="marker caption"><b>{n}</b><span>/ {label}</span></div>
);

const uses = [
  ["Cheaper first", "", "Routine tasks go to lower-cost models before a frontier model is used."],
  ["Checked", "", "An answer is checked before it is accepted: tests, rules, or a reviewer, depending on the task."],
  ["Fallback", "", "When a check fails, the task goes to the frontier model you already use."],
  ["Recorded", "", "What was tried, by which model, and what it cost is kept, and nothing is deleted."],
  ["Under your control", "", "An organisation kill switch, a spending policy, and an off switch for people and machines."],
  ["Honest limits", "", "We say what we have measured, what we have not, and what we do not have yet."],
];

const steps = [
  ["Receive", "A task arrives in the coding or chat tool your team already uses."],
  ["Try cheaper", "If it is routine and checkable, a lower-cost model attempts it first."],
  ["Check", "The answer is checked before anyone sees it as done."],
  ["Escalate", "If the check fails, or the task is not routine, your usual frontier model does it."],
  ["Record", "The attempt, the checks and the cost are kept for you to review."],
];

export default function Home() {
  return (
    <>
      <SectionRail ids={rail} />

      {/* HERO */}
      <section id="top" className="hero frame grid" aria-labelledby="h-hero">
        <h1 id="h-hero" className="display">
          <span className="ln" style={{ "--d": "0ms" } as React.CSSProperties}><span>Routine work on</span></span>
          <span className="ln l2" style={{ "--d": "90ms" } as React.CSSProperties}><span><span className="hl">cheaper</span> models.</span></span>
          <span className="ln l2" style={{ "--d": "180ms" } as React.CSSProperties}><span>Checked first.</span></span>
        </h1>
        <Reveal className="hero-sub stack" delay={250}>
          <p className="lead">keळ sends everyday tasks to lower-cost models, checks each answer, and falls back to the frontier model when a check fails. Every attempt leaves a record you can audit.</p>
          <p className="dim small">Pre-release. Measured on public benchmarks so far, not yet on your team&rsquo;s work.</p>
        </Reveal>
        <Reveal className="hero-install" delay={350}>
          <div style={{ display: "flex", gap: 24, flexWrap: "wrap" }}>
            <Link href="/evidence" className="small" style={{ textDecoration: "underline" }}>See the evidence →</Link>
            <Link href="/trust" className="small" style={{ textDecoration: "underline" }}>Where your data goes →</Link>
            <Link href="/pilot" className="small" style={{ textDecoration: "underline" }}>How a pilot starts →</Link>
          </div>
        </Reveal>
        <InteractiveLogo />
      </section>

      <HeroThreshold />

      {/* 01 WHAT YOU CAN DO */}
      <section id="about" className="section frame grid" aria-labelledby="h-about">
        <Marker n="01" label="WHAT IT DOES" />
        <div className="how-title">
          <h2 id="h-about" className="h1">Send the routine work somewhere cheaper.</h2>
          <p className="lead dim" style={{ marginTop: 24 }}>Keep the frontier model for what needs it.</p>
          <p className="small dim" style={{ marginTop: 12 }}>
            keळ works inside the coding and chat tools your team already uses. Nothing about how people ask for work changes.
          </p>
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

      {/* 02 WHAT YOU GET */}
      <section id="knowledge" className="section frame grid" aria-labelledby="h-know">
        <Marker n="02" label="WHAT YOU GET" />
        <Reveal className="statement">
          <h2 id="h-know" className="h1">The next run starts <span className="dim">with what was learned.</span></h2>
        </Reveal>
        <div className="cells">
          {uses.map(([t, q, d], i) => (
            <Reveal key={t} className="cell" delay={(i % 4) * 60}>
              <div className="n"><span>{String(i + 1).padStart(2, "0")}</span></div>
              <div><span className="q">{q}</span><h3 className="h3">{t}</h3><p>{d}</p></div>
            </Reveal>
          ))}
        </div>
      </section>

      {/* 03 EVIDENCE */}
      <section id="evidence" className="section frame grid" aria-labelledby="h-evidence">
        <Marker n="03" label="EVIDENCE" />
        <Reveal className="statement">
          <h2 id="h-evidence" className="h1">Measured on benchmarks, <span className="dim">not yet on your work.</span></h2>
        </Reveal>
        <div className="col-a" style={{ marginTop: "clamp(28px,4vw,56px)" }}>
          <Reveal><p className="lead">On five public benchmark suites, answers matched Opus alone on most tasks at a lower cost in four of them, and the saving varies a lot by task type.</p></Reveal>
          <Reveal delay={40}><p className="small dim" style={{ marginTop: 10 }}>Samples are small, the graders are automatic, and the newest runs are mixed. We have no result yet on presentations or open-ended questions.</p></Reveal>
          <Reveal delay={80}><p className="lead" style={{ marginTop: 10 }}><Link href="/evidence" style={{ textDecoration: "underline" }}>Read the numbers and their limits →</Link></p></Reveal>
        </div>
      </section>

      {/* 04 TRUST */}
      <section id="trust" className="section frame grid" aria-labelledby="h-trust">
        <Marker n="04" label="TRUST" />
        <Reveal className="statement">
          <h2 id="h-trust" className="h1">Recorded, reviewable, <span className="dim">and honest about gaps.</span></h2>
        </Reveal>
        <div className="col-a" style={{ marginTop: "clamp(28px,4vw,56px)" }}>
          <Reveal><p className="lead">Nothing is deleted, changes to shared knowledge go through a recorded decision, and the audit log is checked for tampering.</p></Reveal>
          <Reveal delay={40}><p className="small dim" style={{ marginTop: 10 }}>keळ is not zero-egress, and it has no SOC 2 report or penetration test yet.</p></Reveal>
          <Reveal delay={80}><p className="lead" style={{ marginTop: 10 }}><Link href="/trust" style={{ textDecoration: "underline" }}>What leaves your machine, and what we do not have →</Link></p></Reveal>
        </div>
      </section>

      {/* 05 START */}
      <section id="use" className="section close frame grid" aria-labelledby="h-use">
        <Marker n="05" label="START" />
        <Reveal className="statement">
          <h2 id="h-use" className="h1">Start with a spend audit.</h2>
        </Reveal>
        <div className="close-links">
          <Link href="/pilot">How a pilot starts <span>→</span></Link>
          <Link href="/evidence">Evidence <span>→</span></Link>
          <Link href="/trust">Data &amp; trust <span>→</span></Link>
          <Link href="/docs">Docs <span>→</span></Link>
        </div>
      </section>
    </>
  );
}
