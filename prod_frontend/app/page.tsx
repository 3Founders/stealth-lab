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
  { id: "learning", label: "Rewards" },
  { id: "use", label: "Use it" },
];

const Marker = ({ n, label }: { n: string; label: string }) => (
  <div className="marker caption"><b>{n}</b><span>/ {label}</span></div>
);

const uses = [
  ["Relevant ways", "", "Find methods that fit this goal and context."],
  ["Context stays attached", "", "Environment, constraints, and requirements come with the work."],
  ["Evidence", "", "See what was actually tried and what happened."],
  ["Verification", "", "Separate claimed success from results that were actually checked."],
  ["Failure knowledge", "", "Know what failed before repeating the same path."],
  ["Reusable work", "", "Use a useful procedure again instead of starting from zero."],
];

const steps = [
  ["Find", "Describe what you're trying to accomplish, including your environment and constraints."],
  ["Explore", "See possible ways to accomplish it."],
  ["Choose", "Compare the available ways and select one that fits your situation."],
  ["Run", "Put the selected way into practice."],
  ["Check", "See what actually happened and whether the goal was achieved."],
  ["Reuse", "Use what was learned the next time a similar goal comes up."],
];

const contributions = [
  ["Add a way", "Submit a procedure for a Goal."],
  ["Improve a way", "Correct or extend an existing procedure."],
  ["Add a benchmark", "Define how success should be checked."],
  ["Add evidence", "Record what actually happened when a way was used."],
  ["Report a failure", "Record where a way did not work."],
];

const rewards = [
  ["+10", "Accepted new procedure"],
  ["+20", "Accepted improvement"],
  ["+5", "Verified independent reuse"],
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
          <p className="lead dim" style={{ marginTop: 24 }}>The MCP does this for you.</p>
          <p className="small dim" style={{ marginTop: 12 }}>
            Connect keळ to your coding agent to find, compare, choose, run, and record a way. You can also explore the same knowledge manually on the website.
          </p>
          <div style={{ display: "flex", gap: 20, flexWrap: "wrap", marginTop: 16 }}>
            <Link href="/docs#getting-started" className="small" style={{ textDecoration: "underline" }}>Set up MCP →</Link>
            <Link href="/goals" className="small" style={{ textDecoration: "underline" }}>Browse manually →</Link>
          </div>
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
          <h2 id="h-exec" className="h1">Make useful work <span className="dim">reusable.</span></h2>
        </Reveal>
        <div className="col-a" style={{ marginTop: "clamp(28px,4vw,56px)" }}>
          <Reveal><p className="lead">Contribute what you learned: add a way, improve an existing way, define how success should be checked, record what happened, or report a failure.</p></Reveal>
        </div>
        <div className="run-ledger" aria-label="Ways to contribute">
          {contributions.map(([b, s]) => (
            <Reveal key={b}><b>{b}</b><span>{s}</span></Reveal>
          ))}
        </div>
        <div className="col-a" style={{ marginTop: 24 }}>
          <Reveal><p className="small dim">Contributions are reviewed. Accepted does not mean verified.</p></Reveal>
          <Reveal delay={40}><p className="lead" style={{ marginTop: 10 }}><Link href="/docs#contribution-rewards" style={{ textDecoration: "underline" }}>How contribution &amp; rewards work →</Link></p></Reveal>
        </div>
      </section>

      {/* 05 REWARDS */}
      <section id="learning" className="section frame grid" aria-labelledby="h-learn">
        <Marker n="05" label="REWARDS" />
        <Reveal className="statement">
          <h2 id="h-learn" className="h1">Useful contributions <span className="dim">earn Credits.</span></h2>
        </Reveal>
        <div className="col-a" style={{ marginTop: "clamp(28px,4vw,56px)" }}>
          <Reveal><p className="lead">Credits are keळ&rsquo;s internal reward for useful contribution. They are not cash.</p></Reveal>
        </div>
        <div className="run-ledger" aria-label="What earns Credits">
          {rewards.map(([amount, label]) => (
            <Reveal key={label}><b>{amount}</b><span>{label}</span></Reveal>
          ))}
        </div>
        <div className="col-a" style={{ marginTop: 24 }}>
          <Reveal><p className="small dim">Benchmarks and reliable evidence can strengthen your Standing, but they do not directly earn Credits.</p></Reveal>
          <Reveal delay={40}><p className="small dim" style={{ marginTop: 10 }}>Self-use, views, copies, claimed success, and duplicate submissions do not earn Credits.</p></Reveal>
          <Reveal delay={80}><p className="small dim" style={{ marginTop: 10 }}>Standing is separate from Credits. Standing reflects contribution history and trust; it cannot be spent.</p></Reveal>
          <Reveal delay={120}><p className="lead" style={{ marginTop: 10 }}><Link href="/docs#contribution-rewards" style={{ textDecoration: "underline" }}>See the contribution &amp; reward system →</Link></p></Reveal>
        </div>
      </section>

      {/* 06 USE IT */}
      <section id="use" className="section close frame grid" aria-labelledby="h-use">
        <Marker n="06" label="USE IT" />
        <Reveal className="statement">
          <h2 id="h-use" className="h1">Start with a goal.</h2>
        </Reveal>
        <div className="close-links">
          <Link href="/#install">Install keळ <span>one command</span></Link>
          <Link href="/goals">Explore goals <span>→</span></Link>
          <Link href="/search">Search knowledge <span>→</span></Link>
          <Link href="/docs">Read the docs <span>→</span></Link>
        </div>
      </section>
    </>
  );
}
