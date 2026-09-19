"use client";
import { useEffect, useRef } from "react";

/** Adds `.in` once the element scrolls into view. Without html.motion (reduced motion / no JS) content is always visible. */
export default function Reveal({
  children, delay = 0, className = "", as: Tag = "div", style,
}: { children: React.ReactNode; delay?: number; className?: string; as?: React.ElementType; style?: React.CSSProperties }) {
  const ref = useRef<HTMLElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (!("IntersectionObserver" in window)) { el.classList.add("in"); return; }
    const io = new IntersectionObserver(
      (entries) => entries.forEach((e) => { if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); } }),
      { rootMargin: "0px 0px -8% 0px", threshold: 0.05 },
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);
  return (
    <Tag ref={ref} className={`reveal ${className}`} style={{ "--d": `${delay}ms`, ...style } as React.CSSProperties}>
      {children}
    </Tag>
  );
}
