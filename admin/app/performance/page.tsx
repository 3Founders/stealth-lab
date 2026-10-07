"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { AdminNotice, Hero, Stat } from "@/components/Admin";
import OrgGate from "@/components/OrgGate";
import { RangePicker, useRange } from "@/components/Range";
import { CsvButton, Delta, Updated } from "@/components/Widgets";
import { isRange, previousRange } from "@/lib/range";
import { getPerformance, getPerformanceSummary, type ApiState, type Org, type PerfGroupBy, type PerfSummaryRow, type PerformanceRow } from "@/lib/org-api";
import { METRICS, cacheRollup, dailySeries, dayOf, fmtMs, fmtRate, num, rowKey, tierLabel, type Metric } from "@/lib/perf";


function PerformancePanel({ org }: { org: Org }) {
  const { range, ready, setRange } = useRange(14);
  const [state, setState] = useState<ApiState<{ rows: PerformanceRow[] }>>({ kind: "loading" });
  const [prev, setPrev] = useState<ApiState<{ rows: PerformanceRow[] }>>({ kind: "loading" });
  const [loadedAt, setLoadedAt] = useState<Date | null>(null);
  const [nonce, setNonce] = useState(0);
  const refresh = useCallback(() => setNonce((n) => n + 1), []);
  // Merged-over-the-range latency: percentiles computed by the backend from the raw calls, never averaged here.
  const [groupBy, setGroupBy] = useState<PerfGroupBy>("model");
  const [summary, setSummary] = useState<ApiState<{ group_by: PerfGroupBy; rows: PerfSummaryRow[] }>>({ kind: "loading" });
  const [totalNow, setTotalNow] = useState<PerfSummaryRow | null>(null);
  const [totalPrev, setTotalPrev] = useState<PerfSummaryRow | null>(null);
  const [selectedDay, setSelectedDay] = useState("");
  const [series, setSeries] = useState("");
  const [metric, setMetric] = useState<Metric>("provider_p95_ms");

  useEffect(() => {
    if (!ready || !isRange(range)) return;
    const ac = new AbortController();
    setState({ kind: "loading" });
    setPrev({ kind: "loading" });
    const p = previousRange(range);
    Promise.all([
      getPerformance(org.organization_id, `${range.since}T00:00:00Z`, `${range.until}T00:00:00Z`, ac.signal),
      getPerformance(org.organization_id, `${p.since}T00:00:00Z`, `${p.until}T00:00:00Z`, ac.signal),
    ]).then(([cur, before]) => {
      setState(cur);
      setPrev(before);
      if (cur.kind === "ok") setLoadedAt(new Date());
    });
    return () => ac.abort();
  }, [org.organization_id, range, ready, nonce]);

  useEffect(() => {
    if (!ready || !isRange(range)) return;
    const ac = new AbortController();
    setSummary({ kind: "loading" });
    getPerformanceSummary(org.organization_id, `${range.since}T00:00:00Z`, `${range.until}T00:00:00Z`, groupBy, ac.signal).then(setSummary);
    return () => ac.abort();
  }, [org.organization_id, range, ready, groupBy, nonce]);

  // Overall merged percentiles for this range and the one before it: a true, comparable p95 (unlike averaging daily rows).
  useEffect(() => {
    if (!ready || !isRange(range)) return;
    const ac = new AbortController();
    const p = previousRange(range);
    Promise.all([
      getPerformanceSummary(org.organization_id, `${range.since}T00:00:00Z`, `${range.until}T00:00:00Z`, "total", ac.signal),
      getPerformanceSummary(org.organization_id, `${p.since}T00:00:00Z`, `${p.until}T00:00:00Z`, "total", ac.signal),
    ]).then(([cur, before]) => {
      setTotalNow(cur.kind === "ok" ? cur.data.rows[0] ?? null : null);
      setTotalPrev(before.kind === "ok" ? before.data.rows[0] ?? null : null);
    });
    return () => ac.abort();
  }, [org.organization_id, range, ready, nonce]);

  const rows = state.kind === "ok" ? state.data.rows : [];
  const days =useMemo(() => [...new Set(rows.map(dayOf))].sort().reverse(), [rows]);
  const seriesKeys = useMemo(() => [...new Set(rows.map(rowKey))].sort(), [rows]);
  const activeDay = days.includes(selectedDay) ? selectedDay : days[0] ?? "";
  const activeSeries = seriesKeys.includes(series) ? series : seriesKeys[0] ?? "";

  const dayRows = useMemo(
    () => rows.filter((r) => dayOf(r) === activeDay).sort((a, b) => tierLabel(a.tier).localeCompare(tierLabel(b.tier)) || a.model.localeCompare(b.model) || a.tool.localeCompare(b.tool)),
    [rows, activeDay],
  );
  const trend = useMemo(() => dailySeries(rows, activeSeries, metric), [rows, activeSeries, metric]);
  const trendMax = trend.reduce((m, p) => (p.value != null && p.value > m ? p.value : m), 0);
  const metricInfo = METRICS.find((m) => m.key === metric)!;
  const byTier = useMemo(() => cacheRollup(rows, (r) => tierLabel(r.tier)).sort((a, b) => a.key.localeCompare(b.key)), [rows]);
  const byModel = useMemo(() => cacheRollup(rows, (r) => `${r.provider} / ${r.model}`).sort((a, b) => b.calls - a.calls), [rows]);
  const totalCalls = rows.reduce((s, r) => s + (num(r.calls) ?? 0), 0);
  const overall = useMemo(() => cacheRollup(rows, () => "all")[0], [rows]);
  const prevRows = prev.kind === "ok" ? prev.data.rows : null;
  const prevCalls = prevRows ? prevRows.reduce((s, r) => s + (num(r.calls) ?? 0), 0) : null;
  const prevOverall = useMemo(() => (prevRows ? cacheRollup(prevRows, () => "all")[0] : null), [prevRows]);

  return (
    <>
      <RangePicker range={range} setRange={setRange} idPrefix="perf" />
      <div style={{ gridColumn: "1 / span 12" }}><Updated at={loadedAt} onRefresh={refresh} busy={state.kind === "loading"} /></div>

      {state.kind !== "ok" ? <div style={{ gridColumn: "1 / span 12" }}><AdminNotice state={state} /></div> : rows.length === 0 ? (
        <div className="empty" style={{ gridColumn: "1 / span 12" }}><b>No model calls in this range.</b><p>Only <code>call_model</code> calls are covered so far.</p></div>
      ) : (
        <>
          <div className="stats">
            <Stat label="Calls in range" value={totalCalls}
              note={<>{seriesKeys.length} model · tool · tier series over {days.length} day{days.length === 1 ? "" : "s"}{prevCalls != null && <><br /><Delta cur={totalCalls} prev={prevCalls} upIsBad={false} /></>}</>} />
            <Stat label="Cache hit rate (requests)" value={fmtRate(overall.requestHitRate)}
              note={<>{overall.reporting > 0 ? `${overall.hits} of ${overall.reporting} calls that report cache` : "No provider reported cache tokens"}
                {overall.requestHitRate != null && prevOverall?.requestHitRate != null && <><br /><Delta cur={overall.requestHitRate * 100} prev={prevOverall.requestHitRate * 100} upIsBad={false} /></>}</>} />
          </div>

          {totalNow && (
            <div className="stats">
              <Stat label="Provider latency p95 (whole range)" value={fmtMs(totalNow.provider_p95_ms)}
                note={<>p50 {fmtMs(totalNow.provider_p50_ms)} · p99 {fmtMs(totalNow.provider_p99_ms)}
                  {num(totalNow.provider_p95_ms) != null && num(totalPrev?.provider_p95_ms) != null && <><br /><Delta cur={num(totalNow.provider_p95_ms)!} prev={num(totalPrev!.provider_p95_ms)!} /></>}</>} />
              <Stat label="Router overhead p95 (whole range)" value={fmtMs(totalNow.gate_p95_ms)}
                note={<>p50 {fmtMs(totalNow.gate_p50_ms)} · p99 {fmtMs(totalNow.gate_p99_ms)}
                  {num(totalNow.gate_p95_ms) != null && num(totalPrev?.gate_p95_ms) != null && <><br /><Delta cur={num(totalNow.gate_p95_ms)!} prev={num(totalPrev!.gate_p95_ms)!} /></>}</>} />
              <Stat label="Our share of request time" value={fmtRate(totalNow.gate_time_share, "—")} note="Router time ÷ (router + provider)" />
            </div>
          )}

          <div className="panel scroll-x">
            <div style={{ display: "flex", flexWrap: "wrap", gap: 16, alignItems: "end", justifyContent: "space-between" }}>
              <h2 className="h3">Whole range, merged</h2>
              <div className="cfield"><label htmlFor="pg">Group by</label>
                <select id="pg" value={groupBy} onChange={(e) => setGroupBy(e.target.value as PerfGroupBy)}>
                  {(["total", "model", "provider", "tool", "tier"] as const).map((g) => <option key={g} value={g}>{g}</option>)}
                </select></div>
              {summary.kind === "ok" && (
                <CsvButton filename={`performance-merged-${groupBy}-${range.since}-${range.until}.csv`}
                  header={[groupBy, "Calls", "Provider p50 ms", "Provider p95 ms", "Provider p99 ms", "Router p50 ms", "Router p95 ms", "Router p99 ms", "Our share of time", "Calls reporting cache", "Cache hit rate (requests)", "Cache hit rate (tokens)"]}
                  rows={() => summary.data.rows.map((r) => [r.key ?? "untiered", num(r.calls), num(r.provider_p50_ms), num(r.provider_p95_ms), num(r.provider_p99_ms), num(r.gate_p50_ms), num(r.gate_p95_ms), num(r.gate_p99_ms), num(r.gate_time_share), num(r.calls_reporting_cache), num(r.cache_hit_rate_requests), num(r.cache_hit_rate_tokens)])} />
              )}
            </div>
            {summary.kind !== "ok" ? <AdminNotice state={summary} /> : (
              <table className="dtable">
                <thead>
                  <tr>
                    <th rowSpan={2}>{groupBy === "total" ? "Scope" : groupBy}</th><th rowSpan={2}>Calls</th>
                    <th colSpan={3}>Provider latency</th><th colSpan={3}>Router overhead</th><th rowSpan={2}>Our share of time</th><th colSpan={2}>Cache hit rate</th>
                  </tr>
                  <tr><th>p50</th><th>p95</th><th>p99</th><th>p50</th><th>p95</th><th>p99</th><th>Requests</th><th>Tokens</th></tr>
                </thead>
                <tbody>
                  {summary.data.rows.map((r) => (
                    <tr key={r.key ?? "(none)"}>
                      <td>{groupBy === "total" ? "All calls" : groupBy === "tier" ? tierLabel(r.key) : r.key ?? "—"}</td><td>{num(r.calls) ?? 0}</td>
                      <td>{fmtMs(r.provider_p50_ms)}</td><td>{fmtMs(r.provider_p95_ms)}</td><td>{fmtMs(r.provider_p99_ms)}</td>
                      <td>{fmtMs(r.gate_p50_ms)}</td><td>{fmtMs(r.gate_p95_ms)}</td><td>{fmtMs(r.gate_p99_ms)}</td>
                      <td>{fmtRate(r.gate_time_share, "—")}</td>
                      <td>{fmtRate(r.cache_hit_rate_requests)}</td><td>{fmtRate(r.cache_hit_rate_tokens)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <p className="small dim">These percentiles are computed by the backend from the raw calls over the whole range, so they are true p50/p95/p99 values and can be compared across periods. Router overhead is the time our code spends before the provider is called (find unit, policy and budget reservation, endpoint check, credential lookup).</p>
          </div>

          <p className="small dim" style={{ gridColumn: "1 / span 12", maxWidth: "52em" }}>
            The day-by-day views below show each percentile exactly as measured for one day, model, tool and tier; they are never averaged together, because the mean of two p95s is not a p95.
          </p>

          <div className="panel scroll-x">
            <div style={{ display: "flex", flexWrap: "wrap", gap: 16, alignItems: "end", justifyContent: "space-between" }}>
              <h2 className="h3">By tier and model</h2>
              <CsvButton filename={`performance-${activeDay}.csv`}
                header={["Day", "Tier", "Provider", "Model", "Tool", "Calls", "Provider p50 ms", "Provider p95 ms", "Provider p99 ms", "Router p50 ms", "Router p95 ms", "Router p99 ms", "Our share of time", "Cache hit rate (requests)", "Cache hit rate (tokens)"]}
                rows={() => dayRows.map((r) => [dayOf(r), tierLabel(r.tier), r.provider, r.model, r.tool, num(r.calls), num(r.provider_p50_ms), num(r.provider_p95_ms), num(r.provider_p99_ms), num(r.gate_p50_ms), num(r.gate_p95_ms), num(r.gate_p99_ms), num(r.gate_time_share), num(r.cache_hit_rate_requests), num(r.cache_hit_rate_tokens)])} />
              <div className="cfield"><label htmlFor="pd">Day (UTC)</label>
                <select id="pd" value={activeDay} onChange={(e) => setSelectedDay(e.target.value)}>{days.map((d) => <option key={d} value={d}>{d}</option>)}</select></div>
            </div>
            <table className="dtable">
              <thead>
                <tr>
                  <th rowSpan={2}>Tier</th><th rowSpan={2}>Model</th><th rowSpan={2}>Tool</th><th rowSpan={2}>Calls</th>
                  <th colSpan={3}>Provider latency</th><th colSpan={3}>Router overhead</th><th rowSpan={2}>Our share of time</th><th colSpan={2}>Cache hit rate</th>
                </tr>
                <tr><th>p50</th><th>p95</th><th>p99</th><th>p50</th><th>p95</th><th>p99</th><th>Requests</th><th>Tokens</th></tr>
              </thead>
              <tbody>
                {dayRows.map((r, i) => (
                  <tr key={`${rowKey(r)}-${i}`}>
                    <td>{tierLabel(r.tier)}</td><td>{r.provider} / {r.model}</td><td>{r.tool}</td><td>{num(r.calls) ?? 0}</td>
                    <td>{fmtMs(r.provider_p50_ms)}</td><td>{fmtMs(r.provider_p95_ms)}</td><td>{fmtMs(r.provider_p99_ms)}</td>
                    <td>{fmtMs(r.gate_p50_ms)}</td><td>{fmtMs(r.gate_p95_ms)}</td><td>{fmtMs(r.gate_p99_ms)}</td>
                    <td>{fmtRate(r.gate_time_share, "—")}</td>
                    <td>{fmtRate(r.cache_hit_rate_requests)}</td><td>{fmtRate(r.cache_hit_rate_tokens)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="small dim">&ldquo;Untiered&rdquo; means the connection didn&rsquo;t set a tier; it is not inferred from price. &ldquo;Not reported&rdquo; means the provider sent no cache tokens, which is not the same as 0%.</p>
          </div>

          <div className="panel">
            <h2 className="h3">Daily trend</h2>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 16 }}>
              <div className="cfield" style={{ minWidth: 260 }}><label htmlFor="ts">Series</label>
                <select id="ts" value={activeSeries} onChange={(e) => setSeries(e.target.value)}>{seriesKeys.map((k) => <option key={k} value={k}>{k}</option>)}</select></div>
              <div className="cfield"><label htmlFor="tm">Measure</label>
                <select id="tm" value={metric} onChange={(e) => setMetric(e.target.value as Metric)}>{METRICS.map((m) => <option key={m.key} value={m.key}>{m.label}</option>)}</select></div>
            </div>
            <div role="img" aria-label={`Daily ${metricInfo.short} for ${activeSeries}`} style={{ display: "flex", alignItems: "flex-end", gap: 3, height: 140, borderBottom: "1px solid var(--rule-strong)" }}>
              {trend.map((p) => (
                <div key={p.day} title={`${p.day}: ${p.value == null ? "no data" : fmtMs(p.value)} (daily ${metricInfo.short})`}
                  style={{ flex: 1, minWidth: 3, height: p.value != null && trendMax > 0 ? `${(p.value / trendMax) * 100}%` : 2, background: p.value == null ? "var(--rule)" : "var(--cobalt)" }} />
              ))}
            </div>
            <p className="small dim">Each bar is that day&rsquo;s own {metricInfo.short}, not a merged figure. Tallest bar: {fmtMs(trendMax || null)}.</p>
          </div>

          {[["Cache hit rate by tier", "Tier", byTier], ["Cache hit rate by model", "Provider / model", byModel]].map(([title, label, groups]) => (
            <div className="panel scroll-x" key={title as string}>
              <h2 className="h3">{title as string}</h2>
              <table className="dtable">
                <thead><tr><th>{label as string}</th><th>Calls</th><th>Calls reporting cache</th><th>Calls with a hit</th><th>Request hit rate</th></tr></thead>
                <tbody>
                  {(groups as ReturnType<typeof cacheRollup>).map((g) => (
                    <tr key={g.key}><td>{g.key}</td><td>{g.calls}</td><td>{g.reporting}</td><td>{g.hits}</td><td>{fmtRate(g.requestHitRate)}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
          <p className="small dim" style={{ gridColumn: "1 / span 12" }}>
            Request hit rates above are merged exactly (hits ÷ calls that report cache). Token hit rates and percentiles can&rsquo;t be merged from this data, so they appear only per day in the table. Only <code>call_model</code> calls are covered so far.
          </p>
        </>
      )}
    </>
  );
}

export default function PerformancePage() {
  return (
    <>
      <Hero marker="TECH LEAD" title="Performance" lead="Latency by model and tier, router overhead, and cache hit rates." />
      <section className="frame grid" style={{ paddingBottom: 120, rowGap: 32 }}>
        <OrgGate need="admin">{(org) => <PerformancePanel key={org.organization_id} org={org} />}</OrgGate>
      </section>
    </>
  );
}
