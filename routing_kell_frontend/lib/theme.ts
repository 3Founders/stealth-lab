// Light, dark, or follow the device. The choice is kept on this device (localStorage) and applied as data-theme on <html>; with
// "system" no attribute is set and the stylesheet follows prefers-color-scheme.
export type Theme = "system" | "light" | "dark";
export const THEME_KEY = "kell:theme";

export const isTheme = (v: unknown): v is Theme => v === "system" || v === "light" || v === "dark";

export function readTheme(storage: Pick<Storage, "getItem"> | null = typeof localStorage === "undefined" ? null : localStorage): Theme {
  try { const v = storage?.getItem(THEME_KEY); return isTheme(v) ? v : "system"; } catch { return "system"; }
}

/** Puts the theme on <html>. "system" removes the attribute so the device's setting decides. */
export function applyTheme(t: Theme, root: HTMLElement | null = typeof document === "undefined" ? null : document.documentElement): void {
  if (!root) return;
  if (t === "system") root.removeAttribute("data-theme"); else root.setAttribute("data-theme", t);
}

export function saveTheme(t: Theme, storage: Pick<Storage, "setItem"> | null = typeof localStorage === "undefined" ? null : localStorage): void {
  try { storage?.setItem(THEME_KEY, t); } catch { /* storage blocked: the choice lasts until the page closes */ }
}

/** Runs in <head> before the page paints, so a dark page never flashes light first. Must stay in step with readTheme and applyTheme. */
export const THEME_INIT = `try{var t=localStorage.getItem('${THEME_KEY}');if(t==='light'||t==='dark')document.documentElement.setAttribute('data-theme',t)}catch(e){}`;
