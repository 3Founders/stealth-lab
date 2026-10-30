"use client";
import { Suspense, useCallback, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Hero } from "@/components/Admin";
import { enrollTotp, mfaRequired, mfaState, nextStep, removeFactor, verifyTotp, type MfaState } from "@/lib/mfa";
import { getSession, isSafeRedirectPath } from "@/lib/session";

/** Second-factor sign-in: enrol an authenticator app, then enter its code. Required for billing when the deployment says so. */
function Inner() {
  const router = useRouter();
  const params = useSearchParams();
  const raw = params.get("redirect");
  const back = isSafeRedirectPath(raw) ? raw : "/";
  const [signedIn, setSignedIn] = useState<boolean | undefined>(undefined);
  const [state, setState] = useState<MfaState | null>(null);
  const [enrol, setEnrol] = useState<{ id: string; qr: string; secret: string } | null>(null);
  const [code, setCode] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => { setSignedIn(Boolean(await getSession())); setState(await mfaState()); }, []);
  useEffect(() => { void load(); }, [load]);

  const verified = state?.factors.filter((f) => f.status === "verified") ?? [];
  const step = state ? nextStep(state) : null;

  async function start() {
    setBusy(true); setMsg("");
    const r = await enrollTotp();
    setBusy(false);
    if ("error" in r) setMsg(r.error); else setEnrol(r);
  }
  async function confirm(factorId: string, finishing: boolean) {
    setBusy(true); setMsg("");
    const err = await verifyTotp(factorId, code);
    setBusy(false);
    if (err) { setMsg(err); return; }
    setCode(""); setEnrol(null);
    await load();
    if (!finishing || mfaRequired()) router.push(back);
  }
  async function remove(id: string) {
    if (!window.confirm("Remove this authenticator? You will need to enrol again to open billing.")) return;
    const err = await removeFactor(id);
    setMsg(err ?? "Removed.");
    await load();
  }

  return (
    <>
      <Hero marker="SECURITY" title="Second factor" lead="Sign in to the admin console with a password and a code from an authenticator app. Billing needs this when it is switched on for this deployment." />
      <section className="frame grid" style={{ paddingBottom: 120 }}>
        <div className="cform profile-card" style={{ maxWidth: 560 }}>
          {signedIn === false && <p className="small" role="status">Sign in first, then come back here.</p>}
          {signedIn && state && !state.configured && <p className="small" role="alert">Sign-in isn&rsquo;t configured for this site.</p>}
          {signedIn && state?.configured && (
            <>
              <p className="small">Status: <b>{state.current === "aal2" ? "second factor passed for this session" : step === "verify" ? "enter your code" : "no authenticator yet"}</b>. {mfaRequired() ? "This console requires a second factor." : "This console does not require a second factor yet."}</p>
              {step === "verify" && verified[0] && (
                <form onSubmit={(e) => { e.preventDefault(); void confirm(verified[0].id, false); }} className="cform" style={{ gridColumn: "auto" }}>
                  <div className="cfield"><label htmlFor="code">Code from your authenticator app</label><input id="code" inputMode="numeric" autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value)} maxLength={8} autoFocus /></div>
                  <div className="cluster"><button className="btn-ink" type="submit" disabled={busy || code.trim().length < 6}>{busy ? "Checking…" : "Continue"}</button></div>
                </form>
              )}
              {step === "enroll" && !enrol && <div className="cluster"><button type="button" className="btn-ink" onClick={() => void start()} disabled={busy}>Set up an authenticator</button></div>}
              {enrol && (
                <form onSubmit={(e) => { e.preventDefault(); void confirm(enrol.id, true); }} className="cform" style={{ gridColumn: "auto" }}>
                  <p className="small">Scan this with your authenticator app, or type the key by hand. Then enter the 6-digit code it shows.</p>
                  {/* eslint-disable-next-line @next/next/no-img-element -- an SVG data URL from the auth service */}
                  <img src={enrol.qr} alt="QR code for your authenticator app" width={180} height={180} style={{ background: "#fff", padding: 8, borderRadius: 8 }} />
                  <p className="small">Key: <code>{enrol.secret}</code></p>
                  <div className="cfield"><label htmlFor="code2">Code</label><input id="code2" inputMode="numeric" autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value)} maxLength={8} /></div>
                  <div className="cluster"><button className="btn-ink" type="submit" disabled={busy || code.trim().length < 6}>{busy ? "Checking…" : "Turn on"}</button></div>
                </form>
              )}
              {verified.length > 0 && state.current === "aal2" && (
                <ul className="small" style={{ paddingLeft: 18 }}>
                  {verified.map((f) => <li key={f.id}>{f.name ?? "Authenticator"} <button type="button" className="btn-line" onClick={() => void remove(f.id)}>Remove</button></li>)}
                </ul>
              )}
              <p className="small dim">There are no recovery codes: if you lose your authenticator, another admin must remove your factor. Keep at least two admins. Security keys and passkeys are not offered yet.</p>
            </>
          )}
          {msg && <p className="small" role="status">{msg}</p>}
        </div>
      </section>
    </>
  );
}

export default function Security() {
  return <Suspense fallback={null}><Inner /></Suspense>;
}
