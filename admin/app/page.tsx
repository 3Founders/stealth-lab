"use client";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Hero } from "@/components/Admin";
import { Bars, ControlRow, Kpi, Section, ShareBars, StatusBanner } from "@/components/ExecWidgets";
import OrgGate from "@/components/OrgGate";
import { useOrg } from "@/components/OrgContext";
import { RangePicker, useRange } from "@/components/Range";
import { Delta, Updated } from "@/components/Widgets";
import { anomalousDays } from "@/lib/alerts";
import { reasonText } from "@/lib/denials";
import { activeUsers, activeUsersByDay, adoptionRate, concentration, costPerRequest, failureByDay, failureRate, successRate, topShares, verdict } from "@/lib/exec";
import { formatUsd, toMicros } from "@/lib/money";
import {
  getAudit, getBudget, getDenials, getMembers, getPerformanceSummary, getPolicy, getUsage, getUsageByUser,
  type ApiState, type AuditPage, type BudgetPosition, type Member, type Org, type OrgPolicy, type PerfSummaryRow, type UsageRow, type UserUsageRow,
} from "@/lib/org-api";
import { displayName, totalsByUser, userBudgets } from "@/lib/people";
import { fmtMs } from "@/lib/perf";
import { isRange, previousRange } from "@/lib/range";
import { dayOf, groupBy, projectMonth, sumAll } from "@/lib/usage";

type Denials = { counts_by_reason: { reason_code: string; count: number }[] };
const usd = (m: bigint) => Number(m) / 1e6; // charts and change chips only; every sum stays in exact micro-dollars
const pct1 = (v: number | null) => (v == null ? "—" : `${(v * 100).toFixed(1)}%`);
const num = (v: unknown) => (v == null || v === "" || !Number.isFinite(Number(v)) ? null : Number(v));
const ok = <T,>(s: ApiState<T>) => (s.kind === "ok" ? s.data : null);

function ExecutivePanel({ org }: { org: Org }) {
  const { range, ready, setRange } = useRange(30);
  const id = org.organization_id;
  const [usage, setUsage] = useState<ApiState<{ rows: UsageRow[] }>>({ kind: "loading" });
  const [prevUsage, setPrevUsage] = useState<ApiState<{ rows: UsageRow[] }>>({ kind: "loading" });
  const [users, setUsers] = useState<ApiState<{ rows: UserUsageRow[] }>>({ kind: "loading" });
  const [prevUsers, setPrevUsers] = useState<ApiState<{ rows: UserUsageRow[] }>>({ kind: "loading" });
  const [perf, setPerf] = useState<ApiState<{ rows: PerfSummaryRow[] }>>({ kind: "loading" });
  const [prevPerf, setPrevPerf] = useState<ApiState<{ rows: PerfSummaryRow[] }>>({ kind: "loading" });
  const [policy, setPolicy] = useState<ApiState<OrgPolicy>>({ kind: "loading" });
  const [budget, setBudget] = useState<ApiState<BudgetPosition>>({ kind: "loading" });
  const [denials, setDenials] = useState<ApiState<Denials>>({ kind: "loading" });
  const [audit, setAudit] = useState<ApiState<AuditPage>>({ kind: "loading" });
  const [members, setMembers] = useState<ApiState<{ members: Member[] }>>({ kind: "loading" });
  const [loadedAt, setLoadedAt] = useState<Date | null>(null);
  const [nonce, setNonce] = useState(0);
  const refresh = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    if (!ready || !isRange(range)) return;
    const ac = new AbortController();
    const s = ac.signal;
    const [a, b] = [`${range.since}T00:00:00Z`, `${range.until}T00:00:00Z`];
    const p = previousRange(range);
    const [pa, pb] = [`${p.since}T00:00:00Z`, `${p.until}T00:00:00Z`];
    [setUsage, setPrevUsage, setUsers, setPrevUsers, setPerf, setPrevPerf, setPolicy, setBudget, setDenials, setAudit, setMembers].forEach((f) => (f as (v: ApiState<never>) => void)({ kind: "loading" }));
    // Every source loads and fails independently: an unavailable one blanks its own card, never the page.
    Promise.all([
      getUsage(id, a, b, s).then(setUsage), getUsage(id, pa, pb, s).then(setPrevUsage),
      getUsageByUser(id, a, b, s).then(setUsers), getUsageByUser(id, pa, pb, s).then(setPrevUsers),
      getPerformanceSummary(id, a, b, "total", s).then(setPerf), getPerformanceSummary(id, pa, pb, "total", s).then(setPrevPerf),
      getPolicy(id, s).then(setPolicy), getBudget(id, s).then(setBudget),
      getDenials(id, a, b, undefined, 1, s).then(setDenials), getAudit(id, a, b, 0, 500, s).then(setAudit), getMembers(id, s).then(setMembers),
    ]).then(() => { if (!s.aborted) setLoadedAt(new Date()); });
    return () => ac.abort();
  }, [id, range, ready, nonce]);

  const rows = ok(usage)?.rows ?? [];
  const total = useMemo(() => sumAll(rows), [rows]);
  const prevTotal = useMemo(() => (ok(prevUsage) ? sumAll(ok(prevUsage)!.rows) : null), [prevUsage]);
  const byDay = useMemo(() => groupBy(rows, dayOf).sort((a, b) => a.key.localeCompare(b.key)), [rows]);
  const models = useMemo(() => topShares(groupBy(rows, (r) => `${r.provider} / ${r.model}`), 4), [rows]);
  const spikes = useMemo(() => anomalousDays(byDay.map((t) => ({ day: t.key, value: usd(t.total) }))), [byDay]);

  const userRows = ok(users)?.rows ?? [];
  const people = ok(members)?.members ?? [];
  const spenders = useMemo(() => totalsByUser(userRows), [userRows]);
  const active = activeUsers(userRows);
  const prevActive = ok(prevUsers) ? activeUsers(ok(prevUsers)!.rows) : null;
  const memberCount = people.filter((m) => m.is_active).length;
  const adoption = adoptionRate(active, memberCount);
  const grand = spenders.reduce((s, t) => s + t.total, 0n);
  const top5 = spenders.slice(0, 5).reduce((s, t) => s + t.total, 0n);
  const top5pct = grand > 0n ? Number((top5 * 1000n) / grand) / 10 : null;

  const perfNow = ok(perf)?.rows[0] ?? null;
  const perfPrev = ok(prevPerf)?.rows[0] ?? null;
  const b = ok(budget);
  const pol = ok(policy);
  const noPolicy = (budget.kind === "error" && budget.status === 409) || (policy.kind === "error" && policy.status === 409);

  // Budget position: the live ledger when available, else this period's rows if they reach the 1st of the month.
  const now = new Date();
  const daysInMonth = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() + 1, 0)).getUTCDate();
  const fromRows = range.since <= `${now.toISOString().slice(0, 7)}-01` ? projectMonth(rows, now) : null;
  const monthToDate = b ? toMicros(b.monthly.used_usd) : fromRows?.monthToDate ?? 0n;
  const projected = b ? (monthToDate * BigInt(daysInMonth)) / BigInt(now.getUTCDate()) : fromRows?.projected ?? 0n;
  const monthlyBudget = b ? toMicros(b.monthly.budget_usd) : pol ? toMicros(pol.monthly_budget_usd) : null;
  const dailyPace = monthlyBudget != null && monthlyBudget > 0n ? usd(monthlyBudget) / daysInMonth : null;
  const atCap = b ? userBudgets(b).filter((u) => u.position.state === "at") : [];

  const fail = failureRate(total.calls, total.failed);
  const ready_ = usage.kind === "ok";
  const v = ready_ ? verdict({
    calls: total.calls, killSwitch: pol?.kill_switch ?? b?.kill_switch ?? null, noPolicy, budget: monthlyBudget, monthToDate, projected,
    failureRate: fail, unpricedCalls: total.upperBoundCalls, spikeDays: spikes.size, usersAtCap: atCap.length,
    auditIntact: audit.kind === "ok" ? audit.data.chain_intact : null,
  }) : null;

  const refusals = ok(denials)?.counts_by_reason ?? [];
  const refused = refusals.reduce((s, c) => s + c.count, 0);
  const topRefusal = [...refusals].sort((x, y) => y.count - x.count)[0];
  const perCall = costPerRequest(total.total, total.calls);
  const prevPerCall = prevTotal ? costPerRequest(prevTotal.total, prevTotal.calls) : null;
  const unavailable = (s: ApiState<unknown>) => (s.kind === "loading" ? "…" : "unavailable");

  if (usage.kind !== "ok" && usage.kind !== "loading") {
    return (
      <div className="empty" style={{ gridColumn: "1 / span 12" }}>
        <b>{usage.kind === "forbidden" ? "Executive view needs an owner or admin account." : usage.kind === "unauthenticated" ? "Sign in to continue." : "The numbers couldn’t be loaded."}</b>
        <p>{usage.kind === "error" ? usage.message : usage.kind === "unconfigured" ? "No backend is connected." : "The organisation’s governance data is served only to its owners and admins."}</p>
        {usage.kind === "unauthenticated" && <p><Link href="/sign-in" style={{ textDecoration: "underline" }}>Sign in</Link></p>}
      </div>
    );
  }

  return (
    <>
      <div className="no-print"><RangePicker range={range} setRange={setRange} idPrefix="exec" /></div>
      <div style={{ gridColumn: "1 / span 12", display: "flex", gap: 16, alignItems: "center", flexWrap: "wrap" }}>
        <Updated at={loadedAt} onRefresh={refresh} busy={usage.kind === "loading"} />
        <button type="button" className="btn-line no-print" onClick={() => window.print()}>Print / save as PDF</button>
        <span className="small dim print-only">{org.name} · {range.since} to {range.until} (UTC)</span>
      </div>

      <StatusBanner verdict={v} scope={`${org.name} · ${range.since} → ${range.until}`} />

      <div className="stats">
        <Kpi label="AI spend" value={usage.kind === "loading" ? "…" : formatUsd(total.total)}
          change={prevTotal && <Delta cur={usd(total.total)} prev={usd(prevTotal.total)} />}
          caption={monthlyBudget != null && monthlyBudget > 0n ? `Month to date ${formatUsd(monthToDate)} of ${formatUsd(monthlyBudget)} budget · on pace for ${formatUsd(projected)}` : "No monthly budget set"} />
        <Kpi label="People using it" value={users.kind === "ok" ? active : unavailable(users)}
          change={prevActive != null && <Delta cur={active} prev={prevActive} upIsBad={false} />}
          caption={adoption != null ? `${(adoption * 100).toFixed(0)}% of ${memberCount} members` : members.kind === "ok" ? "No member count" : undefined} />
        <Kpi label="Requests" value={usage.kind === "loading" ? "…" : total.calls.toLocaleString()}
          change={prevTotal && <Delta cur={total.calls} prev={prevTotal.calls} upIsBad={false} />}
          caption={fail == null ? "None in this period" : `${pct1(successRate(total.calls, total.failed))} succeeded`} />
        <Kpi label="Average cost per request" value={perCall == null ? "—" : formatUsd(perCall)}
          change={perCall != null && prevPerCall != null && <Delta cur={usd(perCall)} prev={usd(prevPerCall)} />} caption="Spend ÷ requests" />
        <Kpi label="Typical wait (95th pct.)" value={perf.kind === "ok" ? fmtMs(perfNow?.provider_p95_ms) : unavailable(perf)}
          change={num(perfNow?.provider_p95_ms) != null && num(perfPrev?.provider_p95_ms) != null && <Delta cur={num(perfNow!.provider_p95_ms)!} prev={num(perfPrev!.provider_p95_ms)!} />}
          caption={perfNow ? `Half of requests finish within ${fmtMs(perfNow.provider_p50_ms)}` : "19 in 20 requests are faster than this"} />
      </div>

      <Section title="Spending" lead="What is being spent, how it is trending, and where it goes.">
        <Bars ariaLabel="Daily AI spend" points={byDay.map((t) => ({ label: t.key, value: usd(t.total), note: spikes.has(t.key) ? "unusually high" : undefined }))}
          format={(x) => formatUsd(toMicros(x.toFixed(6)))} reference={dailyPace} referenceLabel="even pace to stay on the monthly budget" />
        <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 32 }}>
          <div>
            <h3 className="small dim" style={{ fontWeight: 400, marginBottom: 8 }}>By model</h3>
            <ShareBars shares={models} label="Spend by model" />
            {concentration(models) && models.length > 0 && <p className="small dim" style={{ marginTop: 8 }}>{concentration(models)!.pct.toFixed(0)}% of spend is on {concentration(models)!.key}.</p>}
          </div>
          <div>
            <h3 className="small dim" style={{ fontWeight: 400, marginBottom: 8 }}>By person</h3>
            {users.kind !== "ok" ? <p className="small dim">Per-person spend is {unavailable(users)}.</p> : spenders.length === 0 ? <p className="small dim">No attributed spend.</p> : (
              <>
                <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "grid", gap: 6 }} aria-label="Top spenders">
                  {spenders.slice(0, 5).map((t) => (
                    <li key={t.subject} style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
                      <span>{displayName(t.subject, people)}</span><span><b>{grand > 0n ? Math.round(Number((t.total * 1000n) / grand) / 10) : 0}%</b> <span className="dim">· {formatUsd(t.total)}</span></span>
                    </li>
                  ))}
                </ul>
                {top5pct != null && spenders.length > 5 && <p className="small dim" style={{ marginTop: 8 }}>The top 5 people account for {top5pct.toFixed(0)}% of spend.</p>}
              </>
            )}
          </div>
        </div>
      </Section>

      <Section title="Adoption" lead="Whether people are actually using it, and whether that is growing.">
        {users.kind !== "ok" ? <p className="small dim">Adoption data is {unavailable(users)}.</p> : (
          <>
            <Bars ariaLabel="People active per day" points={activeUsersByDay(userRows).map((p) => ({ label: p.day, value: p.value }))} format={(x) => `${x} ${x === 1 ? "person" : "people"}`} />
            <p className="small">{active} of {memberCount || "an unknown number of"} members made at least one request in this period{adoption != null ? ` (${(adoption * 100).toFixed(0)}%)` : ""}.</p>
          </>
        )}
      </Section>

      <Section title="Reliability and speed" lead="Whether requests work, and how quickly they come back.">
        <Bars ariaLabel="Share of requests that failed, per day" color="var(--ink)" points={failureByDay(byDay).map((p) => ({ label: p.day, value: p.value }))}
          format={(x) => `${(x * 100).toFixed(1)}% failed`} reference={0.05} referenceLabel="5% watch level" />
        <div className="stats">
          <Kpi label="Succeeded" value={pct1(successRate(total.calls, total.failed))} caption={`${total.failed} of ${total.calls.toLocaleString()} requests failed`} />
          <Kpi label="Slowest 1 in 100" value={perf.kind === "ok" ? fmtMs(perfNow?.provider_p99_ms) : unavailable(perf)} caption="99th percentile wait" />
          <Kpi label="Our share of wait time" value={perfNow && num(perfNow.gate_time_share) != null ? pct1(num(perfNow.gate_time_share)) : "—"} caption="Time spent in our checks before the model is called" />
        </div>
      </Section>

      <Section title="Risk and control" lead="Whether the safeguards are in place and working.">
        <div>
          <ControlRow name="Emergency stop" ok={pol || b ? !(pol?.kill_switch ?? b?.kill_switch) : null}
            state={pol || b ? ((pol?.kill_switch ?? b?.kill_switch) ? "ON: blocking all calls" : "Off") : unavailable(policy)} detail="Can halt all AI use for the organisation instantly." />
          <ControlRow name="Usage policy" ok={noPolicy ? false : pol ? true : null} state={noPolicy ? "Not set" : pol ? "In force" : unavailable(policy)}
            detail={pol ? `Allows ${pol.allowed_models.length} model${pol.allowed_models.length === 1 ? "" : "s"} and ${pol.allowed_tools.length} tool${pol.allowed_tools.length === 1 ? "" : "s"}; version ${pol.version}.` : "Which models, tools and data classes are permitted."} />
          <ControlRow name="Spending limits" ok={monthlyBudget == null ? null : monthlyBudget > 0n ? true : false}
            state={monthlyBudget == null ? unavailable(budget) : monthlyBudget > 0n ? "Set" : "Zero (blocks spend)"}
            detail={monthlyBudget != null && b ? `${formatUsd(monthlyBudget)} a month, ${formatUsd(toMicros(b.per_user_daily_budget_usd))} per person per day.` : undefined} />
          <ControlRow name="Requests refused by policy" ok={denials.kind === "ok" ? refused === 0 : null} state={denials.kind === "ok" ? `${refused} in this period` : unavailable(denials)}
            detail={topRefusal ? `Most common: ${reasonText(topRefusal.reason_code)} (${topRefusal.count}). Covers policy and budget refusals only.` : denials.kind === "ok" ? "Covers policy and budget refusals only." : undefined} />
          <ControlRow name="Audit log integrity" ok={audit.kind === "ok" ? audit.data.chain_intact : null}
            state={audit.kind === "ok" ? (audit.data.chain_intact ? "Verified" : "FAILED") : unavailable(audit)}
            detail={audit.kind === "ok" ? `${audit.data.events.length}${audit.data.next_after_id != null ? "+" : ""} events checked${audit.data.chain_intact ? "; none altered." : "; the log may have been altered."}` : "Tamper-evident record of governance actions."} />
          <ControlRow name="People at today's personal cap" ok={b ? atCap.length === 0 : null} state={b ? String(atCap.length) : unavailable(budget)} detail="Individuals blocked until tomorrow by their daily spending cap." />
        </div>
      </Section>

      <Section title="Return on investment" lead="The value delivered for the spend above.">
        <div className="empty">
          <b>Not measured yet.</b>
          <p>This view shows what is spent and how it is used. It does not yet show what that spending achieved (tasks completed, hours saved, work avoided), and it will not estimate that without a stated method and a comparison group. Cost per request is a unit cost, not a measure of value.</p>
        </div>
      </Section>

      <p className="small dim" style={{ gridColumn: "1 / span 12", maxWidth: "60em" }}>
        Figures cover model calls made through keळ only, in UTC days, and change is measured against the equal-length period just before the one shown.
        Spend is summed exactly from the ledger; a request whose cost couldn&rsquo;t be determined is counted at its worst case, never zero.
        Status thresholds are defaults: a failure rate of 5% or more is a watch item and 15% or more needs action.
      </p>
    </>
  );
}

export default function ExecutivePage() {
  const { orgs } = useOrg();
  return (
    <>
      <Hero marker="EXECUTIVE" title="At a glance" lead="Spend, adoption, reliability and control for your organisation's use of keळ." />
      <section className="frame grid" style={{ paddingBottom: 120, rowGap: 32 }}>
        {orgs.kind === "ok" && orgs.data.length === 0 ? (
          <div className="empty" style={{ gridColumn: "1 / span 12" }}>
            <b>This account isn&rsquo;t in an organisation.</b>
            <p>The executive view is for an organisation&rsquo;s owners and admins. Platform operators can use <Link href="/operations" style={{ textDecoration: "underline" }}>Operations</Link>.</p>
          </div>
        ) : (
          <OrgGate need="admin">{(org) => <ExecutivePanel key={org.organization_id} org={org} />}</OrgGate>
        )}
      </section>
    </>
  );
}
