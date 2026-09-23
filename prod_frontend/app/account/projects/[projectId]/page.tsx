"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import StateNotice from "@/components/NotConnected";
import { getMyStealthProject, unsyncStealthProject, timeAgo, type StealthProjectDetail, type DecryptedProjectContent } from "@/lib/kel-api";
import { getSession, type Session } from "@/lib/session";
import { decryptJson, deriveRecoveryKek, unwrapProjectKey } from "@/lib/sync-crypto";
import { getCachedProjectKey, setCachedProjectKey } from "@/lib/sync-key-cache";
import { enableLocalSyncOnThisDevice, type EnableLocalSyncResult } from "@/lib/sync";
import type { ApiState } from "@/lib/api";

// The browser fetches CIPHERTEXT from the REST API and decrypts it
// entirely client-side (docs/local_project_sync_security.md §D) -- the
// decrypted content below NEVER goes back through any API call.
type DecryptState =
  | { kind: "need-passphrase" }
  | { kind: "decrypting" }
  | { kind: "wrong-passphrase" }
  | { kind: "no-ciphertext-yet" }
  | { kind: "ok"; data: DecryptedProjectContent };

export default function StealthProjectDetailPage() {
  const { projectId } = useParams<{ projectId: string }>();
  const router = useRouter();
  const [session, setSession] = useState<Session | null | undefined>(undefined);
  const [project, setProject] = useState<ApiState<StealthProjectDetail>>({ kind: "loading" });
  const [decrypted, setDecrypted] = useState<DecryptState>({ kind: "need-passphrase" });
  const [passphrase, setPassphrase] = useState("");
  const [unsyncing, setUnsyncing] = useState(false);
  const [enableLocalSync, setEnableLocalSync] = useState<EnableLocalSyncResult | "checking" | null>(null);

  useEffect(() => { getSession().then(setSession); }, []);

  useEffect(() => {
    if (session === undefined) return;
    if (!session) {
      setProject({ kind: "unauthenticated" });
      return;
    }
    const ac = new AbortController();
    getMyStealthProject(projectId, ac.signal).then(setProject);
    return () => ac.abort();
  }, [session, projectId]);

  useEffect(() => {
    if (project.kind !== "ok") return;
    const p = project.data;
    if (!p.ciphertext_base64 || !p.wrapped_p_dek) {
      setDecrypted({ kind: "no-ciphertext-yet" });
      return;
    }
    const cached = getCachedProjectKey(projectId);
    if (cached) {
      setDecrypted({ kind: "decrypting" });
      decryptJson<DecryptedProjectContent>(cached, p.ciphertext_base64)
        .then((data) => setDecrypted({ kind: "ok", data }))
        .catch(() => setDecrypted({ kind: "need-passphrase" }));
    } else {
      setDecrypted({ kind: "need-passphrase" });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project, projectId]);

  async function unlock(e: React.FormEvent) {
    e.preventDefault();
    if (project.kind !== "ok" || !project.data.wrapped_p_dek || !project.data.recovery_salt || !project.data.ciphertext_base64) return;
    setDecrypted({ kind: "decrypting" });
    try {
      const { kek } = await deriveRecoveryKek(passphrase, project.data.recovery_salt);
      const pDek = await unwrapProjectKey(project.data.wrapped_p_dek, kek);
      const data = await decryptJson<DecryptedProjectContent>(pDek, project.data.ciphertext_base64);
      setCachedProjectKey(projectId, pDek);
      setDecrypted({ kind: "ok", data });
      setPassphrase("");
    } catch {
      setDecrypted({ kind: "wrong-passphrase" });
    }
  }

  async function handleUnsync() {
    if (!window.confirm("Stop syncing this project and remove its synchronized copy from your keळ account?")) return;
    setUnsyncing(true);
    const result = await unsyncStealthProject(projectId);
    setUnsyncing(false);
    if (result.kind === "ok") {
      router.push("/account");
      router.refresh();
    }
  }

  // New-device follow-on: once content is decrypted here (via the cache
  // or a freshly-entered recovery passphrase), offer to also hand this
  // device's local MCP process the P-DEK, if it happens to have its own
  // checkout of this SAME project — so ongoing local sync starts working
  // from here too, without re-deriving or re-uploading anything.
  async function handleEnableLocalSync() {
    const key = getCachedProjectKey(projectId);
    if (!key) return;
    setEnableLocalSync("checking");
    const result = await enableLocalSyncOnThisDevice(projectId, key);
    setEnableLocalSync(result);
  }

  if (project.kind !== "ok") {
    return (
      <section className="frame grid" style={{ paddingTop: 160, paddingBottom: 120 }}>
        <StateNotice
          state={project}
          empty={project.kind === "error" && project.status === 404 ? "No such local project, or it isn't yours." : undefined}
        />
      </section>
    );
  }

  const p = project.data;
  const synced = timeAgo(p.synced_at);

  return (
    <>
      <section className="page-hero frame grid">
        <div className="marker caption" style={{ gridColumn: "1 / -1" }}>
          <Link href="/account" style={{ textDecoration: "underline" }}>ACCOUNT</Link> — PROJECT
        </div>
        <h1 className="display" style={{ fontFamily: "monospace", fontSize: "clamp(28px, 4vw, 44px)" }}>{p.project_id}</h1>
        <p className="lead">
          {p.bootstrapped_at ? "Backed up to your account" : "Synced — not yet backed up"}
          {synced ? ` — synced ${synced}` : ""}
        </p>
      </section>

      <section className="frame grid" style={{ paddingBottom: 120, rowGap: 40 }}>
        {decrypted.kind === "need-passphrase" || decrypted.kind === "wrong-passphrase" ? (
          <div className="signin" style={{ gridColumn: "1 / span 6" }}>
            <h2 className="h3" style={{ marginBottom: 4 }}>Enter your sync recovery passphrase</h2>
            <p className="small dim">
              This project&rsquo;s contents are end-to-end encrypted — decryption happens only in your browser,
              using the recovery passphrase you set when you first synced it. keळ never sees the decrypted content.
            </p>
            <form onSubmit={unlock} className="cform" style={{ gridColumn: "auto", gap: 16 }}>
              <div className="cfield">
                <label htmlFor="passphrase">Recovery passphrase</label>
                <input
                  id="passphrase" type="password" value={passphrase} required autoFocus
                  onChange={(e) => setPassphrase(e.target.value)}
                />
              </div>
              <div className="cform-submit">
                <button className="btn-ink" type="submit"><span>Unlock</span></button>
                {decrypted.kind === "wrong-passphrase" && <span className="small dim">That passphrase didn&rsquo;t work.</span>}
              </div>
            </form>
          </div>
        ) : decrypted.kind === "decrypting" ? (
          <div className="empty" style={{ gridColumn: "1 / span 12" }}><p>Decrypting locally…</p></div>
        ) : decrypted.kind === "no-ciphertext-yet" ? (
          <div className="empty" style={{ gridColumn: "1 / span 12" }}>
            <p>Claimed, not yet backed up — no encrypted content is available for this project yet.</p>
          </div>
        ) : (
          <>
            <div style={{ gridColumn: "1 / span 12" }}>
              <div className="marker caption"><b>FILES</b></div>
            </div>
            {Object.keys(decrypted.data.files).length > 0 ? (
              <div style={{ gridColumn: "1 / span 12", display: "flex", flexDirection: "column", gap: 12 }}>
                {Object.entries(decrypted.data.files).map(([name, content]) => (
                  <details key={name} style={{ border: "1px solid var(--rule-strong)", padding: "12px 16px" }}>
                    <summary style={{ fontFamily: "monospace", fontSize: 16, cursor: "pointer" }}>{name}</summary>
                    <pre className="mono small" style={{ marginTop: 12, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                      {content}
                    </pre>
                  </details>
                ))}
              </div>
            ) : (
              <div className="empty" style={{ gridColumn: "1 / span 12" }}><p>No workspace files were present at sync time.</p></div>
            )}

            <div style={{ gridColumn: "1 / span 12" }}>
              <div className="marker caption"><b>ACTIVITY</b></div>
            </div>
            {decrypted.data.activity.length > 0 ? (
              <ul className="list" style={{ marginTop: 0, gridColumn: "1 / span 12" }} aria-label="Project activity">
                {decrypted.data.activity.map((a, i) => (
                  <li key={`${a.timestamp}-${i}`}>
                    <span className="n" style={{ fontFamily: "monospace", fontSize: 14 }}>{a.file_path}</span>
                    <div>
                      <h3 style={{ fontSize: 16 }}>{a.summary}</h3>
                      <p className="desc">{a.timestamp ? new Date(a.timestamp).toLocaleString() : "—"}</p>
                    </div>
                  </li>
                ))}
              </ul>
            ) : (
              <div className="empty" style={{ gridColumn: "1 / span 12" }}><p>No activity recorded for this project yet.</p></div>
            )}

            {enableLocalSync === null ? (
              <div style={{ gridColumn: "1 / span 12" }}>
                <button type="button" className="link-btn" onClick={handleEnableLocalSync}>
                  Enable ongoing sync from this device
                </button>
              </div>
            ) : (
              <p className="small dim" style={{ gridColumn: "1 / span 12" }}>
                {enableLocalSync === "checking" && "Checking for a local keळ project on this device…"}
                {enableLocalSync === "enabled" && "Ongoing sync enabled from this device."}
                {enableLocalSync === "no_local_bridge" && "No local keळ process detected on this device."}
                {enableLocalSync === "not_found_on_this_machine" && "This device doesn't have a local checkout of this project."}
                {enableLocalSync === "error" && "Couldn't enable local sync on this device — try again later."}
              </p>
            )}
          </>
        )}

        <div style={{ gridColumn: "1 / span 12", marginTop: 20 }}>
          <button type="button" className="link-btn" onClick={handleUnsync} disabled={unsyncing}>
            {unsyncing ? "Unsyncing…" : "Unsync this project"}
          </button>
          <p className="small dim" style={{ marginTop: 4 }}>
            Removes the encrypted copy from your account. Your local files are never touched.
          </p>
        </div>
      </section>
    </>
  );
}
