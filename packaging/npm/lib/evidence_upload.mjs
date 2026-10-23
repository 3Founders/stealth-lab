// What a check-evidence report is allowed to contain (docs/evidence_upload_design.md section 3). This file is the whitelist:
// a pure function from one evidence record to the one object that may leave the machine, or null. It reads no file, sends
// nothing and writes nothing; the sender (the outbox path) is a separate step.
//
// Sent: ids, verdicts, timestamps, git object ids, blob shas, and HMACs of the check command and of each path.
// Never sent: the command text, a path in the clear, output, prompt text, a diff, a claim statement, review text, a repository
// name or URL, or the HMAC key itself.
import { checkHash, hmacHex, pathHash } from "./evidence_key.mjs";

export const CHECK_EVIDENCE_FIELDS = [
  "repo_ref", "target_kind", "target_id", "goal_id", "procedure_id", "method", "result", "observed_at", "commit_sha", "tree_id",
  "hashes", "where_ran", "check_hash", "client_event_id", "client_version", "reviewer_model", "executor_model",
];

const TARGET_KINDS = new Set(["library_entry", "plan_node"]);
const METHODS = new Set(["deterministic_check", "independent_agent"]);
const WHERE = new Set(["worktree", "in-place"]);
const TARGET_ID = /^[NL]-[A-Za-z0-9_.-]{1,40}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const MODEL = /^[A-Za-z0-9._:\/-]{1,80}$/;
const GIT_ID = /^[0-9a-f]{40}$/;
const REPO_REF = /^[a-z]:[0-9a-f]{16}$/;           // the hashed repository identity the survey already sends (r:/c:/p:)

// A stable id per evidence line, so the outbox retrying a send cannot create a second row: an HMAC of what identifies the line,
// laid out as a uuid. The same line gives the same id on every attempt.
export function clientEventId(key, ev) {
  const h = hmacHex(key, "event", [ev.target, ev.method, ev.result, ev.at, ev.tree || "-", ev.check].join("\0"));
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20, 32)}`;
}

// Upload is OFF unless the user turns it on: what leaves the machine is a decision, not a default (packaging/npm/README.md,
// "What leaves your machine").
export const uploadEnabled = (env = process.env) => String(env.STEALTHLAB_EVIDENCE_UPLOAD || "").trim().toLowerCase() === "on";

const HEX64 = /^[0-9a-f]{64}$/;
const SHA = /^[0-9a-f]{4,64}$/;

/** The strict check applied to anything about to be queued or sent: exactly the whitelisted keys, each a validated shape.
 * -> a clean copy, or null. toUploadPayload builds through it, and the outbox applies it again before it writes or sends. */
export function validateUploadPayload(obj) {
  if (!obj || typeof obj !== "object" || Array.isArray(obj)) return null;
  for (const k of Object.keys(obj)) if (!CHECK_EVIDENCE_FIELDS.includes(k)) return null;
  const o = obj;
  if (!REPO_REF.test(o.repo_ref || "") || !TARGET_KINDS.has(o.target_kind) || !TARGET_ID.test(o.target_id || "")) return null;
  if (!METHODS.has(o.method) || !(o.result === "pass" || o.result === "fail")) return null;
  if (!HEX64.test(o.check_hash || "") || !UUID.test(o.client_event_id || "")) return null;
  if (typeof o.observed_at !== "string" || Number.isNaN(Date.parse(o.observed_at))) return null;
  if (!Array.isArray(o.hashes) || o.hashes.length > 500) return null;
  const hashes = [];
  for (const h of o.hashes) {
    if (!h || typeof h !== "object" || Object.keys(h).sort().join() !== "path_hmac,sha") return null;
    if (!HEX64.test(h.path_hmac || "") || !SHA.test(h.sha || "")) return null;
    hashes.push({ path_hmac: h.path_hmac, sha: h.sha });
  }
  const out = { repo_ref: o.repo_ref, target_kind: o.target_kind, target_id: o.target_id, method: o.method, result: o.result,
    observed_at: o.observed_at, check_hash: o.check_hash, hashes, client_event_id: o.client_event_id };
  for (const k of ["commit_sha", "tree_id"]) if (o[k] !== undefined) { if (!GIT_ID.test(o[k] || "")) return null; out[k] = o[k]; }
  if (o.where_ran !== undefined) { if (!WHERE.has(o.where_ran)) return null; out.where_ran = o.where_ran; }
  for (const k of ["goal_id", "procedure_id"]) if (o[k] !== undefined) { if (!UUID.test(o[k] || "")) return null; out[k] = o[k]; }
  if (o.client_version !== undefined) { if (!/^[0-9A-Za-z.+-]{1,40}$/.test(o.client_version || "")) return null; out.client_version = o.client_version; }
  if (o.method === "independent_agent") {
    if (!MODEL.test(o.reviewer_model || "") || !MODEL.test(o.executor_model || "")) return null;
    if (o.reviewer_model.trim().toLowerCase() === o.executor_model.trim().toLowerCase()) return null;   // the reviewer is not the executor
    out.reviewer_model = o.reviewer_model;
    out.executor_model = o.executor_model;
  } else if (o.reviewer_model !== undefined || o.executor_model !== undefined) {
    return null;
  }
  return out;
}

/**
 * @param {object} ev a parsed evidence line (lib/status.mjs parseEvidence)
 * @param {{key: Buffer, repoRef: string, targetKind: string, goalId?: string, procedureId?: string, clientVersion?: string,
 *   reviewerModel?: string, executorModel?: string}} ctx
 * @returns {object|null} exactly the whitelisted keys that apply, or null when the record cannot be reported safely
 */
export function toUploadPayload(ev, ctx) {
  const { key } = ctx || {};
  if (!key || !Buffer.isBuffer(key) || key.length !== 32 || !ev) return null;
  if (!TARGET_KINDS.has(ctx.targetKind) || !METHODS.has(ev.method) || !(ev.result === "pass" || ev.result === "fail")) return null;
  if (!TARGET_ID.test(ev.target || "") || !REPO_REF.test(ctx.repoRef || "") || !(ev.check || "").trim()) return null;
  const out = {
    repo_ref: ctx.repoRef,
    target_kind: ctx.targetKind,
    target_id: ev.target,
    method: ev.method,
    result: ev.result,
    observed_at: ev.at || null,
    check_hash: checkHash(key, ev.check),
    hashes: (ev.hashes || []).filter((h) => h && h.path && /^[0-9a-f]{4,64}$/.test(h.sha || ""))
      .map((h) => ({ path_hmac: pathHash(key, h.path), sha: h.sha })),
    client_event_id: clientEventId(key, ev),
  };
  if (GIT_ID.test(ev.commit || "")) out.commit_sha = ev.commit;
  if (GIT_ID.test(ev.tree || "")) out.tree_id = ev.tree;
  if (WHERE.has(ev.where)) out.where_ran = ev.where;
  if (UUID.test(ctx.goalId || "")) out.goal_id = ctx.goalId;
  if (UUID.test(ctx.procedureId || "")) out.procedure_id = ctx.procedureId;
  if (ctx.clientVersion && /^[0-9A-Za-z.+-]{1,40}$/.test(ctx.clientVersion)) out.client_version = ctx.clientVersion;
  if (ev.method === "independent_agent") {
    if (!MODEL.test(ctx.reviewerModel || "") || !MODEL.test(ctx.executorModel || "")) return null;   // independence needs both names
    out.reviewer_model = ctx.reviewerModel;
    out.executor_model = ctx.executorModel;
  }
  return validateUploadPayload(out);
}
