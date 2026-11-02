"use client";
import { useRef } from "react";

type RefFn = (el: HTMLElement | null) => void;

/** Wires one menu: finds the row under the pointer or the keyboard and tells the CSS where it is. Returns the ref callback. */
function makeRef(itemSel: string, active: string): RefFn {
  let undo: (() => void) | null = null;
  return (el) => {
    undo?.();
    undo = null;
    if (!el) return;
    el.classList.add("dg-slide");

    const place = (row: HTMLElement | null) => {
      if (!row) { el.style.setProperty("--hl-on", "0"); return; }
      const r = row.getBoundingClientRect();
      const c = el.getBoundingClientRect();
      if (el.style.getPropertyValue("--hl-on") !== "1") {            // first appearance: placed at once, not slid in from elsewhere
        el.setAttribute("data-snap", "");
        requestAnimationFrame(() => requestAnimationFrame(() => el.removeAttribute("data-snap")));
      }
      el.style.setProperty("--hl-x", `${r.left - c.left + el.scrollLeft - el.clientLeft}px`);
      el.style.setProperty("--hl-y", `${r.top - c.top + el.scrollTop - el.clientTop}px`);
      el.style.setProperty("--hl-w", `${r.width}px`);
      el.style.setProperty("--hl-h", `${r.height}px`);
      el.style.setProperty("--hl-on", "1");
    };
    const rowOf = (t: EventTarget | null) => (t instanceof Element ? t.closest<HTMLElement>(itemSel) : null);
    const hovered = () => [...el.querySelectorAll<HTMLElement>(itemSel)].find((n) => n.matches(":hover")) ?? null;
    const current = () => hovered() ?? el.querySelector<HTMLElement>(active);

    const onOver = (e: PointerEvent) => { const r = rowOf(e.target); if (r && el.contains(r)) place(r); };
    const onLeave = () => place(el.querySelector<HTMLElement>(active));
    const onFocus = (e: FocusEvent) => { const r = rowOf(e.target); if (r && el.contains(r)) place(r); };
    el.addEventListener("pointerover", onOver);
    el.addEventListener("pointerleave", onLeave);
    el.addEventListener("focusin", onFocus);
    // A row chosen with the keyboard in a list that marks it with data-active: follow it.
    const mo = new MutationObserver(() => { if (!hovered()) place(el.querySelector<HTMLElement>(active)); });
    mo.observe(el, { attributes: true, attributeFilter: ["data-active"], subtree: true });
    place(current());

    undo = () => {
      el.removeEventListener("pointerover", onOver);
      el.removeEventListener("pointerleave", onLeave);
      el.removeEventListener("focusin", onFocus);
      mo.disconnect();
      el.classList.remove("dg-slide");
    };
  };
}

/**
 * The highlight under the row you point at (or reach with the keyboard) slides from the old row to the new one instead of jumping, the way
 * menus on iOS and macOS do. One soft yellow shape is drawn by CSS (.dg-slide::before); this hook only tells it where the row is, as custom
 * properties, and the move itself is a transform transition on the Apple ease, so it costs almost nothing. Leaving the menu hides it, or
 * returns it to the keyboard's row.
 *
 * Use as a ref: <ul ref={slide('li[role="option"]')}>. `active` names the row the keyboard has chosen (default: data-active="true").
 * The same selector pair gives the same ref function every render, so a re-render does not re-wire the menu.
 */
export function useSlideHighlight(): (itemSel: string, active?: string) => RefFn {
  const cache = useRef(new Map<string, RefFn>());
  return (itemSel, active = '[data-active="true"]') => {
    const key = `${itemSel}\u0000${active}`;
    let fn = cache.current.get(key);
    if (!fn) { fn = makeRef(itemSel, active); cache.current.set(key, fn); }
    return fn;
  };
}
