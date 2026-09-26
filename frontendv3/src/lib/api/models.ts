export interface Problem {
  id: string;
  title: string;
  description: string | null;
  objective: string | null;
  constraints: unknown[];
  status: string;
  proposer: string | null;
  provenance: string | null;
  metadata: Record<string, unknown>;
  visibility: string;
  created_at: string;
  updated_at: string;
}

export interface LeaderboardEntry {
  solution_id: string;
  solution_type: string;
  target_id: string;
  run_count: number;
  successes: number;
  verified_successes: number;
  success_rate: number | null;
  verified_success_rate: number | null;
  verified_success_wilson_lower: number;
  verified_success_wilson_upper: number;
  p50_latency_s: number | null;
  p95_latency_s: number | null;
  cost: number | null;
  first_pass_success_rate: number | null;
  evaluations: number;
  incomparable_evaluations: number;
  state: string; // BEST_VERIFIED | PROMISING | INSUFFICIENT_EVIDENCE (backend bands)
}

export interface Leaderboard {
  problem_id: string;
  benchmark_id: string | null;
  leaderboard: LeaderboardEntry[];
  current_best: string[];
  current_best_is_tie: boolean;
  conditional_leaders: Record<string, string | null>;
}

export interface Evaluation {
  id: string;
  problem_id: string;
  benchmark_id: string;
  solution_id: string;
  procedure_id: string | null;
  procedure_version: number | null;
  implementation_id: string | null;
  implementation_version: number | null;
  environment: Record<string, unknown>;
  methodology: Record<string, unknown>;
  status: string;
  run_count?: number | null;
  metrics?: Record<string, unknown> | null;
  verification_summary?: Record<string, unknown> | null;
  provenance: string | null;
}

export interface BestWay {
  goal: string;
  matched_problem: Problem | null;
  benchmark_id?: string | null;
  result: string; // "verified" | "no verified solution yet" | "no matching problem"
  current_best: string[];
  current_best_is_tie?: boolean;
  leaderboard?: LeaderboardEntry[];
  conditional_leaders?: Record<string, string | null>;
  other_matched_problems?: string[];
}


export type Availability<T> = { available: true; data: T } | { available: false; reason: string };
export interface BillingSubscription { plan: string; usage: number | null; payment_status: string | null; }
export interface MCPConnection { url: string; status: 'not_checked' | 'connected' | 'disconnected'; }
export const billing: Availability<BillingSubscription> = {available:false, reason:'Billing and usage are not available in this deployment.'};
