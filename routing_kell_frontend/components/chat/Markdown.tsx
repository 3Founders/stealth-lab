"use client";
import katex from "katex";
import "katex/dist/katex.min.css";
import { marked, type Token, type TokenizerAndRendererExtension } from "marked";
import { useRef, useState, type ReactNode } from "react";

/**
 * Markdown from a model, rendered as React elements - never as raw HTML. Fenced code, inline code, headings, lists,
 * quotes, tables, emphasis, strikethrough and links are supported. A link becomes a link only for http(s); anything else
 * (javascript:, data:, a file) stays text. Pictures stay a labelled link rather than being fetched (a picture request is
 * how data leaves), matching the click-to-load rule in MessageText.
 *
 * Streaming: a reply is re-rendered on every token. Each completed block is memoized by its source text, so only the block
 * still arriving is parsed/rendered again; finished paragraphs, code and tables keep the same React nodes (this is the
 * blockMemo approach that makes streaming markdown cheap). The cache is per message and bounded.
 */

/**
 * Maths in a chat answer: $$...$$ and \[...\] are display formulas, $...$ and \(...\) are inline. A single $ only opens a formula when the text after it
 * does not start with a space and the closing $ is not followed by a digit, so "it costs $5 and $10" stays text. KaTeX escapes its own output and, with trust
 * off, cannot make links or run anything (the same settings the canvas uses for LaTeX documents).
 */
const mathExt: TokenizerAndRendererExtension = {
  name: "math", level: "inline",
  // Other places that call marked.parse (the legal pages) share this instance: there a formula stays the text it was, escaped, and never throws.
  renderer(token) { return String(token.raw ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); },
  start(src: string) { const i = src.search(/\$|\\[([]/); return i < 0 ? undefined : i; },
  tokenizer(src: string) {
    let m = /^\$\$([\s\S]+?)\$\$/.exec(src);
    if (m) return { type: "math", raw: m[0], text: m[1].trim(), display: true };
    m = /^\\\[([\s\S]+?)\\\]/.exec(src);
    if (m) return { type: "math", raw: m[0], text: m[1].trim(), display: true };
    m = /^\\\(([\s\S]+?)\\\)/.exec(src);
    if (m) return { type: "math", raw: m[0], text: m[1].trim(), display: false };
    m = /^\$(?!\s)((?:[^$\n\\]|\\.)+?)(?<!\s)\$(?!\d)/.exec(src);
    if (m) return { type: "math", raw: m[0], text: m[1], display: false };
    return undefined;
  },
};
let mathInstalled = false;
if (!mathInstalled) { marked.use({ extensions: [mathExt] }); mathInstalled = true; }

function typeset(src: string, display: boolean): string {
  return katex.renderToString(src, { displayMode: display, throwOnError: false, strict: "ignore", trust: false, output: "html", maxSize: 20, maxExpand: 500 });
}

/** A fenced block with its language and a Copy button (the text goes to the clipboard as written, not as shown). */
function CodeBlock({ code, lang }: { code: string; lang?: string }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    try {
      void navigator.clipboard.writeText(code).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500); }, () => {});
    } catch { /* no clipboard here: the text can still be selected */ }
  };
  return (
    <div className="dg-md-codebox">
      <div className="dg-md-codehead">
        <span>{lang || "code"}</span>
        <button type="button" className="dg-md-copy" onClick={copy} aria-label="Copy code">{copied ? "Copied" : "Copy"}</button>
      </div>
      <pre className="dg-md-pre"><code>{code}</code></pre>
    </div>
  );
}

type Tok = Token & {
  text?: string; raw?: string; href?: string; depth?: number; ordered?: boolean; start?: number;
  tokens?: Token[]; items?: Tok[]; header?: Tok[]; rows?: Tok[][]; lang?: string; display?: boolean; task?: boolean; checked?: boolean;
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
      case "math": return <span key={i} className={x.display ? "dg-md-math dg-md-math-d" : "dg-md-math"} dangerouslySetInnerHTML={{ __html: typeset(x.text ?? "", Boolean(x.display)) }} />;
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
  return tokens.map((t, i) => block(t as Tok, i));
}

function block(x: Tok, i: number): ReactNode {
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
    case "code": return <CodeBlock key={i} code={x.text ?? ""} lang={(x.lang ?? "").split(/\s+/)[0]} />;
    case "blockquote": return <blockquote key={i}>{blocks(x.tokens ?? [])}</blockquote>;
    case "list": {
      const items = (x.items ?? []).map((it, j) => {
        const parts = (it.tokens ?? []).filter((k) => k.type !== "checkbox");   // marked puts the [ ] / [x] marker in its own token; the box below replaces it
        const only = parts.length === 1 && parts[0].type === "text" ? (parts[0] as Tok) : null;
        const box = it.task ? <input type="checkbox" className="dg-md-check" checked={Boolean(it.checked)} readOnly disabled aria-label={it.checked ? "done" : "not done"} /> : null;
        return <li key={j} className={it.task ? "dg-md-task" : undefined}>{box}{only ? inline(only.tokens) : blocks(parts)}</li>;
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
}

export default function Markdown({ text }: { text: string }) {
  const cache = useRef<Map<string, ReactNode>>(new Map());
  if (!text.trim()) return null;
  const tokens = marked.lexer(text, { ...marked.defaults, gfm: true, breaks: true }) as Token[];   // the defaults carry the maths extension; passing bare options would drop it
  const nodes = tokens.map((t, i) => {
    const x = t as Tok;
    if (x.type === "space") return null;
    const key = `${i}:${x.raw ?? JSON.stringify(x)}`;
    const hit = cache.current.get(key);
    if (hit !== undefined) return hit;
    const node = block(x, i);
    cache.current.set(key, node);
    if (cache.current.size > 400) {                       // a long-lived message cannot grow it without bound
      const oldest = cache.current.keys().next().value;
      if (oldest !== undefined) cache.current.delete(oldest);
    }
    return node;
  });
  return <div className="dg-md">{nodes}</div>;
}
