"use client";
// Maths, diagrams and charts in replies (docs/desktop_requirements_2026-11.md, P1 #3 and #4, 2026-11-17).
//   $...$ and $$...$$  -> KaTeX (typeset once, no scripts: KaTeX's own HTML with trust off)
//   ```mermaid          -> a Mermaid diagram (loaded only when a reply has one; securityLevel "strict")
//   ```chart            -> a bar, line or pie chart drawn here as SVG from {type, title, labels, series:[{name, values}]}
import katex from "katex";
import "katex/dist/katex.min.css";
import { useEffect, useId, useState } from "react";

export function MathSpan({ tex, display }: { tex: string; display: boolean }) {
  let html = "";
  try { html = katex.renderToString(tex, { displayMode: display, throwOnError: false, trust: false, strict: "ignore", output: "html" }); }
  catch { return <code>{tex}</code>; }
  return display
    ? <div className="dg-math" dangerouslySetInnerHTML={{ __html: html }} />
    : <span className="dg-math-i" dangerouslySetInnerHTML={{ __html: html }} />;
}

export function MermaidBlock({ code }: { code: string }) {
  const id = "m" + useId().replace(/[^a-zA-Z0-9]/g, "");
  const [svg, setSvg] = useState("");
  const [err, setErr] = useState("");
  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const mermaid = (await import("mermaid")).default;
        const dark = document.documentElement.dataset.theme === "dark";
        mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme: dark ? "dark" : "neutral", fontFamily: "inherit" });
        const out = await mermaid.render(id, code);
        if (live) setSvg(out.svg);
      } catch (e: any) { if (live) setErr(String(e?.message || e).slice(0, 200)); }
    })();
    return () => { live = false; };
  }, [code, id]);
  if (err) return <pre className="dg-md-pre" title={err}><code>{code}</code></pre>;
  return svg ? <div className="dg-diagram" dangerouslySetInnerHTML={{ __html: svg }} /> : <div className="dg-diagram dk-note">Drawing the diagram…</div>;
}

type Chart = { type?: string; title?: string; labels?: unknown[]; series?: { name?: string; values?: unknown[] }[] };
const COLORS = ["#2E6DB4", "#E08A1E", "#3A9A5B", "#C2453A", "#7A5AA6", "#2BA3A3"];

export function ChartBlock({ json }: { json: string }) {
  let c: Chart;
  try { c = JSON.parse(json); } catch { return <pre className="dg-md-pre"><code>{json}</code></pre>; }
  const labels = (c.labels || []).map(String);
  const series = (c.series || []).map((s) => ({ name: String(s.name ?? ""), values: (s.values || []).map((v) => Number(v) || 0) }));
  if (!labels.length || !series.length) return <pre className="dg-md-pre"><code>{json}</code></pre>;
  const W = 560, H = 280, L = 48, B = 36, T = c.title ? 28 : 10, R = 12;
  const legend = series.length > 1 || c.type === "pie" || c.type === "doughnut";
  if (c.type === "pie" || c.type === "doughnut") {
    const vals = series[0].values, sum = vals.reduce((a, b) => a + Math.max(0, b), 0) || 1;
    let a0 = -Math.PI / 2;
    const cx = 150, cy = T + (H - T) / 2, r = Math.min(120, (H - T) / 2 - 8);
    const arcs = vals.map((v, i) => {
      const a1 = a0 + (2 * Math.PI * Math.max(0, v)) / sum;
      const large = a1 - a0 > Math.PI ? 1 : 0;
      const d = `M${cx},${cy} L${cx + r * Math.cos(a0)},${cy + r * Math.sin(a0)} A${r},${r} 0 ${large} 1 ${cx + r * Math.cos(a1)},${cy + r * Math.sin(a1)} Z`;
      a0 = a1;
      return <path key={i} d={d} fill={COLORS[i % COLORS.length]} stroke="var(--paper, #fff)" strokeWidth="1.5"><title>{`${labels[i]}: ${v}`}</title></path>;
    });
    return (
      <figure className="dg-chart">
        <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={c.title || "Pie chart"}>
          {c.title && <text x={W / 2} y={18} textAnchor="middle" className="t">{c.title}</text>}
          {arcs}
          {c.type === "doughnut" && <circle cx={cx} cy={cy} r={r * 0.55} fill="var(--paper, #fff)" />}
          {labels.map((l, i) => (
            <g key={i} transform={`translate(300, ${T + 14 + i * 20})`}>
              <rect width="12" height="12" fill={COLORS[i % COLORS.length]} />
              <text x="18" y="10" className="l">{`${l} — ${vals[i]} (${Math.round((100 * Math.max(0, vals[i])) / sum)}%)`}</text>
            </g>
          ))}
        </svg>
      </figure>
    );
  }
  const all = series.flatMap((s) => s.values);
  const max = Math.max(0, ...all), min = Math.min(0, ...all), span = max - min || 1;
  const plotW = W - L - R, plotH = H - T - B - (legend ? 18 : 0);
  const y = (v: number) => T + plotH - ((v - min) / span) * plotH;
  const step = plotW / labels.length;
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => min + f * span);
  const fmt = (v: number) => (Math.abs(v) >= 1000 ? `${Math.round(v / 100) / 10}k` : String(Math.round(v * 100) / 100));
  return (
    <figure className="dg-chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={c.title || "Chart"}>
        {c.title && <text x={W / 2} y={18} textAnchor="middle" className="t">{c.title}</text>}
        {ticks.map((v, i) => (
          <g key={i}>
            <line x1={L} x2={W - R} y1={y(v)} y2={y(v)} className="g" />
            <text x={L - 6} y={y(v) + 4} textAnchor="end" className="l">{fmt(v)}</text>
          </g>
        ))}
        {labels.map((lab, i) => (
          <text key={i} x={L + step * i + step / 2} y={T + plotH + 16} textAnchor="middle" className="l">{lab.length > 10 ? lab.slice(0, 9) + "…" : lab}</text>
        ))}
        {c.type === "line" || c.type === "area"
          ? series.map((s, k) => {
            const pts = s.values.map((v, i) => `${L + step * i + step / 2},${y(v)}`).join(" ");
            return (
              <g key={k}>
                {c.type === "area" && <polygon points={`${L + step / 2},${y(0)} ${pts} ${L + step * (s.values.length - 1) + step / 2},${y(0)}`} fill={COLORS[k % COLORS.length]} opacity=".18" />}
                <polyline points={pts} fill="none" stroke={COLORS[k % COLORS.length]} strokeWidth="2.2" />
                {s.values.map((v, i) => <circle key={i} cx={L + step * i + step / 2} cy={y(v)} r="3" fill={COLORS[k % COLORS.length]}><title>{`${s.name ? s.name + ", " : ""}${labels[i]}: ${v}`}</title></circle>)}
              </g>
            );
          })
          : series.map((s, k) => {
            const bw = (step * 0.72) / series.length;
            return s.values.map((v, i) => (
              <rect key={`${k}-${i}`} x={L + step * i + step * 0.14 + bw * k} y={Math.min(y(v), y(0))} width={Math.max(1, bw - 2)} height={Math.abs(y(v) - y(0))} fill={COLORS[k % COLORS.length]} rx="2">
                <title>{`${s.name ? s.name + ", " : ""}${labels[i]}: ${v}`}</title>
              </rect>
            ));
          })}
        {legend && series.map((s, k) => (
          <g key={k} transform={`translate(${L + k * 130}, ${H - 12})`}>
            <rect width="10" height="10" y="-9" fill={COLORS[k % COLORS.length]} />
            <text x="14" y="0" className="l">{s.name}</text>
          </g>
        ))}
      </svg>
    </figure>
  );
}
