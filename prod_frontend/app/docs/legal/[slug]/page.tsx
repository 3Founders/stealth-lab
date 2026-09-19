import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { LEGAL_DOCS, getLegalDoc, renderLegal } from "@/lib/legal";

export function generateStaticParams() {
  return LEGAL_DOCS.map((d) => ({ slug: d.slug }));
}

export async function generateMetadata({ params }: { params: Promise<{ slug: string }> }): Promise<Metadata> {
  const doc = getLegalDoc((await params).slug);
  return { title: doc ? doc.title : "Legal" };
}

export default async function LegalDocPage({ params }: { params: Promise<{ slug: string }> }) {
  const doc = getLegalDoc((await params).slug);
  if (!doc) notFound();
  const html = renderLegal(doc);
  return (
    <>
      <section className="page-hero frame grid">
        <div className="marker caption" style={{ gridColumn: "1 / -1" }}>
          <b>DOCS</b><span>/ <Link href="/docs/legal" style={{ textDecoration: "underline" }}>LEGAL</Link> / {doc.title.toUpperCase()}</span>
        </div>
        <h1 className="h1" style={{ gridColumn: "1 / span 10" }}>{doc.title}</h1>
      </section>
      <div className="frame docs">
        <nav className="docs-nav" aria-label="Legal documents">
          {LEGAL_DOCS.map((d) => (
            <Link key={d.slug} href={`/docs/legal/${d.slug}`} aria-current={d.slug === doc.slug ? "page" : undefined}>{d.title}</Link>
          ))}
        </nav>
        <article className="prose" dangerouslySetInnerHTML={{ __html: html }} />
      </div>
    </>
  );
}
