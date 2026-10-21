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

// What a developer gets, not how it is done.
const gets = [
  ["A smaller bill", "Everyday coding tasks cost a fraction of what your main model charges for them."],
  ["The same bar for your code", "Nothing reaches your code unless it passes your project's own checks. When a cheaper result doesn't pass, your usual model does the task, as it always has."],
  ["No new workflow", "Keep working in Claude Code or Cursor and type the way you do today. There's nothing new to learn."],
  ["A repo that remembers", "What keळ learns about your project lives in a .stealth folder in your repo: plain text you can read, edit and commit. The next task starts from it."],
  ["Better the more you use it", "keळ learns which kinds of tasks it can take in your project and which it should leave to your model, so the savings grow and the misses shrink."],
  ["You stay in control", "Start a prompt with !opus to skip keळ for it, undo any change it made with one command, and turn it off any time."],
];

export default function Home() {
  return (
    <>
      <SectionRail ids={rail} />

      {/* HERO */}
      <section id="top" className="hero frame grid" aria-labelledby="h-hero">
        <h1 id="h-hero" className="display">
          <span className="ln" style={{ "--d": "0ms" } as React.CSSProperties}><span>Same work.</span></span>
          <span className="ln l2" style={{ "--d": "90ms" } as React.CSSProperties}><span>A <span className="hl">smaller</span> bill.</span></span>
        </h1>
        <Reveal className="hero-sub stack" delay={250}>
          <p className="lead">keळ works inside the coding agent you already use and cuts what each task costs, without lowering the bar for what reaches your code.</p>
          <p className="dim small">What it learns about your repository stays in your repository.</p>
        </Reveal>
        <Reveal className="hero-install" delay={350}><InstallCommand /></Reveal>
        <InteractiveLogo />
      </section>

      <HeroThreshold />

      {/* 01 WHAT YOU GET */}
      <section id="about" className="section frame grid" aria-labelledby="h-about">
        <Marker n="01" label="WHAT YOU GET" />
        <Reveal className="statement">
          <h2 id="h-about" className="h1">Pay less for the work. <span className="dim">Keep the quality.</span></h2>
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
          <h2 id="h-action" className="h1">Three tasks, <span className="dim">three outcomes.</span></h2>
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
