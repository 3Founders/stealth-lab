"use client";
import { useEffect, useRef, useState } from "react";
import type { billingApi } from "@/lib/billing";
import type { Plan } from "@/lib/plans";
import { forgetCode, recallCode, rememberCode } from "@/lib/referral";

import FreeCodeRedeem from "@/components/billing/FreeCodeRedeem";
import { kellCredits } from "@/lib/billing";

type Api = Pick<ReturnType<typeof billingApi>, "checkCode" | "redeemCode">;

/**
 * The referral code, above the plans: a person enters it, the server says whether it is active, and when it is the Plans
 * section below shows exactly one card - the one-seat (individual) Pilot the code unlocks. Built to docs/billing.md and the
 * legal briefing (docs/referral_legality_2026-11.md):
 *  - the code's exact effect is stated before it is used: which plan, its price, its period and the usage included. They come
 *    from the plan the page is showing (the server's current numbers, or the built-in copy while billing is off), never typed here;
 *  - nothing here unlocks anything by itself: the server checks the code again when the order is made, and a stored code is
 *    only a convenience so the choice survives signing in and back;
 *  - no countdown, no scarcity, no crossed-out price, no claim that the code is a saving against another plan.
 * A code applied in this browser is remembered (lib/referral.ts) and handed to the page on mount.
 */
export default function ReferralCode({ api, pilot, onApplied, onCleared }: {
  api: Api; pilot: Plan | null; onApplied: (code: string) => void; onCleared: () => void;
}) {
  const [value, setValue] = useState("");
  const [applied, setApplied] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [free, setFree] = useState<{ code: string; creditUsd: string; team: boolean; placesLeft: number | null } | null>(null);
  const live = useRef(true);
  useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);

  // A code applied here before is applied again after a sign-in round trip (the page is rebuilt and the field would be empty).
  useEffect(() => {
    const saved = recallCode();
    if (saved) { setApplied(saved); onApplied(saved); }
  }, [onApplied]);

  async function apply(e?: React.FormEvent) {
    e?.preventDefault();
    const code = value.trim();
    if (!code || busy) return;
    setBusy(true); setNote("");
    const r = await api.checkCode(code);
    if (!live.current) return;
    setBusy(false);
    if (r.kind === "free") {
      // a free-trial code has its OWN offer: it never shows (or unlocks) the paid plan, and is not remembered as a plan code
      setFree({ code: code.toUpperCase(), creditUsd: r.creditUsd, team: r.team, placesLeft: r.placesLeft }); setValue("");
    } else if (r.kind === "valid") {
      const c = code.toUpperCase();
      rememberCode(c); setApplied(c); setValue(""); onApplied(c);
    } else if (r.kind === "off") {
      setNote("We can't check codes yet: buying plans is not switched on. Nothing is charged.");
    } else {
      setNote(r.message);
    }
  }

  function remove() {
    forgetCode(); setApplied(null); setNote(""); onCleared();
  }

  return (
    <div className="profile-card" aria-labelledby="code-title">
      <h2 className="h3" id="code-title">Have a code?</h2>

      {free ? (
        <div style={{ display: "grid", gap: 8, marginTop: 10 }}>
          <p className="small" role="status">
            <b>Code {free.code}: a free trial.</b>{" "}
            {free.team
              ? <>It puts you in a trial team, paid from the team&rsquo;s balance with a small daily limit for each person. No payment is taken.</>
              : <>It adds {kellCredits(free.creditUsd)} kell credits to your account, free. No payment is taken.</>}
            {free.placesLeft !== null ? <> {free.placesLeft} {free.placesLeft === 1 ? "place is" : "places are"} left.</> : null}
          </p>
          <FreeCodeRedeem api={api} initialCode={free.code} bare />
          <div><button type="button" className="btn-line" onClick={() => setFree(null)}><span>Use a different code</span></button></div>
        </div>
      ) : applied ? (
        <div style={{ display: "grid", gap: 8, marginTop: 10 }}>
          <p className="small" role="status">
            <b>Code {applied} applied.</b>{" "}
            {pilot
              ? <>The plans below show the one plan it unlocks: {pilot.name}, {pilot.price} {pilot.per}.</>
              : <>The plan it unlocks is not on sale right now.</>}
          </p>
          <div><button type="button" className="btn-line" onClick={remove}><span>Remove code</span></button></div>
        </div>
      ) : (
        <form className="cform" onSubmit={apply} style={{ marginTop: 10 }}>
          <div className="cfield">
            <label htmlFor="referral-code">Your code</label>
            <input id="referral-code" type="text" value={value} onChange={(e) => setValue(e.target.value)}
              placeholder="The code you were given" autoComplete="off" spellCheck={false} maxLength={32} />
          </div>
          <div className="cform-submit">
            <button className="btn-ink" type="submit" disabled={busy || !value.trim()}>
              <span>{busy ? "Checking…" : "Apply"}</span>
            </button>
          </div>
          {note && <p className="small" role="status">{note}</p>}
        </form>
      )}
    </div>
  );
}
