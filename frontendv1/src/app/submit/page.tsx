"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { submitDecomposition } from "@/lib/api/client";
import {
  createProcedure,
  createProcedureFromText,
  type CreatedProcedure,
} from "@/lib/api/contribute";
import { getAuth, onAuthChange } from "@/lib/auth";
import { ScopeBadge } from "@/components/scope-badge";
import type { DecomposeResponse } from "@/lib/api/types";

type Tab = "quick" | "propose";
type QuickMode = "fields" | "paste";

// ---------------------------------------------------------------- Quick add

function QuickAdd({ initial }: { initial: string }) {
  const [signedIn, setSignedIn] = useState(false);
  const [mode, setMode] = useState<QuickMode>("fields");
  const [name, setName] = useState("");
  const [goal, setGoal] = useState(initial);
  const [steps, setSteps] = useState("");
  const [applicability, setApplicability] = useState("");
  const [paste, setPaste] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<CreatedProcedure | null>(null);

  useEffect(() => {
    const sync = () => setSignedIn(Boolean(getAuth()));
    sync();
    return onAuthChange(sync);
  }, []);

  const lines = (s: string) =>
    s.split("\n").map((x) => x.trim()).filter(Boolean);

  async function submit() {
    setBusy(true);
    setError(null);
    setCreated(null);
    try {
      const result =
        mode === "paste"
          ? await createProcedureFromText(paste, { name: name || undefined })
          : await createProcedure({
              name,
              goal,
              steps: lines(steps),
              applicability: lines(applicability),
            });
      setCreated(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save.");
    } finally {
      setBusy(false);
    }
  }

  if (!signedIn) {
    return (
      <p className="mt-6 rounded-lg border border-neutral-200 bg-neutral-50 px-4 py-3 text-sm text-neutral-600">
        <Link href="/auth" className="font-medium underline">
          Sign in
        </Link>{" "}
        to add a procedure to your private library. It is yours only — nothing
        becomes public until you explicitly publish it.
      </p>
    );
  }

  if (created) {
    return (
      <div className="mt-6 space-y-3 rounded-lg border border-emerald-200 bg-emerald-50 p-5 text-sm">
        <p className="font-medium text-emerald-800">
          Saved to your private library.
        </p>
        <p className="text-emerald-700">
          <ScopeBadge visibility="private" verification_state="candidate" />{" "}
          <span className="ml-1 text-xs">candidate · not verified · not public</span>
        </p>
        <div className="flex gap-3 pt-1">
          <Link
            href={`/procedures/${created.id}`}
            className="rounded-lg bg-neutral-900 px-4 py-2 text-xs font-medium text-neutral-50"
          >
            View it
          </Link>
          <button
            type="button"
            onClick={() => {
              setCreated(null);
              setName("");
              setGoal("");
              setSteps("");
              setApplicability("");
              setPaste("");
            }}
            className="rounded-lg border border-neutral-200 bg-white px-4 py-2 text-xs text-neutral-700"
          >
            Add another
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="mt-6 space-y-3">
      <div className="flex gap-1 text-xs">
        {(["fields", "paste"] as const).map((m) => (
          <button
            key={m}
            type="button"
            onClick={() => setMode(m)}
            className={`rounded-md px-3 py-1.5 ${
              mode === m
                ? "bg-neutral-900 text-neutral-50"
                : "bg-neutral-100 text-neutral-600"
            }`}
          >
            {m === "fields" ? "Fields" : "Paste a doc"}
          </button>
        ))}
      </div>

      {mode === "fields" ? (
        <>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Name — e.g. Zero-downtime Postgres column rename"
            maxLength={200}
            className="w-full rounded-lg border border-neutral-200 bg-white px-4 py-2.5 text-sm outline-none focus:border-neutral-300 focus:ring-[3px] focus:ring-neutral-950/5"
          />
          <textarea
            value={goal}
            onChange={(e) => setGoal(e.target.value)}
            rows={2}
            placeholder="Goal — what it accomplishes and when to use it"
            maxLength={4000}
            className="w-full rounded-lg border border-neutral-200 bg-white px-4 py-2.5 text-sm outline-none focus:border-neutral-300 focus:ring-[3px] focus:ring-neutral-950/5"
          />
          <textarea
            value={steps}
            onChange={(e) => setSteps(e.target.value)}
            rows={5}
            placeholder="Steps — one per line"
            className="w-full rounded-lg border border-neutral-200 bg-white px-4 py-2.5 font-mono text-xs outline-none focus:border-neutral-300 focus:ring-[3px] focus:ring-neutral-950/5"
          />
          <textarea
            value={applicability}
            onChange={(e) => setApplicability(e.target.value)}
            rows={2}
            placeholder="When it applies — one condition per line (optional)"
            className="w-full rounded-lg border border-neutral-200 bg-white px-4 py-2.5 text-sm outline-none focus:border-neutral-300 focus:ring-[3px] focus:ring-neutral-950/5"
          />
        </>
      ) : (
        <>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Name (optional — taken from the doc's frontmatter if omitted)"
            maxLength={200}
            className="w-full rounded-lg border border-neutral-200 bg-white px-4 py-2.5 text-sm outline-none focus:border-neutral-300 focus:ring-[3px] focus:ring-neutral-950/5"
          />
          <textarea
            value={paste}
            onChange={(e) => setPaste(e.target.value)}
            rows={12}
            placeholder={
              "Paste a SKILL.md-style doc:\n\n---\nname: my-skill\n---\nUse when ...\n\n## Steps\n1. ...\n2. ..."
            }
            className="w-full rounded-lg border border-neutral-200 bg-white px-4 py-3 font-mono text-xs outline-none focus:border-neutral-300 focus:ring-[3px] focus:ring-neutral-950/5"
          />
        </>
      )}

      <div className="flex items-center gap-3">
        <button
          type="button"
          disabled={
            busy ||
            (mode === "fields" ? !name.trim() || !goal.trim() : !paste.trim())
          }
          onClick={() => void submit()}
          className="h-9 rounded-lg bg-neutral-900 px-5 text-sm font-medium text-neutral-50 hover:bg-neutral-800 disabled:opacity-50"
        >
          {busy ? "Saving…" : "Add to my library"}
        </button>
        <span className="text-xs text-neutral-400">
          Private · saved instantly · indexed for search
        </span>
      </div>

      {error ? (
        <p className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
          {error}
        </p>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------- Propose (reviewed)

function ProposeToCommons({ initial }: { initial: string }) {
  const [problem, setProblem] = useState(initial);
  const [result, setResult] = useState<DecomposeResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    const p = problem.trim();
    if (!p) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      setResult(await submitDecomposition(p));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Submission failed.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <p className="mt-2 text-sm text-neutral-500">
        Describe a problem or a method. The backend drafts a structured
        proposal that stays quarantined until a human approves it — it never
        enters the shared commons automatically.
      </p>

      <div className="mt-4 rounded-lg border border-neutral-200 bg-neutral-50 p-4 text-xs text-neutral-600">
        <p className="font-medium text-neutral-800">
          What proposing to the commons does
        </p>
        <ul className="mt-2 space-y-1">
          <li>
            • Visibility changes:{" "}
            <ScopeBadge visibility="private" verification_state="candidate" /> →{" "}
            <ScopeBadge visibility="public" verification_state="candidate" />.
          </li>
          <li>• It runs the publication gate: provenance, license, and privacy/secret checks.</li>
          <li>
            • Your <span className="font-medium">private execution history and
            evidence stay private</span> — a Global Candidate starts with zero
            independent verification and earns it from other people&apos;s runs.
          </li>
          <li>• Nothing is published automatically; a human approves each proposal.</li>
        </ul>
      </div>
      <form onSubmit={onSubmit} className="mt-6 space-y-3">
        <textarea
          value={problem}
          onChange={(e) => setProblem(e.target.value)}
          rows={5}
          required
          maxLength={20000}
          aria-label="Problem or method description"
          placeholder="e.g. How do I safely rename a Postgres column in a large table without downtime?"
          className="w-full rounded-lg border border-neutral-200 bg-white px-4 py-3 text-sm text-neutral-900 placeholder:text-neutral-400 outline-none transition-colors focus:border-neutral-300 focus:ring-[3px] focus:ring-neutral-950/5"
        />
        <div className="flex items-center gap-3">
          <button
            type="submit"
            disabled={loading || !problem.trim()}
            className="h-9 rounded-lg bg-neutral-900 px-5 text-sm font-medium text-neutral-50 transition-colors hover:bg-neutral-800 disabled:opacity-50"
          >
            {loading ? "Analyzing…" : "Submit proposal"}
          </button>
          {loading ? (
            <span className="text-sm text-neutral-400">
              This can take up to a minute.
            </span>
          ) : null}
        </div>
      </form>

      {error ? (
        <p
          role="alert"
          className="mt-6 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800"
        >
          {error}
        </p>
      ) : null}

      {result ? (
        <section aria-label="Proposal result" className="mt-8 space-y-4">
          <div className="rounded-lg border border-neutral-200 p-5">
            <div className="flex flex-wrap items-center gap-3 text-sm">
              <span
                className={
                  result.feasible
                    ? "font-medium text-green-700"
                    : "font-medium text-amber-700"
                }
              >
                {result.feasible ? "Feasible" : "Not feasible"}
              </span>
              <span className="text-neutral-400">
                {result.node_count} task nodes proposed
              </span>
              {result.is_novel ? (
                <span className="text-neutral-400">Novel contribution</span>
              ) : null}
              <span className="text-xs text-neutral-400">
                Status: quarantined, awaiting approval
              </span>
            </div>
            <p className="mt-3 text-sm text-neutral-600">{result.reasoning}</p>
            {result.structural_problems.length > 0 ? (
              <ul className="mt-3 list-inside list-disc text-sm text-amber-700">
                {result.structural_problems.map((p, i) => (
                  <li key={i}>{p}</li>
                ))}
              </ul>
            ) : null}
            {result.objections.length > 0 ? (
              <ul className="mt-3 list-inside list-disc text-sm text-amber-700">
                {result.objections.map((o, i) => (
                  <li key={i}>{o}</li>
                ))}
              </ul>
            ) : null}
            {result.related_existing.length > 0 ? (
              <div className="mt-3 text-sm text-neutral-500">
                Related existing knowledge:{" "}
                <span className="font-mono text-xs">
                  {result.related_existing.slice(0, 5).join(", ")}
                </span>
              </div>
            ) : null}
          </div>

          {result.ops.length > 0 ? (
            <div className="rounded-lg border border-neutral-200 p-5">
              <h2 className="text-sm font-medium text-neutral-900">
                Proposed steps
              </h2>
              <ol className="mt-3 space-y-3">
                {result.ops.map((op, i) => (
                  <li key={i} className="text-sm">
                    <span className="mr-2 text-neutral-400">{i + 1}.</span>
                    <span className="text-neutral-900">
                      {String(op.name ?? "")}
                    </span>
                    {op.description ? (
                      <span className="block pl-6 text-neutral-500">
                        {String(op.description)}
                      </span>
                    ) : null}
                  </li>
                ))}
              </ol>
            </div>
          ) : null}

          <p className="text-sm text-neutral-400">
            Proposal id{" "}
            <span className="font-mono text-xs">{result.id}</span> — approved or
            rejected via the backend review flow.
          </p>
        </section>
      ) : null}
    </>
  );
}

// ---------------------------------------------------------------- Page

function SubmitInner() {
  const params = useSearchParams();
  const initial = params.get("problem") ?? "";
  const [tab, setTab] = useState<Tab>("quick");

  return (
    <div className="pt-10">
      <div className="max-w-2xl">
        <h1 className="text-2xl font-medium tracking-tight text-neutral-900">
          Add a procedure
        </h1>

        <div className="mt-4 flex gap-1 text-sm">
          <button
            type="button"
            onClick={() => setTab("quick")}
            className={`rounded-md px-3 py-1.5 ${
              tab === "quick"
                ? "bg-neutral-900 text-neutral-50"
                : "bg-neutral-100 text-neutral-600"
            }`}
          >
            Quick add (private)
          </button>
          <button
            type="button"
            onClick={() => setTab("propose")}
            className={`rounded-md px-3 py-1.5 ${
              tab === "propose"
                ? "bg-neutral-900 text-neutral-50"
                : "bg-neutral-100 text-neutral-600"
            }`}
          >
            Propose to commons (reviewed)
          </button>
        </div>

        {tab === "quick" ? (
          <QuickAdd initial={initial} />
        ) : (
          <ProposeToCommons initial={initial} />
        )}

        <Link
          href="/"
          className="mt-10 inline-block text-sm text-neutral-500 underline"
        >
          Back to search
        </Link>
      </div>
    </div>
  );
}

export default function SubmitPage() {
  return (
    <Suspense fallback={null}>
      <SubmitInner />
    </Suspense>
  );
}
