import Link from "next/link";
import type { Metadata } from "next";

import { Section } from "@/components/section";
import { GUIDE_DOCS } from "@/lib/docs";
import { LEGAL_DOCS } from "@/lib/legal-docs";

export const metadata: Metadata = {
  title: "Docs — keळ",
  description: "Guides to how keळ works, plus legal and policy documents.",
};

export default function DocsIndexPage() {
  return (
    <>
      <Section tone="paper" innerClassName="pt-16 pb-10">
        <h1 className="font-display text-4xl tracking-tight text-ink">Docs</h1>
        <p className="mt-3 max-w-xl text-base text-ink/65">
          How the product actually works, written plainly.
        </p>
      </Section>

      <Section tone="paper" innerClassName="pb-16">
        <h2 className="text-sm font-medium uppercase tracking-widest text-ink/65">Guide</h2>
        <ul className="mt-4 divide-y divide-ink/10">
          {GUIDE_DOCS.map((doc) => (
            <li key={doc.slug}>
              <Link href={`/docs/${doc.slug}`} className="block py-4 transition-colors">
                <span className="text-lg text-ink group-hover:underline">{doc.title}</span>
                <p className="mt-1 text-sm text-ink/70">{doc.summary}</p>
              </Link>
            </li>
          ))}
        </ul>

        <h2 className="mt-14 text-sm font-medium uppercase tracking-widest text-ink/65">
          Legal &amp; policies
        </h2>
        <ul className="mt-4 divide-y divide-ink/10">
          {LEGAL_DOCS.map((doc) => (
            <li key={doc.slug}>
              <Link
                href={`/legal/${doc.slug}`}
                className="block py-3 text-base text-ink/80 hover:text-ink"
              >
                {doc.title}
              </Link>
            </li>
          ))}
        </ul>
      </Section>
    </>
  );
}
