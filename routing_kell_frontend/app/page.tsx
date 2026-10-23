import Link from "next/link";
import InstallCommand from "@/components/InstallCommand";
import Reveal from "@/components/Reveal";
import SectionRail from "@/components/SectionRail";
import ActionWalkthrough from "@/components/ActionWalkthrough";
import MonthInRepo from "@/components/MonthInRepo";
import InteractiveLogo from "@/components/interactive-logo/InteractiveLogo";
import HeroThreshold from "@/components/HeroThreshold";

const rail = [
  { id: "top", label: "Introduction" },
  { id: "about", label: "What you get" },
  { id: "action", label: "See it work" },
  { id: "use", label: "Use it" },
];

const Marker = ({ n, label }: { n: string; label: string }) => (
  <div className="marker caption"><b>{n}</b><span>/ {label}</span></div>
);

// What a developer gets: the four words of the headline, then the rule behind them and the controls. Outcomes, not how
// keळ decides.
const gets = [
  ["Recall", "Your agent starts every task knowing your repo: facts about your code, each pointing to where it comes from, and kept true as the code changes."],
  ["Reuse", "Problems already solved in this repo come back with the fix and the test that proved it, so nothing is solved twice."],
  ["Route", "Each task goes to the cheapest model that can do it well in your repo. Your usual model takes the rest, as it always has."],
  ["Reduce", "A smaller bill for the same work, and it keeps shrinking as keळ learns which tasks your repo can hand off."],
  ["Your tests decide", "Nothing reaches your code until it passes your project's own checks. An agent saying it's done never counts."],
  ["You stay in control", "Start a prompt with !opus to skip keळ for it, undo any change it made with one command, and turn it off any time."],
];

export default function Home() {
  return (
    <>
      <SectionRail ids={rail} />

      {/* HERO */}
      <section id="top" className="hero frame grid" aria-labelledby="h-hero">
        <h1 id="h-hero" className="display">
          <span className="ln" style={{ "--d": "0ms" } as React.CSSProperties}><span>Recall, reuse,</span></span>
          <span className="ln l2" style={{ "--d": "90ms" } as React.CSSProperties}><span>route, <span className="hl">reduce</span>.</span></span>
        </h1>
        <Reveal className="hero-sub stack" delay={250}>
          <p className="lead">keळ gives your coding agent a memory of your repo, reuses what&rsquo;s already been solved, and hands each task to the cheapest model that can do it. Nothing reaches your code until your own tests pass.</p>
          <p className="dim small">Works inside Claude Code and Cursor. What it learns stays in your repository.</p>
        </Reveal>
        <Reveal className="hero-install" delay={350}><InstallCommand /></Reveal>
        <InteractiveLogo />
      </section>

      <HeroThreshold />

      {/* 01 WHAT YOU GET */}
      <section id="about" className="section frame grid" aria-labelledby="h-about">
        <Marker n="01" label="WHAT YOU GET" />
        <Reveal className="statement">
          <h2 id="h-about" className="h1">Pay less for the work. <span className="dim">Keep the bar where it is.</span></h2>
        </Reveal>
        <div className="cells">
          {gets.map(([t, d], i) => (
            <Reveal key={t} className="cell" delay={(i % 4) * 60}>
              <div className="n"><span>{String(i + 1).padStart(2, "0")}</span></div>
              <div><h3 className="h3">{t}</h3><p>{d}</p></div>
            </Reveal>
          ))}
        </div>
      </section>

      {/* 02 SEE IT WORK */}
      <section id="action" className="section frame grid" aria-labelledby="h-action">
        <Marker n="02" label="SEE IT WORK" />
        <Reveal className="statement">
          <h2 id="h-action" className="h1">Recall, reuse, route, reduce. <span className="dim">Watch it happen.</span></h2>
        </Reveal>
        <ActionWalkthrough />
        <MonthInRepo />
      </section>

      {/* 03 USE IT */}
      <section id="use" className="section close frame grid" aria-labelledby="h-use">
        <Marker n="03" label="USE IT" />
        <Reveal className="statement">
          <h2 id="h-use" className="h1">Set it up once. <span className="dim">Then just work.</span></h2>
        </Reveal>
        <div className="close-links">
          <Link href="/#install">Install keळ <span>one command</span></Link>
          <Link href="/docs#getting-started">Getting started <span>→</span></Link>
          <Link href="/docs#teams">For teams <span>→</span></Link>
          <Link href="/docs">Read the docs <span>→</span></Link>
        </div>
      </section>
    </>
  );
}
