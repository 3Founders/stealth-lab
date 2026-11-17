"use client"
// The quick-ask box (electron/presence.ts): Ctrl+Shift+Space over any app. One field; Enter sends the question to a new chat in
// the main window, Shift+Enter adds a line, Escape hides the box. The window is transparent, so this page draws its own card.
import { useEffect, useRef, useState } from "react"

type Quick = { ask: (t: string) => void; hide: () => void; openMain: () => void; onShown: (cb: () => void) => () => void }
const quick = (): Quick | undefined => (typeof window !== "undefined" ? (window as any).kellDesktop?.quick : undefined)

export default function QuickAsk() {
  const [text, setText] = useState("")
  const ref = useRef<HTMLTextAreaElement>(null)
  useEffect(() => {
    document.documentElement.style.background = "transparent"
    document.body.style.background = "transparent"
    ref.current?.focus()
    return quick()?.onShown(() => { setText(""); window.setTimeout(() => ref.current?.focus(), 0) })
  }, [])
  const send = () => { const t = text.trim(); if (!t) return; quick()?.ask(t); setText("") }
  return (
    <div className="qk-card" onKeyDown={(e) => { if (e.key === "Escape") { e.preventDefault(); quick()?.hide() } }}>
      <style>{`
        .qk-card { position: fixed; inset: 8px; display: flex; flex-direction: column; gap: 8px; padding: 12px 14px;
          background: var(--paper, #F8F6EF); color: var(--ink, #1d1b16); border: 1px solid var(--rule-strong, rgba(0,0,0,.16));
          border-radius: 14px; box-shadow: 0 8px 28px rgba(0,0,0,.22); -webkit-app-region: drag; }
        .qk-card textarea { -webkit-app-region: no-drag; flex: 1; resize: none; border: 0; outline: 0; background: transparent;
          color: inherit; font: inherit; font-size: 16px; line-height: 1.4; }
        .qk-foot { display: flex; align-items: center; gap: 10px; font-size: 12px; opacity: .7; -webkit-app-region: no-drag; }
        .qk-foot button { background: none; border: 0; color: inherit; font: inherit; text-decoration: underline; cursor: pointer; padding: 0; }
        .qk-foot .sp { flex: 1; }
      `}</style>
      <textarea ref={ref} value={text} rows={2} aria-label="Ask kell" placeholder="Ask kell anything…"
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send() } }} />
      <div className="qk-foot">
        <span>Enter to ask · Shift+Enter for a new line · Esc to close</span>
        <span className="sp" />
        <button type="button" onClick={() => quick()?.openMain()}>Open kell</button>
      </div>
    </div>
  )
}
