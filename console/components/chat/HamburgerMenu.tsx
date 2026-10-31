"use client";
import { useEffect, useRef, useState } from "react";

/** The menu button at the left of the room header: Manage rooms, show or close the room list, pause agents. Escape or a click outside closes it. */
export default function HamburgerMenu({ paused, panelOpen, onManageRooms, onTogglePanel, onPause }: {
  paused: boolean; panelOpen: boolean; onManageRooms: () => void; onTogglePanel: () => void; onPause: () => void;
}) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const off = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", off); document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", off); document.removeEventListener("keydown", esc); };
  }, [open]);
  const run = (fn: () => void) => () => { setOpen(false); fn(); };
  return (
    <div className="dg-hamburger" ref={box}>
      <button type="button" className="dg-burger" aria-haspopup="menu" aria-expanded={open} aria-label="Menu" onClick={() => setOpen((o) => !o)}>
        <span aria-hidden="true" className="dg-burger-bars"><i /><i /><i /></span>
      </button>
      {open && (
        <div className="dg-menu-pop" role="menu" aria-label="Chat menu">
          <button type="button" role="menuitem" onClick={run(onManageRooms)}>Manage rooms</button>
          <button type="button" role="menuitem" onClick={run(onTogglePanel)}>{panelOpen ? "Close room list" : "Show room list"}</button>
          <hr />
          <button type="button" role="menuitem" onClick={run(onPause)}>{paused ? "Resume agents" : "Pause agents"}</button>
        </div>
      )}
    </div>
  );
}
