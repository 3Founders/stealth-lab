"use client";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { AdminNotice, Hero, Stat } from "@/components/Admin";
import { useOrg } from "@/components/OrgContext";
import { BudgetBanner, Delta, Updated } from "@/components/Widgets";
import { anomalousDays, budgetStatus } from "@/lib/alerts";
import { formatUsd, percent, toMicros } from "@/lib/money";
import { getPolicy, getUsage, isAdminRole, type OrgPolicy, type UsageRow } from "@/lib/org-api";
import { lastDays, previousRange } from "@/lib/range";
import { dayOf, groupBy, projectMonth, sumAll } from "@/lib/usage";
import { getIndexLag, getIngestionStatus, listSubmissions, timeAgo, type ApiState, type IndexLag, type IngestionAutoStatus } from "@/lib/admin-api";

const sections: [string, string, string][] = [
  ["/ingestion", "Ingestion", "Scheduler status, index freshness, failure-route processing."],
  ["/review", "Review", "Accept or reject procedure and benchmark submissions."],
  ["/moderation", "Moderation", "Hide, restore or remove a published way."],
  ["/credits", "Credits", "Look up a contributor's balance and ledger; claw back an event."],
];

/** The signed-in admin's own organisation at a glance: state of the kill switch, spend against budget, call volume. */
function OrgKpis() {
  const { orgs, current } = useOrg();
  const [policy, setPolicy] = useState<ApiState<OrgPolicy>>({ kind: "loading" });
  const [usage, setUsage] = useState<ApiState<{ rows: UsageRow[] }>>({ kind: "loading" });
  const [prevUsage, setPrevUsage] = useState<ApiState<{ rows: UsageRow[] }>>({ kind: "loading" });
  const [at, setAt] = useState<Date | null>(null);
  const [nonce, setNonce] = useState(0);
  const orgId = current?.organization_id;
  const admin = isAdminRole(current?.role);

  useEffect(() => {
    if (!orgId || !admin) return;
    const ac = new AbortController();
    const week = lastDays(7);
    const before = previousRange(week);
    const month = lastDays(30);
    setPolicy({ kind: "loading" });
    Promise.all([
      getPolicy(orgId, ac.signal),
      getUsage(orgId, `${month.since}T00:00:00Z`, `${month.until}T00:00:00Z`, ac.signal),
      getUsage(orgId, `${before.since}T00:00:00Z`, `${before.until}T00:00:00Z`, ac.signal),
    ]).then(([p, u, pu]) => { setPolicy(p); setUsage(u); setPrevUsage(pu); if (u.kind === "ok") setAt(new Date()); });
    return () => ac.abort();
  }, [orgId, admin, nonce]);

  const rows = usage.kind === "ok" ? usage.data.rows : [];
  const week = lastDays(7);
  const weekRows = rows.filter((r) => dayOf(r) >= week.since);
  const week$ = sumAll(weekRows);
  const prevWeek$ = prevUsage.kind === "ok" ? sumAll(prevUsage.data.rows) : null;
  const month = useMemo(() => projectMonth(rows, new Date()), [rows]);
  const budget = policy.kind === "ok" ? toMicros(policy.data.monthly_budget_usd) : null;
  const status = budgetStatus(month.monthToDate, month.projected, budget);
  const spikes = useMemo(() => anomalousDays(groupBy(rows, dayOf).map((t) => ({ day: t.key, value: Number(t.total) / 1e6 }))), [rows]);

  if (orgs.kind !== "ok" || !current || !admin) return null;
  return (
    <>
      <div style={{ gridColumn: "1 / span 12", display: "flex", justifyContent: "space-between", alignItems: "baseline", flexWrap: "wrap", gap: 12 }}>
        <h2 className="h3">{current.name}</h2>
        <Updated at={at} onRefresh={() => setNonce((n) => n + 1)} busy={usage.kind === "loading"} />
      </div>
      <BudgetBanner status={status} />
      {spikes.size > 0 && <p className="notice-ok" role="alert" style={{ gridColumn: "1 / span 12" }}><b>Unusual spend on {[...spikes].join(", ")}.</b> See Usage.</p>}
      {policy.kind === "ok" && policy.data.kill_switch && (
        <p className="notice-ok" role="alert" style={{ gridColumn: "1 / span 12", background: "var(--yellow)", borderColor: "var(--ink)" }}>
          <b>Kill switch is ON.</b> Every model call from this organisation is being refused{policy.data.kill_switch_reason ? `: ${policy.data.kill_switch_reason}` : "."}
        </p>
      )}
      {usage.kind !== "ok" ? <div style={{ gridColumn: "1 / span 12" }}><AdminNotice state={usage} /></div> : (
        <div className="stats">
          <Stat label="Kill switch" value={policy.kind === "ok" ? (policy.data.kill_switch ? "ON" : "Off") : "…"} bad={policy.kind === "ok" && policy.data.kill_switch}
            note={policy.kind === "ok" ? "Organisation may call models" : policy.kind === "error" && policy.status === 409 ? "No policy yet: nothing is allowed" : undefined} />
          <Stat label="Spend this month" value={formatUsd(month.monthToDate)} note={budget != null && budget > 0n ? `${percent(month.monthToDate, budget)}% of ${formatUsd(budget)} budget` : "No budget set"} />
          <Stat label="Projected month end" value={formatUsd(month.projected)} note="Linear pace" bad={budget != null && budget > 0n && month.projected > budget} />
          <Stat label="Last 7 days" value={formatUsd(week$.total)} note={<>{week$.calls} calls{prevWeek$ && <><br /><Delta cur={Number(week$.total) / 1e6} prev={Number(prevWeek$.total) / 1e6} label="vs the 7 days before" /></>}</>} />
        </div>
      )}
      <hr className="rule" style={{ gridColumn: "1 / span 12" }} />
      <h2 className="h3" style={{ gridColumn: "1 / span 12" }}>Platform</h2>
    </>
  );
}

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

  const blocked = ["forbidden", "unauthenticated", "unconfigured"].includes(ingest.kind);
  return (
    <>
      <Hero marker="OVERVIEW" title="Operations" lead="Health of ingestion and retrieval, and the work waiting on a human." />
      <section className="frame grid" style={{ paddingBottom: 120, rowGap: 40 }}>
        <OrgKpis />
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
