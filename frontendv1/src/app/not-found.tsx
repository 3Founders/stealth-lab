import Link from "next/link";

import { Mark } from "@/components/logo";
import { Section } from "@/components/section";

export default function NotFound() {
  return (
    <Section tone="paper" innerClassName="flex min-h-[60vh] flex-col items-center justify-center py-24 text-center">
      <Mark className="h-16 w-16 opacity-25 grayscale" />
      <h1 className="mt-6 font-display text-3xl tracking-tight text-ink">
        Nothing here.
      </h1>
      <p className="mt-2 max-w-sm text-sm text-ink/70">
        This page doesn&apos;t exist, or it moved. It&apos;s not a sign the
        thing you&apos;re looking for isn&apos;t in the commons — try search.
      </p>
      <div className="mt-6 flex gap-3">
        <Link
          href="/search"
          className="rounded-lg bg-ink px-5 py-2.5 text-sm font-medium text-paper hover:bg-ink-soft"
        >
          Search keळ
        </Link>
        <Link
          href="/"
          className="rounded-lg border border-ink/15 bg-paper px-5 py-2.5 text-sm text-ink hover:bg-paper-dim"
        >
          Go home
        </Link>
      </div>
    </Section>
  );
}
