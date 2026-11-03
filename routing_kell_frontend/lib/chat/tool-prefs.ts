// The person's tool preference, on the hosted MCP origin: may kell use tools that need a sign-in?
//
// Same website-session token as the rooms API; the server stores it per account (backend/app/tool_routing/prefs.py), so the
// toggle in Settings and the room agent (which runs server-side) agree. The default is on.
import { MCP_ORIGIN } from "@/lib/connector";

async function call(token: string, method: "GET" | "PUT", body?: unknown): Promise<boolean> {
  const base = (MCP_ORIGIN || "").replace(/\/+$/, "");
  if (!base) throw new Error("kell's server address is not configured");
  const res = await fetch(`${base}/v1/tools/prefs`, {
    method, cache: "no-store", credentials: "omit", referrerPolicy: "no-referrer",
    headers: { authorization: `Bearer ${token}`, ...(body !== undefined ? { "content-type": "application/json" } : {}) },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  });
  if (!res.ok) throw new Error(res.status === 401 ? "Sign in again to change this." : `The server answered ${res.status}.`);
  const j = (await res.json()) as { allow_signin_tools?: unknown };
  return j.allow_signin_tools !== false;
}

export const getToolPrefs = (token: string): Promise<boolean> => call(token, "GET");
export const setToolPrefs = (token: string, allow: boolean): Promise<boolean> => call(token, "PUT", { allow_signin_tools: allow });
