/**
 * Fast contribution calls (launch: "a signed-in user adds a procedure as
 * fast as possible"). Kept out of client.ts so it composes API_URL +
 * authHeaders without touching the read client. Every call here is
 * authenticated — the backend derives the owner from the bearer token.
 */
import { authHeaders } from "@/lib/auth";
import { API_URL, ApiError } from "@/lib/api/client";

export interface CreatedProcedure {
  procedure_id: string;
  id: string;
  scope: "PRIVATE";
  verification: "candidate";
  owner_id: string;
  embedded?: boolean;
  next: string;
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    method: "POST",
    headers: { ...authHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  if (!res.ok) {
    let detail: string = res.statusText;
    try {
      const j = (await res.json()) as { detail?: unknown };
      if (j?.detail)
        detail =
          typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {
      /* non-JSON body */
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

export interface ProcedureDraft {
  name: string;
  goal: string;
  steps: string[];
  applicability?: string[];
  tools?: string[];
  failure_conditions?: string[];
  domain?: string;
  embed?: boolean;
}

export function createProcedure(draft: ProcedureDraft) {
  return post<CreatedProcedure>("/v1/procedures", draft);
}

export function createProcedureFromText(text: string, opts: { name?: string; domain?: string; embed?: boolean } = {}) {
  return post<CreatedProcedure>("/v1/procedures/from_text", { text, ...opts });
}
