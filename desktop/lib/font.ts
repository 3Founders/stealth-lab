// The app's typeface choice (View > Font): the default stack, Inter, or Poppins.
//
// Efficient on purpose: the font CSS is self-hosted and loaded once (app/layout.tsx); the browser downloads a face only
// when rendered text uses that family, so the family that is not chosen costs nothing but a few @font-face lines. The
// choice is one localStorage key, restored before paint by public/font-init.js, and mirrored to desktop-prefs.json by
// prefs-init.js like every other kell setting.
export type FontChoice = "default" | "inter" | "poppins"

export const FONT_KEY = "kell.font"

export function readFont(): FontChoice {
  try {
    const f = localStorage.getItem(FONT_KEY)
    return f === "inter" || f === "poppins" ? f : "default"
  } catch {
    return "default"
  }
}

export function applyFont(font: FontChoice): void {
  const el = document.documentElement
  if (font === "default") delete el.dataset.font
  else el.dataset.font = font
}

export function saveFont(font: FontChoice): void {
  try { localStorage.setItem(FONT_KEY, font) } catch { /* storage blocked: this run only */ }
  applyFont(font)
}
