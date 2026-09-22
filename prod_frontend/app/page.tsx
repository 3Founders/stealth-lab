import Link from "next/link";
import InstallCommand from "@/components/InstallCommand";
import Reveal from "@/components/Reveal";
import SectionRail from "@/components/SectionRail";
import ActionWalkthrough from "@/components/ActionWalkthrough";
import InteractiveLogo from "@/components/interactive-logo/InteractiveLogo";
import HeroThreshold from "@/components/HeroThreshold";

const rail = [
  { id: "top", label: "Introduction" },
  { id: "about", label: "What you can do" },
  { id: "knowledge", label: "What you get" },
  { id: "action", label: "Knowledge in action" },
  { id: "execution", label: "Contribution" },
  { id: "learning", label: "Value" },
  { id: "use", label: "Use it" },
];

const Marker = ({ n, label }: { n: string; label: string }) => (
  <div className="marker caption"><b>{n}</b><span>/ {label}</span></div>
);

const uses = [
  ["Ways", "", "Reusable ways to accomplish a goal."],
  ["Routes", "", "Different possible paths to the same goal."],
  ["Implementations", "", "The tools, systems, agents, APIs, or mechanisms a way can use."],
  ["Runs", "", "What actually happened when a way was tried."],
  ["Evidence", "", "What supports confidence in a way or outcome."],
  ["Benchmarks", "", "How a way can be tested under defined conditions."],
  ["Claims", "", "Useful knowledge discovered along the way."],
  ["Artifacts", "", "What the work produces."],
  ["History", "", "Where a way came from and how it changed."],
  ["Usage", "", "Where and how a way has been reused."],
  ["Contributions", "", "Ways and improvements added by people or agents."],
  ["Human input", "", "Places where a person had to decide, provide information, approve, or intervene."],
];

const steps = [
  ["Find", "Describe what you're trying to accomplish, including your environment and constraints."],
  ["Explore", "See possible ways to accomplish it, rather than receiving one generic answer."],
  ["Choose", "Compare the available ways and select one appropriate to your situation."],
  ["Run", "Put the selected way into practice."],
  ["Check", "See what happened, and whether the goal was actually achieved."],
  ["Reuse", "Use what was learned the next time a similar goal comes up."],
];

const contributions = [
  ["Add a way", "Share a procedure that worked."],
  ["Improve a way", "Adapt or correct an existing way."],
  ["Add evidence", "Record what happened when it was actually used."],
  ["Add a benchmark", "Help define how success should be measured."],
  ["Report what failed", "Help prevent the same bad path from being repeated."],
];

export default function Home() {
  return (
    <>
      <SectionRail ids={rail} />

      {/* HERO */}
      <section id="top" className="hero frame grid" aria-labelledby="h-hero">
        <h1 id="h-hero" className="display">
          <span className="ln" style={{ "--d": "0ms" } as React.CSSProperties}><span>Find ways to do</span></span>
          <span className="ln l2" style={{ "--d": "90ms" } as React.CSSProperties}><span><span className="hl">anything</span>.</span></span>
          <span className="ln l2" style={{ "--d": "180ms" } as React.CSSProperties}><span>Make them better.</span></span>
        </h1>
        <Reveal className="hero-sub stack" delay={250}>
          <p className="lead">keळ finds ways to accomplish a goal in your specific environment and constraints, puts them into practice, and learns from what happens.</p>
          <p className="dim small">What you learn can make the next run better.</p>
        </Reveal>
        <Reveal className="hero-install" delay={350}><InstallCommand /></Reveal>
        <InteractiveLogo />
      </section>

      <HeroThreshold />

      {/* 01 WHAT YOU CAN DO */}
      <section id="about" className="section frame grid" aria-labelledby="h-about">
        <Marker n="01" label="WHAT YOU CAN DO" />
        <div className="how-title">
          <h2 id="h-about" className="h1">Start with something you need to do.</h2>
          <p className="lead dim" style={{ marginTop: 24 }}>Automatic, through your coding agent.</p>
          <p className="small dim" style={{ marginTop: 16 }}>
            Prefer to look something up yourself? <Link href="/search" style={{ textDecoration: "underline" }}>Search keळ directly</Link>.
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
          <h2 id="h-know" className="h1">Everything you need <span className="dim">to pick up the work again.</span></h2>
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

      {/* 03 KNOWLEDGE IN ACTION */}
      <section id="action" className="section frame grid" aria-labelledby="h-action">
        <Marker n="03" label="KNOWLEDGE IN ACTION" />
        <Reveal className="statement">
          <h2 id="h-action" className="h1">One goal, <span className="dim">followed all the way through.</span></h2>
        </Reveal>
        <ActionWalkthrough />
      </section>

      {/* 04 CONTRIBUTION */}
      <section id="execution" className="section frame grid" aria-labelledby="h-exec">
        <Marker n="04" label="CONTRIBUTION" />
        <Reveal className="statement">
          <h2 id="h-exec" className="h1">Leave something <span className="dim">useful behind.</span></h2>
        </Reveal>
        <div className="col-a" style={{ marginTop: "clamp(28px,4vw,56px)" }}>
          <Reveal><p className="lead">You don't only use keळ. What you learn (a way that worked, a fix, a failure worth knowing about) can improve it for the next run.</p></Reveal>
        </div>
        <div className="run-ledger" aria-label="Ways to contribute">
          {contributions.map(([b, s]) => (
            <Reveal key={b}><b>{b}</b><span>{s}</span></Reveal>
          ))}
        </div>
      </section>

      {/* 05 VALUE */}
      <section id="learning" className="section frame grid" aria-labelledby="h-learn">
        <Marker n="05" label="VALUE" />
        <Reveal className="statement">
          <h2 id="h-learn" className="h1">Useful work should <span className="dim">become useful again.</span></h2>
        </Reveal>
        <div className="col-a" style={{ marginTop: "clamp(28px,4vw,56px)" }}>
          <Reveal><p className="lead">When a way is reused, improved, and backed by more experience, its value to the network can grow.</p></Reveal>
        </div>
        <ol className="loop">
          {["Contribute", "Use", "Verify", "Improve", "Reward"].map((t, i) => (
            <Reveal as="li" key={t + i} delay={i * 50}><span>{String(i + 1).padStart(2, "0")}</span>{t}</Reveal>
          ))}
        </ol>
        <p className="loop-back">What "reward" means here is still being worked out.</p>
      </section>

      {/* 06 USE IT */}
      <section id="use" className="section close frame grid" aria-labelledby="h-use">
        <Marker n="06" label="USE IT" />
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
