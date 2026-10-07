/**
 * Typed client for the admin-only surface of the keळ backend. Every call goes through lib/api.ts, so it carries
 * the signed-in user's Supabase token; the backend alone decides whether that user holds ADMIN_OPS (a 403 comes
 * back as `{kind:"forbidden"}`). Nothing here is fabricated: an unavailable endpoint is an explicit state.
 */
import { apiGet, apiPost, type ApiState } from "@/lib/api";

export type { ApiState };

export interface IngestionAutoStatus {
  enabled: boolean;
  mode: string;
  interval_seconds: number;
  workspace?: string | null;
  trace_dir?: string | null;
  max_sessions: number;
  promote_limit: number;
  extract_limit: number;
  job_limit: number;
  run_count: number;
  last_run_started_at?: string | null;
  last_run_completed_at?: string | null;
  last_result?: Record<string, unknown> | null;
  last_error?: string | null;
  last_error_at?: string | null;
}

export interface IndexLag {
  current_recipe: string;
  lag_count: number;
  recipe_drift_count: number;
  total_stale: number;
  sample: Record<string, unknown>[];
}

export type Row = Record<string, unknown>;
export type SubmissionKind = "procedure" | "benchmark";
export type SubmissionStatus = "" | "pending" | "needs_review" | "accepted" | "rejected";

export const getIngestionStatus = (signal?: AbortSignal) => apiGet<IngestionAutoStatus>("/v1/admin/ingestion/auto-status", signal);
export const getIndexLag = (limit = 25, signal?: AbortSignal) => apiGet<IndexLag>(`/v1/admin/index-lag?limit=${limit}`, signal);
export const processFailureRoutes = () => apiPost<{ applied: Record<string, number> }>("/v1/admin/failure-routes/process", {});

export function listSubmissions(kind: SubmissionKind, status: SubmissionStatus, limit = 50, signal?: AbortSignal) {
  const q = new URLSearchParams({ limit: String(limit) });
  if (status) q.set("status", status);
  const path = kind === "procedure" ? "procedure-submissions" : "benchmark-submissions";
  return apiGet<{ submissions: Row[] }>(`/v1/economy/${path}?${q}`, signal);
}

export function reviewSubmission(kind: SubmissionKind, id: string, decision: "accepted" | "rejected" | "needs_review", note?: string) {
  const path = kind === "procedure" ? "procedure-submissions" : "benchmark-submissions";
  return apiPost<Row>(`/v1/economy/${path}/${encodeURIComponent(id)}/review`, { decision, note: note || null });
}

export const moderateWay = (procedureRowId: string, action: "hide" | "restore" | "remove", reason: string) =>
  apiPost<Row>(`/v1/economy/ways/${encodeURIComponent(procedureRowId)}/moderate`, { action, reason });

export const getCreditBalance = (contributorId: string, signal?: AbortSignal) =>
  apiGet<{ contributor_id: string; balance: number }>(`/v1/economy/contributors/${encodeURIComponent(contributorId)}/credits`, signal);

export const getCreditHistory = (contributorId: string, limit = 50, signal?: AbortSignal) =>
  apiGet<{ contributor_id: string; events: Row[] }>(`/v1/economy/contributors/${encodeURIComponent(contributorId)}/credits/history?limit=${limit}`, signal);

export const clawbackCredit = (eventId: string, reasonText: string) =>
  apiPost<Row>(`/v1/economy/credits/${encodeURIComponent(eventId)}/clawback`, { reason_text: reasonText });

/** "3 minutes ago" style, for ISO timestamps from the backend. */
export function timeAgo(iso?: string | null): string {
  if (!iso) return "never";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (Number.isNaN(s)) return "unknown";
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}
