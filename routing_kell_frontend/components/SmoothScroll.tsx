"use client";
import { useEffect } from "react";
import { usePathname } from "next/navigation";
import Lenis from "lenis";

declare global {
  interface Window { __kelScrollTo?: (target: string) => void; __kelBooted?: boolean }
}

const OFFSET = -96; // clears the floating nav

/** Lenis smooth scroll, disabled entirely under prefers-reduced-motion. Also owns the /#about jump. */
export default function SmoothScroll() {
  const pathname = usePathname();

  useEffect(() => {
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let lenis: Lenis | null = null;
    let raf = 0;

    if (!reduced) {
      lenis = new Lenis({ lerp: 0.11, wheelMultiplier: 0.9 });
      const tick = (t: number) => { lenis!.raf(t); raf = requestAnimationFrame(tick); };
      raf = requestAnimationFrame(tick);
    }

    window.__kelScrollTo = (target: string) => {
      const el = document.querySelector(target);
      if (!el) return;
      if (lenis) lenis.scrollTo(el as HTMLElement, { offset: OFFSET, duration: 1.3 });
      else (el as HTMLElement).scrollIntoView({ behavior: "auto", block: "start" });
    };

    // Arrived from another page inside the site via /#about (a client navigation): start at the top, then glide to the section.
    // A fresh load or reload is the main page, not a jump: a leftover "#about" in the address bar (from clicking About earlier) is
    // dropped and the page stays at the top, instead of scrolling straight to About every time.
    // The URL's hash is committed slightly after this effect runs, so it is read after a short delay.
    const firstLoad = !window.__kelBooted;
    window.__kelBooted = true;
    let timer: number | undefined;
    if (pathname === "/") {
      timer = window.setTimeout(() => {
        const hash = window.location.hash;
        if (!hash) return;
        if (firstLoad) {
          window.history.replaceState(null, "", window.location.pathname + window.location.search);
          window.scrollTo(0, 0);
          return;
        }
        window.scrollTo(0, 0);
        window.__kelScrollTo?.(hash);
      }, 220);
    }

    return () => {
      window.clearTimeout(timer);
      cancelAnimationFrame(raf);
      lenis?.destroy();
      delete window.__kelScrollTo;
    };
  }, [pathname]);

  return null;
}
