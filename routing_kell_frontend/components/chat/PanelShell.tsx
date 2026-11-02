"use client";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

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
  // Opening and closing slide like an iOS sheet: the panel is mounted first (off to the right), then moved in on the next frame; the chat
  // makes room by easing its right edge with the same curve. On close it slides out and is removed once it has left.
  const [mounted, setMounted] = useState(open);
  const [shown, setShown] = useState(false);
  useEffect(() => {
    if (open) {
      setMounted(true);
      const a = requestAnimationFrame(() => requestAnimationFrame(() => setShown(true)));
      return () => cancelAnimationFrame(a);
    }
    setShown(false);
    const t = window.setTimeout(() => setMounted(false), 460);
    return () => window.clearTimeout(t);
  }, [open]);

  useEffect(() => { setWidth(clampWidth(readWidth())); }, []);
  useEffect(() => {
    const root = document.documentElement;
    if (shown) root.style.setProperty("--dg-canvas-w", `${width}px`); else root.style.removeProperty("--dg-canvas-w");
  }, [shown, width]);
  useEffect(() => () => { document.documentElement.style.removeProperty("--dg-canvas-w"); document.documentElement.classList.remove("dg-resizing"); }, []);
  useEffect(() => { document.documentElement.classList.toggle("dg-resizing", dragging); }, [dragging]);
  useEffect(() => {
    const re = () => setWidth((w) => clampWidth(w));
    window.addEventListener("resize", re);
    return () => window.removeEventListener("resize", re);
  }, []);

  const save = (w: number) => { try { window.localStorage.setItem(KEY, String(Math.round(w))); } catch { /* storage blocked */ } };
  const wrap = useRef<HTMLDivElement>(null);
  const startDrag = useCallback((e: React.PointerEvent<HTMLElement>) => {
    e.preventDefault();
    const start = e.clientX, from = width, target = e.currentTarget;
    target.setPointerCapture(e.pointerId);
    setDragging(true);
    document.body.style.userSelect = "none";
    let last = from, frame = 0;
    // The width is written straight to the panel and the page's right edge in an animation frame, so dragging costs no React render.
    const paint = () => {
      frame = 0;
      if (wrap.current) wrap.current.style.width = `${last}px`;
      document.documentElement.style.setProperty("--dg-canvas-w", `${last}px`);
    };
    const move = (ev: PointerEvent) => { last = clampWidth(from - (ev.clientX - start)); if (!frame) frame = requestAnimationFrame(paint); };
    const up = () => {
      target.removeEventListener("pointermove", move); target.removeEventListener("pointerup", up); target.removeEventListener("pointercancel", up);
      if (frame) cancelAnimationFrame(frame);
      document.body.style.userSelect = ""; setDragging(false); setWidth(last); save(last);
    };
    target.addEventListener("pointermove", move); target.addEventListener("pointerup", up); target.addEventListener("pointercancel", up);
  }, [width]);

  if (!mounted) return null;
  return (
    <div ref={wrap} className="dg-canvas-wrap" data-state={shown ? "open" : "closed"} data-dragging={dragging || undefined} data-wide={wide || undefined} style={{ width }}>
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
