"use client";
import { useState } from "react";
import { kellCredits, type billingApi } from "@/lib/billing";
import { getSupabase } from "@/lib/supabase";

type Api = Pick<ReturnType<typeof billingApi>, "redeemCode">;

/**
 * A code that gives free usage credit with no payment (a company trial). It states its effect after it is used, from the server's own
 * answer (the amount and the balance), never from a number typed here. The server decides everything: this only sends the code.
 */
export default function FreeCodeRedeem({ api, onRedeemed, initialCode = "", bare = false }: { api: Api; onRedeemed?: () => void; initialCode?: string; bare?: boolean }) {
  const [value, setValue] = useState(initialCode);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [ok, setOk] = useState(false);
  const [step, setStep] = useState<"code" | "phone" | "otp">("code");
  const [phone, setPhone] = useState("");
  const [otp, setOtp] = useState("");

  async function redeem(e: React.FormEvent) {
    e.preventDefault();
    const code = value.trim();
    if (!code || busy) return;
    setBusy(true); setNote(""); setOk(false);
    const r = await api.redeemCode(code);
    setBusy(false);
    if (r.kind === "ok") {
      setOk(true); setValue("");
      setNote(r.data.team ? "You have joined the trial team. Your use is paid from the team's balance, with a small daily limit for each person."
        : `${kellCredits(r.data.creditedUsd)} kell credits were added. Your balance is now ${kellCredits(r.data.remainingUsd)} kell credits.`);
      onRedeemed?.();
    } else if (r.kind === "error" && /verify your phone/i.test(r.message)) {
      setStep("phone"); setNote("This offer needs a confirmed phone number, one account per number. We text you a code to confirm it.");
    } else if (r.kind === "signed-out") setNote("Sign in to use a code.");
    else if (r.kind === "off") setNote("Codes can't be used yet: this isn't switched on.");
    else setNote(r.message);
  }

  // Confirming a phone is Supabase Auth's own phone verification: it texts a one-time code and, once confirmed, the number is on the sign-in.
  async function sendCode(e: React.FormEvent) {
    e.preventDefault();
    const client = getSupabase();
    const number = phone.replace(/[^\d+]/g, "");
    if (!client || !/^\+\d{8,15}$/.test(number)) { setNote("Enter the number with its country code, like +91 98765 43210."); return; }
    setBusy(true); setNote("");
    const { error } = await client.auth.updateUser({ phone: number });
    setBusy(false);
    if (error) setNote(error.message || "We couldn't text that number just now.");
    else { setPhone(number); setStep("otp"); setNote("We sent you a code. Enter it below."); }
  }

  async function confirmCode(e: React.FormEvent) {
    e.preventDefault();
    const client = getSupabase();
    if (!client || !otp.trim()) return;
    setBusy(true); setNote("");
    const { error } = await client.auth.verifyOtp({ phone, token: otp.trim(), type: "phone_change" });
    if (error) { setBusy(false); setNote(error.message || "That code didn't match."); return; }
    await client.auth.refreshSession();                  // the confirmed number is now in the sign-in token the server reads
    setBusy(false); setOtp(""); setStep("code");
    await redeem(e);
  }

  return (
    <div className={bare ? undefined : "profile-card"} aria-labelledby={bare ? undefined : "free-code-title"}>
      {bare ? null : <><h2 className="h3" id="free-code-title">Have a trial code?</h2>
      <p className="small dim">A trial code adds a small amount of free kell credits to your account. No payment is taken.</p></>}
      {step === "phone" ? (
        <form onSubmit={sendCode} style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 10 }}>
          <input value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+91 98765 43210" aria-label="Phone number" inputMode="tel" autoComplete="tel"
            style={{ flex: "1 1 160px", minWidth: 0, minHeight: 44, fontSize: 16 }} />
          <button className="btn-ink" type="submit" disabled={busy || !phone.trim()} style={{ minHeight: 44 }}>{busy ? "Sending…" : "Text me a code"}</button>
        </form>
      ) : step === "otp" ? (
        <form onSubmit={confirmCode} style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 10 }}>
          <input value={otp} onChange={(e) => setOtp(e.target.value)} placeholder="6-digit code" aria-label="Code from the text message" inputMode="numeric" autoComplete="one-time-code"
            style={{ flex: "1 1 160px", minWidth: 0, minHeight: 44, fontSize: 16 }} />
          <button className="btn-ink" type="submit" disabled={busy || !otp.trim()} style={{ minHeight: 44 }}>{busy ? "Checking…" : "Confirm and use code"}</button>
        </form>
      ) : (
      <form onSubmit={redeem} style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 10 }}>
        <input value={value} onChange={(e) => setValue(e.target.value)} placeholder="Your code" aria-label="Trial code" autoCapitalize="characters"
          autoComplete="off" spellCheck={false} style={{ flex: "1 1 160px", minWidth: 0, minHeight: 44, fontSize: 16 }} />
        <button className="btn-ink" type="submit" disabled={busy || !value.trim()} style={{ minHeight: 44 }}>{busy ? "Checking…" : "Use code"}</button>
      </form>
      )}
      {note ? <p className="small" role="status" style={{ marginTop: 10 }}>{note}</p> : null}
    </div>
  );
}
