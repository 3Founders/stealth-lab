/**
 * Whether the rooms have explained Direct / Auto / Ask yet (components/chat/ModesIntro.tsx).
 *
 * Kept out of the component so the "once" part can be tested on its own, and so the desktop app can keep the same
 * rule with its own storage (its origin changes every start, so its copy goes through app/api/prefs instead).
 */
export const MODES_SEEN = "kell:modes-seen"

/** False only when this browser has never dismissed the explanation. A blocked storage reads as seen, not as "ask again". */
export function modesSeen(): boolean {
  try { return window.localStorage.getItem(MODES_SEEN) === "1" } catch { return true }
}

export function markModesSeen(): void {
  try { window.localStorage.setItem(MODES_SEEN, "1") } catch { /* shown again next time */ }
}
