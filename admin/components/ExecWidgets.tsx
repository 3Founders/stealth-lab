"use client";
import type { ReactNode } from "react";
import type { Level, Share, Verdict } from "@/lib/exec";
import { formatUsd } from "@/lib/money";

const LEVEL_STYLE: Record<Level, { bg: string; mark: string; border: string }> = {
  good: { bg: "rgba(255,255,255,.5)", mark: "●", border: "1px solid var(--rule-strong)" },
  quiet: { bg: "rgba(255,255,255,.5)", mark: "○", border: "1px solid var(--rule-strong)" },
  watch: { bg: "var(--soft)", mark: "▲", border: "1px solid var(--ink)" },
  action: { bg: "var(--yellow)", mark: "■", border: "2px solid var(--ink)" },
};

/** The answer to "is everything okay?" in a word, with the reasons beneath. The shape and the word, not just the colour, carry the level. */
export function StatusBanner({ verdict, scope }: { verdict: Verdict | null; scope: string }) {
  if (!verdict) return <div className="empty" style={{ gridColumn: "1 / span 12" }}><p>Working out the status…</p></div>;
  const st = LEVEL_STYLE[verdict.level];
  return (
    <section aria-label="Overall status" style={{ gridColumn: "1 / span 12", background: st.bg, border: st.border, padding: "20px 24px", display: "grid", gap: 10 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 14, flexWrap: "wrap" }}>
        <span aria-hidden="true" style={{ fontSize: 28 }}>{st.mark}</span>
        <span className="h2" style={{ lineHeight: 1 }}>{verdict.headline}</span>
        <span className="small dim">{scope}</span>
      </div>
      {verdict.reasons.length === 0 ? (
        <p className="small">{verdict.level === "quiet" ? "No requests were made in this period." : "Spending, reliability and controls are all within their normal bounds."}</p>
      ) : (
        <ul className="small" style={{ margin: 0, paddingLeft: 20, display: "grid", gap: 4 }}>
          {verdict.reasons.map((r, i) => (<li key={i}><b>{r.level === "action" ? "Action: " : "Watch: "}</b>{r.text}</li>))}
        </ul>
      )}
    </section>
  );
}

/** One headline number: big, labelled, with what changed and one line of context. */
export function Kpi({ label, value, change, caption }: { label: string; value: ReactNode; change?: ReactNode; caption?: ReactNode }) {
  return (
    <div className="stat" style={{ gap: 8 }}>
      <span className="caption dim">{label}</span>
      <span className="v" style={{ fontSize: "clamp(32px, 3.6vw, 48px)" }}>{value}</span>
      {change && <span>{change}</span>}
      {caption && <span className="small dim">{caption}</span>}
    </div>
  );
}

export function Section({ title, lead, children }: { title: string; lead?: string; children: ReactNode }) {
  return (
    <section className="panel" style={{ breakInside: "avoid" }}>
      <div>
        <h2 className="h3">{title}</h2>
        {lead && <p className="small dim" style={{ maxWidth: "46em" }}>{lead}</p>}
      </div>
      {children}
    </section>
  );
}

export interface BarPoint { label: string; value: number | null; note?: string }

/** A plain bar series over time. `reference` draws a dashed line (for example the daily budget pace) at a value on the same scale. */
export function Bars({ points, ariaLabel, reference, referenceLabel, format, color = "var(--cobalt)", height = 120 }: {
  points: BarPoint[]; ariaLabel: string; reference?: number | null; referenceLabel?: string; format: (v: number) => string; color?: string; height?: number;
}) {
  const max = Math.max(0, reference ?? 0, ...points.map((p) => p.value ?? 0));
  if (points.length === 0 || max <= 0) return <p className="small dim">Nothing to chart for this period.</p>;
  const pct = (v: number) => `${Math.max(1, (v / max) * 100)}%`;
  return (
    <div>
      <div role="img" aria-label={ariaLabel} style={{ position: "relative", display: "flex", alignItems: "flex-end", gap: 3, height, borderBottom: "1px solid var(--rule-strong)" }}>
        {points.map((p) => (
          <div key={p.label} title={`${p.label}: ${p.value == null ? "no data" : format(p.value)}${p.note ? ` · ${p.note}` : ""}`}
            style={{ flex: 1, minWidth: 3, height: p.value == null ? 2 : pct(p.value), background: p.value == null ? "var(--rule)" : color }} />
        ))}
        {reference != null && reference > 0 && (
          <div aria-hidden="true" style={{ position: "absolute", left: 0, right: 0, bottom: `${(reference / max) * 100}%`, borderTop: "2px dashed var(--ink)" }} />
        )}
      </div>
      <p className="small dim" style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
        <span>{points[0].label} → {points[points.length - 1].label}</span>
        {reference != null && reference > 0 && <span>Dashed line: {referenceLabel ?? "reference"} ({format(reference)})</span>}
        <span>Tallest: {format(max)}</span>
      </p>
    </div>
  );
}

/** Where the money goes, as labelled bars that add to 100%. */
export function ShareBars({ shares, label }: { shares: Share[]; label: string }) {
  if (shares.length === 0) return <p className="small dim">No spend in this period.</p>;
  return (
    <ul aria-label={label} style={{ listStyle: "none", padding: 0, margin: 0, display: "grid", gap: 10 }}>
      {shares.map((s) => (
        <li key={s.key} style={{ display: "grid", gridTemplateColumns: "minmax(120px, 240px) 1fr auto", gap: 12, alignItems: "center" }}>
          <span style={{ overflowWrap: "anywhere" }}>{s.key}</span>
          <span aria-hidden="true" style={{ background: "var(--rule)", height: 14 }}><span style={{ display: "block", height: "100%", width: `${s.pct}%`, background: s.key === "Everything else" ? "var(--grey)" : "var(--cobalt)" }} /></span>
          <span><b>{s.pct.toFixed(0)}%</b> <span className="dim">· {formatUsd(s.amount)}</span></span>
        </li>
      ))}
    </ul>
  );
}

/** A labelled control with a plain-language state, for the Risk & control section. */
export function ControlRow({ name, state, detail, ok }: { name: string; state: string; detail?: ReactNode; ok: boolean | null }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "minmax(180px, 260px) 150px 1fr", gap: 12, padding: "10px 0", borderBottom: "1px solid var(--rule)", alignItems: "baseline" }}>
      <span>{name}</span>
      <b style={ok === false ? { background: "var(--yellow)", padding: "0 6px", width: "fit-content" } : undefined}>
        <span aria-hidden="true">{ok === null ? "– " : ok ? "✓ " : "! "}</span>{state}
      </b>
      <span className="small dim">{detail}</span>
    </div>
  );
}
