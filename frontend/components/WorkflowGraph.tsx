"use client";

import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
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

// mermaid's own viewBox already accounts for stroke width, arrowheads and
// label overflow — SVGElement.getBBox() does not, and undermeasures wide
// diagrams enough to clip them against the viewport.
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
// Floor for the *auto-fit* scale specifically (initial load + reset). Wide
// diagrams would otherwise fit-to-width down to an unreadably small scale;
// below this floor we'd rather start legible and let the user pan to see
// the rest, or zoom out manually past this floor if they want the overview.
const MIN_FIT_SCALE = 0.6;

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
  const contentRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{ startX: number; startY: number; origX: number; origY: number } | null>(null);

  const [svg, setSvg] = useState<string | null>(null);
  const [naturalSize, setNaturalSize] = useState<{ width: number; height: number } | null>(null);
  const [transform, setTransform] = useState({ scale: 1, x: 0, y: 0 });

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
          setSvg(rendered);
          setNaturalSize(parseSvgSize(rendered));
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

  function fitToViewport() {
    if (!naturalSize || !viewportRef.current) return;
    const el = viewportRef.current;
    const scaleX = naturalSize.width > 0 ? (el.clientWidth - 16) / naturalSize.width : 1;
    const scaleY = naturalSize.height > 0 ? (el.clientHeight - 16) / naturalSize.height : 1;
    const fit = Math.max(MIN_FIT_SCALE, Math.min(1, scaleX, scaleY));
    setTransform({ scale: fit, x: 8, y: 8 });
  }

  // Fit the diagram to the viewport once it's rendered, so large graphs
  // start zoomed out (matching the old behaviour) instead of clipped.
  useLayoutEffect(() => {
    fitToViewport();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [svg, naturalSize]);

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
    if (!dragRef.current) return;
    const dx = e.clientX - dragRef.current.startX;
    const dy = e.clientY - dragRef.current.startY;
    setTransform((t) => ({ ...t, x: dragRef.current!.origX + dx, y: dragRef.current!.origY + dy }));
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
        <button type="button" className="ask-button" style={{ padding: "0.15rem 0.6rem" }} onClick={fitToViewport}>
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
          ref={contentRef}
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
