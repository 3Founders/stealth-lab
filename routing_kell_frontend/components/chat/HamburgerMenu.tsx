"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { SettingsIcon } from "@/components/chat/icons";
import NotificationPanel, { type AlertsProps } from "@/components/chat/NotificationBell";
import Popover from "@/components/chat/Popover";
import { useSlideHighlight } from "@/components/chat/useSlideHighlight";
import type { SectionId } from "@/components/chat/SettingsDialog";

/**
 * The menu button at the left of the room header. It keeps the side panel short: everything that is not a room or a person lives
 * here or in Settings. Opens on click or Enter, closes on Escape or a click outside, and arrow keys move through the items.
 */
export default function HamburgerMenu({ paused, onSettings, onPause, onSignOut, onManageRooms, onTogglePanel, panelOpen, alerts, onSearch, onTemporary, onCanvas, canvasCount = 0 }: {
  /** Opens the search over your rooms (also Cmd+K or Ctrl+K). */
  onSearch?: () => void;
  /** Starts a temporary room: not saved, not synced. */
  onTemporary?: () => void;
  /** Opens the canvas beside the chat, and how many canvases this room has. */
  onCanvas?: () => void; canvasCount?: number;
  paused: boolean; onSettings: (s: SectionId) => void; onPause: () => void; onSignOut: () => void;
  onManageRooms: () => void; onTogglePanel: () => void; panelOpen: boolean;
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
          {onSearch && <button type="button" role="menuitem" onClick={run(onSearch)}>Search<kbd>Ctrl K</kbd></button>}
          {onCanvas && <button type="button" role="menuitem" onClick={run(onCanvas)}>Canvas{canvasCount > 0 && <span className="dg-unread" aria-label={`${canvasCount} in this room`}>{canvasCount}</span>}</button>}
          {onTemporary && <button type="button" role="menuitem" onClick={run(onTemporary)}>New temporary room</button>}
          <button type="button" role="menuitem" onClick={run(onManageRooms)}>Manage rooms</button>
          <button type="button" role="menuitem" onClick={run(onTogglePanel)}>{panelOpen ? "Close room list" : "Show room list"}</button>
          <hr />
          {alerts && <button type="button" role="menuitem" onClick={() => setView("alerts")}>Alerts{unread > 0 && <span className="dg-unread" aria-label={`${unread} unread`}>{unread > 99 ? "99+" : unread}</span>}</button>}
          <button type="button" role="menuitem" onClick={run(() => onSettings("preferences"))}>Preferences</button>
          <button type="button" role="menuitem" onClick={run(() => onSettings("notifications"))}>Notifications</button>
          <button type="button" role="menuitem" onClick={run(() => onSettings("data"))}>Your data</button>
          <button type="button" role="menuitem" onClick={run(() => onSettings("agents"))}>Agents and models</button>
          <button type="button" role="menuitem" onClick={run(() => onSettings("shortcuts"))}>Keyboard shortcuts</button>
          <hr />
          <button type="button" role="menuitem" onClick={run(onPause)}>{paused ? "Resume agents" : "Pause agents"}</button>
          <hr />
          <Link role="menuitem" href="/docs" onClick={() => close(false)}>Help and docs</Link>
          <Link role="menuitem" href="/contact" onClick={() => close(false)}>Contact</Link>
          <button type="button" role="menuitem" onClick={run(() => onSettings("about"))}>About and privacy</button>
          <hr />
          <button type="button" role="menuitem" onClick={run(() => onSettings("account"))}>Account</button>
          <button type="button" role="menuitem" onClick={run(onSignOut)}>Sign out</button>
        </div>
      )}
      </Popover>
    </div>
  );
}
