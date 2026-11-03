/** A handle without its "@". The "@" belongs to calling someone (typing it, the mention list); a name shown on
 * screen is plain. */
export function bare(handle: string): string {
  return handle.replace(/^@/, "");
}

/** The first letter in capitals. Names are shown this way everywhere; the stored handle stays as typed. */
export function cap(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** How a person or agent is shown: no "@", first letter in capitals ("@scout" -> "Scout"). */
export function nameOf(handle: string): string {
  const h = bare(handle);
  // A connected agent is shown exactly as it joined, "asha/claude-code" or "asha/claude-code/Q4 plan review" (its chat's own title, spaces and
  // capitals kept): that is also how another person calls it (@asha/claude).
  if (h.includes("/")) return h;
  return cap(h);
}

/** How a room is shown: no "#", first letter in capitals ("#general" -> "General"). */
export function roomName(label: string): string {
  return cap(label.replace(/^#/, ""));
}
