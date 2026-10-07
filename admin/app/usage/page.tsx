"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { AdminNotice, Hero, Stat } from "@/components/Admin";
import OrgGate from "@/components/OrgGate";
import { RangePicker, useRange } from "@/components/Range";
import { BudgetBanner, CsvButton, Delta, Updated } from "@/components/Widgets";
import { anomalousDays, budgetStatus } from "@/lib/alerts";
import { formatUsd, percent, perMillionTokens, toMicros, type Micros } from "@/lib/money";
import { getPolicy, getUsage, type ApiState, type Org, type OrgPolicy, type UsageRow } from "@/lib/org-api";
import { isRange, previousRange } from "@/lib/range";
import { dayOf, groupBy, monthStartOf, processing, projectMonth, sumAll, tokensProcessing, type Totals } from "@/lib/usage";

const compact = (n: number) => new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 }).format(n);
const pct = (p: number | null) => (p == null ? "—" : `${p.toFixed(1)}%`);
const usd = (m: Micros) => Number(m) / 1e6; // display/delta only; all sums stay in exact micro-dollars

// Bucket colours come from the shared tokens; the legend and the numbers below carry the meaning, not colour alone.
const COLORS = { input: "var(--ink)", output: "var(--yellow)", processing: "var(--cobalt)", other: "var(--grey)" } as const;
const LABELS = { input: "Input", output: "Output", processing: "Processing", other: "Other" } as const;

function buckets(t: Totals): Record<keyof typeof LABELS, Micros> {
  return { input: t.input, output: t.output, processing: processing(t), other: t.other };
}

function Legend() {
  return (
    <ul className="small" style={{ display: "flex", flexWrap: "wrap", gap: "6px 20px", listStyle: "none", padding: 0, margin: 0 }} aria-label="Cost buckets">
      {(Object.keys(LABELS) as (keyof typeof LABELS)[]).map((k) => (
        <li key={k} style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <i aria-hidden="true" style={{ width: 12, height: 12, background: COLORS[k], display: "inline-block", border: "1px solid var(--rule-strong)" }} />{LABELS[k]}
        </li>
      ))}
    </ul>
  );
}

function BreakdownTable({ title, label, rows, file, flagged }: { title: string; label: string; rows: Totals[]; file: string; flagged?: Set<string> }) {
  return (
    <div className="panel scroll-x">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
        <h2 className="h3">{title}</h2>
        <CsvButton filename={file} header={[label, "Calls", "Failed", "Input USD", "Output USD", "Processing USD", "Cache read USD", "Cache write USD", "Other USD", "Total USD", "Unpriced calls"]}
          rows={() => rows.map((t) => [t.key, t.calls, t.failed, formatUsd(t.input, { cents: true }).slice(1), formatUsd(t.output, { cents: true }).slice(1), formatUsd(processing(t), { cents: true }).slice(1),
            formatUsd(t.cacheRead, { cents: true }).slice(1), formatUsd(t.cacheWrite, { cents: true }).slice(1), formatUsd(t.other, { cents: true }).slice(1), formatUsd(t.total, { cents: true }).slice(1), t.upperBoundCalls])} />
      </div>
      <table className="dtable">
        <thead><tr><th>{label}</th><th>Calls</th><th>Input</th><th>Output</th><th>Processing</th><th>Other</th><th>Total</th></tr></thead>
        <tbody>
          {rows.map((t) => (
            <tr key={t.key}>
              <td>{t.key}{flagged?.has(t.key) && <span title="Unusually high against the previous 14 days" style={{ background: "var(--yellow)", marginLeft: 8, padding: "0 6px", fontSize: 12, letterSpacing: ".06em", textTransform: "uppercase" }}>spike</span>}</td>
              <td>{t.calls}</td>
              <td>{formatUsd(t.input)}</td><td>{formatUsd(t.output)}</td><td>{formatUsd(processing(t))}</td><td>{formatUsd(t.other)}</td>
              <td>{formatUsd(t.total)}{t.upperBoundCalls > 0 && <span className="dim"> · {t.upperBoundCalls} unpriced</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function UsagePanel({ org }: { org: Org }) {
  const { range, ready, setRange } = useRange(30);
  const [usage, setUsage] = useState<ApiState<{ rows: UsageRow[] }>>({ kind: "loading" });
  const [prev, setPrev] = useState<ApiState<{ rows: UsageRow[] }>>({ kind: "loading" });
  const [policy, setPolicy] = useState<ApiState<OrgPolicy>>({ kind: "loading" });
  const [loadedAt, setLoadedAt] = useState<Date | null>(null);
  const [nonce, setNonce] = useState(0);
  const refresh = useCallback(() => setNonce((n) => n + 1), []);
  const valid = isRange(range);

  useEffect(() => {
    const ac = new AbortController();
    getPolicy(org.organization_id, ac.signal).then(setPolicy);
    return () => ac.abort();
  }, [org.organization_id, nonce]);

  useEffect(() => {
    if (!ready || !valid) return;
    const ac = new AbortController();
    setUsage({ kind: "loading" });
    setPrev({ kind: "loading" });
    const p = previousRange(range);
    Promise.all([
      getUsage(org.organization_id, `${range.since}T00:00:00Z`, `${range.until}T00:00:00Z`, ac.signal),
      getUsage(org.organization_id, `${p.since}T00:00:00Z`, `${p.until}T00:00:00Z`, ac.signal),
    ]).then(([cur, before]) => {
      setUsage(cur);
      setPrev(before);
      if (cur.kind === "ok") setLoadedAt(new Date());
    });
    return () => ac.abort();
  }, [org.organization_id, range, ready, valid, nonce]);

  const rows = usage.kind === "ok" ? usage.data.rows : [];
  const prevTotal = useMemo(() => (prev.kind === "ok" ? sumAll(prev.data.rows) : null), [prev]);
  const total = useMemo(() => sumAll(rows), [rows]);
  const byDay = useMemo(() => groupBy(rows, dayOf).sort((a, b) => a.key.localeCompare(b.key)), [rows]);
  const byModel = useMemo(() => groupBy(rows, (r) => `${r.provider} / ${r.model}`).sort((a, b) => (b.total > a.total ? 1 : -1)), [rows]);
  const byTool = useMemo(() => groupBy(rows, (r) => r.tool || "(none)").sort((a, b) => b.calls - a.calls), [rows]);
  const spikes = useMemo(() => anomalousDays(byDay.map((t) => ({ day: t.key, value: usd(t.total) }))), [byDay]);

  const now = new Date();
  const covers = range.since <= monthStartOf(now);
  const month = useMemo(() => projectMonth(rows, new Date()), [rows]);
  const budget = policy.kind === "ok" ? toMicros(policy.data.monthly_budget_usd) : null;
  const perUserDaily = policy.kind === "ok" ? toMicros(policy.data.per_user_daily_budget_usd) : null;
  const status = covers ? budgetStatus(month.monthToDate, month.projected, budget) : null;
  const hasSplit = rows.some((r) => r.cost_input_usd !== undefined);

  // Cumulative month-to-date spend, one bar per day, against the monthly budget line: the burn-down.
  const burn = useMemo(() => {
    const prefix = new Date().toISOString().slice(0, 7);
    let run = 0n;
    return byDay.filter((t) => t.key.startsWith(prefix)).map((t) => ({ day: t.key, cum: (run += t.total) }));
  }, [byDay]);
  const burnMax = burn.reduce((m, p) => (p.cum > m ? p.cum : m), budget ?? 0n);

  const b = buckets(total);
  const keys = Object.keys(LABELS) as (keyof typeof LABELS)[];
  const maxDay = byDay.reduce((m, t) => (t.total > m ? t.total : m), 0n);
  const unit: { label: string; cost: Micros; tokens: number }[] = [
    { label: "Input (fresh)", cost: total.input, tokens: total.tokensInput },
    { label: "Output", cost: total.output, tokens: total.tokensOutput },
    { label: "Processing: cache read", cost: total.cacheRead, tokens: total.tokensCacheRead },
    { label: "Processing: cache write", cost: total.cacheWrite, tokens: total.tokensCacheWrite },
    { label: "Processing (combined)", cost: processing(total), tokens: tokensProcessing(total) },
  ];
  const d = (cur: Micros, p: Micros | undefined) => (prevTotal && p !== undefined ? <Delta cur={usd(cur)} prev={usd(p)} /> : null);

  return (
    <>
      <RangePicker range={range} setRange={setRange} idPrefix="usage" />
      <div style={{ gridColumn: "1 / span 12" }}><Updated at={loadedAt} onRefresh={refresh} busy={usage.kind === "loading"} /></div>
      <BudgetBanner status={status} />

      {usage.kind !== "ok" ? <div style={{ gridColumn: "1 / span 12" }}><AdminNotice state={usage} /></div> : rows.length === 0 ? (
        <div className="empty" style={{ gridColumn: "1 / span 12" }}><b>No model calls in this range.</b><p>Only <code>call_model</code> calls are ledgered so far.</p></div>
      ) : (
        <>
          {!hasSplit && (
            <p className="notice-ok" style={{ gridColumn: "1 / span 12" }}>
              This backend doesn&rsquo;t report the per-component cost split yet, so input, output and processing show $0 and the whole amount is under Other.
            </p>
          )}
          {total.upperBoundCalls > 0 && (
            <p className="notice-ok" role="alert" style={{ gridColumn: "1 / span 12" }}>
              <b>{total.upperBoundCalls} call{total.upperBoundCalls === 1 ? " has" : "s have"} unknown cost; the worst case is counted</b>, never zero. Totals here are an upper bound for those calls.
            </p>
          )}
          {total.inconsistentRows > 0 && (
            <p className="notice-ok" role="alert" style={{ gridColumn: "1 / span 12" }}>
              {total.inconsistentRows} row{total.inconsistentRows === 1 ? "" : "s"} where the components don&rsquo;t add up to the row total (beyond rounding). The totals shown follow the ledger&rsquo;s own total; please report this.
            </p>
          )}
          {spikes.size > 0 && (
            <p className="notice-ok" role="alert" style={{ gridColumn: "1 / span 12" }}>
              <b>Unusual spend on {[...spikes].join(", ")}</b>: at least 1.5× and a statistical outlier against the previous 14 days. See the daily table.
            </p>
          )}

          <div className="stats">
            <Stat label="Input cost" value={formatUsd(b.input)} note={<>{pct(percent(b.input, total.total))} of spend · {compact(total.tokensInput)} fresh tokens<br />{d(b.input, prevTotal?.input)}</>} />
            <Stat label="Output cost" value={formatUsd(b.output)} note={<>{pct(percent(b.output, total.total))} of spend · {compact(total.tokensOutput)} tokens<br />{d(b.output, prevTotal?.output)}</>} />
            <Stat label="Processing cost" value={formatUsd(b.processing)}
              note={<>{pct(percent(b.processing, total.total))} of spend · cache read {formatUsd(total.cacheRead)} + write {formatUsd(total.cacheWrite)}<br />{d(b.processing, prevTotal ? processing(prevTotal) : undefined)}</>} />
            <Stat label="Other (no split)" value={formatUsd(b.other)} note={`${pct(percent(b.other, total.total))} · provider-reported or estimated`} bad={total.upperBoundCalls > 0} />
            <Stat label="Total" value={formatUsd(total.total)} note={<>{total.calls} calls · {total.failed} failed<br />{d(total.total, prevTotal?.total)}</>} />
          </div>
          {prev.kind === "ok" && prevTotal && <p className="small dim" style={{ gridColumn: "1 / span 12" }}>Changes compare against the previous {byDay.length > 0 ? "period of equal length" : "period"} ({previousRange(range).since} → {previousRange(range).until}); {prevTotal.calls} calls then.</p>}

          <div className="panel">
            <h2 className="h3">Month to date</h2>
            {!covers ? <p className="small dim">Widen the range to include the 1st of this month to see month-to-date, a projection and the burn-down.</p> : (
              <>
                <div className="stats">
                  <Stat label="Month to date" value={formatUsd(month.monthToDate)} note={`day ${month.daysElapsed} of ${month.daysInMonth}`} />
                  <Stat label="Projected month end" value={formatUsd(month.projected)} note="Linear: month to date ÷ days elapsed × days in month. A trend, not a forecast."
                    bad={budget != null && budget > 0n && month.projected > budget} />
                  <Stat label="Monthly budget" value={budget == null ? "—" : formatUsd(budget)}
                    note={budget == null ? "Policy unavailable" : budget > 0n ? `${pct(percent(month.monthToDate, budget))} used · projection ${pct(percent(month.projected, budget))} of budget` : "0 means no spend is allowed"}
                    bad={budget != null && month.monthToDate >= budget} />
                  <Stat label="Per-user daily budget" value={perUserDaily == null ? "—" : formatUsd(perUserDaily)} note={<>A per-user cap. Each person&rsquo;s spend against it is on the <a href="/people" style={{ textDecoration: "underline" }}>People</a> page.</>} />
                </div>
                {burn.length > 0 && (
                  <>
                    <h3 className="small dim" style={{ fontWeight: 400, marginTop: 8 }}>Burn-down: cumulative spend this month{budget != null && budget > 0n ? " against the budget line" : ""}</h3>
                    <div role="img" aria-label="Cumulative spend this month against the monthly budget" style={{ position: "relative", display: "flex", alignItems: "flex-end", gap: 3, height: 120, borderBottom: "1px solid var(--rule-strong)" }}>
                      {burn.map((p) => (
                        <div key={p.day} title={`${p.day}: ${formatUsd(p.cum)} cumulative`}
                          style={{ flex: 1, minWidth: 3, height: burnMax > 0n ? `${Number((p.cum * 1000n) / burnMax) / 10}%` : 0, background: budget != null && p.cum >= budget ? "var(--ink)" : "var(--cobalt)" }} />
                      ))}
                      {budget != null && budget > 0n && burnMax > 0n && (
                        <div aria-hidden="true" title={`Budget ${formatUsd(budget)}`} style={{ position: "absolute", left: 0, right: 0, bottom: `${Number((budget * 1000n) / burnMax) / 10}%`, borderTop: "2px dashed var(--ink)" }} />
                      )}
                    </div>
                    <p className="small dim">Dashed line = monthly budget ({budget != null ? formatUsd(budget) : "n/a"}). Bars turn dark once cumulative spend reaches it.</p>
                  </>
                )}
              </>
            )}
          </div>

          <div className="panel">
            <h2 className="h3">Daily trend</h2>
            <Legend />
            <div role="img" aria-label="Daily cost by bucket, stacked" style={{ display: "flex", alignItems: "flex-end", gap: 3, height: 140, borderBottom: "1px solid var(--rule-strong)" }}>
              {byDay.map((t) => {
                const bk = buckets(t);
                const h = maxDay > 0n ? Number((t.total * 1000n) / maxDay) / 10 : 0;
                return (
                  <div key={t.key} title={`${t.key}: ${formatUsd(t.total)} (input ${formatUsd(bk.input)}, output ${formatUsd(bk.output)}, processing ${formatUsd(bk.processing)}, other ${formatUsd(bk.other)})${spikes.has(t.key) ? " · spike" : ""}`}
                    style={{ flex: 1, minWidth: 3, height: `${h}%`, display: "flex", flexDirection: "column-reverse", outline: spikes.has(t.key) ? "2px solid var(--ink)" : undefined }}>
                    {keys.map((k) => (
                      <div key={k} style={{ background: COLORS[k], height: t.total > 0n ? `${Number((bk[k] * 1000n) / t.total) / 10}%` : 0 }} />
                    ))}
                  </div>
                );
              })}
            </div>
            <p className="small dim">{byDay[0]?.key} → {byDay[byDay.length - 1]?.key} (UTC days). Outlined bars are spikes. Hover a bar for its split; the table below has every number.</p>
          </div>

          <div className="panel scroll-x">
            <h2 className="h3">Effective price per 1M tokens</h2>
            <p className="small dim">Dollars the ledger recorded ÷ tokens, so it reflects the prices that applied at call time. Skipped where there were no tokens.</p>
            <table className="dtable">
              <thead><tr><th>Component</th><th>Cost</th><th>Tokens</th><th>$ per 1M tokens</th></tr></thead>
              <tbody>
                {unit.map((u) => {
                  const p = perMillionTokens(u.cost, u.tokens);
                  return (<tr key={u.label}><td>{u.label}</td><td>{formatUsd(u.cost)}</td><td>{compact(u.tokens)}</td><td>{p == null ? "—" : formatUsd(p, { cents: true })}</td></tr>);
                })}
              </tbody>
            </table>
          </div>

          <BreakdownTable title="By model" label="Provider / model" rows={byModel} file={`usage-by-model-${range.since}-${range.until}.csv`} />
          <BreakdownTable title="By tool" label="Tool" rows={byTool} file={`usage-by-tool-${range.since}-${range.until}.csv`} />
          <BreakdownTable title="By day" label="Day (UTC)" rows={[...byDay].reverse()} file={`usage-by-day-${range.since}-${range.until}.csv`} flagged={spikes} />
        </>
      )}
    </>
  );
}

export default function UsagePage() {
  return (
    <>
      <Hero marker="GOVERNANCE" title="Usage" lead="Model calls and spend split into input, output and processing cost, straight from the provider-call ledger." />
      <section className="frame grid" style={{ paddingBottom: 120, rowGap: 32 }}>
        <OrgGate need="admin">{(org) => <UsagePanel key={org.organization_id} org={org} />}</OrgGate>
      </section>
    </>
  );
}
