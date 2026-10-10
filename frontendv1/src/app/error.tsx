"use client";

import { useEffect } from "react";

import { Mark } from "@/components/logo";
import { Section } from "@/components/section";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Surfaced to the console for now; wire to real telemetry when it exists.
    console.error(error);
  }, [error]);

  return (
    <Section tone="paper" innerClassName="flex min-h-[60vh] flex-col items-center justify-center py-24 text-center">
      <Mark className="h-16 w-16 opacity-70" />
      <h1 className="mt-6 font-display text-3xl tracking-tight text-ink">
        Something broke on this page.
      </h1>
      <p className="mt-2 max-w-sm text-sm text-ink/70">
        Not your fault. Try again, or come back in a moment.
        {error.digest ? (
          <span className="mt-1 block font-mono text-xs text-ink/65">
            ref {error.digest}
          </span>
        ) : null}
      </p>
      <button
        type="button"
        onClick={() => reset()}
        className="mt-6 rounded-lg bg-ink px-5 py-2.5 text-sm font-medium text-paper hover:bg-ink-soft"
      >
        Try again
      </button>
    </Section>
  );
}
