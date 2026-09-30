/**
 * OAuth consent for MCP clients (Claude, Cursor, VS Code, MCP Inspector)
 * signing in to the keळ MCP server.
 *
 * Supabase Auth is the OAuth 2.1 authorization server
 * (backend/app/mcp_server/oauth_resource.py has the whole flow). When a
 * client sends the user to Supabase's /authorize, Supabase redirects to
 * this app's authorization path (/oauth/consent, set in the Supabase
 * dashboard) with an `authorization_id`. This module turns that id into
 * what the page needs, and records the user's decision. Supabase issues
 * the code and redirects back to the client; nothing here sees a token
 * meant for the client.
 *
 * Every call goes through lib/supabase.ts's client; this file never
 * creates its own.
 */
"use client";

import { getSupabase } from "@/lib/supabase";

export type ConsentState =
  | { kind: "not-configured" }
  | { kind: "missing-id" }
  | { kind: "sign-in"; href: string }
  | { kind: "redirect"; url: string }
  | {
      kind: "ask";
      authorizationId: string;
      clientName: string;
      clientUri: string | null;
      redirectUri: string;
      scopes: string[];
      email: string;
    }
  | { kind: "error"; message: string };

/** Plain-language meaning of each scope Supabase can grant. */
export const SCOPE_TEXT: Record<string, string> = {
  openid: "Confirm who you are",
  email: "See your email address",
  profile: "See your name and profile picture",
  phone: "See your phone number",
};

export function consentPath(authorizationId: string): string {
  return `/oauth/consent?authorization_id=${encodeURIComponent(authorizationId)}`;
}

/** Where to send a signed-out user so they come back to this request. */
export function signInHref(authorizationId: string): string {
  return `/sign-in?redirect=${encodeURIComponent(consentPath(authorizationId))}`;
}

const SCRIPT_SCHEMES = new Set(["javascript:", "data:", "vbscript:", "blob:", "file:"]);

/** Supabase only hands back redirects to the client's registered callback,
 * which may be http(s) (a localhost callback, a web app) or an app's own
 * scheme (cursor://, vscode://). Still, never follow a URL that would run
 * script in this page. */
export function safeRedirect(url: string | undefined | null): string | null {
  if (!url) return null;
  try {
    const u = new URL(url);
    return SCRIPT_SCHEMES.has(u.protocol) ? null : u.toString();
  } catch {
    return null;
  }
}

export async function loadConsent(authorizationId: string | null): Promise<ConsentState> {
  const client = getSupabase();
  if (!client) return { kind: "not-configured" };
  if (!authorizationId) return { kind: "missing-id" };

  const { data: sessionData } = await client.auth.getSession();
  if (!sessionData.session) return { kind: "sign-in", href: signInHref(authorizationId) };

  const { data, error } = await client.auth.oauth.getAuthorizationDetails(authorizationId);
  if (error || !data) {
    return { kind: "error", message: "This sign-in request has expired or is not valid. Start again from your app." };
  }
  if (!("authorization_id" in data)) {
    // Already approved before: Supabase answers with the redirect straight away.
    const url = safeRedirect(data.redirect_url);
    return url ? { kind: "redirect", url } : { kind: "error", message: "The app's return address is not valid." };
  }
  return {
    kind: "ask",
    authorizationId: data.authorization_id,
    clientName: data.client?.name || "An app",
    clientUri: data.client?.uri || null,
    redirectUri: data.redirect_uri,
    scopes: (data.scope || "").split(/\s+/).filter(Boolean),
    email: data.user?.email || "",
  };
}

/** Records the decision and returns where to send the browser next. */
export async function decide(authorizationId: string, approve: boolean): Promise<string | null> {
  const client = getSupabase();
  if (!client) return null;
  const opts = { skipBrowserRedirect: true };
  const { data, error } = approve
    ? await client.auth.oauth.approveAuthorization(authorizationId, opts)
    : await client.auth.oauth.denyAuthorization(authorizationId, opts);
  if (error || !data) return null;
  return safeRedirect(data.redirect_url);
}

export interface ConnectedApp {
  clientId: string;
  name: string;
  uri: string | null;
  scopes: string[];
  grantedAt: string;
}

/** Apps the signed-in user has allowed. null when it can't be read. */
export async function listConnectedApps(): Promise<ConnectedApp[] | null> {
  const client = getSupabase();
  if (!client) return null;
  const { data, error } = await client.auth.oauth.listGrants();
  if (error || !data) return null;
  return data.map((g) => ({
    clientId: g.client.id,
    name: g.client.name || "An app",
    uri: g.client.uri || null,
    scopes: g.scopes || [],
    grantedAt: g.granted_at,
  }));
}

/** Removes an app's access: it can't refresh its token or sign in again
 * without asking. An access token it already holds stays valid until it
 * expires (Supabase access tokens are short-lived, one hour by default). */
export async function revokeConnectedApp(clientId: string): Promise<boolean> {
  const client = getSupabase();
  if (!client) return false;
  const { error } = await client.auth.oauth.revokeGrant({ clientId });
  return !error;
}
