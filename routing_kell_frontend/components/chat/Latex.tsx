"use client";
import katex from "katex";
import "katex/dist/katex.min.css";
import { Fragment, useMemo, type ReactNode } from "react";
import { parseLatex, type Block, type Inline, type LatexDoc, type MathRenderer } from "@/lib/chat/latex";

/**
 * LaTeX source, typeset: the canvas's counterpart of Markdown.tsx for documents written in LaTeX. Maths goes through KaTeX (which escapes its own
 * output and, with trust off, cannot make links or run anything); everything else is built as React elements, never raw HTML. See
 * lib/chat/latex.ts for what is read and what is not.
 */
const typeset: MathRenderer = (src, display, macros) =>
  katex.renderToString(src, { displayMode: display, throwOnError: false, strict: "ignore", trust: false, output: "html", macros: { ...macros }, maxSize: 20, maxExpand: 500 });

const safeHref = (h: string): string | null => { try { const u = new URL(h, "https://example.invalid"); return u.protocol === "http:" || u.protocol === "https:" ? h : null; } catch { return null; } };

function Inl({ nodes, doc }: { nodes: Inline[]; doc: LatexDoc }): ReactNode {
  return nodes.map((n, i) => {
    switch (n.t) {
      case "text": return <Fragment key={i}>{n.s}</Fragment>;
      case "b": return <strong key={i}><Inl nodes={n.c} doc={doc} /></strong>;
      case "i": return <em key={i}><Inl nodes={n.c} doc={doc} /></em>;
      case "u": return <u key={i}><Inl nodes={n.c} doc={doc} /></u>;
      case "tt": return <code key={i}><Inl nodes={n.c} doc={doc} /></code>;
      case "sc": return <span key={i} className="dg-tex-sc"><Inl nodes={n.c} doc={doc} /></span>;
      case "sf": return <span key={i} className="dg-tex-sf"><Inl nodes={n.c} doc={doc} /></span>;
      case "fn": return <span key={i} className="dg-tex-fn" title={flat(n.c)}>⁽ⁿ⁾<span className="dg-tex-fn-text"> [{flat(n.c)}]</span></span>;
      case "br": return <br key={i} />;
      case "math": return <span key={i} className="dg-tex-m" dangerouslySetInnerHTML={{ __html: typeset(n.s, false, {}) }} />;
      case "link": { const h = safeHref(n.href); return h ? <a key={i} href={h} target="_blank" rel="noopener noreferrer"><Inl nodes={n.c} doc={doc} /></a> : <Fragment key={i}><Inl nodes={n.c} doc={doc} /></Fragment>; }
      case "ref": { const v = doc.labels[n.label]; return <span key={i} className="dg-tex-ref" data-missing={v === undefined || undefined}>{n.paren ? `(${v ?? "?"})` : (v ?? "??")}</span>; }
      case "cite": return <span key={i} className="dg-tex-ref">[{n.keys.map((k) => doc.cites[k] ?? "?").join(", ")}]</span>;
    }
  });
}

const flat = (c: Inline[]): string => c.map((n) => (n.t === "text" ? n.s : "c" in n ? flat(n.c as Inline[]) : n.t === "math" ? n.s : "")).join("");

function Blk({ b, doc }: { b: Block; doc: LatexDoc }): ReactNode {
  switch (b.t) {
    case "title":
      return (
        <header className="dg-tex-title">
          {b.title.length > 0 && <h1><Inl nodes={b.title} doc={doc} /></h1>}
          {b.author.length > 0 && <p className="dg-tex-author"><Inl nodes={b.author} doc={doc} /></p>}
          {b.date.length > 0 && <p className="dg-tex-date"><Inl nodes={b.date} doc={doc} /></p>}
        </header>
      );
    case "h": {
      const Tag = (b.level <= 1 ? "h2" : b.level === 2 ? "h3" : "h4") as "h2";
      return <Tag className={`dg-tex-h dg-tex-h${Math.min(b.level, 4)}`}>{b.num && <span className="dg-tex-num">{b.num}</span>}<Inl nodes={b.c} doc={doc} /></Tag>;
    }
    case "p": return <p><Inl nodes={b.c} doc={doc} /></p>;
    case "list": {
      const items = b.items.map((it, i) => (
        <li key={i}>{it.label && <b className="dg-tex-dt"><Inl nodes={it.label} doc={doc} /> </b>}<Blks bs={it.c} doc={doc} /></li>
      ));
      return b.kind === "ol" ? <ol>{items}</ol> : b.kind === "dl" ? <ul className="dg-tex-dl">{items}</ul> : <ul>{items}</ul>;
    }
    case "math": return <div className="dg-tex-display" dangerouslySetInnerHTML={{ __html: b.html }} />;
    case "thm":
      return (
        <div className={`dg-tex-thm${b.proof ? " is-proof" : ""}`}>
          <b className="dg-tex-thm-head">{b.name}{b.num ? ` ${b.num}` : ""}{b.note && <span> (<Inl nodes={b.note} doc={doc} />)</span>}.</b>{" "}
          <Blks bs={b.c} doc={doc} inline />
          {b.proof && <span className="dg-tex-qed" aria-hidden="true">∎</span>}
        </div>
      );
    case "table":
      return (
        <div className="dg-tex-table-wrap">
          <table className="dg-tex-table">
            <tbody>
              {b.rows.map((row, r) => (
                <tr key={r}>
                  {row.map((c, k) => (
                    <td key={k} colSpan={c.span} data-a={c.align} data-t={c.top || undefined} data-b={c.bottom || undefined} data-l={c.left || undefined} data-r={c.right || undefined}>
                      <Inl nodes={c.c} doc={doc} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    case "float":
      return (
        <figure className={`dg-tex-float dg-tex-${b.kind}`}>
          <Blks bs={b.c} doc={doc} />
          {b.caption && <figcaption><b>{b.kind === "table" ? "Table" : "Figure"} {b.num}.</b> <Inl nodes={b.caption} doc={doc} /></figcaption>}
        </figure>
      );
    case "image": return <div className="dg-tex-image" role="img" aria-label={b.name}>picture: {b.name}</div>;
    case "pre": return <pre className="dg-md-pre"><code>{b.s}</code></pre>;
    case "quote": return <blockquote><Blks bs={b.c} doc={doc} /></blockquote>;
    case "center": return <div className="dg-tex-center"><Blks bs={b.c} doc={doc} /></div>;
    case "abstract": return <div className="dg-tex-abstract"><b>Abstract</b><Blks bs={b.c} doc={doc} /></div>;
    case "toc":
      return (
        <nav className="dg-tex-toc" aria-label="Contents">
          <b>Contents</b>
          <ul>{b.items.filter((h) => h.level >= 1 && h.level <= 3).map((h, i) => <li key={i} style={{ paddingLeft: `${(h.level - 1) * 16}px` }}>{h.num && <span className="dg-tex-num">{h.num}</span>}<Inl nodes={h.c} doc={doc} /></li>)}</ul>
        </nav>
      );
    case "bib":
      return (
        <section className="dg-tex-bib">
          <h2 className="dg-tex-h dg-tex-h1">References</h2>
          <ol>{b.items.map((it) => <li key={it.key} value={it.num}><Inl nodes={it.c} doc={doc} /></li>)}</ol>
        </section>
      );
  }
}

function Blks({ bs, doc, inline }: { bs: Block[]; doc: LatexDoc; inline?: boolean }): ReactNode {
  return bs.map((b, i) => (inline && b.t === "p" && i === 0 ? <span key={i}><Inl nodes={b.c} doc={doc} /></span> : <Blk key={i} b={b} doc={doc} />));
}

export default function Latex({ text }: { text: string }) {
  const doc = useMemo(() => {
    try { return parseLatex(text, typeset); } catch { return null; }
  }, [text]);
  if (!text.trim()) return null;
  if (!doc) return <pre className="dg-md-pre"><code>{text}</code></pre>;
  return (
    <article className="dg-tex" lang="en">
      <Blks bs={doc.blocks} doc={doc} />
      {doc.errors > 0 && <p className="dg-tex-warn small dim" role="status">Some of this document was too complex to show, so part may be missing.</p>}
    </article>
  );
}
