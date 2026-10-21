/**
 * Orchestrates the local project sync flow end to end: local bridge
 * handshake -> user selection -> client-side encryption -> ciphertext
 * upload -> local-key handoff for ongoing sync. See
 * docs/local_project_sync_security.md and its Implementation Closure.
 *
 * Split into two phases matching the UI's own two steps (discover-and-
 * show, then sync-what-was-selected) rather than one monolithic call, so
 * the "SYNC LOCAL PROJECTS?" list can be shown to the user BEFORE any
 * encryption or network upload happens -- no project is ever synced
 * merely because the browser could see it was available.
 */
import { API_URL } from "@/lib/api";
import { issueSyncDevice } from "@/lib/kel-api";
import {
  isLocalBridgeAvailable, startHandshake, listLocalProjects, preparePayload, registerLocalKeys,
  type LocalProjectListing,
} from "@/lib/local-bridge";
import { getAccessToken } from "@/lib/session";
import {
  generateProjectKey, exportProjectKeyRaw, encryptJson, deriveRecoveryKek, wrapProjectKey,
} from "@/lib/sync-crypto";

export interface DiscoveryResult {
  available: boolean;
  projects: LocalProjectListing[];
  /** Capability for the NEXT step (prepare-payload) -- opaque, ~60s TTL,
   * single-use. Pass it straight to `syncSelectedProjects`. */
  prepareCapability: string | null;
}

/** Step 1: is a local bridge running, and if so, what local projects does
 * it know about? Performs the FULL start-handshake + list-projects
 * exchange (the browser must already hold a real Supabase session for
 * start-handshake to succeed) but uploads nothing and encrypts nothing --
 * this is read-only from the account's perspective. */
export async function discoverLocalProjects(): Promise<DiscoveryResult> {
  const available = await isLocalBridgeAvailable();
  if (!available) return { available: false, projects: [], prepareCapability: null };

  const token = await getAccessToken();
  if (!token) return { available: true, projects: [], prepareCapability: null };

  try {
    const listCapability = await startHandshake(token);
    const { projects, capability } = await listLocalProjects(listCapability);
    return { available: true, projects, prepareCapability: capability };
  } catch {
    return { available: true, projects: [], prepareCapability: null };
  }
}

export interface SyncProgress {
  projectId: string;
  status: "encrypting" | "uploading" | "done" | "error";
  message?: string;
}

export interface SyncResult {
  succeeded: string[];
  failed: Record<string, string>;
}

/** Step 2: the user picked `projectIds` and confirmed "Sync this local
 * project? This will connect this private local project to your keळ
 * account." For EACH project: generate a fresh P-DEK, encrypt its
 * plaintext bundle client-side, wrap the P-DEK under the
 * recovery-passphrase-derived KEK, upload ciphertext + wrapped key to the
 * REST API (authenticated by a freshly-issued sync device credential, NOT
 * the Supabase session), then hand the raw P-DEK + device credential back
 * to the local bridge over loopback so ongoing sync keeps working after
 * this tab closes. */
export async function syncSelectedProjects(
  prepareCapability: string,
  projectIds: string[],
  recoveryPassphrase: string,
  onProgress?: (p: SyncProgress) => void,
): Promise<SyncResult> {
  const { payloads, capability: registerCapability } = await preparePayload(prepareCapability, projectIds);

  const succeeded: string[] = [];
  const failed: Record<string, string> = {};
  const registerEntries: Array<{ project_id: string; p_dek_base64: string; sync_device_token: string }> = [];

  for (const projectId of projectIds) {
    const payload = payloads[projectId];
    if (!payload) {
      failed[projectId] = "not available on this machine";
      continue;
    }
    try {
      onProgress?.({ projectId, status: "encrypting" });
      const pDek = await generateProjectKey();
      const ciphertextBase64 = await encryptJson(pDek, { files: payload.files, activity: payload.activity });
      const { kek, saltBase64, params } = await deriveRecoveryKek(recoveryPassphrase);
      const wrappedPDek = await wrapProjectKey(pDek, kek);

      onProgress?.({ projectId, status: "uploading" });
      const deviceResult = await issueSyncDevice(projectId);
      if (deviceResult.kind !== "ok") {
        failed[projectId] = "could not issue a sync device credential";
        continue;
      }
      const deviceToken = deviceResult.data.token;

      const uploadRes = await fetch(`${API_URL}/v1/me/synced-projects/${encodeURIComponent(projectId)}/sync`, {
        method: "POST",
        headers: { "content-type": "application/json", authorization: `Bearer ${deviceToken}` },
        body: JSON.stringify({
          revision: 1, ciphertext_base64: ciphertextBase64,
          wrapped_p_dek: wrappedPDek, recovery_salt: saltBase64, kdf_params: params,
        }),
      });
      if (!uploadRes.ok) {
        failed[projectId] = `upload failed (${uploadRes.status})`;
        continue;
      }

      const pDekRaw = await exportProjectKeyRaw(pDek);
      registerEntries.push({ project_id: projectId, p_dek_base64: pDekRaw, sync_device_token: deviceToken });
      succeeded.push(projectId);
      onProgress?.({ projectId, status: "done" });
    } catch (e) {
      failed[projectId] = e instanceof Error ? e.message : "sync failed";
      onProgress?.({ projectId, status: "error", message: failed[projectId] });
    }
  }

  if (registerEntries.length > 0) {
    try {
      await registerLocalKeys(registerCapability, registerEntries);
    } catch {
      // Best-effort: the account-side sync already succeeded for these
      // projects (ciphertext + wrapped key are durably stored) even if
      // the local-bridge handoff for ONGOING sync fails here -- ongoing
      // automatic sync simply won't be available on this machine until a
      // fresh browser-mediated sync succeeds; never surfaced as a failure
      // of the sync itself.
    }
  }

  return { succeeded, failed };
}

export type EnableLocalSyncResult = "enabled" | "no_local_bridge" | "not_found_on_this_machine" | "error";

/** New-device / recovery follow-on: after a project's content has been
 * decrypted client-side (via the recovery passphrase, on a device that
 * never held the P-DEK before), THIS device may also be running a local
 * MCP process against its own checkout of the SAME project (same
 * `stable_project_id` — a fresh clone of the same repo gets a different
 * id, per docs/local_project_sync_security.md's own explicitly-out-of-
 * scope "cloned directory" note; this only handles the same, already-
 * known-to-this-machine checkout). If so, hand the already-unwrapped P-DEK
 * to that local bridge over loopback (the same register-local-key step
 * the initial sync uses) so ONGOING sync also starts working from this
 * device — without ever re-deriving or re-uploading anything. */
export async function enableLocalSyncOnThisDevice(projectId: string, pDek: CryptoKey): Promise<EnableLocalSyncResult> {
  const available = await isLocalBridgeAvailable();
  if (!available) return "no_local_bridge";

  const token = await getAccessToken();
  if (!token) return "error";

  try {
    const listCapability = await startHandshake(token);
    const { projects, capability: prepareCapability } = await listLocalProjects(listCapability);
    if (!projects.some((p) => p.project_id === projectId)) return "not_found_on_this_machine";

    const { capability: registerCapability } = await preparePayload(prepareCapability, [projectId]);
    const deviceResult = await issueSyncDevice(projectId);
    if (deviceResult.kind !== "ok") return "error";

    const pDekRaw = await exportProjectKeyRaw(pDek);
    await registerLocalKeys(registerCapability, [
      { project_id: projectId, p_dek_base64: pDekRaw, sync_device_token: deviceResult.data.token },
    ]);
    return "enabled";
  } catch {
    return "error";
  }
}
