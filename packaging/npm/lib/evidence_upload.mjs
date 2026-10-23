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
  for (const k of Object.keys(out)) if (!CHECK_EVIDENCE_FIELDS.includes(k)) delete out[k];
  return out;
}
