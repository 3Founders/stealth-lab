/** A handle without its "@". The "@" belongs to calling someone (typing it, the mention list); a name shown on
 * screen is plain. */
export function bare(handle: string): string {
  return handle.replace(/^@/, "");
}
