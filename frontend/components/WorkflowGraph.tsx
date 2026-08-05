"use client";

import { useEffect, useId, useRef, useState } from "react";
import { GraphEdge, GraphNode } from "@/lib/api";

/**
 * Renders a workflow subgraph with mermaid, wrapped in a small pan/zoom
 * viewport. Mermaid gives us layout + SVG for free; it has no zoom UI of
 * its own, so that part is a thin manual layer (wheel-to-zoom, drag-to-pan,
 * +/-/reset controls) around the rendered <svg>.
 *
 * Styling is pushed into the SVG via themeCSS referencing the app's own
 * CSS variables, so the diagram inherits the paper theme instead of
 * mermaid's defaults.
 */

const THEME_CSS = `
  .node rect, .node polygon { fill: transparent; stroke: var(--rule-paper); stroke-width: 1px; }
  .node.center rect, .node.center polygon { fill: var(--paper-dim); stroke: var(--pass); stroke-width: 2px; }
  .node.knowledge rect, .node.knowledge polygon { stroke-dasharray: 4 3; }
  .nodeLabel { font-family: var(--font-serif); font-size: 13px; color: var(--paper-text); }
  .edgeLabel { font-family: var(--font-mono); font-size: 9px; color: var(--paper-text-dim); background: transparent !important; }
  .edgeLabel rect { fill: transparent !important; }
  .flowchart-link { stroke: var(--rule-paper); }
  .marker { fill: var(--rule-paper); stroke: var(--rule-paper); }
`;

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function sanitizeEdgeLabel(s: string): string {
  return s.replace(/[|"\n]/g, " ").trim();
}

// mermaid draws small: a short chain of nodes might natively be ~300x30px.
// Parsing its own viewBox (not SVGElement.getBBox(), which undermeasures
// stroke/marker overflow) lets the default view scale UP to fill the
// viewport instead of sitting tiny in the corner at "100%".
function parseSvgSize(svgMarkup: string): { width: number; height: number } | null {
  const m = svgMarkup.match(/viewBox="[-\d.]+\s+[-\d.]+\s+([\d.]+)\s+([\d.]+)"/);
  if (!m) return null;
  return { width: parseFloat(m[1]), height: parseFloat(m[2]) };
}

// Floor for interactive zoom-out (via wheel/− button). Deliberately low so
// a wide diagram can always be shrunk enough to see it in full.
const MIN_SCALE = 0.1;
// Ceiling for interactive zoom-in — SVG stays crisp at any scale, so this
// just bounds how far a click/scroll can go.
const MAX_SCALE = 8;
// Ceiling specifically for the *default* auto-scale-up, so a tiny 1-2 node
// graph doesn't blow up to fill the whole viewport at an absurd size.
const MAX_DEFAULT_SCALE = 3;

export default function WorkflowGraph({
  nodes,
  edges,
  center,
}: {
  nodes: GraphNode[];
  edges: GraphEdge[];
  center: string;
}) {
  const reactId = useId().replace(/[^a-zA-Z0-9]/g, "");
  const viewportRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{ startX: number; startY: number; origX: number; origY: number } | null>(null);

  const [svg, setSvg] = useState<string | null>(null);
  const [naturalSize, setNaturalSize] = useState<{ width: number; height: number } | null>(null);
  const [transform, setTransform] = useState({ scale: 1, x: 8, y: 8 });

  useEffect(() => {
    if (nodes.length === 0) {
      setSvg(null);
      return;
    }
    let cancelled = false;

    (async () => {
      const mermaid = (await import("mermaid")).default;
      mermaid.initialize({
        startOnLoad: false,
        securityLevel: "loose",
        theme: "base",
        themeCSS: THEME_CSS,
      });

      const idOf = new Map<string, string>();
      nodes.forEach((n, i) => idOf.set(n.id, `wn${i}`));

      const lines = ["flowchart LR"];
      nodes.forEach((n) => {
        const safeId = idOf.get(n.id)!;
        const isTask = n.table === "task_nodes";
        const tag = isTask ? "TASK" : "KNOWLEDGE";
        const label = n.label.length > 26 ? `${n.label.slice(0, 25)}…` : n.label;
        lines.push(`${safeId}["${tag}<br/>${escapeHtml(label)}"]`);
        const classes = [isTask ? "task" : "knowledge"];
        if (n.id === center) classes.push("center");
        lines.push(`class ${safeId} ${classes.join(",")}`);
      });
      edges.forEach((e) => {
        const s = idOf.get(e.source);
        const t = idOf.get(e.target);
        if (!s || !t) return;
        const label = sanitizeEdgeLabel(e.label ?? "");
        lines.push(label ? `${s} -->|${label}| ${t}` : `${s} --> ${t}`);
      });

      try {
        const { svg: rendered } = await mermaid.render(`workflow-graph-${reactId}`, lines.join("\n"));
        if (!cancelled) {
          const size = parseSvgSize(rendered);
          setSvg(rendered);
          setNaturalSize(size);
          setTransform({ scale: size ? defaultScaleFor(size) : 1, x: 8, y: 8 });
        }
      } catch {
        if (!cancelled) {
          setSvg(null);
          setNaturalSize(null);
        }
      }
    })();

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes, edges, center, reactId]);

  // Scale UP to fill the viewport (mermaid draws small), but never past
  // MAX_DEFAULT_SCALE, and never below 1x. Bounded by height only, not
  // width — a wide single-row diagram (common: flowchart LR) has a lot of
  // unused vertical space to grow into, and horizontal overflow is fine
  // since the viewport is pannable; capping by width too just left wide,
  // short diagrams stuck near 1x.
  function defaultScaleFor(size: { width: number; height: number }): number {
    const el = viewportRef.current;
    if (!el || size.height <= 0) return 1;
    const heightFit = (el.clientHeight - 16) / size.height;
    return Math.min(MAX_DEFAULT_SCALE, Math.max(1, heightFit));
  }

  useEffect(() => {
    const el = viewportRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = el.getBoundingClientRect();
      const cx = e.clientX - rect.left;
      const cy = e.clientY - rect.top;
      const factor = e.deltaY < 0 ? 1.1 : 1 / 1.1;
      setTransform((t) => {
        const scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, t.scale * factor));
        const ratio = scale / t.scale;
        return { scale, x: cx - (cx - t.x) * ratio, y: cy - (cy - t.y) * ratio };
      });
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  function onMouseDown(e: React.MouseEvent) {
    dragRef.current = { startX: e.clientX, startY: e.clientY, origX: transform.x, origY: transform.y };
  }
  function onMouseMove(e: React.MouseEvent) {
    const drag = dragRef.current;
    if (!drag) return;
    const dx = e.clientX - drag.startX;
    const dy = e.clientY - drag.startY;
    // Capture drag into the closure by value, not by re-reading dragRef.current
    // inside the updater: a mouseup (which nulls the ref) can land between
    // this call and React actually invoking the updater, which previously
    // threw "Cannot read properties of null (reading 'origX')".
    setTransform((t) => ({ ...t, x: drag.origX + dx, y: drag.origY + dy }));
  }
  function endDrag() {
    dragRef.current = null;
  }
  function zoomBy(factor: number) {
    setTransform((t) => ({ ...t, scale: Math.min(MAX_SCALE, Math.max(MIN_SCALE, t.scale * factor)) }));
  }

  if (nodes.length === 0) {
    return <p className="case-body">No connected nodes to display.</p>;
  }

  return (
    <div style={{ position: "relative" }}>
      <div style={{ position: "absolute", top: 6, right: 6, zIndex: 1, display: "flex", gap: "0.25rem" }}>
        <button type="button" className="ask-button" style={{ padding: "0.15rem 0.6rem" }} onClick={() => zoomBy(1.25)}>
          +
        </button>
        <button type="button" className="ask-button" style={{ padding: "0.15rem 0.6rem" }} onClick={() => zoomBy(1 / 1.25)}>
          −
        </button>
        <button
          type="button"
          className="ask-button"
          style={{ padding: "0.15rem 0.6rem" }}
          onClick={() => setTransform({ scale: naturalSize ? defaultScaleFor(naturalSize) : 1, x: 8, y: 8 })}
        >
          reset
        </button>
      </div>
      <div
        ref={viewportRef}
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={endDrag}
        onMouseLeave={endDrag}
        style={{
          overflow: "hidden",
          border: "1px solid var(--rule-paper)",
          borderRadius: "3px",
          height: "420px",
          cursor: dragRef.current ? "grabbing" : "grab",
        }}
      >
        <div
          style={{
            transform: `translate(${transform.x}px, ${transform.y}px) scale(${transform.scale})`,
            transformOrigin: "0 0",
            width: "fit-content",
          }}
          {...(svg ? { dangerouslySetInnerHTML: { __html: svg } } : {})}
        />
      </div>
    </div>
  );
}
