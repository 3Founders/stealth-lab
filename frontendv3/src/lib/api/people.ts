import { request } from "./client";
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

export const getMyProfile = () => request<MyProfileResponse>("/v1/me/profile");

export const setMyProfile = (visibility: ProfileVisibility, tagline?: string | null) =>
  request<{ profile: ContributorProfileRow; counts: ContributionCounts }>(
    "/v1/me/profile",
    "PUT",
    { visibility, tagline: tagline ?? null }
  );

export const searchContributors = (q: string, limit = 20) =>
  request<{ query: string; results: ContributorSearchResult[] }>(
    `/v1/contributors/search?q=${encodeURIComponent(q)}&limit=${limit}`
  );

export const getContributor = (userId: string) =>
  request<PublicProfile>(`/v1/contributors/${encodeURIComponent(userId)}`);

export const getContributorLeaderboard = (
  metric: LeaderboardMetric = "verified_procedures",
  limit = 25
) =>
  request<LeaderboardResponse>(
    `/v1/contributors/leaderboard?metric=${metric}&limit=${limit}`
  );
