"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { MAX_ZOOM, MIN_ZOOM, OUT, START, VIEW, clamp, sourceRect, zoomTo, type Crop } from "@/lib/photo-crop";

/**
 * Adjust a chosen picture before it becomes the profile photo: drag to move, pinch / scroll / slider to zoom, arrow keys to nudge.
 * The square is saved as a small JPEG (`OUT` px). Nothing is uploaded until Save; Cancel throws the choice away.
 */
export default function PhotoCropper({ file, onSave, onCancel, busy = false }: {
  file: Blob; onSave: (square: Blob) => void; onCancel: () => void; busy?: boolean;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const bmp = useRef<ImageBitmap | null>(null);
  const [crop, setCrop] = useState<Crop>(START);
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);
  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const pinch = useRef<{ d: number; zoom: number } | null>(null);
  const cropRef = useRef(crop);
  cropRef.current = crop;

  useEffect(() => {
    let live = true;
    setReady(false); setFailed(false); setCrop(START);
    createImageBitmap(file).then((b) => { if (!live) return; bmp.current = b; setReady(true); }).catch(() => { if (live) setFailed(true); });
    return () => { live = false; bmp.current?.close?.(); bmp.current = null; };
  }, [file]);

  const draw = useCallback(() => {
    const b = bmp.current, c = canvas.current, ctx = c?.getContext("2d");
    if (!b || !c || !ctx) return;
    const r = sourceRect(cropRef.current, b.width, b.height);
    ctx.clearRect(0, 0, c.width, c.height);
    ctx.drawImage(b, r.sx, r.sy, r.side, r.side, 0, 0, c.width, c.height);
  }, []);
  useEffect(() => { if (ready) draw(); }, [ready, crop, draw]);

  const set = (next: Crop) => { const b = bmp.current; if (b) setCrop(clamp(next, b.width, b.height)); };
  const zoom = (z: number) => { const b = bmp.current; if (b) setCrop((c) => zoomTo(c, z, b.width, b.height)); };

  function down(e: React.PointerEvent) {
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pointers.current.size === 2) {
      const [a, b] = [...pointers.current.values()];
      pinch.current = { d: Math.hypot(a.x - b.x, a.y - b.y), zoom: cropRef.current.zoom };
    }
  }
  function move(e: React.PointerEvent) {
    const prev = pointers.current.get(e.pointerId);
    if (!prev) return;
    const now = { x: e.clientX, y: e.clientY };
    pointers.current.set(e.pointerId, now);
    if (pointers.current.size === 2 && pinch.current) {
      const [a, b] = [...pointers.current.values()];
      zoom(pinch.current.zoom * (Math.hypot(a.x - b.x, a.y - b.y) / pinch.current.d));
    } else if (pointers.current.size === 1) {
      const scale = VIEW / (canvas.current?.getBoundingClientRect().width || VIEW);
      set({ ...cropRef.current, x: cropRef.current.x + (now.x - prev.x) * scale, y: cropRef.current.y + (now.y - prev.y) * scale });
    }
  }
  function up(e: React.PointerEvent) { pointers.current.delete(e.pointerId); if (pointers.current.size < 2) pinch.current = null; }
  function wheel(e: React.WheelEvent) { zoom(cropRef.current.zoom * (e.deltaY < 0 ? 1.08 : 1 / 1.08)); }
  function key(e: React.KeyboardEvent) {
    const step = 12, c = cropRef.current;
    if (e.key === "ArrowLeft") set({ ...c, x: c.x + step });
    else if (e.key === "ArrowRight") set({ ...c, x: c.x - step });
    else if (e.key === "ArrowUp") set({ ...c, y: c.y + step });
    else if (e.key === "ArrowDown") set({ ...c, y: c.y - step });
    else if (e.key === "+" || e.key === "=") zoom(c.zoom * 1.1);
    else if (e.key === "-") zoom(c.zoom / 1.1);
    else return;
    e.preventDefault();
  }

  function save() {
    const b = bmp.current;
    if (!b) return;
    const out = document.createElement("canvas");
    out.width = out.height = OUT;
    const ctx = out.getContext("2d");
    if (!ctx) { setFailed(true); return; }
    const r = sourceRect(cropRef.current, b.width, b.height);
    ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, OUT, OUT);   // a transparent PNG would turn black as a JPEG
    ctx.drawImage(b, r.sx, r.sy, r.side, r.side, 0, 0, OUT, OUT);
    out.toBlob((blob) => { if (blob) onSave(blob); else setFailed(true); }, "image/jpeg", 0.88);
  }

  if (failed) {
    return (
      <div className="crop">
        <p className="small" role="alert">We couldn&rsquo;t open that picture. Try a different one.</p>
        <div className="cluster"><button type="button" className="btn-line" onClick={onCancel}>Back</button></div>
      </div>
    );
  }
  return (
    <div className="crop">
      <div className="crop-view" tabIndex={0} role="group" aria-label="Adjust your photo. Drag to move, scroll or pinch to zoom, arrow keys to nudge."
        onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up} onWheel={wheel} onKeyDown={key}>
        <canvas ref={canvas} width={OUT} height={OUT} />
        <span className="crop-ring" aria-hidden="true" />
      </div>
      <label className="crop-zoom"><span className="small dim">Zoom</span>
        <input type="range" min={MIN_ZOOM} max={MAX_ZOOM} step={0.01} value={crop.zoom} onChange={(e) => zoom(Number(e.target.value))} aria-label="Zoom" disabled={!ready} />
      </label>
      <div className="cluster">
        <button type="button" className="btn-ink" onClick={save} disabled={!ready || busy}><span>{busy ? "Saving…" : "Save photo"}</span></button>
        <button type="button" className="btn-line" onClick={onCancel} disabled={busy}>Cancel</button>
      </div>
    </div>
  );
}
