"use client"
// The bottom panel under the chat: a terminal in the project folder and a small browser (owner, 2026-11-15: "basic things
// like in built browser and terminal", as in Claude Code). Both live in the Electron main (electron/terminal.ts,
// electron/browser.ts); this draws them. A plain browser (`next dev` without Electron) says they need the app.
//
// The terminal: one shell per project folder, kept while the app runs; switching projects shows that project's shell.
// The browser: a web view laid over the placeholder below its address bar, hidden whenever the panel, the tab or the
// coding pane is not showing (a native view sits above the page and would cover it).
import { useCallback, useEffect, useRef, useState } from "react"
import "@xterm/xterm/css/xterm.css"

type Rect = { x: number; y: number; width: number; height: number }
type BrowserState = { url: string; title: string; loading: boolean; back: boolean; forward: boolean }
type Bridge = {
  term?: {
    create: (cwd: string, cols: number, rows: number) => Promise<{ id: string; shell: string } | { error: string }>
    write: (id: string, data: string) => void
    resize: (id: string, cols: number, rows: number) => void
    kill: (id: string) => void
    pasteText: () => Promise<string>
    onData: (cb: (id: string, data: string) => void) => () => void
    onExit: (cb: (id: string, code: number) => void) => () => void
  }
  browser?: {
    show: (rect: Rect | null) => void
    go: (url: string) => Promise<string | null>
    act: (what: "back" | "forward" | "reload" | "stop") => void
    state: () => Promise<BrowserState>
    external: (url: string) => Promise<void>
    onState: (cb: (s: BrowserState) => void) => () => void
  }
}
const bridge = (): Bridge | undefined => (typeof window !== "undefined" ? (window as unknown as { kellDesktop?: Bridge }).kellDesktop : undefined)

export type PanelTab = "terminal" | "browser"

const HEIGHT_KEY = "kell.bottom.height"
const ico = { width: 15, height: 15, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true }
export const TerminalIcon = ({ size = 15 }: { size?: number }) => (
  <svg {...ico} width={size} height={size}><rect x="3" y="4" width="18" height="16" rx="2" /><path d="m7 9 3 3-3 3M13 15h4" /></svg>
)
export const GlobeIcon = ({ size = 15 }: { size?: number }) => (
  <svg {...ico} width={size} height={size}><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18" /></svg>
)

const DARK = { background: "#1b1a17", foreground: "#e9e6dc", cursor: "#e9e6dc", selectionBackground: "#4a4639" }
const LIGHT = { background: "#fbfaf5", foreground: "#26241f", cursor: "#26241f", selectionBackground: "#d9d3bf" }
const themeNow = () => (typeof document !== "undefined" && document.documentElement.dataset.theme === "dark" ? DARK : LIGHT)

type Term = { id?: string; dir: string; el: HTMLDivElement; xterm: any; fit: any; exited?: number; error?: string; shell?: string }

/** Resolves true once the element has a size xterm can measure. A panel that is hidden, closing, or one frame old
 *  measures 0x0, and a terminal opened there draws nothing and starts its shell at the wrong size. */
const hasSize = (el: HTMLElement, tries = 30) => new Promise<boolean>((resolve) => {
  const check = (left: number) => {
    if (!el.isConnected) return resolve(false)
    const r = el.getBoundingClientRect()
    if (r.width >= 40 && r.height >= 30) return resolve(true)
    if (left <= 0) return resolve(false)
    setTimeout(() => check(left - 1), 40)
  }
  check(tries)
})

export default function BottomPanel({ directory, open, tab, onTab, onClose, active }: {
  directory: string
  /** Shown. A hidden panel stays mounted, so its shells keep running and keep their output. */
  open: boolean
  tab: PanelTab
  onTab: (t: PanelTab) => void
  onClose: () => void
  /** The coding pane shows and nothing covers the panel: the browser view may be placed. */
  active: boolean
}) {
  const d = bridge()
  const [height, setHeight] = useState(() => {
    try { const v = Number(localStorage.getItem(HEIGHT_KEY)); return v >= 120 ? v : 260 } catch { return 260 }
  })
  const [maxed, setMaxed] = useState(false)
  // Open/close motion (docs/ios_animation.md §4.4/§4.5): the panel rises as it fades in, and on close it fades down
  // while its height collapses, so the chat takes the space back instead of jumping. `mounted` keeps it in the DOM
  // for the exit; `hidden` takes it out after it has finished.
  const [mounted, setMounted] = useState(open)
  const [closing, setClosing] = useState(false)
  const [dragging, setDragging] = useState(false)
  const closingRef = useRef(false)
  useEffect(() => { closingRef.current = closing }, [closing])
  useEffect(() => {
    if (open) { setMounted(true); setClosing(false); return }
    if (!mounted) return
    setClosing(true)
    // 280ms, not 240: the exit runs for .24s, and hiding it on the last frame would cut the end off.
    const timer = setTimeout(() => { setClosing(false); setMounted(false) }, 280)
    return () => clearTimeout(timer)
  }, [open, mounted])

  // ---- terminal ----
  const hostRef = useRef<HTMLDivElement>(null)
  const terms = useRef(new Map<string, Term>())
  const [, bump] = useState(0)
  const current = terms.current.get(directory)

  const fitNow = useCallback((t?: Term) => {
    if (!t?.fit || !t.el.isConnected || t.el.offsetParent === null) return
    if (closingRef.current) return          // the height is collapsing: resizing the shell every frame would repaint it
    const r = t.el.getBoundingClientRect()
    if (r.width < 40 || r.height < 30) return   // hidden or closing: a fit here would shrink the shell to a column
    try {
      // Only when the size really changed: every resize makes ConPTY repaint its whole screen, so a ResizeObserver
      // firing on an unchanged box must not cost a repaint.
      const dims = t.fit.proposeDimensions?.()
      if (!dims || !Number.isFinite(dims.cols) || !Number.isFinite(dims.rows)) return
      if (dims.cols === t.xterm.cols && dims.rows === t.xterm.rows) return
      t.fit.fit()
      if (t.id) d?.term?.resize(t.id, t.xterm.cols, t.xterm.rows)
    } catch { /* not laid out yet */ }
  }, [d])

  const start = useCallback(async (dir: string) => {
    const host = hostRef.current
    if (!host || !d?.term || !dir) return
    // xterm measures its character cell once, when it opens, and cannot recover from a 0-width container: the panel is
    // animating in and the tab can be off screen, so wait for real layout before opening it.
    if (!(await hasSize(host))) return
    let t = terms.current.get(dir)
    if (!t) {
      const [{ Terminal }, { FitAddon }] = await Promise.all([import("@xterm/xterm"), import("@xterm/addon-fit")])
      if (terms.current.get(dir) || !host.isConnected) return
      const el = document.createElement("div")
      el.className = "dk-term-screen"
      host.appendChild(el)
      const xterm = new Terminal({ fontFamily: "Cascadia Mono, Consolas, ui-monospace, monospace", fontSize: 13, cursorBlink: true, theme: themeNow(), allowProposedApi: false, scrollback: 5000 })
      const fit = new FitAddon()
      xterm.loadAddon(fit)
      xterm.open(el)
      t = { dir, el, xterm, fit }
      terms.current.set(dir, t)
      const self = t
      xterm.onData((data: string) => { if (self.id) d.term!.write(self.id, data) })
      // Ctrl+C copies when there is a selection (else it goes to the shell as an interrupt). Ctrl+V is the page's own paste.
      // Ctrl/Cmd+J is the app's terminal toggle (app/page.tsx), not a line feed to the shell.
      xterm.attachCustomKeyEventHandler((e: KeyboardEvent) => {
        if (e.type === "keydown" && (e.ctrlKey || e.metaKey) && (e.key === "j" || e.key === "J")) return false
        if (e.type !== "keydown" || !e.ctrlKey || e.key !== "c" || !xterm.hasSelection()) return true
        void navigator.clipboard.writeText(xterm.getSelection()); xterm.clearSelection(); return false
      })
    }
    if (t.id) return
    t.exited = undefined; t.error = undefined
    // Size the terminal before the shell starts, so it never begins at 80x24 and repaints its whole screen at the real
    // size (PowerShell re-wraps its prompt when that happens, which reads as a flash).
    fitNow(t)
    const r = await d.term.create(dir, t.xterm.cols, t.xterm.rows)
    if ("error" in r) { t.error = r.error; t.xterm.writeln(`\r\n${r.error}`) }   // the shell learns the real size
    else { t.id = r.id; t.shell = r.shell; fitNow(t) }
    bump((n) => n + 1)
  }, [d, fitNow])

  // Output and exits, for every shell.
  useEffect(() => {
    if (!d?.term) return
    const off1 = d.term.onData((id, data) => { for (const t of terms.current.values()) if (t.id === id) { t.xterm.write(data); break } })
    const off2 = d.term.onExit((id, code) => {
      for (const t of terms.current.values()) if (t.id === id) {
        t.id = undefined; t.exited = code
        t.xterm.writeln(`\r\n\x1b[2m[the shell ended${code ? ` with code ${code}` : ""}]\x1b[0m`)
        bump((n) => n + 1)
      }
    })
    return () => { off1(); off2() }
  }, [d])

  // The terminal tab shows this project's shell, started on first view.
  useEffect(() => {
    for (const t of terms.current.values()) t.el.style.display = t.dir === directory ? "" : "none"
    if (!open || tab !== "terminal" || !directory) return
    void start(directory)
    const t = terms.current.get(directory)
    if (t) setTimeout(() => { fitNow(t); t.xterm.focus() }, 0)
  }, [open, tab, directory, start, fitNow])

  // Refit on any size change; follow the light/dark theme.
  useEffect(() => {
    const host = hostRef.current
    if (!host) return
    const ro = new ResizeObserver(() => fitNow(terms.current.get(directory)))
    ro.observe(host)
    const mo = new MutationObserver(() => { for (const t of terms.current.values()) t.xterm.options.theme = themeNow() })
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] })
    return () => { ro.disconnect(); mo.disconnect() }
  }, [directory, fitNow])

  // The shells end with the panel's page (the main process also ends them when the app quits).
  useEffect(() => () => { for (const t of terms.current.values()) { if (t.id) d?.term?.kill(t.id); t.xterm.dispose() } }, [d])

  const restart = () => {
    const t = terms.current.get(directory)
    if (!t) return
    if (t.id) d?.term?.kill(t.id)
    t.id = undefined
    t.xterm.reset()
    void start(directory)
  }

  // Right-click pastes, as in Windows Terminal: no menu, and no permission prompt (the clipboard is read in the main
  // process). xterm.paste keeps bracketed paste mode, so a multi-line paste is one edit rather than a command per line.
  const pasteAt = async (e: React.MouseEvent) => {
    e.preventDefault()
    const t = terms.current.get(directory)
    if (!t || !d?.term?.pasteText) return
    const text = await d.term.pasteText()
    if (!text) return
    t.xterm.paste(text)
    t.xterm.focus()
  }

  // ---- browser ----
  const viewRef = useRef<HTMLDivElement>(null)
  const [bs, setBs] = useState<BrowserState>({ url: "", title: "", loading: false, back: false, forward: false })
  const [addr, setAddr] = useState("")
  const editing = useRef(false)
  useEffect(() => {
    if (!d?.browser) return
    void d.browser.state().then(setBs).catch(() => undefined)
    return d.browser.onState((s) => { setBs(s); if (!editing.current) setAddr(s.url) })
  }, [d])
  const showView = open && tab === "browser" && active && !!bs.url
  useEffect(() => {
    const b = d?.browser
    const el = viewRef.current
    if (!b) return
    if (!showView || !el) { b.show(null); return }
    const place = () => { const r = el.getBoundingClientRect(); b.show({ x: r.left, y: r.top, width: r.width, height: r.height }) }
    place()
    const ro = new ResizeObserver(place)
    ro.observe(el)
    window.addEventListener("resize", place)
    return () => { ro.disconnect(); window.removeEventListener("resize", place); b.show(null) }
  }, [showView, d, height, maxed])
  useEffect(() => () => bridge()?.browser?.show(null), [])
  const go = async (raw: string) => {
    editing.current = false
    const url = await d?.browser?.go(raw)
    if (url) setAddr(url)
  }

  // ---- size ----
  const startResize = (e: React.PointerEvent) => {
    e.preventDefault()
    const y0 = e.clientY, h0 = height
    setDragging(true)
    const move = (ev: PointerEvent) => setHeight(Math.max(120, Math.min(window.innerHeight - 160, h0 + (y0 - ev.clientY))))
    const up = () => {
      window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up)
      setDragging(false)
      setHeight((h) => { try { localStorage.setItem(HEIGHT_KEY, String(h)) } catch { /* private storage */ } return h })
    }
    window.addEventListener("pointermove", move); window.addEventListener("pointerup", up)
    setMaxed(false)
  }

  const btn = "dk-icon dk-icon-sm"
  return (
    <section className="dk-bottom" hidden={!mounted} data-open={open ? "true" : undefined}
      data-closing={closing ? "true" : undefined} data-dragging={dragging ? "true" : undefined}
      data-max={maxed ? "true" : undefined} style={closing ? { height: 0 } : maxed ? undefined : { height }}
      aria-hidden={open ? undefined : true} aria-label="Terminal and browser">
      <span className="dk-bottom-grip" role="separator" aria-orientation="horizontal" aria-label="Drag to resize the panel" onPointerDown={startResize} />
      <div className="dk-bottom-head">
        <div className="dk-bottom-tabs" role="tablist" aria-label="Panel">
          <button type="button" role="tab" aria-selected={tab === "terminal"} className="dk-bottom-tab" onClick={() => onTab("terminal")} title="Terminal (Ctrl+J)">
            <TerminalIcon /><span>Terminal</span>
          </button>
          <button type="button" role="tab" aria-selected={tab === "browser"} className="dk-bottom-tab" onClick={() => onTab("browser")} title="Browser">
            <GlobeIcon /><span>Browser</span>
          </button>
        </div>
        {/* Which shell is running, so "is this really PowerShell?" is answered without typing into it. */}
        {tab === "terminal" && current?.shell ? <span className="dk-term-shell" title={`This terminal runs ${current.shell} directly`}>{current.shell}</span> : null}
        <span className="dk-iconrow">
          {tab === "terminal" && (
            <button type="button" className={btn} onClick={restart} disabled={!d?.term || !current} aria-label="Restart the terminal" title="Restart the terminal">
              <svg {...ico}><path d="M21 12a9 9 0 1 1-3-6.7L21 8" /><path d="M21 3v5h-5" /></svg>
            </button>
          )}
          <button type="button" className={btn} onClick={() => setMaxed((m) => !m)} aria-pressed={maxed}
            aria-label={maxed ? "Make the panel smaller" : "Make the panel bigger"} title={maxed ? "Smaller" : "Bigger"}>
            <svg {...ico}>{maxed ? <path d="m6 15 6-6 6 6" transform="rotate(180 12 12)" /> : <path d="m6 15 6-6 6 6" />}</svg>
          </button>
          <button type="button" className={btn} onClick={onClose} aria-label="Hide the panel" title="Hide the panel">
            <svg {...ico}><path d="M18 6 6 18M6 6l12 12" /></svg>
          </button>
        </span>
      </div>

      <div className="dk-bottom-body" hidden={tab !== "terminal"}>
        {!d?.term ? <p className="dk-note dk-bottom-note">The terminal runs in the kell app.</p>
          : !directory ? <p className="dk-note dk-bottom-note">Open a project to get a terminal in its folder.</p> : null}
        <div className="dk-term" ref={hostRef} onContextMenu={(e) => void pasteAt(e)} />
      </div>

      <div className="dk-bottom-body dk-browser" hidden={tab !== "browser"}>
        <form className="dk-browser-bar" onSubmit={(e) => { e.preventDefault(); void go(addr) }}>
          <button type="button" className={btn} onClick={() => d?.browser?.act("back")} disabled={!bs.back} aria-label="Back" title="Back">
            <svg {...ico}><path d="m15 18-6-6 6-6" /></svg>
          </button>
          <button type="button" className={btn} onClick={() => d?.browser?.act("forward")} disabled={!bs.forward} aria-label="Forward" title="Forward">
            <svg {...ico}><path d="m9 18 6-6-6-6" /></svg>
          </button>
          <button type="button" className={btn} onClick={() => d?.browser?.act(bs.loading ? "stop" : "reload")} disabled={!bs.url}
            aria-label={bs.loading ? "Stop loading" : "Reload"} title={bs.loading ? "Stop" : "Reload"}>
            {bs.loading ? <svg {...ico}><path d="M18 6 6 18M6 6l12 12" /></svg> : <svg {...ico}><path d="M21 12a9 9 0 1 1-3-6.7L21 8" /><path d="M21 3v5h-5" /></svg>}
          </button>
          <input className="dk-browser-addr" value={addr} spellCheck={false} disabled={!d?.browser}
            placeholder="Address, localhost:3000, or a search" aria-label="Address"
            onFocus={(e) => { editing.current = true; e.currentTarget.select() }}
            onBlur={() => { editing.current = false }}
            onChange={(e) => setAddr(e.target.value)} />
          <button type="button" className={btn} onClick={() => bs.url && void d?.browser?.external(bs.url)} disabled={!bs.url}
            aria-label="Open in your browser" title="Open in your browser">
            <svg {...ico}><path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" /></svg>
          </button>
        </form>
        <div className="dk-browser-view" ref={viewRef}>
          {!d?.browser ? <p className="dk-note dk-bottom-note">The browser runs in the kell app.</p>
            : !bs.url ? <p className="dk-note dk-bottom-note">Type an address above, for example your project&rsquo;s dev server at localhost:3000.</p> : null}
        </div>
      </div>
    </section>
  )
}
