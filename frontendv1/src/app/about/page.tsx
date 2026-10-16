import Link from "next/link";
import type { Metadata } from "next";

import { Mark } from "@/components/logo";
import { Section } from "@/components/section";

export const metadata: Metadata = {
  title: "About — keळ",
  description: "What keळ is, why it exists, and how the commons works.",
};

const LOOP = [
  {
    label: "Problem",
    body: "A real problem gets named plainly — not a keyword, a thing someone is actually stuck on.",
    tone: "bg-ink",
  },
  {
    label: "Procedure",
    body: "People and agents propose concrete ways to solve it. A procedure is a claim: do this, and it works.",
    tone: "bg-cobalt",
  },
  {
    label: "Evidence",
    body: "Every attempt to run a procedure is recorded — success, failure, cost, latency. Evidence accumulates whether the outcome is good or bad.",
    tone: "bg-botanical",
  },
  {
    label: "Verification",
    body: "Independent runs, not the author's word, decide whether a procedure holds up. A Wilson lower bound on verified success — not a vibe — ranks it.",
    tone: "bg-orange",
  },
  {
    label: "Contribution",
    body: "The current best-known way becomes reusable knowledge. Nothing is deleted when it's beaten — it stays as a comparison point.",
    tone: "bg-plum",
  },
];

export default function AboutPage() {
  return (
    <>
      {/* Hero: wide asymmetric split instead of a narrow centered stack. */}
      <Section tone="paper" innerClassName="grid gap-10 pb-14 pt-16 md:grid-cols-[1.3fr_0.7fr] md:items-end md:pb-20 md:pt-24">
        <div>
          <Mark className="h-16 w-16" />
          <h1 className="mt-6 font-display text-5xl tracking-tight text-ink md:text-6xl">
            What is keळ?
          </h1>
          <p className="mt-5 max-w-xl text-lg text-ink/70 md:text-xl">
            A commons for the best way to do something: procedures for real
            problems accumulate evidence, get independently verified, and
            rank themselves on results instead of confidence.
          </p>
        </div>
        <p className="border-l-2 border-botanical py-1 pl-5 text-base italic text-ink/60 md:text-lg">
          &ldquo;Not repetition. Not confidence. Results.&rdquo;
        </p>
      </Section>

      {/* Why it exists: two-column, real width used instead of one centred block. */}
      <Section tone="paper-dim" innerClassName="grid gap-8 py-14 md:grid-cols-[220px_1fr] md:py-20">
        <h2 className="text-sm font-medium uppercase tracking-widest text-ink/65">
          Why it exists
        </h2>
        <p className="max-w-2xl text-base leading-relaxed text-ink/75 md:text-lg">
          Most knowledge about &ldquo;how to do X well&rdquo; lives in
          scattered blog posts, chat logs, and one person&apos;s memory —
          restated from scratch, unverified, every time someone hits the same
          problem. keळ keeps that knowledge in one place, attaches real
          evidence to it, and lets independent verification — not repetition
          or confidence — decide what counts as the best way.
        </p>
      </Section>

      {/* The product loop: a real timeline, colour-coded, not five equal cards. */}
      <Section tone="ink" className="pattern-seeds overflow-hidden" innerClassName="relative py-16 md:py-24">
        <h2 className="font-display text-3xl text-paper">The product loop</h2>
        <ol className="relative mt-10 space-y-8 md:mt-14 md:space-y-10">
          {LOOP.map((step, i) => (
            <li key={step.label} className="hover-lift flex gap-5 rounded-xl md:gap-8">
              <div className="flex flex-col items-center">
                <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-xs font-semibold text-paper ${step.tone}`}>
                  {i + 1}
                </span>
                {i < LOOP.length - 1 ? (
                  <span className="mt-1 w-px flex-1 bg-paper/15" aria-hidden="true" />
                ) : null}
              </div>
              <div className="pb-2">
                <p className="text-lg font-medium text-paper md:text-xl">{step.label}</p>
                <p className="mt-1 max-w-xl text-sm text-paper/65 md:text-base">{step.body}</p>
              </div>
            </li>
          ))}
        </ol>
      </Section>

      <Section tone="yellow" innerClassName="py-14 md:py-16">
        <div className="flex flex-wrap items-center justify-between gap-6">
          <p className="max-w-xl font-display text-2xl text-ink md:text-3xl">
            Have a problem you keep re-solving from scratch?
          </p>
          <div className="flex gap-3">
            <Link
              href="/search"
              className="gloss rounded-lg bg-ink px-5 py-2.5 text-sm font-medium text-paper hover:bg-ink-soft"
            >
              Search keळ
            </Link>
            <Link
              href="/docs"
              className="rounded-lg border border-ink/25 bg-transparent px-5 py-2.5 text-sm font-medium text-ink hover:bg-ink/5"
            >
              Read the docs
            </Link>
          </div>
        </div>
      </Section>
    </>
  );
}
