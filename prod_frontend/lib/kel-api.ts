import { API_URL, apiDelete, apiGet, apiPost, apiPut, apiUpload, type ApiState } from "@/lib/api";

/**
 * Typed reads for the Goal → Procedure surface. Every shape here mirrors what
 * the backend actually returns today (see backend/db/35_product_model.sql, 83_goals.sql,
 * app/api/goals.py, app/api/procedures.py) — loosely typed with `Record<string, unknown>`
 * as the base, same convention as lib/api.ts's `labelOf`, because these are raw table rows
 * whose exact column set the backend itself says "may vary." Nothing here is fabricated:
 * a field that isn't returned is simply absent, and callers must treat it as such.
 */

export type Row = Record<string, unknown>;

export type GoalResolution = "all" | "resolved" | "unresolved";

export interface GoalRankingSignal {
  label: string;
  value: number | null;
  available: boolean;
}

export interface GoalRanking {
  score: number | null;
  state: string;
  signals: Record<string, GoalRankingSignal>;
  explanation: string;
}

export interface GoalHierarchyNeighbor {
  id: string;
  canonical_name: string;
  description?: string | null;
  status?: string;
  resolved_at?: string | null;
}

export interface GoalCoverage {
  total_count: number;
  resolved_count: number;
  ratio: number;
}

export interface Goal extends Row {
  id: string;
  canonical_name: string;
  description?: string | null;
  rationale?: string | null;
  objective?: string | null;
  expected_outcome?: Row | null;
  verification_requirement?: Row | null;
  constraints?: unknown[];
  status?: string;
  resolved_at?: string | null;
  ranking?: GoalRanking | null;
  // Derived server-side from accepted SPECIALIZES edges. `null` (with
  // hierarchy_available=false) means "could not be derived right now", never
  // "a root with no relations".
  hierarchy_available?: boolean;
  abstraction_level?: number | null;
  specializes?: GoalHierarchyNeighbor[] | null;
  abstracts?: GoalHierarchyNeighbor[] | null;
  benchmarks?: Benchmark[];
  coverage?: GoalCoverage | null;
  // false when a direct parent/child exists but could not be shown right now
  // (its shard was unreachable or its record is being refreshed): the lists
  // below are then not the whole neighbourhood.
  hierarchy_complete?: boolean;
  // Community demand (open Credit commitments). A signal, never truth.
  demand?: GoalDemand | null;
  created_by?: string | null;
  proposer?: string | null;
  scope_type?: string | null;
  scope_entity_id?: string | null;
  visibility?: string | null;
  owner_id?: string | null;
  metadata?: Row;
  t_created?: string | null;
  // Only on the `view=roots` browse listing.
  browse_kind?: "root" | "standalone";
  specific_count?: number;
  specifics?: GoalSpecificPreview[];
}

export interface GoalDemandTotals {
  committed_credits: number;
  supporters: number;
  /** sum over supporters of sqrt(their Credits): one large balance cannot dominate */
  demand_score: number;
}

export interface GoalDemand {
  /** open commitments on this Goal itself */
  direct: GoalDemandTotals;
  /** on this Goal or any accepted more-specific Goal you can see, each counted once */
  aggregated: GoalDemandTotals;
}

export interface GoalCommitment {
  id: string;
  credits: number;
  created_at: string;
  /** null while open; "bounty_payout" once paid to the solver; "goal_commitment_release" when returned */
  settlement: "bounty_payout" | "goal_commitment_release" | null;
  settled_at?: string | null;
}

export interface GoalCommitmentsResult extends GoalDemand {
  goal_id: string;
  resolved: boolean;
  mine: GoalCommitment[];
}

export interface GoalSpecificPreview {
  id: string;
  canonical_name: string;
  status?: string;
  resolved_at?: string | null;
}

export interface GoalPage {
  goals: Goal[];
  has_more: boolean;
}

export type GoalView = "all" | "roots";

export interface GoalListOptions {
  limit?: number;
  offset?: number;
  resolved?: GoalResolution;
  status?: string;
  view?: GoalView;
}

export type GoalResolutionFilter = GoalResolution;
export type GoalListParams = GoalListOptions;

export interface Benchmark extends Row {
  id: string;
  goal_id: string;
  name: string;
  description?: string | null;
  version?: number;
  evaluation_protocol?: Row;
  environment_specification?: Row;
  success_criteria?: Row;
  comparison_policy?: Row;
  status?: string;
  frozen_at?: string | null;
  created_by?: string | null;
}

export interface Solution extends Row {
  id: string;
  goal_id: string;
  solution_type: "procedure" | "task_graph" | "task";
  target_id: string;
  target_table: string;
  version?: number;
  status?: string;
  proposer?: string | null;
}

export interface Evaluation extends Row {
  id: string;
  goal_id: string;
  benchmark_id: string;
  solution_id: string;
  procedure_id?: string | null;
  procedure_version?: number | null;
  run_count?: number;
  aggregate_result?: "pass" | "fail" | "partial" | "inconclusive" | null;
  status?: string;
  metrics?: Row | null;
  verification_summary?: Row | null;
  created_at?: string;
  completed_at?: string | null;
}

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
  rationale?: string | null;
  applicability_summary?: string | null;
  expected_outcome?: Row | null;
  expected_effects?: unknown[];
  postconditions?: unknown[];
  failure_conditions?: unknown[];
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
  evidence_type?: string | null;
  target_type?: string | null;
  target_id?: string | null;
  target_version?: number | null;
  direction?: string | null;
  outcome_status?: string | null;
  success_criteria?: Row | null;
  failure_class?: string | null;
  independence_group?: string | null;
  context_key?: string | null;
  created_by?: string | null;
  source_id?: string | null;
  content_ref?: string | null;
  extractor_version?: string | null;
  t_valid?: string | null;
  t_created?: string | null;
  t_invalid?: string | null;
}

const j = (v: unknown) => encodeURIComponent(String(v));

function goalPage(data: GoalPage | Goal[]): GoalPage {
  if (Array.isArray(data)) return { goals: data, has_more: false };
  return { goals: data.goals ?? [], has_more: Boolean(data.has_more) };
}

function goalQuery(options: GoalListOptions): string {
  const params = new URLSearchParams();
  params.set("limit", String(Math.max(1, Math.min(options.limit ?? 50, 200))));
  params.set("offset", String(Math.max(0, options.offset ?? 0)));
  params.set("resolved", options.resolved ?? "all");
  if (options.status) params.set("status", options.status);
  if (options.view === "roots") params.set("view", "roots");
  return params.toString();
}

function normalizeGoalArguments(
  limitOrOptions: number | GoalListOptions,
  signalOrOffset?: AbortSignal | number,
  resolution?: GoalResolution | AbortSignal,
  maybeSignal?: AbortSignal,
): { options: GoalListOptions; signal?: AbortSignal } {
  const filter = typeof resolution === "string" ? resolution : undefined;
  const signalFromResolution = typeof resolution === "object" ? resolution : undefined;
  if (typeof limitOrOptions === "object") {
    return { options: limitOrOptions, signal: signalOrOffset as AbortSignal | undefined };
  }
  if (typeof signalOrOffset === "number") {
    return {
      options: { limit: limitOrOptions, offset: signalOrOffset, resolved: filter ?? "all" },
      signal: signalFromResolution ?? maybeSignal,
    };
  }
  return {
    options: { limit: limitOrOptions, resolved: filter ?? "all" },
    signal: signalFromResolution ?? (signalOrOffset as AbortSignal | undefined),
  };
}

export function getGoals(
  limitOrOptions: number | GoalListOptions = 50,
  signalOrOffset?: AbortSignal | number,
  resolution?: GoalResolution | AbortSignal,
  signal?: AbortSignal,
) {
  const normalized = normalizeGoalArguments(limitOrOptions, signalOrOffset, resolution, signal);
  return apiGet<GoalPage | Goal[]>(`/v1/goals?${goalQuery(normalized.options)}`, normalized.signal)
    .then((result) => result.kind === "ok" ? { ...result, data: goalPage(result.data) } : result);
}

export function findGoals(
  query: string,
  limitOrOptions: number | GoalListOptions = 10,
  signalOrOffset?: AbortSignal | number,
  resolution?: GoalResolution | AbortSignal,
  signal?: AbortSignal,
) {
  const normalized = normalizeGoalArguments(limitOrOptions, signalOrOffset, resolution, signal);
  const list = goalQuery({ ...normalized.options, limit: Math.min(normalized.options.limit ?? 10, 50) });
  return apiGet<GoalPage | Goal[]>(`/v1/goals/find?q=${j(query)}&${list}`, normalized.signal)
    .then((result) => result.kind === "ok" ? { ...result, data: goalPage(result.data) } : result);
}

export interface GoalInput {
  canonical_name: string;
  description?: string;
  rationale: string;
  objective?: string;
  expected_outcome: Row;
  constraints?: string[];
  scope_type?: string;
  scope_entity_id?: string;
  allow_create_anyway?: boolean;
  use_embeddings?: boolean;
}

/** POST /v1/goals is a two-step "check near matches first" flow, not a
 * one-shot create: it can come back saying the goal already exists
 * (`matched`), that there are close candidates to review (`near_matches`,
 * re-submit with `allow_create_anyway: true` to force creation), or that a
 * brand new goal was made (`created`). */
export interface CreateGoalResult extends Row {
  outcome: "near_matches" | "matched" | "created";
  goal?: Goal;
  candidates?: Goal[];
}

const identitylessGoalFields = new Set([
  "provenance", "visibility", "created_by", "owner_id", "proposer", "submitted_by",
]);

export const createGoal = (body: GoalInput, signal?: AbortSignal) => {
  const payload = { ...body } as Record<string, unknown>;
  for (const field of identitylessGoalFields) delete payload[field];
  return apiPost<CreateGoalResult>("/v1/goals", payload, signal);
};

export const getGoal = (id: string, signal?: AbortSignal) => apiGet<Goal>(`/v1/goals/${j(id)}`, signal);

export const getGoalSolutions = (id: string, signal?: AbortSignal) =>
  apiGet<{ solutions: Solution[] }>(`/v1/goals/${j(id)}/solutions`, signal);

export const getGoalBenchmarks = (id: string, signal?: AbortSignal) =>
  apiGet<{ benchmarks: Benchmark[] }>(`/v1/goals/${j(id)}/benchmarks`, signal);

export const getGoalEvaluations = (id: string, signal?: AbortSignal) =>
  apiGet<{ evaluations: Evaluation[] }>(`/v1/goals/${j(id)}/evaluations`, signal);

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
  name?: string | null;
  goal?: string | null;
  applicability_summary?: string | null;
  created_by?: string | null;
  bucket: "verified" | "candidate" | "needs_evidence" | "verified_failure";
  bucket_label: string;
  rank: number;
  of: number;
  lane?: string;
  ranking_lane?: string;
  cold_start_lane?: string;
  score?: number | null;
  quality_score?: number | null;
  reliability_score?: number | null;
  credible_lower_bound?: number | null;
  credible_upper_bound?: number | null;
  bayesian_lower_bound?: number | null;
  bayesian_upper_bound?: number | null;
  credible_lower_95?: number | null;
  posterior_lower_credible_bound?: number | null;
  posterior_lower_bound?: number | null;
  wilson_lower_bound: number;
  wilson_upper_bound: number;
  evidence_count: number;
  success_count: number;
  failure_count?: number;
  attempts?: number;
  independent_groups: number;
  independent_evidence?: number;
  contexts?: number;
  raw?: Row;
  signals?: Row;
  explanation?: string | null;
  cost?: Row;
  context_matched: boolean;
}

export function rankedProcedureRowId(
  ranked: readonly Pick<RankedProcedure, "procedure_id" | "procedure_row_id">[],
  stableProcedureId: string | null | undefined,
): string | null {
  if (!stableProcedureId) return null;
  return ranked.find((item) => item.procedure_id === stableProcedureId)?.procedure_row_id ?? null;
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

export interface GoalStats {
  ways: number;
  verifiedRuns: number;
  lastActivity: string | null;
}

/** One real-data rollup per Goal, built from /solutions and /evaluations. No number here is invented. */
export async function getGoalStats(id: string, signal?: AbortSignal): Promise<GoalStats | null> {
  const [sol, ev, subs] = await Promise.all([
    getGoalSolutions(id, signal), getGoalEvaluations(id, signal), listProcedureSubmissions(id, undefined, signal),
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
  created_by?: string | null;
  submitted_by?: string | null;
  duplicate_score?: number | null;
  parent_similarity_score?: number | null;
}

export interface ProcedureSubmissionInput {
  goal_id: string;
  submission_type: "new" | "improvement";
  name: string;
  steps: string[];
  rationale: string;
  preconditions: Row[];
  expected_outcome: Row;
  expected_effects?: unknown[];
  postconditions?: unknown[];
  failure_conditions?: unknown[];
  existing_evidence?: unknown[];
  previous_executions?: unknown[];
  known_failure_modes?: unknown[];
  applicability_context?: Row;
  constraints?: string[];
  implementation_requirements?: Row;
  supporting_evidence?: string[];
  parent_procedure_row_id?: string;
}

export interface BenchmarkSubmissionInput {
  goal_id: string;
  name: string;
  description: string;
  success_criteria: Row;
  failure_criteria: string[];
  scope_conditions: string[];
  invariants?: string[];
  verification_method?: Row;
  environment_specification?: Row;
  comparison_policy?: Row;
}

const clientOwnedFields = new Set([
  "provenance", "visibility", "scope_type", "scope_entity_id", "created_by", "owner_id", "proposer", "submitted_by",
]);

function withoutClientOwnedFields<T extends object>(body: T): T {
  const payload = { ...body } as Record<string, unknown>;
  for (const field of clientOwnedFields) delete payload[field];
  return payload as T;
}

export const createProcedureSubmission = (body: ProcedureSubmissionInput, signal?: AbortSignal) =>
  apiPost<SubmissionResult>("/v1/economy/procedure-submissions", withoutClientOwnedFields(body), signal);

export const createBenchmarkSubmission = (body: BenchmarkSubmissionInput, signal?: AbortSignal) =>
  apiPost<SubmissionResult>("/v1/economy/benchmark-submissions", withoutClientOwnedFields(body), signal);

export const listProcedureSubmissions = (goalId: string, status?: string, signal?: AbortSignal) =>
  apiGet<{ submissions: SubmissionResult[] }>(
    `/v1/economy/procedure-submissions?goal_id=${j(goalId)}&limit=20${status ? `&status=${j(status)}` : ""}`, signal,
  );

export const listBenchmarkSubmissions = (goalId: string, status?: string, signal?: AbortSignal) =>
  apiGet<{ submissions: SubmissionResult[] }>(
    `/v1/economy/benchmark-submissions?goal_id=${j(goalId)}&limit=20${status ? `&status=${j(status)}` : ""}`, signal,
  );

/** Reviewer-only (KNOWLEDGE_PUBLISH) -- `require_scopes` re-checks this
 * server-side regardless of what the caller's own /v1/me/profile said, so
 * a stale/forged `is_reviewer` flag client-side can never actually accept
 * or reject anything. */
export const reviewProcedureSubmission = (submissionId: string, decision: "accepted" | "rejected", note?: string, signal?: AbortSignal) =>
  apiPost<SubmissionResult>(`/v1/economy/procedure-submissions/${j(submissionId)}/review`, { decision, note }, signal);

export const reviewBenchmarkSubmission = (submissionId: string, decision: "accepted" | "rejected", note?: string, signal?: AbortSignal) =>
  apiPost<SubmissionResult>(`/v1/economy/benchmark-submissions/${j(submissionId)}/review`, { decision, note }, signal);

// ---- Credit commitments (escrow bounty) ----------------------------------
export const getGoalCommitments = (goalId: string, signal?: AbortSignal) =>
  apiGet<GoalCommitmentsResult>(`/v1/economy/goals/${j(goalId)}/commitments`, signal);

/** Lock Credits on a Goal. Paid to whoever's Procedure resolves it; withdraw any
 * time before that. `idempotencyKey` makes a retried click the same commitment. */
export const commitToGoal = (goalId: string, credits: number, idempotencyKey: string, signal?: AbortSignal) =>
  apiPost<{ id: string; goal_id: string; credits: number; created: boolean }>(
    `/v1/economy/goals/${j(goalId)}/commitments`, { credits, idempotency_key: idempotencyKey }, signal);

export const withdrawCommitment = (goalId: string, commitmentId: string, signal?: AbortSignal) =>
  apiDelete<{ commitment_id: string; released: number }>(
    `/v1/economy/goals/${j(goalId)}/commitments/${j(commitmentId)}`, signal);

// ---- Goal hierarchy review queue (reviewers: knowledge:publish) -----------
export interface ProposedRelation {
  specific_goal: { id: string; canonical_name: string; description?: string | null };
  abstract_goal: { id: string; canonical_name: string; description?: string | null };
  relation: "SPECIALIZES";
  confidence: number | null;
  provenance: string | null;
  judge: Record<string, string>;
  proposed_at: string;
}

export interface HierarchyReviewItem {
  id: string;
  goal_id: string;
  reason: "orphan" | "uncertain";
  status: "open" | "resolved" | "dismissed";
  canonical_name: string;
  short_description?: string | null;
  created_at: string;
}

export const listProposedRelations = (offset = 0, signal?: AbortSignal) =>
  apiGet<{ items: ProposedRelation[]; has_more: boolean }>(`/v1/goal-review/relations?limit=50&offset=${offset}`, signal);

export const decideProposedRelation = (
  specificGoalId: string, abstractGoalId: string, decision: "accept" | "reject", reason: string, signal?: AbortSignal,
) => apiPost<{ relation: Row }>("/v1/goal-review/relations/decide",
  { specific_goal_id: specificGoalId, abstract_goal_id: abstractGoalId, decision, reason }, signal);

export const listHierarchyReviewItems = (signal?: AbortSignal) =>
  apiGet<{ items: HierarchyReviewItem[]; has_more: boolean }>("/v1/goal-review/items?status=open&limit=50", signal);

export const closeHierarchyReviewItem = (itemId: string, status: "resolved" | "dismissed", signal?: AbortSignal) =>
  apiPost<{ id: string; status: string }>(`/v1/goal-review/items/${j(itemId)}/close`, { status }, signal);

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
  /** Display convenience only -- every route that actually accepts/rejects
   * a submission re-checks KNOWLEDGE_PUBLISH itself server-side. */
  is_reviewer: boolean;
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
