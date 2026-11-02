"use client";
import { useEffect, useState, type CSSProperties, type RefObject } from "react";

export interface RemoteCursor { name: string; from: number; to: number }

const MIRRORED = ["fontFamily", "fontSize", "fontWeight", "fontStyle", "lineHeight", "letterSpacing", "tabSize", "whiteSpace", "overflowWrap", "wordBreak",
  "paddingTop", "paddingRight", "paddingBottom", "paddingLeft"] as const;
const MAX_CHARS = 250_000;                                       // past this the copies would cost more than they are worth

/**
 * Other people's carets and selections in a text editor. A textarea cannot show a second caret, so each person gets a transparent copy of the
 * text, laid exactly over the editor (same font, padding and wrapping, scrolled with it), in which only their selection is painted and their
 * caret is a thin coloured line with their name. It never takes the pointer or the focus, so typing and clicking go to the textarea beneath.
 */
export default function CursorLayer({ area, text, cursors, color }: { area: RefObject<HTMLTextAreaElement | null>; text: string; cursors: RemoteCursor[]; color: (name: string) => string }) {
  const [box, setBox] = useState<{ left: number; top: number; width: number; height: number; style: CSSProperties } | null>(null);
  const [scroll, setScroll] = useState({ top: 0, left: 0 });

  useEffect(() => {
    const el = area.current;
    if (!el) return;
    const measure = () => {
      const cs = getComputedStyle(el);
      const style: Record<string, string> = {};
      for (const k of MIRRORED) style[k] = cs[k];
      setBox({ left: el.offsetLeft + el.clientLeft, top: el.offsetTop + el.clientTop, width: el.clientWidth, height: el.clientHeight, style: style as CSSProperties });
    };
    const sync = () => setScroll({ top: el.scrollTop, left: el.scrollLeft });
    measure(); sync();
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(measure) : null;
    ro?.observe(el);
    el.addEventListener("scroll", sync, { passive: true });
    return () => { ro?.disconnect(); el.removeEventListener("scroll", sync); };
  }, [area, text.length === 0]);

  if (!box || cursors.length === 0 || text.length > MAX_CHARS) return null;
  const len = text.length;
  return (
    <div className="dg-rl-box" aria-hidden="true" style={{ left: box.left, top: box.top, width: box.width, height: box.height }}>
      {cursors.slice(0, 8).map((c) => {
        const a = Math.max(0, Math.min(len, c.from)), b = Math.max(a, Math.min(len, c.to));
        return (
          <div key={c.name} className="dg-rl" style={{ ...box.style, transform: `translate(${-scroll.left}px, ${-scroll.top}px)`, ["--c" as string]: color(c.name) }}>
            <span className="dg-rl-t">{text.slice(0, a)}</span>
            {b > a && <mark className="dg-rs">{text.slice(a, b)}</mark>}
            <span className="dg-rc"><span className="dg-rc-name">{c.name}</span></span>
            <span className="dg-rl-t">{text.slice(b)}{"​"}</span>
          </div>
        );
      })}
    </div>
  );
}
