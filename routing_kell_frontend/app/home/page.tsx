"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import LoadMore from "@/components/LoadMore";
import { usePublicWays } from "@/lib/use-public-ways";
import { priceLabel } from "@/lib/way-rate";
import "../route/route.css";

const EXAMPLES = [
  "research a topic",
  "send a slack message",
  "review my code",
  "find CVEs in my repository",
  "summarise a paper",
];

export default function Home() {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const { ways, state, hasMore, loadingMore, load, more } = usePublicWays();

  useEffect(() => {
    void load("");
  }, [load]);

  function go(term: string) {
    const q = term.trim();
    router.push(q ? `/task?q=${encodeURIComponent(q)}` : "/task");
  }

  return (
    <>
      <section className="page-hero frame grid" aria-labelledby="h-home">
        <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>WAYS</b></div>
        <h1 id="h-home" className="display">What do you want to get done?</h1>
        <p className="lead small">
          Find a Way: a reviewed, reusable procedure with its steps, its evidence and a sample output.
          Read it free. Buy one output when you need it.
        </p>

        <form className="ask" role="search" onSubmit={(e) => { e.preventDefault(); go(query); }}>
          <label htmlFor="home-task" className="skip">Describe a task</label>
          <input
            id="home-task"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="e.g. summarise a paper, send a slack message, find CVEs"
            autoComplete="off"
          />
          <button className="btn-ink" type="submit">Find a Way <span className="sq" aria-hidden="true">→</span></button>
        </form>

        <div className="scopes">
          {EXAMPLES.map((example) => (
            <button key={example} type="button" onClick={() => go(example)}>{example}</button>
          ))}
        </div>
      </section>

      <section className="frame grid" style={{ paddingBottom: 120 }} aria-labelledby="h-recommended">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 16, flexWrap: "wrap" }}>
          <div>
            <h2 id="h-recommended" className="h2" style={{ margin: 0 }}>Recommended Ways</h2>
            <p className="small dim" style={{ marginTop: 6 }}>Published, reviewed procedures. Newest first.</p>
          </div>
          <Link href="/task" className="small" style={{ textDecoration: "underline" }}>Browse all tasks →</Link>
        </div>

        {(state === "idle" || state === "loading") && <p className="small dim" role="status">Loading the catalogue…</p>}
        {state === "error" && (
          <p className="small dim">The catalogue could not be reached. <Link href="/task" style={{ textDecoration: "underline" }}>Try the task page</Link>.</p>
        )}
        {state === "ok" && ways.length === 0 && (
          <div className="route-fallback">
            <h2>No published Ways yet</h2>
            <p className="route-lead">Be the first: write a Way and send it for review.</p>
            <Link className="btn-line" href="/submit">Submit a Way</Link>
          </div>
        )}

        {state === "ok" && ways.length > 0 && (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: 16, marginTop: 20 }}>
            {ways.map((way) => {
              const price = way.price_paise && way.price_paise > 0 ? priceLabel(way.price_paise, way.price_currency) : null;
              return (
                <Link key={way.id} href={`/way/${encodeURIComponent(way.id)}`} className="route-goal" style={{ display: "block", textDecoration: "none", color: "inherit" }}>
                  <header>
                    <h2>{way.goal_name}</h2>
                    <span className="caption">{way.mode_label || way.mode}</span>
                  </header>
                  <p className="route-lead" style={{ display: "-webkit-box", WebkitLineClamp: 3, WebkitBoxOrient: "vertical", overflow: "hidden" }}>
                    {way.description || "A published Way."}
                  </p>
                  <div className="route-tool-meta">
                    <span>{way.step_count} step{way.step_count === 1 ? "" : "s"}</span>
                    <span>{price ?? "Free"}</span>
                    <span>{way.status === "approved" ? "Approved" : "AI approved"}</span>
                  </div>
                </Link>
              );
            })}
          </div>
        )}

        {state === "ok" && <LoadMore hasMore={hasMore} busy={loadingMore} onMore={more} />}

        <div className="close-links close-links-2" style={{ marginTop: 40 }}>
          <Link href="/">How kell works <span>→</span></Link>
          <Link href="/submit">Submit a Way <span>→</span></Link>
        </div>
      </section>
    </>
  );
}
