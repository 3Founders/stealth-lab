"use client";
import { useCallback, useEffect, useState } from "react";
import { PRESETS, isRange, lastDays, rangeFromQuery, rangeToQuery, type Range } from "@/lib/range";

const KEY = "kel-admin-range";

/**
 * A date range that survives reloads and can be shared. Precedence on load: the URL (`?since=&until=`, so a pasted
 * link reproduces the view), then the last range this browser used, then `defaultDays`. `ready` is false until that
 * has been resolved, so pages don't fetch once for a default and again for the real range.
 */
export function useRange(defaultDays: number): { range: Range; ready: boolean; setRange: (r: Range) => void } {
  const [range, setR] = useState<Range>(() => lastDays(defaultDays));
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let next: Range | null = rangeFromQuery(window.location.search);
    if (!next) {
      try {
        const saved = JSON.parse(window.localStorage.getItem(KEY) ?? "null");
        if (isRange(saved)) next = saved;
      } catch { /* storage blocked or corrupt: fall back to the default */ }
    }
    if (next) setR(next);
    setReady(true);
  }, []);

  const setRange = useCallback((r: Range) => {
    if (!isRange(r)) return;
    setR(r);
    try { window.localStorage.setItem(KEY, JSON.stringify(r)); } catch { /* ignore */ }
    const url = new URL(window.location.href);
    url.search = rangeToQuery(r);
    window.history.replaceState(null, "", url);
  }, []);

  return { range, ready, setRange };
}

const dateStyle = { font: "inherit", padding: "8px 10px", border: "1px solid var(--rule-strong)", borderRadius: 3, background: "rgba(255,255,255,.5)" } as const;

export function RangePicker({ range, setRange, idPrefix }: { range: Range; setRange: (r: Range) => void; idPrefix: string }) {
  const [copied, setCopied] = useState(false);
  const invalid = !isRange(range);
  const active = PRESETS.findIndex((p) => { const r = p.make(); return r.since === range.since && r.until === range.until; });

  async function copyLink() {
    try {
      await navigator.clipboard.writeText(window.location.href);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch { /* clipboard blocked: the address bar already holds the link */ }
  }

  return (
    <div style={{ gridColumn: "1 / span 12", display: "flex", flexWrap: "wrap", gap: "12px 20px", alignItems: "end" }}>
      <nav className="tabs" style={{ marginTop: 0 }} aria-label="Range presets">
        {PRESETS.map((p, i) => (<button key={p.label} type="button" aria-pressed={active === i} onClick={() => setRange(p.make())}>{p.label}</button>))}
      </nav>
      <div className="cfield"><label htmlFor={`${idPrefix}-s`}>From</label>
        <input id={`${idPrefix}-s`} type="date" value={range.since} onChange={(e) => setRange({ ...range, since: e.target.value })} style={dateStyle} /></div>
      <div className="cfield"><label htmlFor={`${idPrefix}-u`}>Until (exclusive)</label>
        <input id={`${idPrefix}-u`} type="date" value={range.until} onChange={(e) => setRange({ ...range, until: e.target.value })} style={dateStyle} /></div>
      <button type="button" className="btn-line" onClick={copyLink}>{copied ? "Link copied" : "Copy link to this view"}</button>
      {invalid && <span className="small" role="alert">&ldquo;From&rdquo; must be before &ldquo;Until&rdquo;.</span>}
    </div>
  );
}
