import { API_URL, apiDelete, apiGet, apiPost, apiPut, apiUpload, type ApiState } from "@/lib/api";

/**
 * Typed reads for the Problems → Goal → Procedure surface. Every shape here mirrors what
 * the backend actually returns today (see backend/db/35_product_model.sql, 83_goals.sql,
 * app/api/problems.py, app/api/procedures.py) — loosely typed with `Record<string, unknown>`
 * as the base, same convention as lib/api.ts's `labelOf`, because these are raw table rows
 * whose exact column set the backend itself says "may vary." Nothing here is fabricated:
 * a field that isn't returned is simply absent, and callers must treat it as such.
 */

export type Row = Record<string, unknown>;

export interface Problem extends Row {
  id: string;
  title: string;
  description?: string | null;
  objective?: string | null;
  constraints?: unknown[];
  status?: string;
  proposer?: string | null;
  metadata?: Row;
}

export interface Benchmark extends Row {
  id: string;
  problem_id: string;
  name: string;
  description?: string | null;
  version?: number;
  evaluation_protocol?: Row;
  environment_specification?: Row;
  success_criteria?: Row;
  comparison_policy?: Row;
  status?: string;
  frozen_at?: string | null;
}

export interface Solution extends Row {
  id: string;
  problem_id: string;
  solution_type: "procedure" | "task_graph" | "task";
  target_id: string;
  target_table: string;
  status?: string;
  proposer?: string | null;
}

export interface Evaluation extends Row {
  id: string;
  problem_id: string;
  benchmark_id: string;
  solution_id: string;
  procedure_id?: string | null;
  run_count?: number;
  aggregate_result?: "pass" | "fail" | "partial" | "inconclusive" | null;
  status?: string;
  created_at?: string;
  completed_at?: string | null;
}

/**
 * Canonical evidence trust summary (backend: app.services.evidence_trust.summarize).
 * success_count/failure_count are kept for older readers but now mean
 * VERIFIED specifically — verified_success/verified_failure are the same
 * numbers under their honest names; claimed_success/unknown are real,
 * separate buckets, never folded into "success".
 */
export interface EvidenceSummary {
  total?: number;
  outcome_bearing_total?: number;
  unknown?: number;
  claimed_success?: number;
  verified_success?: number;
  verified_failure?: number;
  success_count?: number;
  failure_count?: number;
}

export interface ProcedureDetail extends Row {
  id: string;
  procedure_id?: string;
  version?: number;
  name?: string;
  goal?: string | null;
  display_name?: string | null;
  display_description?: string | null;
  applicability_summary?: string | null;
  failure_modes?: unknown[];
  steps?: unknown[];
  preconditions?: unknown[];
  invariants?: unknown[];
  verification_state?: string;
  staleness?: string | null;
  availability?: string;
  approval_status?: string;
  provenance?: string | null;
  domain?: string | null;
  created_by?: string | null;
  claims?: Row[];
  evidence_summary?: EvidenceSummary;
  executor_kinds?: string[];
  t_created?: string;
}

export interface ProcedureVersionRow extends Row {
  id: string;
  version?: number;
  name?: string;
  t_created?: string;
  created_by?: string | null;
}

export interface EvidenceRow extends Row {
  id: string;
  outcome?: string;
  created_at?: string;
  summary?: string;
  description?: string;
}

const j = (v: unknown) => encodeURIComponent(String(v));

export const getProblems = (limit = 100, signal?: AbortSignal) =>
  apiGet<{ problems: Problem[] }>(`/v1/problems?limit=${limit}`, signal);

export interface ProblemInput {
  title: string;
  description?: string;
  objective?: string;
  constraints?: string[];
}

/** `proposer`/`owner_id` are derived server-side from the caller's verified
 * session (app/api/problems.py) -- never sent from here, there's nothing
 * for this form to spoof. */
export const createProblem = (body: ProblemInput, signal?: AbortSignal) =>
  apiPost<Problem>("/v1/problems", body, signal);

export const getProblem = (id: string, signal?: AbortSignal) => apiGet<Problem>(`/v1/problems/${j(id)}`, signal);

export const getProblemSolutions = (id: string, signal?: AbortSignal) =>
  apiGet<{ solutions: Solution[] }>(`/v1/problems/${j(id)}/solutions`, signal);

export const getProblemBenchmarks = (id: string, signal?: AbortSignal) =>
  apiGet<{ benchmarks: Benchmark[] }>(`/v1/problems/${j(id)}/benchmarks`, signal);

export const getProblemEvaluations = (id: string, signal?: AbortSignal) =>
  apiGet<{ evaluations: Evaluation[] }>(`/v1/problems/${j(id)}/evaluations`, signal);

export const getProcedure = (id: string, signal?: AbortSignal) => apiGet<ProcedureDetail>(`/v1/procedures/${j(id)}`, signal);

export const getProcedureVersions = (id: string, signal?: AbortSignal) =>
  apiGet<ProcedureVersionRow[]>(`/v1/procedures/${j(id)}/versions`, signal);

export const getProcedureEvidence = (id: string, signal?: AbortSignal) =>
  apiGet<EvidenceRow[]>(`/v1/procedures/${j(id)}/evidence`, signal);

/** True once every ApiState in the list has resolved (not idle/loading). */
export function settled(states: ApiState<unknown>[]): boolean {
  return states.every((s) => s.kind !== "idle" && s.kind !== "loading");
}

/**
 * Ranking is now computed by the backend ONLY (GET /v1/economy/goals/{id}/procedures/ranked
 * — app/economy/ranking.py, wrapping the pre-existing Wilson-based capability estimator).
 * The client-side rankScore/verificationBucket heuristic that used to live here is removed —
 * it was a third, less rigorous ranking implementation alongside the backend's real one and
 * the older capability estimator; keeping it would mean the frontend could disagree with the
 * backend about which way is "best". See getRankedProcedures below.
 */
export interface RankedProcedure {
  procedure_row_id: string;
  procedure_id: string;
  version: number;
  display_name?: string | null;
  display_description?: string | null;
  applicability_summary?: string | null;
  created_by?: string | null;
  bucket: "verified" | "candidate" | "needs_evidence" | "verified_failure";
  bucket_label: string;
  rank: number;
  of: number;
  wilson_lower_bound: number;
  wilson_upper_bound: number;
  evidence_count: number;
  success_count: number;
  independent_groups: number;
  cost?: Row;
  context_matched: boolean;
}

export const getRankedProcedures = (goalId: string, contextKey?: string, signal?: AbortSignal) =>
  apiGet<{ goal_id: string; context_key: string | null; note: string; ranked: RankedProcedure[] }>(
    `/v1/economy/goals/${j(goalId)}/procedures/ranked${contextKey ? `?context_key=${encodeURIComponent(contextKey)}` : ""}`,
    signal,
  );

/** Coarse, honest relative time. Never invents a date: no input → no output. */
export function timeAgo(iso?: string | null): string | null {
  if (!iso) return null;
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return null;
  const days = Math.floor((Date.now() - then) / 86_400_000);
  if (days <= 0) return "today";
  if (days === 1) return "1 day ago";
  if (days < 30) return `${days} days ago`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months} month${months > 1 ? "s" : ""} ago`;
  const years = Math.floor(months / 12);
  return `${years} year${years > 1 ? "s" : ""} ago`;
}

/** Flattens a JSONB spec object into short, human-readable "label — value" pairs. Never prints raw JSON. */
export function humanize(obj: Row | undefined | null): [string, string][] {
  if (!obj) return [];
  const out: [string, string][] = [];
  for (const [k, v] of Object.entries(obj)) {
    if (v == null) continue;
    const label = k.replace(/_/g, " ");
    let val: string;
    if (Array.isArray(v)) val = v.map((x) => (typeof x === "object" ? JSON.stringify(x) : String(x))).join(", ");
    else if (typeof v === "object") val = Object.entries(v as Row).map(([kk, vv]) => `${kk}: ${vv}`).join(", ");
    else val = String(v);
    if (val.trim()) out.push([label, val.length > 160 ? val.slice(0, 157) + "…" : val]);
  }
  return out;
}

export interface ProblemStats {
  ways: number;
  verifiedRuns: number;
  lastActivity: string | null;
}

/** One real-data rollup per Problem, built from /solutions and /evaluations. No number here is invented. */
export async function getProblemStats(id: string, signal?: AbortSignal): Promise<ProblemStats | null> {
  const [sol, ev, subs] = await Promise.all([
    getProblemSolutions(id, signal), getProblemEvaluations(id, signal), listProcedureSubmissions(id, signal),
  ]);
  if (sol.kind !== "ok" && ev.kind !== "ok" && subs.kind !== "ok") return null;
  const ways = sol.kind === "ok" ? sol.data.solutions.filter((s) => s.target_table === "procedures").length : 0;
  const evals = ev.kind === "ok" ? ev.data.evaluations : [];
  const verifiedRuns = evals.filter((e) => e.aggregate_result === "pass").reduce((n, e) => n + (e.run_count ?? 0), 0);
  // Recent activity = the most recent of: an evaluation completing, or a
  // procedure/benchmark submission arriving — real timestamps from real
  // rows (§11), not a fabricated popularity score.
  const evalStamps = evals.map((e) => e.completed_at ?? e.created_at).filter(Boolean) as string[];
  const subStamps = subs.kind === "ok" ? (subs.data.submissions.map((s) => s.created_at as string).filter(Boolean)) : [];
  const stamps = [...evalStamps, ...subStamps];
  const lastActivity = stamps.length ? stamps.sort().at(-1)! : null;
  return { ways, verifiedRuns, lastActivity };
}

// ---------------------------------------------------------------------------
// Economy: submissions, ranking already above, contributors, Credits, Standing.
// All authenticated writes go through apiPost (lib/api.ts), which attaches
// the session token (lib/session.ts) and never sends an identity field the
// server would trust — the backend derives the acting identity itself.
// ---------------------------------------------------------------------------

export interface SubmissionResult extends Row {
  id: string;
  status: "candidate" | "needs_review" | "accepted" | "rejected";
  status_reason?: string | null;
  procedure_row_id?: string | null;
  benchmark_id?: string | null;
  duplicate_score?: number | null;
  parent_similarity_score?: number | null;
}

export interface ProcedureSubmissionInput {
  goal_id: string;
  submission_type: "new" | "improvement";
  name: string;
  steps: string[];
  rationale?: string;
  applicability_context?: Row;
  constraints?: string[];
  implementation_requirements?: Row;
  supporting_evidence?: string[];
  parent_procedure_row_id?: string;
  provenance?: string;
  visibility?: "public" | "private";
}

export interface BenchmarkSubmissionInput {
  goal_id: string;
  name: string;
  description?: string;
  success_criteria?: Row;
  invariants?: string[];
  verification_method?: Row;
  provenance?: string;
  visibility?: "public" | "private";
}

export const createProcedureSubmission = (body: ProcedureSubmissionInput, signal?: AbortSignal) =>
  apiPost<SubmissionResult>("/v1/economy/procedure-submissions", body, signal);

export const createBenchmarkSubmission = (body: BenchmarkSubmissionInput, signal?: AbortSignal) =>
  apiPost<SubmissionResult>("/v1/economy/benchmark-submissions", body, signal);

export const listProcedureSubmissions = (goalId: string, signal?: AbortSignal) =>
  apiGet<{ submissions: SubmissionResult[] }>(`/v1/economy/procedure-submissions?goal_id=${j(goalId)}&limit=20`, signal);

export interface GoalContributor {
  contributor_id: string;
  procedures: number;
  improvements: number;
  benchmarks: number;
}

/** Canonical goal-level contributor view — server-computed from accepted
 * submissions, never re-tallied client-side from fetched procedures. */
export const getGoalContributors = (goalId: string, signal?: AbortSignal) =>
  apiGet<{ goal_id: string; note: string; contributors: GoalContributor[] }>(`/v1/economy/goals/${j(goalId)}/contributors`, signal);

export interface StandingResult {
  contributor_id: string;
  standing_score: number;
  accepted_new_procedures: number;
  accepted_improvements: number;
  accepted_benchmarks: number;
  verified_independent_outcomes: number;
  reliable_evidence_count: number;
  method: string;
}

export const getStanding = (contributorId: string, signal?: AbortSignal) =>
  apiGet<StandingResult>(`/v1/economy/contributors/${j(contributorId)}/standing`, signal);

export const getCreditsBalance = (contributorId: string, signal?: AbortSignal) =>
  apiGet<{ contributor_id: string; balance: number }>(`/v1/economy/contributors/${j(contributorId)}/credits`, signal);

export interface CreditEvent extends Row {
  id: string;
  contributor_id: string;
  amount: number;
  reason: "new_procedure" | "improvement" | "verified_reuse" | "clawback" | "admin_adjustment";
  goal_id?: string | null;
  procedure_row_id?: string | null;
  reversal_of_event_id?: string | null;
  created_at: string;
}

export const getCreditsHistory = (contributorId: string, signal?: AbortSignal) =>
  apiGet<{ contributor_id: string; events: CreditEvent[] }>(`/v1/economy/contributors/${j(contributorId)}/credits/history?limit=50`, signal);

// ---------------------------------------------------------------------------
// V1 contributor identity: username, avatar, onboarding, public profile.
// Public keळ identity (username/avatar/tagline) — never authentication.
// See backend/app/api/profile.py and backend/app/api/contributors.py.
// ---------------------------------------------------------------------------

export interface ContributorProfile extends Row {
  user_id: string;
  visibility: "private" | "public";
  disclosed_at: string | null;
  tagline: string | null;
  username: string | null;
  avatar_locator: string | null;
  onboarding_complete: boolean;
  t_created: string;
  t_updated: string;
}

export interface MyProfileResult extends Row {
  profile: ContributorProfile;
  counts: Row;
  display_name: string | null;
  avatar_url: string | null;
  disclosure_required: boolean;
  onboarding_required: boolean;
}

export const getMyProfile = (signal?: AbortSignal) => apiGet<MyProfileResult>("/v1/me/profile", signal);

export interface ProfileUpdateInput {
  visibility?: "private" | "public";
  tagline?: string;
  username?: string;
  onboarding_complete?: boolean;
}

export const updateMyProfile = (body: ProfileUpdateInput, signal?: AbortSignal) =>
  apiPut<MyProfileResult>("/v1/me/profile", body, signal);

export const suggestUsernames = (limit = 1, signal?: AbortSignal) =>
  apiGet<{ suggestions: string[] }>(`/v1/me/profile/username/suggestions?limit=${limit}`, signal);

export const uploadAvatar = (file: File, signal?: AbortSignal) => {
  const form = new FormData();
  form.append("file", file);
  return apiUpload<MyProfileResult>("/v1/me/avatar", form, signal);
};

export const removeAvatar = (signal?: AbortSignal) => apiDelete<MyProfileResult>("/v1/me/avatar", signal);

export interface PublicContributorProfile extends Row {
  username: string;
  display_name: string;
  tagline: string | null;
  profile_since: string;
  counts: Row;
  renamed_to: string | null;
  /** Only ever true when this IS the signed-in caller's own username --
   * the backend never reveals ownership to anyone else. When true, the
   * profile came back even if `visibility` is "private" (see
   * app/api/contributors.py's owner-exception). */
  is_owner: boolean;
  visibility: "private" | "public";
}

export const getPublicProfileByUsername = (username: string, signal?: AbortSignal) =>
  apiGet<PublicContributorProfile>(`/v1/contributors/by-username/${j(username)}`, signal);

/** Always resolves to a real image — the backend serves either the
 * contributor's own picture or a deterministic initials fallback, never a
 * broken link (backend/app/services/avatar.py). */
export const avatarUrlFor = (username: string): string => `${API_URL}/v1/contributors/by-username/${j(username)}/avatar`;

// ---------------------------------------------------------------------------
// Synced local `.stealth` projects — CLIENT-SIDE END-TO-END ENCRYPTED. See
// docs/local_project_sync_security.md and backend/app/api/me.py /
// backend/app/stealth/project_sync.py. A project only appears here once
// explicitly synced via the local bridge + browser encryption flow
// (lib/sync.ts) — this backend never sees plaintext file/activity
// content, ever; everything below is either ciphertext or key-wrapping
// metadata. `project_id` is the STABLE identity persisted in the
// workspace's own `.stealth/meta.json` (survives rename/move).
//
// NAMING: "sync" / "local project sync" / "synced project" / "unsync" —
// never "claim" (that word already means something else in keळ:
// claims.md, verification claims, the claim graph).
// ---------------------------------------------------------------------------

export interface StealthProjectSummary extends Row {
  project_id: string;
  synced_at: string | null;
  bootstrapped_at: string | null;
  revision: number;
}

export const getMyStealthProjects = (signal?: AbortSignal) =>
  apiGet<{ projects: StealthProjectSummary[] }>("/v1/me/stealth-projects", signal);

/** Ciphertext + the key-wrapping metadata needed to decrypt it CLIENT-SIDE
 * (lib/sync-crypto.ts). Never plaintext file/activity fields — the server
 * does not have them. */
export interface StealthProjectDetail extends Row {
  project_id: string;
  synced_at: string | null;
  bootstrapped_at: string | null;
  revision: number;
  wrapped_p_dek: string | null;
  recovery_salt: string | null;
  kdf_params: { m: number; t: number; p: number } | null;
  ciphertext_base64: string | null;
}

export const getMyStealthProject = (projectId: string, signal?: AbortSignal) =>
  apiGet<StealthProjectDetail>(`/v1/me/stealth-projects/${j(projectId)}`, signal);

export const unsyncStealthProject = (projectId: string, signal?: AbortSignal) =>
  apiDelete<{ project_id: string; unsynced: boolean }>(`/v1/me/stealth-projects/${j(projectId)}`, signal);

/** Establishes the sync relationship (if not already synced by this
 * account) and issues a sync device credential — a DIFFERENT, narrowly
 * scoped credential from the ordinary Supabase session, handed to the
 * local bridge afterward so the local MCP process can keep syncing after
 * this tab closes. See docs/local_project_sync_security.md's
 * Implementation Closure §1. */
export const issueSyncDevice = (projectId: string, signal?: AbortSignal) =>
  apiPost<{ token: string; project_id: string; ttl_seconds: number }>(
    "/v1/me/sync-devices", { project_id: projectId }, signal,
  );

/** Decrypted project content shape, client-side only — the server never
 * sees this. Mirrors the plaintext bundle build_bootstrap_snapshot /
 * local_sync_bridge.handle_prepare_payload assembles before encryption. */
export interface DecryptedProjectContent {
  files: Record<string, string>;
  activity: Array<{ timestamp: string | null; file_path: string; summary: string; actor: string }>;
}
