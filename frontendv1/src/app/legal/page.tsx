import Link from "next/link";

import { LEGAL_DOCS } from "@/lib/legal-docs";

export const metadata = {
  title: "Legal — Stealth Lab",
};

export default function LegalIndexPage() {
  return (
    <article className="max-w-2xl pt-16">
      <h1 className="text-2xl font-semibold tracking-tight">Legal &amp; policies</h1>
      <p className="mt-2 text-sm text-neutral-500">
        These documents are drafts generated from the current codebase and are marked
        &ldquo;DRAFT — REQUIRES LEGAL REVIEW&rdquo; until a founder/counsel finalizes them. They
        are not yet in effect.
      </p>
      <ul className="mt-6 space-y-2">
        {LEGAL_DOCS.map((doc) => (
          <li key={doc.slug}>
            <Link
              href={`/legal/${doc.slug}`}
              className="text-sm text-neutral-700 underline underline-offset-2 hover:text-neutral-900"
            >
              {doc.title}
            </Link>
          </li>
        ))}
      </ul>
    </article>
  );
}
