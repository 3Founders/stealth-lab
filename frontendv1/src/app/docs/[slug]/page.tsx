import Link from "next/link";
import { notFound } from "next/navigation";

import { Section } from "@/components/section";
import { GUIDE_DOCS, getGuideDoc } from "@/lib/docs";

export function generateStaticParams() {
  return GUIDE_DOCS.map((doc) => ({ slug: doc.slug }));
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const doc = getGuideDoc(slug);
  return { title: doc ? `${doc.title} — keळ` : "Docs — keळ" };
}

export default async function GuideDocPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const doc = getGuideDoc(slug);
  if (!doc) notFound();

  const otherDocs = GUIDE_DOCS.filter((d) => d.slug !== slug);

  return (
    <Section tone="paper" innerClassName="py-16">
      <div className="grid gap-12 md:grid-cols-[minmax(0,1fr)_220px]">
        <article className="max-w-2xl">
          <Link href="/docs" className="text-sm text-ink/65 underline underline-offset-2">
            ← Docs
          </Link>
          <h1 className="mt-4 font-display text-3xl tracking-tight text-ink">{doc.title}</h1>
          <div className="mt-6 space-y-4 text-[15px] leading-7 text-ink-soft">
            {doc.body.map((para, i) =>
              para.startsWith("## ") ? (
                <h2 key={i} className="pt-2 text-base font-semibold text-ink">
                  {para.slice(3)}
                </h2>
              ) : (
                <p key={i}>{para}</p>
              )
            )}
          </div>
        </article>
        <nav aria-label="More guides" className="hidden md:block">
          <p className="text-xs uppercase tracking-widest text-ink/65">More guides</p>
          <ul className="mt-3 space-y-2">
            {otherDocs.map((d) => (
              <li key={d.slug}>
                <Link
                  href={`/docs/${d.slug}`}
                  className="text-sm text-ink/70 underline decoration-ink/20 underline-offset-4 hover:text-ink"
                >
                  {d.title}
                </Link>
              </li>
            ))}
          </ul>
        </nav>
      </div>
    </Section>
  );
}
