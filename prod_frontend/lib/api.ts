export const API_URL = (process.env.NEXT_PUBLIC_KEL_API_URL || "").replace(/\/$/, "");

export type ApiState<T> =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "unconfigured" }
  | { kind: "error"; message: string }
  | { kind: "ok"; data: T };

/** GET a keळ backend `/v1` route. Never fabricates: failures surface as an explicit state. */
export async function apiGet<T>(path: string, signal?: AbortSignal): Promise<ApiState<T>> {
  if (!API_URL) return { kind: "unconfigured" };
  try {
    const res = await fetch(`${API_URL}${path}`, { signal, headers: { Accept: "application/json" } });
    if (!res.ok) return { kind: "error", message: `The backend answered ${res.status}.` };
    return { kind: "ok", data: (await res.json()) as T };
  } catch (e) {
    if ((e as Error).name === "AbortError") return { kind: "idle" };
    return { kind: "error", message: "Could not reach the backend." };
  }
}

/** Loosely read a title-like field from a row whose exact shape we do not assume. */
export function labelOf(row: Record<string, unknown>): string {
  for (const k of ["title", "name", "statement", "goal", "description", "id"]) {
    const v = row[k];
    if (typeof v === "string" && v.trim()) return v.length > 140 ? v.slice(0, 137) + "…" : v;
  }
  return "Untitled";
}
