/**
 * Display-only title cleanup.
 *
 * Some rows (notably problems seeded by e2e tests) carry an internal
 * "[<slug> <run-id>] real title" prefix. That bracket group is a test
 * artifact, not product content — strip it for display. This does not change
 * stored data; if stripping leaves nothing, the original is kept.
 */
export function displayTitle(
  raw: string | null | undefined,
  fallback = "Untitled"
): string {
  const t = (raw ?? "").trim();
  if (!t) return fallback;
  const stripped = t.replace(/^\[[^\]]*\]\s*/, "").trim();
  return stripped || t;
}
