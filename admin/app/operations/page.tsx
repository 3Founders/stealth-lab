"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { AdminNotice, Hero, Stat } from "@/components/Admin";
import { getIndexLag, getIngestionStatus, listSubmissions, timeAgo, type ApiState, type IndexLag, type IngestionAutoStatus } from "@/lib/admin-api";

const sections: [string, string, string][] = [
  ["/ingestion", "Ingestion", "Scheduler status, index freshness, failure-route processing."],
  ["/review", "Review", "Accept or reject procedure and benchmark submissions."],
  ["/moderation", "Moderation", "Hide, restore or remove a published way."],
  ["/credits", "Credits", "Look up a contributor's balance and ledger; claw back an event."],
];

export default function Overview() {
  const [ingest, setIngest] = useState<ApiState<IngestionAutoStatus>>({ kind: "loading" });
  const [lag, setLag] = useState<ApiState<IndexLag>>({ kind: "loading" });
  const [pending, setPending] = useState<ApiState<number>>({ kind: "loading" });

  useEffect(() => {
    const ac = new AbortController();
    getIngestionStatus(ac.signal).then(setIngest);
    getIndexLag(5, ac.signal).then(setLag);
    Promise.all([
      listSubmissions("procedure", "pending", 200, ac.signal),
      listSubmissions("benchmark", "pending", 200, ac.signal),
    ]).then(([p, b]) => {
      if (p.kind !== "ok") return setPending(p as ApiState<number>);
      if (b.kind !== "ok") return setPending(b as ApiState<number>);
      setPending({ kind: "ok", data: p.data.submissions.length + b.data.submissions.length });
    });
    return () => ac.abort();
  }, []);

  const blocked = ["forbidden", "unauthenticated", "unconfigured", "error"].includes(ingest.kind); // an error is shown as one, never as an endless "…"
  return (
    <>
      <Hero marker="OVERVIEW" title="Operations" lead="Health of ingestion and retrieval, and the work waiting on a human." />
      <section className="frame grid" style={{ paddingBottom: 120, rowGap: 40 }}>
        {blocked ? (
          <div style={{ gridColumn: "1 / span 12" }}><AdminNotice state={ingest} /></div>
        ) : (
          <div className="stats">
            <Stat
              label="Ingestion loop"
              value={ingest.kind === "ok" ? (ingest.data.enabled ? "Running" : "Disabled") : "…"}
              note={ingest.kind === "ok" ? `${ingest.data.run_count} sweeps · last ${timeAgo(ingest.data.last_run_completed_at)}` : undefined}
            />
            <Stat
              label="Last ingestion error"
              value={ingest.kind === "ok" ? (ingest.data.last_error ? "Yes" : "None") : "…"}
              note={ingest.kind === "ok" && ingest.data.last_error ? ingest.data.last_error : undefined}
              bad={ingest.kind === "ok" && !!ingest.data.last_error}
            />
            <Stat
              label="Stale index rows"
              value={lag.kind === "ok" ? lag.data.total_stale : "…"}
              note={lag.kind === "ok" ? `${lag.data.lag_count} lagging · ${lag.data.recipe_drift_count} recipe drift` : undefined}
              bad={lag.kind === "ok" && lag.data.total_stale > 0}
            />
            <Stat
              label="Submissions awaiting review"
              value={pending.kind === "ok" ? pending.data : "…"}
              bad={pending.kind === "ok" && pending.data > 0}
            />
          </div>
        )}
        <div className="panel">
          <h2 className="h3">Jump to</h2>
          <ul className="list" aria-label="Sections">
            {sections.map(([href, title, desc], i) => (
              <li key={href}>
                <Link href={href}>
                  <span className="n">{String(i + 1).padStart(3, "0")}</span>
                  <div><h3>{title}</h3><p className="desc">{desc}</p></div>
                  <span className="caption dim" aria-hidden="true">→</span>
                </Link>
              </li>
            ))}
          </ul>
        </div>
      </section>
    </>
  );
}
