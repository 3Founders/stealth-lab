"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import type { SectionId } from "@/components/chat/SettingsDialog";

/**
 * The menu button at the left of the room header. It keeps the side panel short: everything that is not a room or a person lives
 * here or in Settings. Opens on click or Enter, closes on Escape or a click outside, and arrow keys move through the items.
 */
export default function HamburgerMenu({ paused, onSettings, onPause, onSignOut, onManageRooms, onTogglePanel, panelOpen }: {
  paused: boolean; onSettings: (s: SectionId) => void; onPause: () => void; onSignOut: () => void;
  onManageRooms: () => void; onTogglePanel: () => void; panelOpen: boolean;
}) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const off = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", off);
    requestAnimationFrame(() => box.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus());
    return () => document.removeEventListener("mousedown", off);
  }, [open]);

  function close(refocus = true) { setOpen(false); if (refocus) button.current?.focus(); }
  function key(e: React.KeyboardEvent) {
    if (e.key === "Escape") { e.preventDefault(); close(); return; }
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp" && e.key !== "Home" && e.key !== "End") return;
    e.preventDefault();
    const items = [...(box.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? [])];
    const at = items.indexOf(document.activeElement as HTMLElement);
    const next = e.key === "Home" ? 0 : e.key === "End" ? items.length - 1 : (at + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
    items[next]?.focus();
  }
  const run = (fn: () => void) => () => { close(false); fn(); };

  return (
    <div className="dg-hamburger" ref={box} onKeyDown={key}>
      <button ref={button} type="button" className="dg-burger" aria-haspopup="menu" aria-expanded={open} aria-label="Menu" onClick={() => setOpen((o) => !o)}>
        <span aria-hidden="true" className="dg-burger-bars"><i /><i /><i /></span>
      </button>
      {open && (
        <div className="dg-menu-pop" role="menu" aria-label="Chat menu">
          <button type="button" role="menuitem" onClick={run(onManageRooms)}>Manage rooms</button>
          <button type="button" role="menuitem" onClick={run(onTogglePanel)}>{panelOpen ? "Close room list" : "Show room list"}</button>
          <hr />
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
    </div>
  );
}
