"use client";
import { useEffect, useState } from "react";
import { downloadCsv, type Cell } from "@/lib/export";
import { pctChange } from "@/lib/range";
import type { BurnStatus } from "@/lib/alerts";

/** "Updated 2 min ago" with a refresh button. Re-renders on a timer so the age stays honest while the page is open. */
export function Updated({ at, onRefresh, busy }: { at: Date | null; onRefresh?: () => void; busy?: boolean }) {
  const [, tick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => tick((n) => n + 1), 15_000);
    return () => clearInterval(t);
  }, []);
  const secs = at ? Math.max(0, Math.round((Date.now() - at.getTime()) / 1000)) : null;
  const age = secs == null ? "not loaded yet" : secs < 20 ? "just now" : secs < 3600 ? `${Math.round(secs / 60) || 1} min ago` : `${Math.round(secs / 3600)} h ago`;
  return (
    <span className="small dim" style={{ display: "inline-flex", alignItems: "center", gap: 10 }}>
      Updated {age}
      {onRefresh && <button type="button" className="btn-line" disabled={busy} onClick={onRefresh}>{busy ? "Refreshing…" : "Refresh"}</button>}
    </span>
  );
}

/** Change against the previous period. For a cost or a latency, up is bad; `upIsBad` marks that. */
export function Delta({ cur, prev, upIsBad = true, label = "vs previous period" }: { cur: number; prev: number; upIsBad?: boolean; label?: string }) {
  const c = pctChange(cur, prev);
  if (c == null) return <span className="small dim">{cur > 0 ? `new (none ${label.replace("vs ", "in the ")})` : "no change"}</span>;
  if (Math.abs(c) < 0.05) return <span className="small dim">flat {label}</span>;
  const up = c > 0;
  const bad = up === upIsBad;
  return (
    <span className="small" style={{ fontWeight: 400, background: bad ? "var(--soft)" : "transparent", padding: bad ? "0 4px" : 0 }}>
      <span aria-hidden="true">{up ? "▲" : "▼"}</span> {Math.abs(c).toFixed(1)}% <span className="dim">{label}</span>
    </span>
  );
}

export function BudgetBanner({ status }: { status: BurnStatus | null }) {
  if (!status || status.level === "ok") return null;
  return (
    <p className="notice-ok" role="alert" style={{ gridColumn: "1 / span 12", borderColor: "var(--ink)", background: status.level === "over" ? "var(--yellow)" : "var(--soft)" }}>
      <b>{status.level === "over" ? "Over budget. " : "Budget watch. "}</b>{status.message}
    </p>
  );
}

export function CsvButton({ filename, header, rows, label = "Export CSV" }: { filename: string; header: string[]; rows: () => Cell[][]; label?: string }) {
  return <button type="button" className="btn-line" onClick={() => downloadCsv(filename, header, rows())}>{label}</button>;
}
