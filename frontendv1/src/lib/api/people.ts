/**
 * People layer — opt-in public contributor profiles, people search, and the
 * contributor leaderboard (backend: /v1/contributors/* and /v1/me/profile).
 *
 * Kept in its own file (not client.ts) so it doesn't collide with the
 * retrieval-representation work on the shared typed client. Reuses
 * API_URL / ApiError from client.ts and authHeaders from auth.
 */
import { authHeaders } from "@/lib/auth";
import { API_URL, ApiError } from "@/lib/api/client";

async function call<T>(
  path: string,
  method: "GET" | "PUT" = "GET",
  body?: unknown
): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    method,
    headers: {
      ...authHeaders(),
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
    cache: "no-store",
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = (await res.json()) as { detail?: unknown };
      if (j?.detail)
        detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {
      /* non-JSON */
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

// --- shapes ----------------------------------------------------------------

export interface ContributionCounts {
  procedures_authored: number;
  verified_procedures: number;
  claims_authored: number;
  commons_publications: number;
}

export type ProfileVisibility = "private" | "public";

export interface ContributorProfileRow {
  user_id: string;
  visibility: ProfileVisibility;
  disclosed_at: string | null;
  tagline: string | null;
  t_created: string;
  t_updated: string;
}

export interface MyProfileResponse {
  profile: ContributorProfileRow | null;
  counts: ContributionCounts;
  display_name: string | null;
  disclosure_required: boolean;
}

export interface PublicProfile {
  user_id: string;
  display_name: string;
  tagline: string | null;
  profile_since: string | null;
  counts: ContributionCounts;
}

export interface ContributorSearchResult {
  user_id: string;
  display_name: string;
  tagline: string | null;
}

export const LEADERBOARD_METRICS = [
  "verified_procedures",
  "procedures_authored",
  "claims_authored",
  "commons_publications",
] as const;
export type LeaderboardMetric = (typeof LEADERBOARD_METRICS)[number];

export const METRIC_LABEL: Record<LeaderboardMetric, string> = {
  verified_procedures: "Verified procedures",
  procedures_authored: "Procedures authored",
  claims_authored: "Claims authored",
  commons_publications: "Commons publications",
};

export interface LeaderboardEntry {
  user_id: string;
  display_name: string;
  tagline: string | null;
  counts: ContributionCounts;
  value: number;
}

export interface LeaderboardResponse {
  metric: LeaderboardMetric;
  entries: LeaderboardEntry[];
}

// --- calls ---------------------------------------------------------------

export const getMyProfile = () => call<MyProfileResponse>("/v1/me/profile");

export const setMyProfile = (visibility: ProfileVisibility, tagline?: string | null) =>
  call<{ profile: ContributorProfileRow; counts: ContributionCounts }>(
    "/v1/me/profile",
    "PUT",
    { visibility, tagline: tagline ?? null }
  );

export const searchContributors = (q: string, limit = 20) =>
  call<{ query: string; results: ContributorSearchResult[] }>(
    `/v1/contributors/search?q=${encodeURIComponent(q)}&limit=${limit}`
  );

export const getContributor = (userId: string) =>
  call<PublicProfile>(`/v1/contributors/${encodeURIComponent(userId)}`);

export const getContributorLeaderboard = (
  metric: LeaderboardMetric = "verified_procedures",
  limit = 25
) =>
  call<LeaderboardResponse>(
    `/v1/contributors/leaderboard?metric=${metric}&limit=${limit}`
  );
