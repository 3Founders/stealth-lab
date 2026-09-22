import { getAccessToken } from "@/lib/session";

export const API_URL = (process.env.NEXT_PUBLIC_KEL_API_URL || "").replace(/\/$/, "");

export type ApiState<T> =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "unconfigured" }
  | { kind: "unauthenticated" }
  | { kind: "forbidden"; message: string }
  | { kind: "error"; message: string; status?: number; detail?: unknown }
  | { kind: "ok"; data: T };

async function _errorDetail(res: Response): Promise<unknown> {
  try {
    return await res.json();
  } catch {
    return undefined;
  }
}

/** GET a keळ backend `/v1` route. Never fabricates: failures surface as an explicit state. */
export async function apiGet<T>(path: string, signal?: AbortSignal): Promise<ApiState<T>> {
  if (!API_URL) return { kind: "unconfigured" };
  try {
    const token = await getAccessToken();
    const headers: Record<string, string> = { Accept: "application/json" };
    if (token) headers.Authorization = `Bearer ${token}`;
    const res = await fetch(`${API_URL}${path}`, { signal, headers });
    if (res.status === 401) return { kind: "unauthenticated" };
    if (res.status === 403) return { kind: "forbidden", message: "You don't have access to this." };
    if (!res.ok) return { kind: "error", message: `The backend answered ${res.status}.`, status: res.status, detail: await _errorDetail(res) };
    return { kind: "ok", data: (await res.json()) as T };
  } catch (e) {
    if ((e as Error).name === "AbortError") return { kind: "idle" };
    return { kind: "error", message: "Could not reach the backend." };
  }
}

/**
 * Write a keळ backend `/v1` route as the signed-in session, if any.
 * Requires an access token (see lib/session.ts) — an unauthenticated
 * caller gets `{kind:"unauthenticated"}` WITHOUT the request ever going
 * out, since every write endpoint this hits requires
 * require_authenticated_user server-side anyway; this just avoids a
 * pointless round trip and lets the UI react immediately.
 */
async function apiWrite<T>(
  path: string,
  method: "POST" | "PUT" | "DELETE",
  body: BodyInit | undefined,
  extraHeaders: Record<string, string>,
  signal?: AbortSignal,
): Promise<ApiState<T>> {
  if (!API_URL) return { kind: "unconfigured" };
  const token = await getAccessToken();
  if (!token) return { kind: "unauthenticated" };
  try {
    const res = await fetch(`${API_URL}${path}`, {
      method, signal, body,
      headers: { Accept: "application/json", Authorization: `Bearer ${token}`, ...extraHeaders },
    });
    if (res.status === 401) return { kind: "unauthenticated" };
    if (res.status === 403) return { kind: "forbidden", message: "You don't have access to do this." };
    if (res.status === 429) {
      const detail = await _errorDetail(res);
      return { kind: "error", message: "You're submitting too quickly — please wait and try again.", status: 429, detail };
    }
    if (res.status === 422 || res.status === 409 || res.status === 413) {
      const detail = await _errorDetail(res);
      const message = (detail && typeof detail === "object" && "detail" in detail && typeof (detail as { detail?: unknown }).detail === "string")
        ? (detail as { detail: string }).detail
        : "That couldn't be submitted as written.";
      return { kind: "error", message, status: res.status, detail };
    }
    if (!res.ok) return { kind: "error", message: `The backend answered ${res.status}.`, status: res.status, detail: await _errorDetail(res) };
    if (res.status === 204) return { kind: "ok", data: undefined as T };
    return { kind: "ok", data: (await res.json()) as T };
  } catch (e) {
    if ((e as Error).name === "AbortError") return { kind: "idle" };
    return { kind: "error", message: "Could not reach the backend." };
  }
}

export const apiPost = <T>(path: string, body: unknown, signal?: AbortSignal) =>
  apiWrite<T>(path, "POST", JSON.stringify(body), { "Content-Type": "application/json" }, signal);

export const apiPut = <T>(path: string, body: unknown, signal?: AbortSignal) =>
  apiWrite<T>(path, "PUT", JSON.stringify(body), { "Content-Type": "application/json" }, signal);

export const apiDelete = <T>(path: string, signal?: AbortSignal) =>
  apiWrite<T>(path, "DELETE", undefined, {}, signal);

/** multipart/form-data upload (e.g. an avatar image) — no Content-Type
 * header set explicitly, so the browser attaches its own multipart
 * boundary. */
export const apiUpload = <T>(path: string, form: FormData, signal?: AbortSignal) =>
  apiWrite<T>(path, "POST", form, {}, signal);

/** Loosely read a title-like field from a row whose exact shape we do not assume. */
export function labelOf(row: Record<string, unknown>): string {
  for (const k of ["title", "name", "statement", "goal", "description", "id"]) {
    const v = row[k];
    if (typeof v === "string" && v.trim()) return v.length > 140 ? v.slice(0, 137) + "…" : v;
  }
  return "Untitled";
}
