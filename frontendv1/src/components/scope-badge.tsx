/**
 * Canonical scope labels (launch compliance LC-010 / INV-10). Every
 * procedure surface shows exactly one of:
 *   PRIVATE · ORGANIZATION · GLOBAL CANDIDATE · GLOBAL VERIFIED
 * derived from the backend's `visibility` + `verification_state` — the
 * backend stays authoritative; this is presentation only, never a
 * similarity score standing in for authorization.
 */
import type { ReactNode } from "react";

export interface ScopeInput {
  visibility?: string | null;
  verification_state?: string | null;
}

type Tone = "amber" | "violet" | "sky" | "emerald";

const TONE: Record<Tone, string> = {
  amber: "bg-amber-50 text-amber-800 ring-amber-200",
  violet: "bg-violet-50 text-violet-800 ring-violet-200",
  sky: "bg-sky-50 text-sky-800 ring-sky-200",
  emerald: "bg-emerald-50 text-emerald-800 ring-emerald-200",
};

export function scopeOf({ visibility, verification_state }: ScopeInput): {
  label: string;
  tone: Tone;
  hint: string;
} {
  const v = (visibility ?? "").toLowerCase();
  if (v === "private")
    return {
      label: "PRIVATE",
      tone: "amber",
      hint: "Only you can see this. It never becomes public unless you explicitly publish it.",
    };
  if (v === "org" || v === "organization")
    return {
      label: "ORGANIZATION",
      tone: "violet",
      hint: "Visible to authorized members of your organization.",
    };
  const verified = (verification_state ?? "").toLowerCase() === "verified";
  return verified
    ? {
        label: "GLOBAL VERIFIED",
        tone: "emerald",
        hint: "In the public commons, with independent execution evidence.",
      }
    : {
        label: "GLOBAL CANDIDATE",
        tone: "sky",
        hint: "Published to the public commons, not yet independently verified.",
      };
}

export function ScopeBadge({
  visibility,
  verification_state,
  withHint = false,
}: ScopeInput & { withHint?: boolean }): ReactNode {
  const s = scopeOf({ visibility, verification_state });
  return (
    <span className="inline-flex items-center gap-2">
      <span
        className={`rounded px-1.5 py-0.5 text-[11px] font-medium ring-1 ${TONE[s.tone]}`}
      >
        {s.label}
      </span>
      {withHint ? (
        <span className="text-xs text-neutral-500">{s.hint}</span>
      ) : null}
    </span>
  );
}
