"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { SettingsIcon } from "@/components/chat/icons";
import NotificationPanel, { type AlertsProps } from "@/components/chat/NotificationBell";
import Popover from "@/components/chat/Popover";
import { useSlideHighlight } from "@/components/chat/useSlideHighlight";
import type { SectionId } from "@/components/chat/SettingsDialog";

/**
 * The menu button at the left of the room header. It keeps only what is not already a button in the header or the side panel:
 * temporary rooms, Submit a Way, the room-list toggle, alerts, Settings, pause, help and sign-out. Search, Canvas, Desktop
 * sessions and Manage rooms live in the header/sidebar where they are always visible, so they are not repeated here.
 * Opens on click or Enter, closes on Escape or a click outside, and arrow keys move through the items.
 */
export default function HamburgerMenu({ paused, onSettings, onPause, onSignOut, onTogglePanel, panelOpen, alerts, onTemporary, onSubmitWay }: {
  /** Starts a temporary room: not saved, not synced. */
  onTemporary?: () => void;
  /** Opens "Submit a Way" (components/submit/SubmitWay.tsx) beside the chat. */
  onSubmitWay?: () => void;
  paused: boolean; onSettings: (s: SectionId) => void; onPause: () => void; onSignOut: () => void;
  onTogglePanel: () => void; panelOpen: boolean;
  /** The alerts list and its settings. Given, the menu has an Alerts item (with the unread count) and the button carries a dot. */
  alerts?: AlertsProps;
}) {
  const [open, setOpen] = useState(false);
  const [view, setView] = useState<"menu" | "alerts">("menu");
  const unread = alerts ? alerts.notes.filter((n) => !n.read).length : 0;
  const box = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const slide = useSlideHighlight();
  const pop = useRef<HTMLDivElement>(null);          // the menu is drawn in a portal (Popover), so it is not inside `box`

  useEffect(() => {
    if (!open) return;
    const off = (e: MouseEvent) => { const t = e.target as Node; if (box.current && !box.current.contains(t) && !pop.current?.contains(t)) setOpen(false); };
    document.addEventListener("mousedown", off);
    requestAnimationFrame(() => pop.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus());
    return () => document.removeEventListener("mousedown", off);
  }, [open]);

  function close(refocus = true) { setOpen(false); setView("menu"); if (refocus) button.current?.focus(); }
  function key(e: React.KeyboardEvent) {
    if (e.key === "Escape") { e.preventDefault(); close(); return; }
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp" && e.key !== "Home" && e.key !== "End") return;
    e.preventDefault();
    const items = [...(pop.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? [])];
    const at = items.indexOf(document.activeElement as HTMLElement);
    const next = e.key === "Home" ? 0 : e.key === "End" ? items.length - 1 : (at + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
    items[next]?.focus();
  }
  const run = (fn: () => void) => () => { close(false); fn(); };

  return (
    <div className="dg-hamburger" ref={box} onKeyDown={key}>
      <button ref={button} type="button" className="dg-burger" aria-haspopup="menu" aria-expanded={open} aria-label={unread ? `Menu, ${unread} unread alert${unread === 1 ? "" : "s"}` : "Menu"} onClick={() => { if (open) close(false); else setOpen(true); }}>
        <SettingsIcon />
        {unread > 0 && <span className="dg-burger-dot" aria-hidden="true">{unread > 9 ? "9+" : unread}</span>}
      </button>
      <Popover anchor={button} open={open} popRef={pop}>
      {alerts && view === "alerts" && (
        <NotificationPanel {...alerts} onClose={() => close(false)} onBack={() => setView("menu")} />
      )}
      {view === "menu" && (
        <div className="dg-menu-pop" role="menu" aria-label="Chat menu" ref={slide('[role="menuitem"]', '[role="menuitem"]:focus')}>
          {onSubmitWay && <button type="button" role="menuitem" onClick={run(onSubmitWay)}>Submit a Way</button>}
          {onTemporary && <button type="button" role="menuitem" onClick={run(onTemporary)}>New temporary room</button>}
          <button type="button" role="menuitem" onClick={run(onTogglePanel)}>{panelOpen ? "Close room list" : "Show room list"}</button>
          <hr />
          {alerts && <button type="button" role="menuitem" onClick={() => setView("alerts")}>Alerts{unread > 0 && <span className="dg-unread" aria-label={`${unread} unread`}>{unread > 99 ? "99+" : unread}</span>}</button>}
          <button type="button" role="menuitem" onClick={run(() => onSettings("preferences"))}>Settings</button>
          <hr />
          <button type="button" role="menuitem" onClick={run(onPause)}>{paused ? "Resume agents" : "Pause agents"}</button>
          <hr />
          <Link role="menuitem" href="/docs" onClick={() => close(false)}>Help and docs</Link>
          <button type="button" role="menuitem" onClick={run(onSignOut)}>Sign out</button>
        </div>
      )}
      </Popover>
    </div>
  );
}
