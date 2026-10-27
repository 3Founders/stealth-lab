import Link from "next/link";
import Reveal from "@/components/Reveal";
import SectionRail from "@/components/SectionRail";
import ActionWalkthrough from "@/components/ActionWalkthrough";
import MonthInRepo from "@/components/MonthInRepo";
import InteractiveLogo from "@/components/interactive-logo/InteractiveLogo";
import HeroThreshold from "@/components/HeroThreshold";
import FilmPlayer from "@/components/FilmPlayer";

const rail = [
  { id: "top", label: "Introduction" },
  { id: "about", label: "What you get" },
  { id: "cache", label: "Your cache" },
  { id: "action", label: "See it work" },
  { id: "use", label: "Use it" },
];

const Marker = ({ n, label }: { n: string; label: string }) => (
  <div className="marker caption"><b>{n}</b><span>/ {label}</span></div>
);

// What a developer gets: the four words of the headline, then the rule behind them and the controls. Outcomes, not how
// keळ decides.
const gets = [
  ["Recall", "Your agent starts every task already knowing your repo."],
  ["Reuse", "Solved once, solved for good. Similar problems come back faster and cheaper."],
  ["Route", "Each task is done by the cheapest model that can do it well. Your usual model takes the rest."],
  ["Reduce", "About half the bill over time, for the same work."],
  ["Your tests decide", "Nothing reaches your code unless your own tests pass."],
  ["You stay in control", "Skip keळ for any prompt, undo any change, turn it off any time."],
  ["Big tasks, in parts", "Large changes get cheaper too, not just small fixes."],
  ["Your cache stays warm", "The prompt cache you already pay for keeps working."],
];

// What happens to the prompt cache, and to the bill, with a router that switches models and with keळ.
const cacheRows: { who: string; kel: boolean; cache: [number, string]; bill: [number, string] }[] = [
  { who: "A router that switches models mid-session", kel: false, cache: [4, "lost: everything reread at full price"], bill: [100, "savings eaten by the rereads"] },
  { who: "keळ", kel: true, cache: [100, "kept"], bill: [50, "about half, over time"] },
];

export default function Home() {
  return (
    <>
      <SectionRail ids={rail} />

      {/* HERO */}
      <section id="top" className="hero frame grid" aria-labelledby="h-hero">
        <h1 id="h-hero" className="display">
          <span className="ln" style={{ "--d": "0ms" } as React.CSSProperties}><span>Cut LLM API bills by <span className="hl">50%</span></span></span>
          <span className="ln l2" style={{ "--d": "90ms" } as React.CSSProperties}><span>without losing</span></span>
          <span className="ln l2" style={{ "--d": "180ms" } as React.CSSProperties}><span>prompt cache retention.</span></span>
        </h1>
        <Reveal className="hero-sub stack" delay={250}>
          <p className="lead hero-q">Recall, reuse, route, reduce.</p>
          <p className="lead">keळ remembers your repo, reuses what&rsquo;s already been solved, and sends each task to the cheapest model that can do it, without breaking your agent&rsquo;s prompt cache. Nothing reaches your code until your own tests pass.</p>
          <p className="dim small">Works inside Claude Code and Cursor. What it learns stays in your repository.</p>
        </Reveal>
        <InteractiveLogo />
      </section>

      <FilmPlayer />

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

      {/* 02 YOUR CACHE */}
      <section id="cache" className="section frame grid" aria-labelledby="h-cache">
        <Marker n="02" label="NOT A MODEL ROUTER" />
        <Reveal className="statement">
          <h2 id="h-cache" className="h1">Other routers break your cache. <span className="dim">keळ cuts the bill without touching it.</span></h2>
        </Reveal>
        <Reveal className="cache-copy stack">
          <p className="lead">Most routers switch your agent&rsquo;s model mid-session, and every switch throws away the prompt cache you&rsquo;ve already paid for.</p>
          <p>keळ never switches your agent&rsquo;s model. Your cache keeps working, and the bill still comes down.</p>
        </Reveal>
        <Reveal className="cache-bars" delay={120}>
          {cacheRows.map((row) => (
            <div key={row.who} className="cache-row" data-kel={row.kel}>
              <small className="caption">{row.who}</small>
              <div className="cache-bar"><span>prompt cache</span><i><b style={{ width: `${row.cache[0]}%` }} /></i><em>{row.cache[1]}</em></div>
              <div className="cache-bar"><span>your bill</span><i><b style={{ width: `${row.bill[0]}%` }} /></i><em>{row.bill[1]}</em></div>
            </div>
          ))}
        </Reveal>
      </section>

      {/* 03 SEE IT WORK */}
      <section id="action" className="section frame grid" aria-labelledby="h-action">
        <Marker n="03" label="SEE IT WORK" />
        <Reveal className="statement">
          <h2 id="h-action" className="h1">Recall, reuse, route, reduce. <span className="dim">Watch it happen.</span></h2>
        </Reveal>
        <ActionWalkthrough />
        <MonthInRepo />
        <div className="month results">
          <div className="month-head">
            <div>
              <small className="caption dim">From our tests</small>
              <p className="lead" style={{ marginTop: 4 }}>Over many tasks, about half the cost.</p>
            </div>
          </div>
          <div className="month-stats">
            <div><b>~50%</b><span>less, on average, per task</span></div>
            <div><b>77%</b><span>of tasks done by cheaper models</span></div>
            <div><b>93% &rarr; 91%</b><span>of tasks solved: your model alone, then with keळ</span></div>
          </div>
          <p className="action-note">Measured on public coding benchmarks (DS-1000, BIRD, IFEval, BoolQ, OpenAPI, MultiPL-E), October 2026. Some tasks cost more than your model alone; the total cost less.</p>
        </div>
      </section>

      {/* 04 USE IT */}
      <section id="use" className="section close frame grid" aria-labelledby="h-use">
        <Marker n="04" label="USE IT" />
        <Reveal className="statement">
          <h2 id="h-use" className="h1">Set it up once. <span className="dim">Then just work.</span></h2>
        </Reveal>
        <div className="close-links">
          <Link href="/docs#teams">For teams <span>→</span></Link>
          <Link href="/docs">Read the docs <span>→</span></Link>
        </div>
      </section>
    </>
  );
}
