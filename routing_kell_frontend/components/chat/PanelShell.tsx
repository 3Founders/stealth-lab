"use client";
import { useCallback, useEffect, useState, type ReactNode } from "react";

/**
 * The right-hand panel frame shared by the canvas and the docs: a hairline divider you drag to resize (arrow keys too, at least 300 px,
 * the width remembered), a column that reserves room beside the chat while it is open, and a transparent shield over the content while
 * dragging so a frame inside it cannot swallow the pointer. Laid out after Open WebUI's resizable side panel
 * (https://github.com/open-webui/open-webui); no code copied.
 */
const MIN_W = 300, MIN_SIBLING = 360, DEFAULT_W = 460, KEY = "kell:canvas-width";

const readWidth = (): number => { try { const n = Number(window.localStorage.getItem(KEY)); return n >= MIN_W ? n : DEFAULT_W; } catch { return DEFAULT_W; } };
const clampWidth = (w: number): number => Math.max(MIN_W, Math.min(w, Math.max(MIN_W, window.innerWidth - MIN_SIBLING)));

export default function PanelShell({ open, label, children, wide = false }: { open: boolean; label: string; children: ReactNode; wide?: boolean }) {
  const [width, setWidth] = useState(DEFAULT_W);
  const [dragging, setDragging] = useState(false);

  useEffect(() => { setWidth(clampWidth(readWidth())); }, []);
  useEffect(() => {
    const root = document.documentElement;
    if (open) root.style.setProperty("--dg-canvas-w", `${width}px`); else root.style.removeProperty("--dg-canvas-w");
    return () => { root.style.removeProperty("--dg-canvas-w"); };
  }, [open, width]);
  useEffect(() => {
    const re = () => setWidth((w) => clampWidth(w));
    window.addEventListener("resize", re);
    return () => window.removeEventListener("resize", re);
  }, []);

  const save = (w: number) => { try { window.localStorage.setItem(KEY, String(Math.round(w))); } catch { /* storage blocked */ } };
  const startDrag = useCallback((e: React.PointerEvent<HTMLElement>) => {
    e.preventDefault();
    const start = e.clientX, from = width, target = e.currentTarget;
    target.setPointerCapture(e.pointerId);
    setDragging(true);
    document.body.style.userSelect = "none";
    let last = from;
    const move = (ev: PointerEvent) => { last = clampWidth(from - (ev.clientX - start)); setWidth(last); };
    const up = () => {
      target.removeEventListener("pointermove", move); target.removeEventListener("pointerup", up); target.removeEventListener("pointercancel", up);
      document.body.style.userSelect = ""; setDragging(false); save(last);
    };
    target.addEventListener("pointermove", move); target.addEventListener("pointerup", up); target.addEventListener("pointercancel", up);
  }, [width]);

  if (!open) return null;
  return (
    <div className="dg-canvas-wrap" data-dragging={dragging || undefined} data-wide={wide || undefined} style={{ width }}>
      <div
        className="dg-canvas-resizer" role="separator" tabIndex={0} aria-label="Resize panel" aria-orientation="vertical" aria-valuenow={width} aria-valuemin={MIN_W}
        onPointerDown={startDrag}
        onKeyDown={(e) => {
          if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
          e.preventDefault();
          const w = clampWidth(width + (e.key === "ArrowLeft" ? 10 : -10)); setWidth(w); save(w);
        }}
      ><span /></div>
      <aside className="dg-canvas" aria-label={label} data-lenis-prevent>
        {children}
        {dragging && <div className="dg-canvas-shield" />}
      </aside>
    </div>
  );
}
