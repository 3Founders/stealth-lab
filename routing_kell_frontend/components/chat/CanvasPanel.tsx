"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Markdown from "@/components/chat/Markdown";
import { fileNameFor, viewOf, type Canvas, type CanvasVersion } from "@/lib/chat/canvas";

/**
 * The canvas: a panel at the right of the chat that shows what an agent wrote (a page, code or a document). Laid out after Open WebUI's
 * artifacts panel (https://github.com/open-webui/open-webui, Artifacts.svelte and ResizableSidePanel.svelte), no code copied: a thin divider
 * you drag to resize (arrow keys too), a header with "Version N of M" and chevrons on the left, then Copy, Download, Full screen and a round
 * Close on the right, and below it the content edge to edge. Every version in the room is one step, oldest to newest. There is no editor:
 * you ask an agent for the next version. Pages run in a sandbox that cannot reach this site or your account.
 */
/** A bare fragment (an svg, a div) is wrapped so it has a page to sit in; a whole page is used as it is. */
const pageDoc = (t: string): string => /<html[\s>]|<!doctype/i.test(t) ? t : `<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><style>body{margin:0;font-family:system-ui,sans-serif;background:#fff;color:#171717}</style></head><body>${t}</body></html>`;
const MIN_W = 300, MIN_SIBLING = 360, DEFAULT_W = 420, KEY = "kell:canvas-width";

interface Entry { canvas: Canvas; v: CanvasVersion }

const readWidth = (): number => { try { const n = Number(window.localStorage.getItem(KEY)); return n >= MIN_W ? n : DEFAULT_W; } catch { return DEFAULT_W; } };
const clampWidth = (w: number): number => Math.max(MIN_W, Math.min(w, Math.max(MIN_W, window.innerWidth - MIN_SIBLING)));

export default function CanvasPanel({ open, onClose, canvases, room, selectedId }: {
  open: boolean; onClose: () => void; canvases: Canvas[]; room: string; selectedId: string | null;
}) {
  const entries = useMemo<Entry[]>(() => canvases.filter((c) => c.room === room).flatMap((c) => c.versions.map((v) => ({ canvas: c, v }))).sort((a, b) => a.v.at - b.v.at), [canvases, room]);
  const [idx, setIdx] = useState(0);
  const [copied, setCopied] = useState(false);
  const [width, setWidth] = useState(DEFAULT_W);
  const [dragging, setDragging] = useState(false);
  const frame = useRef<HTMLIFrameElement>(null);
  const timer = useRef<number | undefined>(undefined);

  // The newest step is shown when one is added; asking for a canvas by title shows its newest version.
  const count = entries.length;
  const lastCount = useRef(0);
  useEffect(() => { if (count > lastCount.current) setIdx(count - 1); else setIdx((i) => Math.min(i, Math.max(0, count - 1))); lastCount.current = count; }, [count]);
  useEffect(() => {
    if (!selectedId) return;
    for (let i = entries.length - 1; i >= 0; i--) if (entries[i].canvas.id === selectedId) { setIdx(i); break; }
  }, [selectedId, entries]);

  useEffect(() => { setWidth(clampWidth(readWidth())); }, []);
  useEffect(() => {
    const root = document.documentElement;
    if (open && count > 0) root.style.setProperty("--dg-canvas-w", `${width}px`); else root.style.removeProperty("--dg-canvas-w");
    return () => { root.style.removeProperty("--dg-canvas-w"); };
  }, [open, count, width]);
  useEffect(() => {
    const re = () => setWidth((w) => clampWidth(w));
    window.addEventListener("resize", re);
    return () => window.removeEventListener("resize", re);
  }, []);
  useEffect(() => () => window.clearTimeout(timer.current), []);

  const save = (w: number) => { try { window.localStorage.setItem(KEY, String(Math.round(w))); } catch { /* storage blocked */ } };
  const startDrag = useCallback((e: React.PointerEvent<HTMLElement>) => {
    e.preventDefault();
    const start = e.clientX, from = width;
    const target = e.currentTarget;
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

  if (!open || count === 0) return null;
  const cur = entries[Math.min(idx, count - 1)];
  const text = cur.v.text;
  const view = viewOf(cur.canvas.kind, text);
  const isPage = view === "page";
  const flashCopied = () => { setCopied(true); window.clearTimeout(timer.current); timer.current = window.setTimeout(() => setCopied(false), 2000); };
  const copy = () => { void navigator.clipboard.writeText(text).then(flashCopied, () => {}); };
  const download = () => {
    const url = URL.createObjectURL(new Blob([text], { type: isPage ? "text/html" : "text/plain" }));
    const a = document.createElement("a"); a.href = url; a.download = fileNameFor(cur.canvas); document.body.appendChild(a); a.click(); a.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 30_000);
  };
  const full = () => { const f = frame.current as (HTMLIFrameElement & { webkitRequestFullscreen?: () => void }) | null; if (f?.requestFullscreen) void f.requestFullscreen(); else f?.webkitRequestFullscreen?.(); };

  return (
    <div className="dg-canvas-wrap" data-dragging={dragging || undefined} style={{ width }}>
      <div
        className="dg-canvas-resizer" role="separator" tabIndex={0} aria-label="Resize panel" aria-orientation="vertical" aria-valuenow={width} aria-valuemin={MIN_W}
        onPointerDown={startDrag}
        onKeyDown={(e) => {
          if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
          e.preventDefault();
          const w = clampWidth(width + (e.key === "ArrowLeft" ? 10 : -10)); setWidth(w); save(w);
        }}
      ><span /></div>
      <aside className="dg-canvas" aria-label="Canvas">
        <div className="dg-canvas-head">
          <div className="dg-canvas-headrow">
            <div className="dg-canvas-ver" dir="ltr">
              <button type="button" className="dg-cv-icon" aria-label="Previous version" disabled={count <= 1} onClick={() => setIdx((i) => Math.max(i - 1, 0))}>
                <svg fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.5" width="14" height="14" aria-hidden="true"><path strokeLinecap="round" strokeLinejoin="round" d="M15.75 19.5 8.25 12l7.5-7.5" /></svg>
              </button>
              <div className="dg-cv-label">Version {Math.min(idx, count - 1) + 1} of {count}</div>
              <button type="button" className="dg-cv-icon" aria-label="Next version" disabled={count <= 1} onClick={() => setIdx((i) => Math.min(i + 1, count - 1))}>
                <svg fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2.5" width="14" height="14" aria-hidden="true"><path strokeLinecap="round" strokeLinejoin="round" d="m8.25 4.5 7.5 7.5-7.5 7.5" /></svg>
              </button>
            </div>
            <div className="dg-canvas-tools">
              <button type="button" className="dg-cv-chip dg-cv-copy" onClick={copy}>{copied ? "Copied" : "Copy"}</button>
              <button type="button" className="dg-cv-chip dg-cv-sq" aria-label="Download" title="Download" onClick={download}>
                <svg fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.5" width="14" height="14" aria-hidden="true"><path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5M16.5 12 12 16.5m0 0L7.5 12m4.5 4.5V3" /></svg>
              </button>
              {isPage && (
                <button type="button" className="dg-cv-chip dg-cv-sq" aria-label="Open in full screen" title="Open in full screen" onClick={full}>
                  <svg fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.5" width="14" height="14" aria-hidden="true"><path strokeLinecap="round" strokeLinejoin="round" d="M3.75 3.75v4.5m0-4.5h4.5m-4.5 0L9 9M3.75 20.25v-4.5m0 4.5h4.5m-4.5 0L9 15M20.25 3.75h-4.5m4.5 0v4.5m0-4.5L15 9m5.25 11.25h-4.5m4.5 0v-4.5m0 4.5L15 15" /></svg>
                </button>
              )}
            </div>
          </div>
          <button type="button" className="dg-cv-close" aria-label="Close" onClick={onClose}>
            <svg fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2" width="14" height="14" aria-hidden="true"><path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" /></svg>
          </button>
        </div>
        <div className="dg-canvas-body">
          {isPage ? (
            <iframe ref={frame} key={`${cur.canvas.id}-${cur.v.n}`} title="Content" className="dg-canvas-frame" srcDoc={pageDoc(text)} sandbox="allow-scripts allow-downloads allow-forms" referrerPolicy="no-referrer" />
          ) : view === "markdown" ? (
            <div className="dg-canvas-doc" tabIndex={0} aria-label={cur.canvas.title}><Markdown text={text} /></div>
          ) : (
            <pre className="dg-canvas-pre" data-kind={cur.canvas.kind} tabIndex={0} aria-label={cur.canvas.title}>{text}</pre>
          )}
          {dragging && <div className="dg-canvas-shield" />}
        </div>
      </aside>
    </div>
  );
}
