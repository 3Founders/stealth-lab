// The published-Ways catalogue: the public list behind the homepage and the task search.
// It reads the hosted MCP server, not the website API, because that is where the Way page's
// public projection lives (`GET /v1/goals/submissions/public`).
import { MCP_ORIGIN } from "./connector";
import type { ApiState } from "./api";

export type PublicWay = {
  id: string;
  goal_name: string;
  description: string;
  mode: string;
  mode_label: string;
  status: "approved" | "ai_approved" | string;
  created_at: string;
  parent_procedure_id: string | null;
  price_paise: number | null;
  price_currency: string;
  step_count: number;
};

/** Published Ways, newest first. `q` searches the goal name and the description. */
export async function listPublicWays(q = "", limit = 12): Promise<ApiState<PublicWay[]>> {
  const params = new URLSearchParams();
  if (q.trim()) params.set("q", q.trim());
  params.set("limit", String(Math.max(1, Math.min(limit, 50))));
  try {
    const res = await fetch(`${MCP_ORIGIN}/v1/goals/submissions/public?${params.toString()}`, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    if (!res.ok) {
      return { kind: "error", message: `The catalogue answered ${res.status}.`, status: res.status };
    }
    const body = await res.json().catch(() => null);
    const rows = Array.isArray(body?.ways) ? body.ways : [];
    return { kind: "ok", data: rows as PublicWay[] };
  } catch {
    return { kind: "error", message: "Could not reach the catalogue." };
  }
}
