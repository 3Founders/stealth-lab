/**
 * Privacy & Data calls (launch compliance Phase 7 / LC-008). All
 * authenticated; the backend derives the subject from the bearer token.
 */
import { authHeaders } from "@/lib/auth";
import { API_URL, ApiError } from "@/lib/api/client";

async function call<T>(path: string, method: "GET" | "POST"): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    method,
    headers: { ...authHeaders() },
    cache: "no-store",
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = (await res.json()) as { detail?: unknown };
      if (j?.detail) detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {
      /* non-JSON */
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

export interface DeletionPlan {
  subject: string;
  legal_hold: boolean;
  private_procedures_physical_delete: string[];
  private_procedures_tombstone: string[];
  global_objects_preserved: string[];
  publication_records_retained: string[];
  dry_run?: boolean;
}

export const exportMyData = () => call<Record<string, unknown>>("/v1/me/export", "GET");
export const previewMyDeletion = () => call<DeletionPlan>("/v1/me/deletion", "GET");
export const deleteMyData = () =>
  call<Record<string, unknown>>("/v1/me/deletion", "POST");
