"use client";
import { useEffect, useState } from "react";
import { THEME_EVENT, applyTheme, effectiveTheme, readTheme, saveTheme, type Effective, type Theme } from "@/lib/theme";

/** The theme in effect, and a way to change it. Every toggle and the Settings choice stay in step through one window event. */
export function useTheme(): { mode: Theme; effective: Effective; set: (t: Theme) => void } {
  const [mode, setMode] = useState<Theme>("auto");
  const [effective, setEffective] = useState<Effective>("light");
  useEffect(() => {
    const sync = () => {
      const m = readTheme();
      setMode(m);
      const shown = document.documentElement.getAttribute("data-theme");     // what the page is showing now, including a ?theme= visit
      setEffective(shown === "dark" || shown === "light" ? shown : effectiveTheme(m));
    };
    sync();
    window.addEventListener(THEME_EVENT, sync);
    window.addEventListener("storage", sync);
    return () => { window.removeEventListener(THEME_EVENT, sync); window.removeEventListener("storage", sync); };
  }, []);
  const set = (t: Theme) => { saveTheme(t); applyTheme(t); window.dispatchEvent(new Event(THEME_EVENT)); };
  return { mode, effective, set };
}

/** Keeps an automatic theme in step with the clock while the page stays open (dusk arrives without a reload). Draws nothing. */
export function ThemeWatcher() {
  useEffect(() => {
    const tick = () => { if (readTheme() === "auto") { applyTheme("auto"); window.dispatchEvent(new Event(THEME_EVENT)); } };
    const id = window.setInterval(tick, 5 * 60 * 1000);
    document.addEventListener("visibilitychange", tick);
    return () => { window.clearInterval(id); document.removeEventListener("visibilitychange", tick); };
  }, []);
  return null;
}

/** A button that switches between light and dark: a sun while it is dark (press for light), a moon while it is light (press for dark). */
export default function ThemeToggle({ className = "" }: { className?: string }) {
  const { effective, set } = useTheme();
  const toDark = effective === "light";
  return (
    <button type="button" className={`theme-toggle ${className}`} onClick={() => set(toDark ? "dark" : "light")} aria-label={toDark ? "Switch to dark mode" : "Switch to light mode"} title={toDark ? "Dark mode" : "Light mode"}>
      {toDark
        ? <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5Z" /></svg>
        : <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></svg>}
    </button>
  );
}
