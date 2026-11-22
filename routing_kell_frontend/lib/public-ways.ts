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
  const page = await listPublicWaysPage(q, limit, 0);
  if (page.kind !== "ok") return page;
  return { kind: "ok", data: page.data.ways };
}

export type PublicWayPage = {
  ways: PublicWay[];
  /** True when another page exists; the server answers this without a second count query. */
  hasMore: boolean;
  nextOffset: number;
};

/**
 * One page of the catalogue. `offset` is the number of Ways already shown; the server returns
 * `has_more` and the next offset, so a scroll can stop cleanly instead of asking for an empty page.
 */
export async function listPublicWaysPage(q = "", limit = 12, offset = 0): Promise<ApiState<PublicWayPage>> {
  const params = new URLSearchParams();
  if (q.trim()) params.set("q", q.trim());
  params.set("limit", String(Math.max(1, Math.min(limit, 50))));
  params.set("offset", String(Math.max(0, offset)));
  try {
    const res = await fetch(`${MCP_ORIGIN}/v1/goals/submissions/public?${params.toString()}`, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    if (!res.ok) {
      return { kind: "error", message: `The catalogue answered ${res.status}.`, status: res.status };
    }
    const body = await res.json().catch(() => null);
    const ways = Array.isArray(body?.ways) ? (body.ways as PublicWay[]) : [];
    return {
      kind: "ok",
      data: {
        ways,
        hasMore: body?.has_more === true,
        nextOffset: typeof body?.next_offset === "number" ? body.next_offset : offset + ways.length,
      },
    };
  } catch {
    return { kind: "error", message: "Could not reach the catalogue." };
  }
}
