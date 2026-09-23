"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import AnimatedHeading from "@/components/AnimatedHeading";
import StateNotice from "@/components/NotConnected";
import { getMyStealthProjects, timeAgo, type StealthProjectSummary } from "@/lib/kel-api";
import { getSession, type Session } from "@/lib/session";
import { discoverLocalProjects, syncSelectedProjects, type DiscoveryResult, type SyncProgress } from "@/lib/sync";
import type { ApiState } from "@/lib/api";

export default function AccountPage() {
  // Same "resolve the session client-side first" shape as /account/credits —
  // this page is entirely the signed-in visitor's own private data.
  const [session, setSession] = useState<Session | null | undefined>(undefined);
  const [projects, setProjects] = useState<ApiState<StealthProjectSummary[]>>({ kind: "loading" });
  const [infoOpen, setInfoOpen] = useState(false);

  const [discovery, setDiscovery] = useState<DiscoveryResult | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [passphraseStep, setPassphraseStep] = useState(false);
  const [passphrase, setPassphrase] = useState("");
  const [syncing, setSyncing] = useState(false);
  const [progress, setProgress] = useState<Record<string, SyncProgress["status"]>>({});
  const [syncResult, setSyncResult] = useState<{ succeeded: string[]; failed: Record<string, string> } | null>(null);

  useEffect(() => { getSession().then(setSession); }, []);

  useEffect(() => {
    if (session === undefined) return; // still resolving
    if (!session) {
      setProjects({ kind: "unauthenticated" });
      return;
    }
    const ac = new AbortController();
    getMyStealthProjects(ac.signal).then((r) =>
      setProjects(r.kind === "ok" ? { kind: "ok", data: r.data.projects } : (r as ApiState<StealthProjectSummary[]>))
    );
    return () => ac.abort();
  }, [session]);

  // Detects a local bridge (127.0.0.1:8765) once signed in. Never sends
  // any project CONTENT merely by detecting it — see the "i" note below.
  useEffect(() => {
    if (!session) return;
    discoverLocalProjects().then(setDiscovery);
  }, [session]);

  function toggleSelected(projectId: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(projectId)) next.delete(projectId); else next.add(projectId);
      return next;
    });
  }

  async function confirmSync(e: React.FormEvent) {
    e.preventDefault();
    if (!discovery?.prepareCapability || selected.size === 0) return;
    setSyncing(true);
    setSyncResult(null);
    const ids = Array.from(selected);
    const result = await syncSelectedProjects(discovery.prepareCapability, ids, passphrase, (p: SyncProgress) => {
      setProgress((prev) => ({ ...prev, [p.projectId]: p.status }));
    });
    setSyncing(false);
    setPassphraseStep(false);
    setPassphrase("");
    setSelected(new Set());
    setSyncResult(result);
    if (result.succeeded.length > 0) {
      getMyStealthProjects().then((r) => {
        if (r.kind === "ok") setProjects({ kind: "ok", data: r.data.projects });
      });
    }
  }

  const alreadySyncedIds = new Set(projects.kind === "ok" ? projects.data.map((p) => p.project_id) : []);
  const newlyDiscovered = (discovery?.projects ?? []).filter((p) => !alreadySyncedIds.has(p.project_id));

  return (
    <>
      <section className="page-hero frame grid">
        <div className="marker caption" style={{ gridColumn: "1 / -1" }}><b>ACCOUNT</b></div>
        <h1 className="display"><AnimatedHeading>Your local projects</AnimatedHeading></h1>
        <p className="lead">Private to your account, never published to the Commons on its own.</p>
      </section>

      <section className="frame grid" style={{ paddingBottom: 40, rowGap: 24 }}>
        <div style={{ gridColumn: "1 / span 12" }}>
          <div className="marker caption" style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <b>SYNC LOCAL PROJECTS?</b>
            <button
              type="button"
              aria-expanded={infoOpen}
              aria-controls="sync-privacy-note"
              aria-label="About the privacy of synced projects"
              onClick={() => setInfoOpen((o) => !o)}
              style={{
                width: 16, height: 16, borderRadius: "50%", border: "1px solid var(--rule-strong)",
                background: "none", padding: 0, fontSize: 10, lineHeight: "14px", fontFamily: "serif",
                fontStyle: "italic", color: "var(--grey)", cursor: "pointer",
              }}
            >
              i
            </button>
          </div>
          {infoOpen && (
            <p id="sync-privacy-note" className="small dim" style={{ marginTop: 8, maxWidth: 640 }}>
              Synced project contents are encrypted in your browser before they ever leave your device. keळ
              stores only ciphertext, and never has the key needed to read it. Decryption happens locally too,
              using your recovery passphrase. Never published to Goals, search, or public profiles unless
              you explicitly publish through keळ&rsquo;s separate Commons contribution flow.
            </p>
          )}
        </div>

        {!session ? null : discovery === null ? (
          <p className="small dim" style={{ gridColumn: "1 / span 12" }}>Looking for a local keळ project…</p>
        ) : !discovery.available ? (
          <p className="small dim" style={{ gridColumn: "1 / span 12" }}>
            No local keळ process detected on this machine right now.
          </p>
        ) : newlyDiscovered.length === 0 ? (
          <p className="small dim" style={{ gridColumn: "1 / span 12" }}>
            No new local projects to sync. Everything this machine knows about is already synced.
          </p>
        ) : !passphraseStep ? (
          <div style={{ gridColumn: "1 / span 12" }}>
            <ul className="list" style={{ marginTop: 0 }} aria-label="Local projects available to sync">
              {newlyDiscovered.map((p) => (
                <li key={p.project_id}>
                  <label style={{ display: "grid", gridTemplateColumns: "60px 1fr", alignItems: "center", width: "100%", cursor: "pointer" }}>
                    <span className="n">
                      <input type="checkbox" checked={selected.has(p.project_id)} onChange={() => toggleSelected(p.project_id)} />
                    </span>
                    <div>
                      <h3 style={{ fontFamily: "monospace", fontSize: 16 }}>{p.display_hint ?? p.project_id}</h3>
                      <p className="desc">{p.last_local_activity_at ? `active ${timeAgo(p.last_local_activity_at)}` : ""}</p>
                    </div>
                  </label>
                </li>
              ))}
            </ul>
            <button
              type="button" className="btn-ink" style={{ marginTop: 16 }}
              disabled={selected.size === 0}
              onClick={() => setPassphraseStep(true)}
            >
              <span>Sync selected ({selected.size})</span>
            </button>
          </div>
        ) : (
          <form onSubmit={confirmSync} className="signin" style={{ gridColumn: "1 / span 6" }}>
            <h2 className="h3" style={{ marginBottom: 4 }}>Set a sync recovery passphrase</h2>
            <p className="small dim">
              This will connect {selected.size} private local project{selected.size === 1 ? "" : "s"} to your keळ
              account. Your recovery passphrase encrypts them, keळ never sees it, never stores it, and cannot
              recover your data without it. Choose something memorable; losing it (with no synced device left)
              means this data can&rsquo;t be recovered.
            </p>
            <div className="cfield">
              <label htmlFor="sync-passphrase">Recovery passphrase</label>
              <input
                id="sync-passphrase" type="password" required minLength={8} autoFocus
                value={passphrase} onChange={(e) => setPassphrase(e.target.value)}
              />
            </div>
            <div className="cform-submit">
              <button className="btn-ink" type="submit" disabled={syncing}>
                <span>{syncing ? "Syncing…" : "Confirm sync"}</span>
              </button>
              <button type="button" className="link-btn" onClick={() => setPassphraseStep(false)} disabled={syncing}>Cancel</button>
            </div>
            {syncing && (
              <ul className="small dim" style={{ marginTop: 0 }}>
                {Array.from(selected).map((id) => <li key={id}>{id}: {progress[id] ?? "waiting"}</li>)}
              </ul>
            )}
          </form>
        )}

        {syncResult && (
          <div style={{ gridColumn: "1 / span 12" }}>
            {syncResult.succeeded.length > 0 && (
              <p className="small">Synced {syncResult.succeeded.length} project{syncResult.succeeded.length === 1 ? "" : "s"}.</p>
            )}
            {Object.entries(syncResult.failed).map(([id, reason]) => (
              <p key={id} className="small dim">Could not sync {id}: {reason}</p>
            ))}
          </div>
        )}
      </section>

      <section className="frame grid" style={{ paddingBottom: 120, rowGap: 40 }}>
        <div style={{ gridColumn: "1 / span 12" }}>
          <div className="marker caption"><b>ONGOING PROJECTS</b></div>
        </div>

        {projects.kind === "ok" && projects.data.length > 0 ? (
          <ul className="list" aria-label="Synced projects">
            {projects.data.map((p, i) => {
              const synced = timeAgo(p.synced_at);
              return (
                <li key={p.project_id}>
                  <Link href={`/account/projects/${encodeURIComponent(p.project_id)}`}>
                    <span className="n">{String(i + 1).padStart(3, "0")}</span>
                    <div>
                      <h3 style={{ fontFamily: "monospace", fontSize: 16 }}>{p.project_id}</h3>
                      <div className="meta">
                        <span className="status" data-s={p.bootstrapped_at ? "verified" : "unknown"}>
                          {p.bootstrapped_at ? "SYNCED" : "PENDING"}
                        </span>
                        <span>{synced ? `last synced ${synced}` : ""}</span>
                      </div>
                    </div>
                    <span className="caption dim" aria-hidden="true">→</span>
                  </Link>
                </li>
              );
            })}
          </ul>
        ) : (
          <StateNotice
            state={projects}
            empty={projects.kind === "ok" ? "Your local keळ projects will appear here once you sync one." : undefined}
          />
        )}
      </section>
    </>
  );
}
