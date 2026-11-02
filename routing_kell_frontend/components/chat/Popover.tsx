"use client";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

/**
 * A menu or panel that opens from a button. As docs/UIeffects.md says for glass menus: it is rendered into document.body and placed from the
 * trigger's rectangle, so no ancestor with overflow: hidden (the side panel, the header) can clip it; it opens upward when there is no
 * room below; it follows the trigger on resize and scroll; and while it is closed it is not in the page at all, so idle costs nothing.
 * It is wrapped in .dg-page so the chat's glass tokens and the light or dark theme apply to it as they do inside the page.
 */
export default function Popover({ anchor, open, align = "left", matchWidth = false, popRef, children }: {
  anchor: React.RefObject<HTMLElement | null>;
  open: boolean;
  align?: "left" | "right";
  /** As wide as the trigger at least. */
  matchWidth?: boolean;
  /** The popup's element, so the owner can tell a click inside it from a click outside. */
  popRef?: React.RefObject<HTMLDivElement | null>;
  children: React.ReactNode;
}) {
  const inner = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ top: number; left?: number; right?: number; minWidth?: number; up: boolean } | null>(null);

  const place = () => {
    const a = anchor.current;
    if (!a) return;
    const r = a.getBoundingClientRect();
    const h = inner.current?.offsetHeight ?? 0;
    const room = window.innerHeight - r.bottom - 8;
    const up = h > 0 && h > room && r.top - 8 > room;
    setPos({
      top: up ? Math.max(8, r.top - 8 - h) : r.bottom + 8,
      ...(align === "left" ? { left: Math.max(8, Math.min(r.left, window.innerWidth - 8 - (inner.current?.offsetWidth ?? 0))) } : { right: Math.max(8, window.innerWidth - r.right) }),
      ...(matchWidth ? { minWidth: r.width } : {}),
      up,
    });
  };

  useLayoutEffect(() => { if (open) place(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [open, children]);
  useEffect(() => {
    if (!open) { setPos(null); return; }
    const again = () => place();
    window.addEventListener("resize", again);
    window.addEventListener("scroll", again, true);
    return () => { window.removeEventListener("resize", again); window.removeEventListener("scroll", again, true); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  if (!open || typeof document === "undefined") return null;
  return createPortal(
    <div className="dg-page dg-portal" data-lenis-prevent>
      <div
        className="dg-pop" data-up={pos?.up || undefined}
        ref={(el) => { (inner as React.MutableRefObject<HTMLDivElement | null>).current = el; if (popRef) (popRef as React.MutableRefObject<HTMLDivElement | null>).current = el; }}
        style={{ position: "fixed", zIndex: 80, top: pos?.top ?? -9999, left: pos?.left, right: pos?.right, minWidth: pos?.minWidth, visibility: pos ? "visible" : "hidden" }}
      >
        {children}
      </div>
    </div>,
    document.body,
  );
}
