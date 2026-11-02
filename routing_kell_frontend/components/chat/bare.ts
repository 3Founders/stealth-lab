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
  return cap(bare(handle));
}

/** How a room is shown: no "#", first letter in capitals ("#general" -> "General"). */
export function roomName(label: string): string {
  return cap(label.replace(/^#/, ""));
}
