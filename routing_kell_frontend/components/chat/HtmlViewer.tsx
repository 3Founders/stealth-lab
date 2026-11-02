"use client";
import { useEffect, useRef, useState } from "react";
import { pageDoc } from "@/components/chat/pageDoc";

/**
 * Two ways to see a page an agent made. "Browser" puts it in a small browser window: window dots, back and forward, reload, an address bar
 * and a choice of screen size (desktop, tablet, phone), so you see it as a visitor would. "Page" is the page edge to edge, the whole panel.
 * Both run the page in a sandbox with no same-origin access: it cannot read this site, your account or your storage.
 */
export type ViewerMode = "browser" | "page";
type Device = "desktop" | "tablet" | "phone";
/** The width each screen is shown at. A page is laid out at this width and scaled to fit the panel, so a desktop page looks like a desktop page. */
const WIDTHS: Record<Device, number> = { desktop: 1280, tablet: 768, phone: 390 };

const slug = (t: string) => t.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "page";

export default function HtmlViewer({ html, title, mode }: { html: string; title: string; mode: ViewerMode }) {
  const [device, setDevice] = useState<Device>("desktop");
  const [reloads, setReloads] = useState(0);
  const frame = useRef<HTMLIFrameElement>(null);
  const area = useRef<HTMLDivElement>(null);
  const bodyBox = useRef<HTMLDivElement>(null);
  const [avail, setAvail] = useState(0);        // room for the browser window, from the panel
  const [bodyH, setBodyH] = useState(0);        // height of the window's page area
  useEffect(() => {
    const watch = (el: Element | null, set: (n: number, h: number) => void) => {
      if (!el || typeof ResizeObserver === "undefined") return () => {};
      const ro = new ResizeObserver(() => set((el as HTMLElement).clientWidth, (el as HTMLElement).clientHeight));
      ro.observe(el);
      set((el as HTMLElement).clientWidth, (el as HTMLElement).clientHeight);
      return () => ro.disconnect();
    };
    const a = watch(area.current, (w) => setAvail(w));
    const b = watch(bodyBox.current, (_w, h) => setBodyH(h));
    return () => { a(); b(); };
  }, [mode, device]);
  const full = () => { const f = frame.current as (HTMLIFrameElement & { webkitRequestFullscreen?: () => void }) | null; if (f?.requestFullscreen) void f.requestFullscreen(); else f?.webkitRequestFullscreen?.(); };

  const fill = (
    <iframe
      ref={frame} key={reloads} title={title} className="dg-canvas-frame" srcDoc={pageDoc(html)}
      sandbox="allow-scripts allow-downloads allow-forms" referrerPolicy="no-referrer"
    />
  );
  if (mode === "page") return <div className="dg-viewer-page">{fill}</div>;

  const pad = 24;                                              // the viewer's side padding
  const room = Math.max(240, (avail || 0) - pad);
  // A desktop page is laid out wide, but not so wide that a narrow panel shrinks it to specks: about 1.8 times the room, between 720 and 1280.
  const deviceW = device === "desktop" ? Math.round(Math.min(1280, Math.max(720, room * 1.8))) : WIDTHS[device];
  const winW = device === "desktop" ? room : Math.min(room, deviceW);
  const scale = Math.min(1, winW / deviceW);
  const iframe = (
    <iframe
      ref={frame} key={reloads} title={title} className="dg-canvas-frame dg-scaled" srcDoc={pageDoc(html)}
      sandbox="allow-scripts allow-downloads allow-forms" referrerPolicy="no-referrer"
      style={{ width: deviceW, height: bodyH && scale ? Math.ceil(bodyH / scale) : "100%", transform: `scale(${scale})`, transformOrigin: "0 0" }}
    />
  );
  return (
    <div className="dg-viewer" ref={area}>
      <div className="dg-browser" style={{ width: avail ? winW : "100%" }} data-device={device}>
        <div className="dg-browser-bar">
          <span className="dg-browser-dots" aria-hidden="true"><i /><i /><i /></span>
          <button type="button" className="dg-browser-btn" disabled aria-label="Back"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M15 18l-6-6 6-6" /></svg></button>
          <button type="button" className="dg-browser-btn" disabled aria-label="Forward"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M9 18l6-6-6-6" /></svg></button>
          <button type="button" className="dg-browser-btn" aria-label="Reload" title="Reload" onClick={() => setReloads((n) => n + 1)}><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M21 12a9 9 0 1 1-3-6.7M21 4v5h-5" /></svg></button>
          <span className="dg-browser-url" title={`${slug(title)}.html`}><svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="5" y="11" width="14" height="9" rx="2" /><path d="M8 11V8a4 4 0 0 1 8 0v3" /></svg>kell://{slug(title)}.html</span>
          <button type="button" className="dg-browser-btn" aria-label="Open in full screen" title="Open in full screen" onClick={full}><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7" /></svg></button>
        </div>
        <div className="dg-browser-body" ref={bodyBox}>{iframe}</div>
      </div>
      <div className="dg-viewer-devices" role="group" aria-label="Screen size">
        {(["desktop", "tablet", "phone"] as Device[]).map((d) => <button key={d} type="button" aria-pressed={device === d} onClick={() => setDevice(d)}>{d[0].toUpperCase() + d.slice(1)}</button>)}
      </div>
    </div>
  );
}
