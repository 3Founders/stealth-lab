import type { Problem } from "@/lib/kel-api";

/**
 * MOCK ADAPTER — clearly isolated, temporary.
 *
 * The `problems` table (backend/db/35_product_model.sql) has no category column, and no
 * taxonomy endpoint exists yet. Everything the corpus holds today is coding-related, so
 * every Problem is labelled "Coding" unless its own `metadata.category` (a real, if rarely
 * populated, JSONB field) says otherwise. This is a display default, not a fabricated
 * statistic — no counts, run data or activity are invented here.
 *
 * Replace `categoryOf` with a real field/endpoint once the backend has one, and delete this
 * file. Nothing outside this module should need to change.
 */
export const CATEGORIES = ["All", "Coding"] as const;
export type Category = (typeof CATEGORIES)[number];

export function categoryOf(p: Problem): Exclude<Category, "All"> {
  const fromMetadata = p.metadata && typeof p.metadata.category === "string" ? (p.metadata.category as string) : null;
  return (fromMetadata as Exclude<Category, "All">) || "Coding";
}
