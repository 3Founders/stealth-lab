// Check evidence leaving a run: recorded locally (always, in .stealth/evidence.md) and, only when the user has switched upload on
// (STEALTHLAB_EVIDENCE_UPLOAD=on), reported to the server through the outbox (docs/evidence_upload_design.md).
//
// Rules this file keeps:
//   * OFF BY DEFAULT. Nothing is built for the server, queued or sent unless STEALTHLAB_EVIDENCE_UPLOAD=on.
//   * ONLY THE WHITELIST LEAVES. A report is lib/evidence_upload.mjs toUploadPayload(): ids, verdicts, keyed HMACs, blob shas,
//     timestamps, where. Never the command text, a path, output or the reviewer's text. The key stays in .stealth/evidence.key.
//   * FAILS OPEN. An upload error, a missing key, an unsigned-in user: the run's result and every status are unchanged, and the
//     report is queued in ~/.stealthlab/outbox/ and retried like the other reports.
//   * NEVER A DELAY. The prompt-time caller (dispatch) only QUEUES (no network); `status --run` sends with a short bound.
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { evidenceKey } from "./evidence_key.mjs";
import { toUploadPayload, uploadEnabled } from "./evidence_upload.mjs";
import { reportCheckEvidence } from "./exec/evidence.mjs";
import { appendEvidence, fileShas, headSha, renderEvidence, workTreeId } from "./status.mjs";
import { loadLibrary } from "./library.mjs";

const REPO_REF = /^[a-z]:[0-9a-f]{16}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export { uploadEnabled };

export function clientVersion() {
  try {
    const require = createRequire(import.meta.url);
    return String(require("../package.json").version || "").slice(0, 40) || undefined;
  } catch {
    return undefined;
  }
}

// The hashed repository identity the survey already writes (.stealth/meta.json repo_identity.repo_id) and already sends with
// find_ways. Never a name or URL. null when the survey has not run.
export function repoRefOf(root) {
  try {
    const meta = JSON.parse(fs.readFileSync(path.join(root, ".stealth", "meta.json"), "utf8"));
    const id = meta?.repo_identity?.repo_id;
    return REPO_REF.test(id || "") ? id : null;
  } catch {
    return null;
  }
}

// Evidence records (the objects renderEvidence takes, plus .targetKind, .goalId, .procedureId, .reviewerModel, .executorModel) ->
// {built, queued, sent, skipped}. Builds each report through the whitelist, then queues or sends it. Never throws.
export async function uploadRecords(root, records, { env = process.env, fetchImpl, send = true, timeoutMs = 8000, log = () => {} } = {}) {
  const out = { built: 0, queued: 0, sent: 0, skipped: 0 };
  try {
    if (!uploadEnabled(env)) return out;
    const key = evidenceKey(root, { env });
    const repoRef = repoRefOf(root);
    if (!key || !repoRef) {
      log(`evidence upload: skipped (${!key ? "no evidence key" : "no repository identity: run the survey"})`);
      out.skipped = records.length;
      return out;
    }
    let sending = send;                    // the first report that cannot be sent queues the rest without waiting on the network again
    for (const r of records) {
      const payload = toUploadPayload(
        { target: r.target, method: r.method, result: r.result, at: r.at, commit: r.commit, tree: r.tree, hashes: r.hashes, where: r.where, check: r.check },
        { key, repoRef, targetKind: r.targetKind, goalId: UUID.test(r.goalId || "") ? r.goalId : undefined,
          procedureId: UUID.test(r.procedureId || "") ? r.procedureId : undefined, clientVersion: clientVersion(),
          reviewerModel: r.reviewerModel, executorModel: r.executorModel });
      if (!payload) { out.skipped++; continue; }
      out.built++;
      try {
        const res = await reportCheckEvidence(payload, { env, fetchImpl, timeoutMs, queueOnly: !sending });
        if (res.reported) out.sent++;
        else if (res.queued) { out.queued++; sending = false; }
        else out.skipped++;
      } catch (err) {
        out.skipped++;
        log(`evidence upload: ${String(err?.message || err).slice(0, 120)}`);
      }
    }
  } catch (err) {
    log(`evidence upload: ${String(err?.message || err).slice(0, 120)}`);
  }
  return out;
}

// The evidence for an answer dispatch just accepted and recorded as library entry `entryId`: the check that passed
// (deterministic_check), and, when the strong reviewer said the diff fixes the request AND it is not the model that wrote the
// answer, that verdict (independent_agent). Both go to .stealth/evidence.md at the entry's touched-file hashes; with upload on they
// are QUEUED for the outbox (no network, no wait). Never throws. -> {lines, upload}
export async function recordAcceptedEvidence({ root, entryId, check, reviewerFixes = false, reviewerModel = null, executorModel = null,
  env = process.env, log = () => {}, now = () => new Date() }) {
  const result = { lines: 0, upload: null };
  try {
    if (!entryId || !(check || "").trim()) return result;
    const entry = loadLibrary(root).entries.find((e) => e.id === entryId) || null;      // the parsed entry (readEntry returns raw lines)
    const touches = (entry?.procs || []).flatMap((p) => p.touches || []).filter((t) => t?.path);
    const shas = fileShas(root, touches.map((t) => t.path));
    const hashes = touches.filter((t) => shas.has(t.path)).map((t) => ({ path: t.path, sha: shas.get(t.path).slice(0, 12) }));
    const base = { target: entryId, at: now().toISOString(), commit: headSha(root), tree: workTreeId(root), hashes, where: "worktree",
      check: String(check), targetKind: "library_entry", goalId: entry?.g || undefined, procedureId: entry?.procs?.[0]?.p || undefined };
    const records = [{ ...base, method: "deterministic_check", result: "pass" }];
    const independent = reviewerFixes && reviewerModel && executorModel
      && String(reviewerModel).trim().toLowerCase() !== String(executorModel).trim().toLowerCase();
    if (independent) records.push({ ...base, method: "independent_agent", result: "pass", check: `review by ${reviewerModel}`, reviewerModel, executorModel });
    const lines = records.map((r) => renderEvidence(r)).filter(Boolean);
    result.lines = appendEvidence(root, lines);
    result.upload = await uploadRecords(root, records, { env, send: false, log });
  } catch (err) {
    log(`evidence: not recorded (${String(err?.message || err).slice(0, 120)})`);
  }
  return result;
}
