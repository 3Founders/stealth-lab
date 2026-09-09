import Link from "next/link";
import { notFound } from "next/navigation";

import { LEGAL_DOCS, getLegalDocMeta, readLegalDoc } from "@/lib/legal-docs";

export function generateStaticParams() {
  return LEGAL_DOCS.map((doc) => ({ slug: doc.slug }));
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const doc = getLegalDocMeta(slug);
  return { title: doc ? `${doc.title} — Stealth Lab` : "Legal — Stealth Lab" };
}

export default async function LegalDocPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const doc = getLegalDocMeta(slug);
  if (!doc) {
    notFound();
  }

  let content: string;
  try {
    content = readLegalDoc(doc.filename);
  } catch {
    notFound();
  }

  return (
    <article className="max-w-2xl pt-16 pb-24">
      <Link href="/legal" className="text-sm text-neutral-500 underline underline-offset-2">
        ← All legal documents
      </Link>
      <pre className="mt-6 whitespace-pre-wrap break-words font-sans text-sm leading-relaxed text-neutral-800">
        {content}
      </pre>
    </article>
  );
}
