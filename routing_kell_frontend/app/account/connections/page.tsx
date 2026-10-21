"use client";
import { useEffect, useState } from "react";
import AnimatedHeading from "@/components/AnimatedHeading";
import StateNotice from "@/components/NotConnected";
import { getSession, type Session } from "@/lib/session";
import { listConnectedApps, revokeConnectedApp, SCOPE_TEXT, type ConnectedApp } from "@/lib/oauth-consent";
import type { ApiState } from "@/lib/api";

// Apps (MCP clients) the visitor allowed on /oauth/consent, with a way to remove each one.
export default function ConnectionsPage() {
  const [session, setSession] = useState<Session | null | undefined>(undefined);
  const [apps, setApps] = useState<ApiState<ConnectedApp[]>>({ kind: "loading" });
  const [removing, setRemoving] = useState<string | null>(null);

  useEffect(() => { getSession().then(setSession); }, []);

  function load() {
    listConnectedApps().then((r) =>
      setApps(r === null ? { kind: "error", message: "Couldn’t read your connected apps. Please try again." } : { kind: "ok", data: r }));
  }

  useEffect(() => {
    if (session === undefined) return; // still resolving
    if (!session) { setApps({ kind: "unauthenticated" }); return; }
    load();
  }, [session]);

  async function remove(clientId: string) {
    setRemoving(clientId);
    const ok = await revokeConnectedApp(clientId);
    setRemoving(null);
    if (ok) load();
    else setApps({ kind: "error", message: "Couldn’t remove that app. Please try again." });
  }

  return (
    <>
      <section className="page-hero frame grid">
        <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>CONNECTED APPS</b></div>
        <h1 className="display"><AnimatedHeading>Apps that use keळ as you</AnimatedHeading></h1>
        <p className="lead">Apps like Claude, Cursor or VS Code you allowed to connect. Removing one stops it within an hour.</p>
      </section>

      <section className="frame grid" style={{ paddingBottom: 120, rowGap: 24 }}>
        {apps.kind === "ok" && apps.data.length > 0 ? (
          <ul style={{ gridColumn: "1 / -1", listStyle: "none", padding: 0, display: "grid", gap: 16 }}>
            {apps.data.map((a) => (
              <li key={a.clientId} className="empty" style={{ textAlign: "left" }}>
                <b>{a.name}</b>
                {a.uri && <span className="small dim"> · {a.uri}</span>}
                <p className="small dim">
                  Allowed {new Date(a.grantedAt).toLocaleDateString()}
                  {a.scopes.filter((s) => SCOPE_TEXT[s]).length > 0 &&
                    <> · {a.scopes.filter((s) => SCOPE_TEXT[s]).map((s) => SCOPE_TEXT[s].toLowerCase()).join(", ")}</>}
                </p>
                <button type="button" className="link-btn" onClick={() => remove(a.clientId)} disabled={removing === a.clientId}>
                  {removing === a.clientId ? "Removing…" : "Remove access"}
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <div style={{ gridColumn: "1 / -1" }}>
            <StateNotice state={apps} empty="No apps are connected." />
          </div>
        )}
      </section>
    </>
  );
}
