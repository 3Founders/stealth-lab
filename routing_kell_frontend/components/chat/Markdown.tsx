"use client";
import { marked, type Token } from "marked";
import type { ReactNode } from "react";

/**
 * Markdown from a model, rendered as React elements - never as raw HTML. Fenced code, inline code, headings, lists,
 * quotes, tables, emphasis, strikethrough and links are supported. A link becomes a link only for http(s); anything else
 * (javascript:, data:, a file) stays text. Pictures stay a labelled link rather than being fetched (a picture request is
 * how data leaves), matching the click-to-load rule in MessageText.
 */
type Tok = Token & {
  text?: string; raw?: string; href?: string; depth?: number; ordered?: boolean; start?: number;
  tokens?: Token[]; items?: Tok[]; header?: Tok[]; rows?: Tok[][];
};

function safeHref(href: unknown): string | null {
  if (typeof href !== "string") return null;
  try {
    const u = new URL(href, "https://example.invalid");
    return u.protocol === "http:" || u.protocol === "https:" ? href : null;
  } catch { return null; }
}

function inline(tokens: Token[] | undefined): ReactNode {
  if (!tokens?.length) return null;
  return tokens.map((t, i) => {
    const x = t as Tok;
    switch (x.type) {
      case "text": return <span key={i}>{x.tokens?.length ? inline(x.tokens) : (x.text ?? "")}</span>;
      case "escape": return <span key={i}>{x.text ?? ""}</span>;
      case "strong": return <strong key={i}>{inline(x.tokens)}</strong>;
      case "em": return <em key={i}>{inline(x.tokens)}</em>;
      case "del": return <del key={i}>{inline(x.tokens)}</del>;
      case "codespan": return <code key={i}>{x.text ?? ""}</code>;
      case "br": return <br key={i} />;
      case "link": {
        const href = safeHref(x.href);
        return href
          ? <a key={i} href={href} target="_blank" rel="noopener noreferrer">{inline(x.tokens) ?? x.text}</a>
          : <span key={i}>{x.text ?? ""}</span>;
      }
      case "image": {
        const href = safeHref(x.href);
        return <span key={i} className="dg-md-pic">{href
          ? <a href={href} target="_blank" rel="noopener noreferrer">{x.text || "picture"}</a>
          : (x.text || "picture")}</span>;
      }
      case "html": return <span key={i}>{x.text ?? ""}</span>;
      default: return x.raw ? <span key={i}>{x.raw}</span> : null;
    }
  });
}

function blocks(tokens: Token[]): ReactNode {
  return tokens.map((t, i) => {
    const x = t as Tok;
    switch (x.type) {
      case "space": return null;
      case "heading": {
        const body = <>{inline(x.tokens)}</>;
        return (x.depth ?? 1) <= 1 ? <h3 key={i} className="dg-md-h">{body}</h3>
          : (x.depth ?? 1) === 2 ? <h4 key={i} className="dg-md-h">{body}</h4>
            : <h5 key={i} className="dg-md-h">{body}</h5>;
      }
      case "paragraph": return <p key={i}>{inline(x.tokens)}</p>;
      case "text": return <p key={i}>{inline(x.tokens) ?? x.text}</p>;
      case "code": return <pre key={i} className="dg-md-pre"><code>{x.text ?? ""}</code></pre>;
      case "blockquote": return <blockquote key={i}>{blocks(x.tokens ?? [])}</blockquote>;
      case "list": {
        const items = (x.items ?? []).map((it, j) => {
          const only = it.tokens?.length === 1 && it.tokens[0].type === "text" ? (it.tokens[0] as Tok) : null;
          return <li key={j}>{only ? inline(only.tokens) : blocks(it.tokens ?? [])}</li>;
        });
        return x.ordered
          ? <ol key={i} className="dg-md-list" start={x.start}>{items}</ol>
          : <ul key={i} className="dg-md-list">{items}</ul>;
      }
      case "hr": return <hr key={i} />;
      case "table": {
        const head = x.header ?? [];
        return (
          <table key={i} className="dg-md-table">
            <thead><tr>{head.map((c, j) => <th key={j}>{inline(c.tokens)}</th>)}</tr></thead>
            <tbody>{(x.rows ?? []).map((row, r) => <tr key={r}>{row.map((c, j) => <td key={j}>{inline(c.tokens)}</td>)}</tr>)}</tbody>
          </table>
        );
      }
      case "html": return <p key={i} className="dg-md-raw">{x.text ?? ""}</p>;
      default: return x.raw ? <p key={i}>{x.raw}</p> : null;
    }
  });
}

export default function Markdown({ text }: { text: string }) {
  if (!text.trim()) return null;
  return <div className="dg-md">{blocks(marked.lexer(text, { gfm: true, breaks: true }))}</div>;
}
