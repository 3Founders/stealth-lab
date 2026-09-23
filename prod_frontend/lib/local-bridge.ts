/**
 * Browser-side client for the local project sync bridge (backend/app/
 * mcp_server/local_sync_bridge.py). See docs/local_project_sync_security.md
 * §C. Every request here goes to 127.0.0.1, never to the keळ REST API --
 * the local MCP process and the remote account API are two entirely
 * separate origins this file never conflates.
 */
const BRIDGE_ORIGIN = "http://127.0.0.1:8765";

export interface LocalProjectListing {
  project_id: string;
  display_hint: string | null;
  last_local_activity_at: string | null;
}

export interface PreparedPayload {
  files: Record<string, string>;
  activity: Array<{ timestamp: string | null; file_path: string; summary: string; actor: string }>;
}

/** True only if a local keळ bridge is actually reachable on this machine.
 * Short timeout -- most users will not have one running, and this must
 * never hang the account page waiting for a connection that isn't there. */
export async function isLocalBridgeAvailable(timeoutMs = 800): Promise<boolean> {
  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    const res = await fetch(`${BRIDGE_ORIGIN}/.well-known/stealthlab-local`, { signal: ctrl.signal });
    clearTimeout(timer);
    if (!res.ok) return false;
    const body = await res.json();
    return body?.service === "stealthlab-local-bridge";
  } catch {
    return false;
  }
}

async function post<T>(path: string, body: unknown, capability?: string): Promise<T> {
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (capability) headers["x-sync-capability"] = capability;
  const res = await fetch(`${BRIDGE_ORIGIN}${path}`, { method: "POST", headers, body: JSON.stringify(body ?? {}) });
  if (!res.ok) {
    let reason = `bridge request to ${path} failed (${res.status})`;
    try {
      const err = await res.json();
      if (err?.error) reason = err.error;
    } catch {
      // ignore -- keep the generic reason
    }
    throw new Error(reason);
  }
  return res.json() as Promise<T>;
}

export async function startHandshake(supabaseAccessToken: string): Promise<string> {
  const { capability } = await post<{ capability: string }>("/local-sync/start-handshake", {
    supabase_access_token: supabaseAccessToken,
  });
  return capability;
}

export async function listLocalProjects(capability: string): Promise<{ projects: LocalProjectListing[]; capability: string }> {
  return post("/local-sync/list-projects", {}, capability);
}

export async function preparePayload(
  capability: string, projectIds: string[],
): Promise<{ payloads: Record<string, PreparedPayload>; capability: string }> {
  return post("/local-sync/prepare-payload", { project_ids: projectIds }, capability);
}

export interface RegisterLocalKeyEntry {
  project_id: string;
  p_dek_base64: string;
  sync_device_token: string;
}

export async function registerLocalKeys(capability: string, projects: RegisterLocalKeyEntry[]): Promise<Record<string, string>> {
  const { results } = await post<{ results: Record<string, string> }>(
    "/local-sync/register-local-key", { projects }, capability,
  );
  return results;
}
