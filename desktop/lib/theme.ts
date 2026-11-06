// Light or dark. The app opens light unless the person picked dark (owner, 2026-11-06: it turned dark by itself after 19:00, from the
// website's clock rule). The choice is kept on this device (localStorage). The page always carries the theme
// in effect as data-theme on <html>; the stylesheet reads only that.
export type Theme = "auto" | "light" | "dark";
export type Effective = "light" | "dark";
export const THEME_KEY = "kell:theme";
export const THEME_EVENT = "kell-theme";

/** Dark from this hour in the evening until the morning hour, in the device's local time. */
export const DARK_FROM_HOUR = 19;
export const DARK_UNTIL_HOUR = 7;

export const isTheme = (v: unknown): v is Theme => v === "auto" || v === "light" || v === "dark";

/** The theme the clock asks for: 19:00 to 06:59 is dark. */
export function autoTheme(now: Date = new Date()): Effective {
  const h = now.getHours();
  return h >= DARK_FROM_HOUR || h < DARK_UNTIL_HOUR ? "dark" : "light";
}

export function effectiveTheme(t: Theme, _now: Date = new Date()): Effective {
  return t === "auto" ? "light" : t;              // nothing chosen: light (the clock rule, autoTheme, is not used in the app)
}

/** What is saved: "light", "dark", or "auto" for nothing saved or anything else (an older "system" choice counts as automatic). */
export function readTheme(storage: Pick<Storage, "getItem"> | null = typeof localStorage === "undefined" ? null : localStorage): Theme {
  try { const v = storage?.getItem(THEME_KEY); return v === "light" || v === "dark" ? v : "auto"; } catch { return "auto"; }
}

/** Puts the theme in effect on <html>. */
export function applyTheme(t: Theme, root: HTMLElement | null = typeof document === "undefined" ? null : document.documentElement, now: Date = new Date()): Effective {
  const e = effectiveTheme(t, now);
  root?.setAttribute("data-theme", e);
  return e;
}

export function saveTheme(t: Theme, storage: Pick<Storage, "setItem" | "removeItem"> | null = typeof localStorage === "undefined" ? null : localStorage): void {
  try { if (t === "auto") storage?.removeItem(THEME_KEY); else storage?.setItem(THEME_KEY, t); } catch { /* storage blocked: the choice lasts until the page closes */ }
}

/** Runs in <head> before the page paints, so a dark page never flashes light first. ?theme=dark or ?theme=light shows that theme for this
 * visit only. Must stay in step with readTheme and autoTheme. */
export const THEME_INIT =
  `try{var q=new URLSearchParams(location.search).get('theme'),t=localStorage.getItem('${THEME_KEY}'),h=new Date().getHours();` +
  `var e=(q==='light'||q==='dark')?q:(t==='light'||t==='dark')?t:'light';` +
  `document.documentElement.setAttribute('data-theme',e)}catch(e){}`;
