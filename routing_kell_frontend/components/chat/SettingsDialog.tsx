"use client";
import Link from "next/link";
import { useEffect, useRef } from "react";
import NotificationSettings from "@/components/chat/NotificationSettings";
import { DEFAULT_CHAT_PREFS, type ChatPrefs, type TextSize } from "@/lib/chat/prefs";
import type { Prefs } from "@/lib/chat/notify";

export type SectionId = "preferences" | "notifications" | "data" | "agents" | "account" | "shortcuts" | "about";

export const SECTIONS: Array<{ id: SectionId; label: string }> = [
  { id: "preferences", label: "Preferences" },
  { id: "notifications", label: "Notifications" },
  { id: "data", label: "Your data" },
  { id: "agents", label: "Agents and models" },
  { id: "account", label: "Account" },
  { id: "shortcuts", label: "Keyboard shortcuts" },
  { id: "about", label: "About and privacy" },
];

const SIZES: Array<[TextSize, string]> = [["small", "Small"], ["default", "Default"], ["large", "Large"]];

export interface SettingsProps {
  open: boolean; section: SectionId; room: string;
  onSection: (s: SectionId) => void; onClose: () => void;
  chatPrefs: ChatPrefs; onChatPrefs: (p: ChatPrefs) => void;
  notifyPrefs: Prefs; onNotifyPrefs: (p: Prefs) => void;
  data: { onDownload: () => void; onImport: () => void; onDelete: () => void };
  agents: { modelText: string; modelReady: boolean; count: number; paused: boolean; onPause: (p: boolean) => void; maxHops: number };
  account: { name: string; onSignOut: () => void };
}

/**
 * The big Settings window opened from the menu: sections on the left (a row of tabs on a phone), the chosen section on the right.
 * It is a native <dialog>, so focus stays inside, Escape closes it, and the page behind is inert. Closing eases out like the
 * profile-photo question (the iOS sheet curve in globals.css).
 */
export default function SettingsDialog(p: SettingsProps) {
  const dialog = useRef<HTMLDialogElement>(null);
  const tabs = useRef<Record<string, HTMLButtonElement | null>>({});

  useEffect(() => {
    const d = dialog.current;
    if (!d) return;
    if (p.open && !d.open) d.showModal();
    if (!p.open && d.open) d.close();
  }, [p.open]);

  function close() {
    const d = dialog.current;
    if (!d || !d.open) { p.onClose(); return; }
    const calm = typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
    d.setAttribute("data-closing", "");
    window.setTimeout(() => { d.removeAttribute("data-closing"); p.onClose(); }, calm ? 0 : 240);
  }

  function tabKey(e: React.KeyboardEvent, i: number) {
    const to = e.key === "ArrowDown" || e.key === "ArrowRight" ? i + 1 : e.key === "ArrowUp" || e.key === "ArrowLeft" ? i - 1 : e.key === "Home" ? 0 : e.key === "End" ? SECTIONS.length - 1 : null;
    if (to === null) return;
    e.preventDefault();
    const next = SECTIONS[(to + SECTIONS.length) % SECTIONS.length];
    p.onSection(next.id);
    tabs.current[next.id]?.focus();
  }

  const set = (patch: Partial<ChatPrefs>) => p.onChatPrefs({ ...p.chatPrefs, ...patch });

  return (
    <dialog ref={dialog} className="dg-settings" aria-labelledby="dg-settings-title" onCancel={(e) => { e.preventDefault(); close(); }} onClose={p.onClose}
      onClick={(e) => { if (e.target === dialog.current) close(); }}>
      <div className="dg-set-frame">
        <header className="dg-set-head">
          <h2 id="dg-settings-title">Settings</h2>
          <button type="button" className="dg-set-x" onClick={close} aria-label="Close settings">Done</button>
        </header>
        <div className="dg-set-body">
          <nav className="dg-set-nav" role="tablist" aria-label="Settings sections" aria-orientation="vertical">
            {SECTIONS.map((s, i) => (
              <button key={s.id} ref={(el) => { tabs.current[s.id] = el; }} type="button" role="tab" id={`dg-tab-${s.id}`} aria-selected={p.section === s.id} aria-controls={`dg-panel-${s.id}`}
                tabIndex={p.section === s.id ? 0 : -1} onClick={() => p.onSection(s.id)} onKeyDown={(e) => tabKey(e, i)}>{s.label}</button>
            ))}
          </nav>
          <div className="dg-set-panel" role="tabpanel" id={`dg-panel-${p.section}`} aria-labelledby={`dg-tab-${p.section}`} tabIndex={0} data-lenis-prevent>
            {p.section === "preferences" && (
              <section>
                <h3>Preferences</h3>
                <fieldset className="dg-set-group">
                  <legend>Text size</legend>
                  <div className="dg-seg" role="radiogroup" aria-label="Text size">
                    {SIZES.map(([v, l]) => (
                      <label key={v} data-on={p.chatPrefs.textSize === v}><input type="radio" name="dg-size" checked={p.chatPrefs.textSize === v} onChange={() => set({ textSize: v })} />{l}</label>
                    ))}
                  </div>
                </fieldset>
                <label className="dg-set-row"><input type="checkbox" checked={p.chatPrefs.compact} onChange={(e) => set({ compact: e.target.checked })} /><span><b>Compact messages</b><small>Less space between messages, so more fit on screen.</small></span></label>
                <label className="dg-set-row"><input type="checkbox" checked={p.chatPrefs.showTimes} onChange={(e) => set({ showTimes: e.target.checked })} /><span><b>Show times</b><small>The time next to each message.</small></span></label>
                <label className="dg-set-row"><input type="checkbox" checked={p.chatPrefs.enterToSend} onChange={(e) => set({ enterToSend: e.target.checked })} /><span><b>Enter sends the message</b><small>{p.chatPrefs.enterToSend ? "Shift+Enter starts a new line." : "Enter starts a new line; Ctrl or Cmd+Enter sends."}</small></span></label>
                <button type="button" className="btn-line dg-mini-btn" onClick={() => p.onChatPrefs(DEFAULT_CHAT_PREFS)}>Reset to defaults</button>
              </section>
            )}
            {p.section === "notifications" && (
              <section>
                <h3>Notifications</h3>
                <p className="dg-set-lead">Choose what tells you about new activity, and when to stay quiet.</p>
                <NotificationSettings prefs={p.notifyPrefs} room={p.room} onPrefs={p.onNotifyPrefs} />
              </section>
            )}
            {p.section === "data" && (
              <section>
                <h3>Your data</h3>
                <p className="dg-set-lead">Your rooms and chats are saved in this browser, encrypted, and synced to your account so they follow you to your other devices.</p>
                <div className="dg-set-cards">
                  <div><b>Download</b><small>Keep a copy of your own, as a file.</small><button type="button" className="btn-line dg-mini-btn" onClick={p.data.onDownload}>Download</button></div>
                  <div><b>Import</b><small>Bring a downloaded copy back in.</small><button type="button" className="btn-line dg-mini-btn" onClick={p.data.onImport}>Import</button></div>
                  <div><b>Delete history</b><small>Removes the saved chats from this browser. Rooms on your account stay.</small><button type="button" className="btn-line dg-mini-btn" onClick={p.data.onDelete}>Delete history</button></div>
                </div>
              </section>
            )}
            {p.section === "agents" && (
              <section>
                <h3>Agents and models</h3>
                <p className="dg-set-lead">{p.agents.count} agent{p.agents.count === 1 ? "" : "s"} in your rooms.</p>
                <p className="small"><b>Model for agents in this tab:</b> {p.agents.modelReady ? p.agents.modelText : "none set up yet"}. <Link href="/account/models" style={{ textDecoration: "underline" }}>Change under Models</Link>.</p>
                <label className="dg-set-row"><input type="checkbox" checked={p.agents.paused} onChange={(e) => p.agents.onPause(e.target.checked)} /><span><b>Pause agents in this tab</b><small>They will not be woken until you resume.</small></span></label>
                <p className="small dim">Agents in this tab hand off to each other at most {p.agents.maxHops} times before a person must step in. Agents on the relay stay on your account and keep working with this page closed.</p>
              </section>
            )}
            {p.section === "account" && (
              <section>
                <h3>Account</h3>
                <p className="dg-set-lead">Signed in as <b>{p.account.name || "you"}</b>.</p>
                <ul className="dg-set-links">
                  <li><Link href="/account/profile">Profile and photo</Link></li>
                  <li><Link href="/account/models">Models and keys</Link></li>
                  <li><Link href="/account/connections">Connected apps</Link></li>
                </ul>
                <button type="button" className="btn-line dg-mini-btn" onClick={p.account.onSignOut}>Sign out</button>
              </section>
            )}
            {p.section === "shortcuts" && (
              <section>
                <h3>Keyboard shortcuts</h3>
                <dl className="dg-keys">
                  <div><dt><kbd>Enter</kbd></dt><dd>{p.chatPrefs.enterToSend ? "Send the message" : "New line"}</dd></div>
                  <div><dt><kbd>Shift</kbd> + <kbd>Enter</kbd></dt><dd>{p.chatPrefs.enterToSend ? "New line" : "New line"}</dd></div>
                  <div><dt><kbd>Ctrl</kbd> / <kbd>Cmd</kbd> + <kbd>Enter</kbd></dt><dd>{p.chatPrefs.enterToSend ? "Send the message" : "Send the message"}</dd></div>
                  <div><dt><kbd>@</kbd></dt><dd>Open the list of people and agents to mention</dd></div>
                  <div><dt><kbd>↑</kbd> <kbd>↓</kbd></dt><dd>Move through that list</dd></div>
                  <div><dt><kbd>Enter</kbd> / <kbd>Tab</kbd></dt><dd>Pick the highlighted name</dd></div>
                  <div><dt><kbd>Esc</kbd></dt><dd>Close the list, a menu or this window</dd></div>
                </dl>
              </section>
            )}
            {p.section === "about" && (
              <section>
                <h3>About and privacy</h3>
                <ul className="dg-about">
                  <li><b>Rooms are open.</b> They follow you to your other devices, Claude, ChatGPT and other connectors can join them, and keळ can read them. They are not end-to-end encrypted.</li>
                  <li><b>Where your messages go.</b> Messages are stored on keळ&rsquo;s server (with sensitive-looking text redacted) so your rooms sync. A model reply sends the prompt to the model provider you chose.</li>
                  <li><b>Not yet audited.</b> keळ does not have a SOC 2 report or an independent penetration test yet.</li>
                </ul>
                <p className="small">
                  <Link href="/docs/legal/privacy" style={{ textDecoration: "underline" }}>Privacy policy</Link> · <Link href="/docs/legal/terms" style={{ textDecoration: "underline" }}>Terms</Link> · <Link href="/docs" style={{ textDecoration: "underline" }}>Docs</Link> · <Link href="/contact" style={{ textDecoration: "underline" }}>Contact</Link>
                </p>
                <p className="small dim">Pre-release.</p>
              </section>
            )}
          </div>
        </div>
      </div>
    </dialog>
  );
}
