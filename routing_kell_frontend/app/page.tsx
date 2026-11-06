import Link from "next/link";
import HeroActions from "@/components/HeroActions";
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
  { id: "tools", label: "The best tool" },
  { id: "room", label: "One room" },
  { id: "action", label: "See it work" },
  { id: "use", label: "Use it" },
];

const Marker = ({ n, label }: { n: string; label: string }) => (
  <div className="marker caption"><b>{n}</b><span>/ {label}</span></div>
);

// What a team gets: the right model, the best tool, one room -- then the control. Outcomes, not how keळ decides.
const gets = [
  ["Route", "Each task goes to the cheapest model that can do it well. Your usual model takes the rest."],
  ["Big tasks, in parts", "When keळ has fixed something like it in your repo before, it splits the new task the same way, a cheap model per part."],
  ["Your tests decide", "Nothing reaches your code unless your own tests pass."],
  ["The best tool, found for you", "keळ searches thousands of tool servers for the one that fits this request, ranked by what has worked before."],
  ["Nothing to install", "keळ runs the tool for you. If it needs an account, you sign in once, in one click."],
  ["Your team and its agents", "People, the agent of your choice and your pal's agents in one room. Call any agent, with its owner's approval."],
  ["One canvas for everyone", "Docs, slides, pages and papers everyone edits at once, with every version kept."],
  ["You stay in control", "Skip keळ for any prompt, undo any change, turn it off any time. Chats stay encrypted on your devices."],
];

// Each section's comparison: the usual way, then keळ. Every bar is [label, width %, what it means].
type Row = { who: string; kel: boolean; bars: [string, number, string][] };

// What happens to the prompt cache, and to the bill, with a router that switches models and with keळ.
const cacheRows: Row[] = [
  { who: "A router that switches models mid-session", kel: false, bars: [["prompt cache", 4, "lost: everything reread at full price"], ["your bill", 100, "savings eaten by the rereads"]] },
  { who: "keळ", kel: true, bars: [["prompt cache", 100, "kept"], ["your bill", 50, "about half, over time"]] },
];

// Which tools an agent can reach, and what it takes to set them up.
const toolRows: Row[] = [
  { who: "An agent with the tools you installed", kel: false, bars: [["tools", 5, "only the few you set up, fit or not"], ["setup", 100, "by hand, for each tool"]] },
  { who: "keळ", kel: true, bars: [["tools", 100, "thousands, searched for every request"], ["setup", 3, "none; sign in once if a tool needs it"]] },
];

// Who can work on a request, and how many places the work is spread over.
const roomRows: Row[] = [
  { who: "An agent in its own window", kel: false, bars: [["agents", 10, "only your own"], ["places", 100, "chat, agent, doc: paste between them"]] },
  { who: "A keळ room", kel: true, bars: [["agents", 100, "yours and your pal's, with approval"], ["places", 8, "one room and one canvas"]] },
];

const Compare = ({ rows }: { rows: Row[] }) => (
  <Reveal className="cache-bars" delay={120}>
    {rows.map((row) => (
      <div key={row.who} className="cache-row" data-kel={row.kel}>
        <small className="caption">{row.who}</small>
        {row.bars.map(([label, w, text]) => (
          <div key={label} className="cache-bar"><span>{label}</span><i><b style={{ width: `${w}%` }} /></i><em>{text}</em></div>
        ))}
      </div>
    ))}
  </Reveal>
);

export default function Home() {
  return (
    <>
      <SectionRail ids={rail} />

      {/* HERO */}
      <section id="top" className="hero frame grid" aria-labelledby="h-hero">
        <h1 id="h-hero" className="display">
          <span className="ln" style={{ "--d": "0ms" } as React.CSSProperties}><span>Cut LLM API bills by <span className="hl">50%</span></span></span>
          <span className="ln l2" style={{ "--d": "90ms" } as React.CSSProperties}><span>with cache</span></span>
          <span className="ln l2" style={{ "--d": "180ms" } as React.CSSProperties}><span>retention</span></span>
          <span className="ln l2" style={{ "--d": "270ms" } as React.CSSProperties}><span>and tool search.</span></span>
        </h1>
        <Reveal className="hero-sub stack" delay={250}>
          <HeroActions />
        </Reveal>
        <InteractiveLogo />
      </section>

      <FilmPlayer />

      <HeroThreshold />


      {/* 01 WHAT YOU GET */}
      <section id="about" className="section frame grid" aria-labelledby="h-about">
        <Marker n="01" label="WHAT YOU GET" />
        <Reveal className="statement">
          <h2 id="h-about" className="h1">The right model, the best tool, one room. <span className="dim">Pay less, keep the bar where it is.</span></h2>
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
        <Compare rows={cacheRows} />
      </section>

      {/* 03 THE BEST TOOL */}
      <section id="tools" className="section frame grid" aria-labelledby="h-tools">
        <Marker n="03" label="NOT A TOOL STORE" />
        <Reveal className="statement">
          <h2 id="h-tools" className="h1">Your agent only has the tools you installed. <span className="dim">keळ finds the best one for each request.</span></h2>
        </Reveal>
        <Reveal className="cache-copy stack">
          <p className="lead">Most agents can only use the tool servers you found, installed and configured yourself, and they pick from those whether they fit or not.</p>
          <p>keळ searches thousands of tool servers for the one that fits this request, ranked by fit and by what has worked before, from verified publishers. It runs the tool for you; if the tool needs an account, you sign in once, in one click.</p>
          <p>In our tests, the right tool was in the top five 98% of the time.</p>
        </Reveal>
        <Compare rows={toolRows} />
      </section>

      {/* 04 ONE ROOM */}
      <section id="room" className="section frame grid" aria-labelledby="h-room">
        <Marker n="04" label="NOT ANOTHER CHAT APP" />
        <Reveal className="statement">
          <h2 id="h-room" className="h1">Ten tabs and copy-paste. <span className="dim">Or one room for your team and its agents.</span></h2>
        </Reveal>
        <Reveal className="cache-copy stack">
          <p className="lead">The agent works in one window, the team talks in another, the doc lives in a third, and everything gets pasted between them.</p>
          <p>In a keळ room, people, the agent of your choice and your pal&rsquo;s agents work in one chat. Call any agent with its owner&rsquo;s approval, and its answer lands where everyone sees it. The work lands in one canvas everyone edits at once, with every version kept.</p>
          <p>keळ remembers how your team works, and chats stay encrypted on your devices.</p>
        </Reveal>
        <Compare rows={roomRows} />
      </section>

      {/* 05 SEE IT WORK */}
      <section id="action" className="section frame grid" aria-labelledby="h-action">
        <Marker n="05" label="SEE IT WORK" />
        <Reveal className="statement">
          <h2 id="h-action" className="h1">Models, tools and agents at work. <span className="dim">Watch it happen.</span></h2>
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

      {/* 06 USE IT */}
      <section id="use" className="section close frame grid" aria-labelledby="h-use">
        <Marker n="06" label="USE IT" />
        <Reveal className="statement">
          <h2 id="h-use" className="h1">Set it up once. <span className="dim">Then just work.</span></h2>
        </Reveal>
        <div className="close-links">
          <a href="/download">Download for Windows <span>↓</span></a>
          <Link href="/docs#teams">For teams <span>→</span></Link>
          <Link href="/docs">Read the docs <span>→</span></Link>
        </div>
      </section>
    </>
  );
}
