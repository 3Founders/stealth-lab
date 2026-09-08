/**
 * Typed backend contract — derived from .scratch/frontend_backend_contract.md
 * (source of truth: backend/app/api/* and backend/app/services/*).
 * Do not invent fields the backend does not return.
 */

export type ScopeType = string;
export type EpistemicStatus =
  | "verified"
  | "experimental"
  | "community_reported"
  | "disputed"
  | "outdated"
  | string;

// ---------- Search ----------

/** Shared evidence rollup surfaced on search cards and detail pages. */
export interface EvidenceSummary {
  successes: number;
  attempts: number;
  distinct_contexts: number;
}

/**
 * Meaningful match strength, backed by the measured relevance gate.
 * `null` when the gate cutoff has not been measured yet (fail-open) or
 * the hit was capability-ranked with no vector to score.
 */
export type RelevanceLabel = "strong" | "relevant" | null;

export interface ProcedureSearchHit {
  id: string;
  procedure_id: string;
  /** Machine identity / lookup handle. Not a UI title. */
  name: string;
  goal: string | null;
  /** Human-facing title + capability sentence (plan Part 9/10). */
  display_name: string;
  display_description: string;
  applicability_summary: string | null;
  relevance_label: RelevanceLabel;
  relevance_reason: string | null;
  evidence_summary: EvidenceSummary | null;
  failure_modes: string[];
  provenance: string | null;
  scope: Record<string, unknown>;
  verification_state: string;
  staleness: string | null;
  availability: string | null;
  approval_status: string | null;
  scope_type: ScopeType | null;
  scope_entity_id: string | null;
  /** Debug/advanced only — never the user-facing meaning of relevance. */
  similarity_score: number | null;
  version: number | string | null;
}

export interface TaskSearchHit {
  id: string;
  name: string;
  description: string | null;
  scope_type: ScopeType | null;
  scope_entity_id: string | null;
  score: number | null;
  matched_by: string | null;
}

export interface ClaimSearchHit {
  id: string;
  name: string;
  subject: string | null;
  predicate: string | null;
  object: string | null;
  truth_state: string | null;
  scope_type: ScopeType | null;
  scope_entity_id: string | null;
  score: number | null;
  matched_by: string | null;
}

export interface SearchResponse {
  query: string;
  object_types: ("procedure" | "task" | "claim")[];
  results: {
    procedure: ProcedureSearchHit[];
    task: TaskSearchHit[];
    claim: ClaimSearchHit[];
  };
  counts: { procedure: number; task: number; claim: number };
}

// ---------- Solutions ----------

export interface Capability {
  p_estimate: number | null;
  p_lower: number | null;
  p_upper: number | null;
  evidence_count: number | null;
  success_count: number | null;
  independent_groups: number | null;
  band: string | null;
  routing: unknown | null;
  level_gated: null;
  provisional?: boolean;
}

export interface Provenance {
  [key: string]: unknown;
}

export interface ClaimSummary {
  [key: string]: unknown;
  id?: string;
  name?: string;
}

export interface SolutionSearchHit {
  type: "procedure" | "task";
  id: string;
  /** Human-facing title (display_name for a procedure). Lead with this. */
  title: string;
  /** Machine slug — secondary technical label, procedures only. */
  name?: string | null;
  /** Human-facing capability sentence (display_description for a procedure). */
  goal: string | null;
  applicability_summary?: string | null;
  relevance_label?: RelevanceLabel;
  relevance_reason?: string | null;
  evidence_summary?: EvidenceSummary | null;
  failure_modes?: string[];
  applicable: boolean | null;
  verification: { verification_state?: string | null } | null;
  capability: Capability | null;
  provenance: Provenance | string | null;
  claims: ClaimSummary[];
  scope?: Record<string, unknown>;
  scope_type: ScopeType | null;
  scope_entity_id: string | null;
  version: number | string | null;
  /** Debug/advanced only — not the user-facing meaning of relevance. */
  native_score: number | null;
  native_rank: number | null;
  matched_by?: string | null;
}

export interface SolutionSearchResponse {
  query: string;
  results: SolutionSearchHit[];
  counts: { procedure: number; task: number; blended: number };
  interleave_strategy: string;
  note: string;
  reason?: string;
}

export interface SolutionKindImplementation {
  kind: string;
  supported: boolean;
  strategy: string | null;
  reason: string | null;
}

export interface SolutionDetail {
  procedure_row_id: string;
  procedure_id: string;
  version: number | string | null;
  /** Machine slug. Secondary label only. */
  name: string;
  goal: string | null;
  /** Human-facing (plan Part 15). */
  display_name: string;
  display_description: string;
  applicability_summary: string | null;
  failure_modes: string[];
  implementation_id: string | null;
  implementations: Record<string, SolutionKindImplementation>;
  runtime_execution: unknown | null;
  verification_state: string;
  staleness: string | null;
  availability: string | null;
  approval_status: string | null;
  provenance: Provenance | null;
  created_by: string | null;
  owner_id: string | null;
  visibility: string | null;
  scope_type: ScopeType | null;
  scope_entity_id: string | null;
  claims: ClaimSummary[];
  capability: Capability | null;
  cost: Record<string, unknown> | null;
  evidence_count: number | null;
}

// ---------- Procedures ----------

export interface ProcedureEvidenceSummary {
  total?: number | null;
  success_count?: number | null;
  failure_count?: number | null;
  p_estimate?: number | null;
  p_lower?: number | null;
  p_upper?: number | null;
  evidence_count?: number | null;
  independent_groups?: number | null;
  band?: string | null;
}

export interface ProcedureDetail {
  id: string;
  procedure_id: string;
  version: number | string | null;
  family_id: string | null;
  /** Machine slug. Shown as a small secondary label, never the title. */
  name: string;
  goal: string | null;
  /** Human-facing (plan Part 14). */
  display_name: string;
  display_description: string;
  applicability_summary: string | null;
  failure_modes: string[];
  steps: { [key: string]: unknown }[];
  preconditions: unknown[];
  invariants: unknown[];
  verification_state: string;
  staleness: string | null;
  availability: string | null;
  approval_status: string | null;
  scope_type: ScopeType | null;
  scope_entity_id: string | null;
  provenance: Provenance | null;
  domain: string | null;
  created_by: string | null;
  owner_id: string | null;
  visibility: string | null;
  t_valid: string | null;
  t_invalid: string | null;
  t_created: string | null;
  claims: ClaimSummary[];
  evidence_summary: ProcedureEvidenceSummary | null;
}
// ---------- Tasks ----------

export interface TaskDependentProcedure {
  id: string;
  procedure_id: string;
  version: number | string | null;
  name: string;
  goal: string | null;
  verification_state: string;
  staleness: string | null;
  availability: string | null;
  approval_status: string | null;
  t_created: string | null;
}

export interface TaskCapabilityStatistic {
  procedure_row_id: string;
  procedure_id: string;
  version: number | string | null;
  p_estimate: number | null;
  p_lower: number | null;
  p_upper: number | null;
  evidence_count: number | null;
  success_count: number | null;
  independent_groups: number | null;
  environments_held: unknown | null;
  level: unknown | null;
  level_label: string | null;
  routing: unknown | null;
}

export interface TaskDetail {
  id: string;
  name: string;
  description: string | null;
  io_schema: unknown | null;
  skill_ref: string | null;
  success_criteria: unknown | null;
  cost_estimate: unknown | null;
  latency_estimate_ms: number | null;
  pert_optimistic_ms: number | null;
  pert_likely_ms: number | null;
  pert_pessimistic_ms: number | null;
  provenance: Provenance | null;
  scope_type: ScopeType | null;
  scope_entity_id: string | null;
  t_created: string | null;
  dependent_procedures: TaskDependentProcedure[];
  capability_statistics: TaskCapabilityStatistic[];
  known_failure_modes: string[];
}

// ---------- Claims ----------

export interface ClaimDetail {
  id: string;
  node_type: string | null;
  name: string;
  properties: Record<string, unknown>;
  t_valid: string | null;
  t_invalid: string | null;
  created_by: string | null;
  scope_type: string | null;
  scope_entity_id: string | null;
}

export interface Evidence {
  id: string;
  evidence_type: string | null;
  target_type: string | null;
  target_id: string | null;
  outcome_status: string | null;
  direction: string | null;
  strength_score: number | null;
  failure_class: string | null;
  created_by: string | null;
  t_valid: string | null;
}

export interface ClaimDependent {
  id: string;
  name: string;
}

// ---------- Implementations ----------

export type ImplementationStatus =
  | "candidate"
  | "active"
  | "deprecated"
  | "disabled"
  | "quarantined";

export interface Implementation {
  id: string;
  name: string | null;
  kind: string | null;
  provider: string | null;
  description: string | null;
  version: string | null;
  locator: string | null;
  invocation: unknown | null;
  requirements: unknown | null;
  auth_requirements: unknown | null;
  source_ref: string | null;
  author: string | null;
  license: string | null;
  derived_from: string | null;
  status: ImplementationStatus | null;
  verification_status: string | null;
  visibility: string | null;
  created_by: string | null;
  t_created: string | null;
  [key: string]: unknown;
}

// ---------- Repositories / Projects ----------

export interface RepositoryClaim {
  id: string;
  name: string;
  statement: string | null;
  subject: string | null;
  predicate: string | null;
  object: string | null;
  truth_state: string | null;
  confidence: string | number | null;
  lifecycle_state: string | null;
  t_valid: string | null;
}

export interface RepositoryProcedure {
  id: string;
  procedure_id: string;
  name: string;
  goal: string | null;
  version: number | string | null;
  verification_state: string;
  staleness: string | null;
  availability: string | null;
  approval_status: string | null;
}

export interface KnowledgeConfidenceSummary {
  total_claims: number | null;
  claims_by_lifecycle_state: Record<string, number> | null;
  total_procedures: number | null;
  procedures_by_verification_state: Record<string, number> | null;
}

export interface RepositoryKnowledgeResponse {
  repository_id: string;
  claims: RepositoryClaim[];
  relevant_procedures: RepositoryProcedure[];
  conflicts: RepositoryClaim[];
  confidence_summary: KnowledgeConfidenceSummary | null;
}

export interface ProjectKnowledgeResponse {
  project_id: string;
  claims: RepositoryClaim[];
  relevant_procedures: RepositoryProcedure[];
  conflicts: RepositoryClaim[];
  confidence_summary: KnowledgeConfidenceSummary | null;
}

// ---------- Me ----------

export interface MeExecution {
  id: string;
  execution_plan_id: string | null;
  task_graph_id: string | null;
  procedure_id: string | null;
  procedure_version: number | string | null;
  outcome: string | null;
  started_at: string | null;
  ended_at: string | null;
  trace_id: string | null;
}

export interface MeResponse {
  actor: string;
  submitted_procedures: (RepositoryProcedure & { t_created: string | null })[];
  submitted_claims: {
    id: string;
    name: string;
    properties: Record<string, unknown>;
    t_created: string | null;
  }[];
  executions: MeExecution[];
}

// ---------- Contribute (decompose) ----------

export interface DecomposeResponse {
  id: string;
  feasible: boolean;
  reasoning: string;
  ops: Record<string, unknown>[];
  node_count: number;
  safe_to_propose: boolean;
  structural_problems: string[];
  objections: string[];
  suspected_manipulation: boolean;
  input_flagged: boolean;
  input_truncated: boolean;
  related_existing: string[];
  reused_nodes: Record<string, unknown>[];
  is_novel: boolean;
}

export interface RecommendAlternative {
  id: string;
  name: string;
  goal: string | null;
  verification_state: string;
  similarity_score: number | null;
  capability_note?: string | null;
  verdict?: string;
}

export interface RecommendResponse {
  goal: string;
  recommendation: {
    id: string;
    name: string;
    display_name: string;
    display_description: string | null;
    applicability_summary: string | null;
    relevance_label: RelevanceLabel;
    relevance_reason: string | null;
    goal: string | null;
    verification_state: string;
    similarity_score: number | null;
    verdict: string | null;
    reason: string | null;
    evidence: string[];
    capability_note: string | null;
  } | null;
  alternatives: RecommendAlternative[];
  confidence: string | null;
  reason: string | null;
}


export interface SubgraphResponse {
  center: string;
  nodes: { id: string; table: "knowledge_nodes" | "task_nodes"; label: string }[];
  edges: { id: string; source: string; target: string; label: string }[];
}

