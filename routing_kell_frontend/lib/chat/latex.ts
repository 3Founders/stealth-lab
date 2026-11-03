/**
 * LaTeX source to a small document tree, for the canvas's LaTeX viewer (components/chat/Latex.tsx). It is a reader for the commonly used part of
 * LaTeX, not a TeX engine: sections, paragraphs, text styles, lists, tables, theorem-like environments and proofs, floats with captions,
 * verbatim, a bibliography, \label with \ref/\eqref/\cite, and maths (which is handed to KaTeX untouched, apart from numbering).
 * Honest limits: no TikZ, no included pictures (a labelled box stands in), no page layout, and a macro with arguments is not expanded in text
 * (simple \newcommand{\x}{...} macros are passed to KaTeX for maths). Anything it does not know is shown as its argument text or dropped:
 * it never throws and never loops forever.
 */

export type Inline =
  | { t: "text"; s: string }
  | { t: "b" | "i" | "u" | "tt" | "sc" | "sf" | "fn"; c: Inline[] }
  | { t: "math"; s: string }
  | { t: "link"; href: string; c: Inline[] }
  | { t: "ref"; label: string; paren?: boolean }
  | { t: "cite"; keys: string[] }
  | { t: "br" };

export interface Cell { c: Inline[]; span: number; align: "l" | "c" | "r"; top: boolean; bottom: boolean; left: boolean; right: boolean }
export type Block =
  | { t: "title"; title: Inline[]; author: Inline[]; date: Inline[] }
  | { t: "h"; level: number; num: string; c: Inline[] }
  | { t: "p"; c: Inline[] }
  | { t: "list"; kind: "ul" | "ol" | "dl"; items: Array<{ label: Inline[] | null; num: number; c: Block[] }> }
  | { t: "math"; html: string }
  | { t: "thm"; name: string; num: string; note: Inline[] | null; c: Block[]; proof: boolean }
  | { t: "table"; rows: Cell[][] }
  | { t: "float"; kind: "table" | "figure"; num: string; caption: Inline[] | null; c: Block[] }
  | { t: "image"; name: string }
  | { t: "pre"; s: string }
  | { t: "quote"; c: Block[] }
  | { t: "center"; c: Block[] }
  | { t: "abstract"; c: Block[] }
  | { t: "toc"; items: Array<{ level: number; num: string; c: Inline[] }> }
  | { t: "bib"; items: Array<{ num: number; key: string; c: Inline[] }> };

export interface LatexDoc { blocks: Block[]; labels: Record<string, string>; cites: Record<string, number>; errors: number }
export type MathRenderer = (src: string, display: boolean, macros: Record<string, string>) => string;

const MAX_STEPS = 200_000;
const THEOREMS: Record<string, string> = {
  theorem: "Theorem", lemma: "Lemma", proposition: "Proposition", corollary: "Corollary", definition: "Definition", remark: "Remark",
  example: "Example", claim: "Claim", conjecture: "Conjecture", notation: "Notation", fact: "Fact", observation: "Observation", exercise: "Exercise",
};
const SKIP_BLOCK = new Set(["newpage", "clearpage", "pagebreak", "noindent", "centering", "bigskip", "medskip", "smallskip", "hfill", "vfill",
  "raggedright", "raggedleft", "normalsize", "small", "footnotesize", "large", "Large", "LARGE", "huge", "Huge", "tiny", "scriptsize", "indent",
  "bibliographystyle", "pagestyle", "thispagestyle", "usepackage", "documentclass", "geometry", "setlength", "setcounter", "hline", "toprule",
  "midrule", "bottomrule", "appendix", "flushleft", "flushright", "protect", "allowdisplaybreaks", "frenchspacing", "sloppy"]);
const ARG1_SKIP = new Set(["vspace", "hspace", "usepackage", "documentclass", "geometry", "setlength", "setcounter", "pagestyle", "thispagestyle",
  "bibliographystyle", "bibliography", "input", "include", "label_skip", "renewcommand_skip", "graphicspath", "addtolength", "newcounter", "hypersetup"]);

/** Remove % comments (a % that is not escaped), keeping \\% and everything inside verbatim-like environments. */
export function stripComments(src: string): string {
  const cut = (text: string) => text.split("\n").map((line) => {
    for (let i = 0; i < line.length; i++) {
      if (line[i] === "\\") { i++; continue; }
      if (line[i] === "%") return line.slice(0, i);
    }
    return line;
  }).join("\n");
  let out = "", last = 0;
  const re = /\\begin\{(verbatim|lstlisting|minted|Verbatim)\}[\s\S]*?\\end\{\1\}/g;
  for (const m of src.matchAll(re)) {
    out += cut(src.slice(last, m.index)) + m[0];
    last = (m.index ?? 0) + m[0].length;
  }
  return out + cut(src.slice(last));
}

/** Index just past the } matching the { at s[i] (or s.length when there is none). */
export function groupEnd(s: string, i: number): number {
  let depth = 0;
  for (let k = i; k < s.length; k++) {
    const ch = s[k];
    if (ch === "\\") { k++; continue; }
    if (ch === "{") depth++;
    else if (ch === "}") { depth--; if (depth === 0) return k + 1; }
  }
  return s.length;
}

function skipWs(s: string, i: number): number { while (i < s.length && /\s/.test(s[i])) i++; return i; }

/** {group} starting at (or after spaces from) i: [its text, index after it]. */
function readGroup(s: string, i: number): [string, number] | null {
  const j = skipWs(s, i);
  if (s[j] !== "{") return null;
  const e = groupEnd(s, j);
  return [s.slice(j + 1, Math.max(j + 1, e - 1)), e];
}
function readOpt(s: string, i: number): [string, number] | null {
  const j = skipWs(s, i);
  if (s[j] !== "[") return null;
  let depth = 0;
  for (let k = j; k < s.length; k++) {
    if (s[k] === "{") k = groupEnd(s, k) - 1;
    else if (s[k] === "[") depth++;
    else if (s[k] === "]") { depth--; if (depth === 0) return [s.slice(j + 1, k), k + 1]; }
  }
  return null;
}

/** Index of the \end{name} that closes the \begin{name} whose body starts at `from`. */
function envEnd(s: string, name: string, from: number): { bodyEnd: number; after: number } {
  const open = `\\begin{${name}}`, close = `\\end{${name}}`;
  let depth = 1, i = from;
  while (i < s.length) {
    const o = s.indexOf(open, i), c = s.indexOf(close, i);
    if (c < 0) return { bodyEnd: s.length, after: s.length };
    if (o >= 0 && o < c) { depth++; i = o + open.length; continue; }
    depth--;
    if (depth === 0) return { bodyEnd: c, after: c + close.length };
    i = c + close.length;
  }
  return { bodyEnd: s.length, after: s.length };
}

interface Ctx {
  macros: Record<string, string>;
  theorems: Record<string, string>;
  counters: Record<string, number>;
  cur: string;                       // the number of the thing last numbered: what a \label names
  labels: Record<string, string>;
  known: Record<string, string>;     // labels found by the first pass
  cites: Record<string, number>;
  headings: Array<{ level: number; num: string; c: Inline[] }>;
  meta: { title: Inline[]; author: Inline[]; date: Inline[] };
  math: MathRenderer;
  steps: number;
  errors: number;
}

const plain = (s: string): Inline[] => (s ? [{ t: "text", s }] : []);

/** Collapse source whitespace the way TeX does inside a paragraph. */
const squash = (s: string) => s.replace(/\s*\n\s*/g, " ").replace(/[ \t]{2,}/g, " ");

function inlineMathEnd(s: string, i: number, close: string): number {
  for (let k = i; k < s.length; k++) {
    if (s[k] === "\\" && close !== "$") { if (s.startsWith(close, k)) return k; k++; continue; }
    if (s[k] === "\\") { k++; continue; }
    if (close === "$" && s[k] === "$") return k;
  }
  return -1;
}

const STYLE: Record<string, Inline["t"]> = { textbf: "b", bfseries: "b", textit: "i", emph: "i", textsl: "i", itshape: "i", underline: "u", texttt: "tt", textsc: "sc", textsf: "sf" };
const SPACES: Record<string, string> = { quad: " ", qquad: "  ", ",": " ", ";": " ", ":": " ", "!": "", " ": " ", "/": "", "@": "" };
const SYMBOLS: Record<string, string> = { ldots: "…", dots: "…", cdots: "⋯", LaTeX: "LaTeX", TeX: "TeX", textbackslash: "\\", textasciitilde: "~", textbar: "|",
  copyright: "©", dag: "†", ddag: "‡", S: "§", P: "¶", pounds: "£", euro: "€", textellipsis: "…", today: new Date().toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" }) };

/** Text (with commands and $maths$) to inline nodes. */
export function parseInline(src: string, ctx: Ctx, depth = 0): Inline[] {
  const out: Inline[] = [];
  let buf = "";
  const flush = () => { if (buf) { out.push({ t: "text", s: buf }); buf = ""; } };
  const s = src;
  let i = 0;
  while (i < s.length) {
    if (++ctx.steps > MAX_STEPS || depth > 40) { ctx.errors++; break; }
    const ch = s[i];
    if (ch === "$") {
      const double = s[i + 1] === "$";
      const end = inlineMathEnd(s, i + (double ? 2 : 1), "$");
      if (end > 0) { flush(); out.push({ t: "math", s: s.slice(i + (double ? 2 : 1), end) }); i = end + (double ? 2 : 1); continue; }
      buf += "$"; i++; continue;
    }
    if (ch === "\\") {
      const nx = s[i + 1] ?? "";
      if (nx === "(") { const e = s.indexOf("\\)", i + 2); if (e > 0) { flush(); out.push({ t: "math", s: s.slice(i + 2, e) }); i = e + 2; continue; } }
      if (nx === "\\") {
        flush(); out.push({ t: "br" }); i += 2;
        const o = readOpt(s, i); if (o && /^[\d.\s]*(pt|em|ex|cm|mm|in)?$/.test(o[0].trim())) i = o[1];
        continue;
      }
      if (/[%&$#_{}]/.test(nx)) { buf += nx; i += 2; continue; }
      if (nx in SPACES) { buf += SPACES[nx]; i += 2; continue; }
      if (nx === "-") { i += 2; continue; }
      const m = /^[a-zA-Z]+\*?/.exec(s.slice(i + 1));
      if (!m) { buf += nx; i += 2; continue; }
      const name = m[0].replace(/\*$/, "");
      let j = i + 1 + m[0].length;
      if (name === "verb") {
        const d = s[j]; const e = s.indexOf(d, j + 1);
        if (e > 0) { flush(); out.push({ t: "tt", c: plain(s.slice(j + 1, e)) }); i = e + 1; continue; }
      }
      if (name in STYLE) {
        const g = readGroup(s, j);
        if (g) { flush(); out.push({ t: STYLE[name] as "b", c: parseInline(g[0], ctx, depth + 1) }); i = g[1]; continue; }
        i = j; continue;                      // a declaration (\bfseries): ignored
      }
      if (name === "text" || name === "textnormal" || name === "textrm" || name === "mbox" || name === "hbox" || name === "mathrm") {
        const g = readGroup(s, j); if (g) { buf += ""; flush(); out.push(...parseInline(g[0], ctx, depth + 1)); i = g[1]; continue; }
      }
      if (name === "href") {
        const u = readGroup(s, j); const t = u ? readGroup(s, u[1]) : null;
        if (u && t) { flush(); out.push({ t: "link", href: u[0].trim(), c: parseInline(t[0], ctx, depth + 1) }); i = t[1]; continue; }
      }
      if (name === "url") { const u = readGroup(s, j); if (u) { flush(); out.push({ t: "link", href: u[0].trim(), c: plain(u[0].trim()) }); i = u[1]; continue; } }
      if (name === "footnote" || name === "footnotetext") { const g = readGroup(s, j); if (g) { flush(); out.push({ t: "fn", c: parseInline(g[0], ctx, depth + 1) }); i = g[1]; continue; } }
      if (name === "cite" || name === "citep" || name === "citet") {
        const o = readOpt(s, j); const g = readGroup(s, o ? o[1] : j);
        if (g) { flush(); out.push({ t: "cite", keys: g[0].split(",").map((k) => k.trim()).filter(Boolean) }); i = g[1]; continue; }
      }
      if (name === "ref" || name === "eqref" || name === "autoref" || name === "cref" || name === "pageref") {
        const g = readGroup(s, j); if (g) { flush(); out.push({ t: "ref", label: g[0].trim(), paren: name === "eqref" }); i = g[1]; continue; }
      }
      if (name === "label") { const g = readGroup(s, j); if (g) { ctx.labels[g[0].trim()] = ctx.cur; i = g[1]; continue; } }
      if (name in SYMBOLS) { buf += SYMBOLS[name]; i = j; if (s[i] === "{" && s[i + 1] === "}") i += 2; continue; }
      if (name === "textquoteleft") { buf += "‘"; i = j; continue; }
      if (name === "item" || name === "begin" || name === "end") { i = j; const g = readGroup(s, j); if (g && name !== "item") i = g[1]; continue; }
      if (name === "newline" || name === "linebreak") { flush(); out.push({ t: "br" }); i = j; continue; }
      if (SKIP_BLOCK.has(name)) { i = j; const g = ARG1_SKIP.has(name) ? readGroup(s, j) : null; if (g) i = g[1]; continue; }
      if (ARG1_SKIP.has(name)) { const o = readOpt(s, j); const g = readGroup(s, o ? o[1] : j); i = g ? g[1] : j; continue; }
      // an unknown command: show its braced argument, drop the command
      const g = readGroup(s, j);
      if (g) { flush(); out.push(...parseInline(g[0], ctx, depth + 1)); i = g[1]; continue; }
      i = j; continue;
    }
    if (ch === "{") {
      const e = groupEnd(s, i);
      const inner = s.slice(i + 1, Math.max(i + 1, e - 1));
      flush();
      const decl = /^\s*\\(bf|bfseries|it|itshape|em|tt|ttfamily|sc|scshape|sl|sffamily)\b/.exec(inner);
      if (decl) {
        const kind = ({ bf: "b", bfseries: "b", it: "i", itshape: "i", em: "i", sl: "i", tt: "tt", ttfamily: "tt", sc: "sc", scshape: "sc", sffamily: "sf" } as Record<string, Inline["t"]>)[decl[1]];
        out.push({ t: kind as "b", c: parseInline(inner.slice(decl[0].length), ctx, depth + 1) });
      } else out.push(...parseInline(inner, ctx, depth + 1));
      i = e; continue;
    }
    if (ch === "~") { buf += " "; i++; continue; }
    if (ch === "-" && s[i + 1] === "-") { if (s[i + 2] === "-") { buf += "—"; i += 3; } else { buf += "–"; i += 2; } continue; }
    if (ch === "`" && s[i + 1] === "`") { buf += "“"; i += 2; continue; }
    if (ch === "'" && s[i + 1] === "'") { buf += "”"; i += 2; continue; }
    if (ch === "`") { buf += "‘"; i++; continue; }
    if (ch === "\n") { buf += " "; i++; continue; }
    if (ch === "}" ) { i++; continue; }
    buf += ch; i++;
  }
  flush();
  // collapse doubled spaces made by dropped commands
  return out.map((n) => (n.t === "text" ? { t: "text", s: n.s.replace(/ {2,}/g, " ") } : n));
}

const SECTION_LEVEL: Record<string, number> = { part: 0, chapter: 0, section: 1, subsection: 2, subsubsection: 3, paragraph: 4, subparagraph: 5 };

/**
 * A display formula to HTML. Numbering is KaTeX's own (continuous through the page, see the counter in chat.css): a numbered equation, and every
 * row of an align or gather that is not marked \nonumber or \notag, count one each; this function counts the same way so \ref and \eqref agree.
 */
function mathHtml(ctx: Ctx, src: string, env: string, numbered: boolean): string {
  const labels = [...src.matchAll(/\\label\{([^}]*)\}/g)].map((m) => m[1].trim());
  const body = src.replace(/\\label\{[^}]*\}/g, "").trim();
  const rows = body.split(/\\\\(?![a-zA-Z])/).filter((r, k, a) => r.trim() || k < a.length - 1);
  const count = (n: number) => { ctx.counters.equation = (ctx.counters.equation ?? 0) + n; ctx.cur = String(ctx.counters.equation); };
  let tex = body;
  if (env === "equation") { tex = numbered ? `\\begin{equation}${body}\\end{equation}` : `\\begin{equation*}${body}\\end{equation*}`; if (numbered) count(1); }
  else if (env === "align" || env === "flalign" || env === "eqnarray") {
    const name = numbered ? "align" : "align*";
    tex = `\\begin{${name}}${body}\\end{${name}}`;
    if (numbered) count(rows.filter((r) => !/\\(nonumber|notag)\b/.test(r)).length);
  } else if (env === "alignat") { const name = numbered ? "alignat" : "alignat*"; tex = `\\begin{${name}}${body}\\end{${name}}`; if (numbered) count(rows.filter((r) => !/\\(nonumber|notag)\b/.test(r)).length); }
  else if (env === "gather") {
    const name = numbered ? "gather" : "gather*";
    tex = `\\begin{${name}}${body}\\end{${name}}`;
    if (numbered) count(rows.filter((r) => !/\\(nonumber|notag)\b/.test(r)).length);
  } else if (env === "multline") { tex = numbered ? `\\begin{gather}${body.replace(/\\\\(?![a-zA-Z])/g, " ")}\\end{gather}` : body; if (numbered) count(1); }
  else if (env === "split") tex = `\\begin{aligned}${body}\\end{aligned}`;
  for (const l of labels) ctx.labels[l] = ctx.cur;
  return ctx.math(tex, true, ctx.macros);
}

function parseTable(body: string, spec: string, ctx: Ctx): Block {
  const cols: Array<"l" | "c" | "r"> = [];
  const bars: boolean[] = [];                       // bars[k]: a vertical rule before column k; bars[cols.length]: after the last
  const cleaned = spec.replace(/@\{[^}]*\}/g, "").replace(/[pmb]\{[^}]*\}/g, "l").replace(/\*\{(\d+)\}\{([^}]*)\}/g, (_m, n, c) => String(c).repeat(Number(n)));
  let bar = false;
  for (const c of cleaned) {
    if (c === "|") bar = true;
    else if (c === "l" || c === "c" || c === "r") { cols.push(c); bars.push(bar); bar = false; }
  }
  bars.push(bar);
  const rows: Cell[][] = [];
  let top: boolean = false;
  const text = body.replace(/\\(hline|toprule|midrule|bottomrule)\b/g, "\u0001").replace(/\\cline\{[^}]*\}/g, "");
  for (const rawRow of text.split(/\\\\(?:\[[^\]]*\])?/)) {
    let row = rawRow;
    let hasTop: boolean = top; top = false;
    while (row.trimStart().startsWith("\u0001")) { hasTop = true; row = row.trimStart().slice(1); }
    const trailing = row.includes("\u0001");
    row = row.replace(/\u0001/g, "");
    if (!row.trim()) { if ((trailing || hasTop) && rows.length) rows[rows.length - 1].forEach((c) => (c.bottom = true)); else top = hasTop; continue; }
    const cells: Cell[] = [];
    let col = 0;
    for (const raw of row.split(/(?<!\\)&/)) {
      let t = raw.trim(), span = 1, align = cols[col] ?? "l";
      const mc = /^\\multicolumn\{(\d+)\}\{([^}]*)\}\{([\s\S]*)\}$/.exec(t);
      if (mc) { span = Number(mc[1]); align = (/[lcr]/.exec(mc[2])?.[0] as "l" | "c" | "r") ?? "l"; t = mc[3]; }
      cells.push({ c: parseInline(t, ctx), span, align, top: hasTop, bottom: trailing, left: !!bars[col], right: col + span >= cols.length && !!bars[cols.length] });
      col += span;
    }
    rows.push(cells);
  }
  return { t: "table", rows };
}

function parseList(body: string, kind: "ul" | "ol" | "dl", ctx: Ctx): Block {
  const items: Array<{ label: Inline[] | null; num: number; c: Block[] }> = [];
  // split at \item that belong to this list (not to a nested one)
  let depth = 0, last = -1, label: string | null = null;
  const marks: Array<{ at: number; end: number; label: string | null }> = [];
  for (let i = 0; i < body.length; i++) {
    if (body.startsWith("\\begin{", i)) { depth++; i += 6; continue; }
    if (body.startsWith("\\end{", i)) { depth = Math.max(0, depth - 1); i += 4; continue; }
    if (depth === 0 && body.startsWith("\\item", i) && !/[a-zA-Z]/.test(body[i + 5] ?? "")) {
      const o = readOpt(body, i + 5);
      marks.push({ at: i, end: o ? o[1] : i + 5, label: o ? o[0] : null });
      i += 4;
    }
  }
  void last; void label;
  marks.forEach((m, k) => {
    const chunk = body.slice(m.end, k + 1 < marks.length ? marks[k + 1].at : body.length);
    const n = k + 1;
    if (kind === "ol") ctx.cur = String(n);
    items.push({ label: m.label !== null ? parseInline(m.label, ctx) : null, num: n, c: parseBlocks(chunk, ctx) });
  });
  return { t: "list", kind, items };
}

function parseBlocks(src: string, ctx: Ctx): Block[] {
  const out: Block[] = [];
  let buf = "";
  let i = 0;
  const flush = () => {
    const t = buf.trim();
    buf = "";
    if (t) { const c = parseInline(squash(t), ctx); if (c.some((n) => n.t !== "text" || n.s.trim())) out.push({ t: "p", c }); }
  };
  while (i < src.length) {
    if (++ctx.steps > MAX_STEPS) { ctx.errors++; break; }
    const rest = src.slice(i, i + 40);
    if (/^\n[ \t]*\n/.test(rest)) { flush(); i += 1; continue; }
    if (src.startsWith("\\[", i) || src.startsWith("$$", i)) {
      const close = src.startsWith("\\[", i) ? "\\]" : "$$";
      const e = src.indexOf(close, i + 2);
      if (e > 0) { flush(); out.push({ t: "math", html: mathHtml(ctx, src.slice(i + 2, e), "displaymath", false) }); i = e + 2; continue; }
    }
    if (src[i] === "\\") {
      const m = /^\\([a-zA-Z]+)(\*?)/.exec(src.slice(i, i + 40));
      if (m) {
        const name = m[1], star = m[2] === "*";
        let j = i + m[0].length;
        if (name === "begin") {
          const g = readGroup(src, j);
          if (g) {
            const env = g[0].trim();
            const { bodyEnd, after } = envEnd(src, env, g[1]);
            const body = src.slice(g[1], bodyEnd);
            flush();
            envBlock(env, body, src, g[1], ctx, out);
            i = after; continue;
          }
        }
        if (name in SECTION_LEVEL) {
          const o = readOpt(src, j); const g = readGroup(src, o ? o[1] : j);
          if (g) {
            flush();
            const level = SECTION_LEVEL[name];
            let num = "";
            if (!star && level >= 1 && level <= 3) {
              ctx.counters[`s${level}`] = (ctx.counters[`s${level}`] ?? 0) + 1;
              for (let l = level + 1; l <= 3; l++) ctx.counters[`s${l}`] = 0;
              num = [1, 2, 3].slice(0, level).map((l) => ctx.counters[`s${l}`] ?? 0).join(".");
              ctx.cur = num; ctx.counters.equation = ctx.counters.equation ?? 0;
            }
            const c = parseInline(g[0], ctx);
            ctx.headings.push({ level, num, c });
            out.push({ t: "h", level, num, c });
            i = g[1]; continue;
          }
        }
        if (name === "maketitle") { flush(); out.push({ t: "title", ...ctx.meta }); i = j; continue; }
        if (name === "tableofcontents") { flush(); out.push({ t: "toc", items: ctx.headings.slice() }); i = j; continue; }
        if (name === "title" || name === "author" || name === "date" || name === "thanks") { const g = readGroup(src, j); if (g) { if (name !== "thanks") ctx.meta[name] = parseInline(g[0], ctx); i = g[1]; continue; } }
        if (name === "newcommand" || name === "renewcommand" || name === "providecommand" || name === "newtheorem" || name === "DeclareMathOperator" || name === "def") {
          i = declaration(src, i, name, ctx); continue;
        }
        if (name === "includegraphics") {
          const o = readOpt(src, j); const g = readGroup(src, o ? o[1] : j);
          if (g) { flush(); out.push({ t: "image", name: g[0].trim() }); i = g[1]; continue; }
        }
        if (SKIP_BLOCK.has(name) || ARG1_SKIP.has(name)) {
          let k = j;
          if (ARG1_SKIP.has(name)) { const o = readOpt(src, k); const g = readGroup(src, o ? o[1] : k); k = g ? g[1] : k; }
          i = k; continue;
        }
      }
    }
    buf += src[i]; i++;
  }
  flush();
  return out;
}

/** \newcommand / \newtheorem / \DeclareMathOperator in the text: remembered, shows nothing. Returns the index after it. */
function declaration(src: string, at: number, name: string, ctx: Ctx): number {
  let j = at + name.length + 1;
  if (name === "newtheorem") {
    if (src[j] === "*") j++;
    const g = readGroup(src, j);
    if (!g) return j;
    const o1 = readOpt(src, g[1]); const t = readGroup(src, o1 ? o1[1] : g[1]);
    if (t) { ctx.theorems[g[0].trim()] = t[0].trim(); const o2 = readOpt(src, t[1]); return o2 ? o2[1] : t[1]; }
    return g[1];
  }
  if (name === "def") {
    const m = /^\s*(\\[a-zA-Z]+)/.exec(src.slice(j));
    if (!m) return j;
    const b = readGroup(src, j + m[0].length);
    if (b && !/#\d/.test(b[0])) ctx.macros[m[1]] = b[0];
    return b ? b[1] : j + m[0].length;
  }
  let nm: string | null = null;
  const g = readGroup(src, j);
  if (g) { nm = g[0].trim(); j = g[1]; } else { const m = /^\s*(\\[a-zA-Z]+)/.exec(src.slice(j)); if (m) { nm = m[1]; j += m[0].length; } }
  const nOpt = readOpt(src, j); if (nOpt) j = nOpt[1];
  const dOpt = readOpt(src, j); if (dOpt) j = dOpt[1];
  const b = readGroup(src, j);
  if (!b || !nm) return j;
  if (name === "DeclareMathOperator") ctx.macros[nm] = `\\operatorname{${b[0]}}`;
  else if (!nOpt) ctx.macros[nm] = b[0];
  return b[1];
}

function envBlock(env: string, body: string, src: string, bodyStart: number, ctx: Ctx, out: Block[]): void {
  const bare = env.replace(/\*$/, "");
  const starred = env.endsWith("*");
  if (["equation", "align", "gather", "multline", "eqnarray", "flalign", "alignat", "displaymath", "math", "split"].includes(bare)) {
    out.push({ t: "math", html: mathHtml(ctx, bare === "alignat" ? body.replace(/^\s*\{\d+\}/, "") : body, bare === "alignat" ? "align" : bare, !starred) });
  } else if (bare === "itemize" || bare === "enumerate" || bare === "description") {
    out.push(parseList(body, bare === "itemize" ? "ul" : bare === "enumerate" ? "ol" : "dl", ctx));
  } else if (bare === "abstract") out.push({ t: "abstract", c: parseBlocks(body, ctx) });
  else if (bare === "quote" || bare === "quotation") out.push({ t: "quote", c: parseBlocks(body, ctx) });
  else if (bare === "center") out.push({ t: "center", c: parseBlocks(body, ctx) });
  else if (bare === "verbatim" || bare === "lstlisting" || bare === "minted" || bare === "Verbatim") out.push({ t: "pre", s: body.replace(/^\s*\[[^\]]*\]/, "").replace(/^\n/, "").replace(/\n$/, "") });
  else if (bare === "tabular" || bare === "tabularx" || bare === "array" || bare === "longtable") {
    const g = readGroup(body, 0);
    const w = bare === "tabularx" ? readGroup(body, g ? g[1] : 0) : null;
    const specGroup = bare === "tabularx" && w ? w : g;
    out.push(parseTable(specGroup ? body.slice(specGroup[1]) : body, specGroup ? specGroup[0] : "", ctx));
  } else if (bare === "table" || bare === "figure" || bare === "wrapfigure" || bare === "wraptable") {
    const kind = bare.endsWith("table") ? "table" : "figure";
    ctx.counters[kind] = (ctx.counters[kind] ?? 0) + 1; ctx.cur = String(ctx.counters[kind]);
    let caption: Inline[] | null = null;
    let clean = body;
    const cm = /\\caption\*?/.exec(body);
    if (cm) {
      const j = cm.index + cm[0].length;
      const o = readOpt(body, j);
      const g = readGroup(body, o ? o[1] : j);
      if (g) { caption = parseInline(g[0], ctx); clean = body.slice(0, cm.index) + body.slice(g[1]); }
    }
    clean = clean.replace(/^\s*\[[^\]]*\]/, "");                      // the placement, [h] or [htbp]
    out.push({ t: "float", kind, num: ctx.cur, caption, c: parseBlocks(clean, ctx) });
  } else if (bare === "thebibliography") {
    const items: Array<{ num: number; key: string; c: Inline[] }> = [];
    const parts = body.replace(/^\s*\{[^}]*\}/, "").split(/\\bibitem/).slice(1);
    parts.forEach((p, k) => {
      const o = readOpt(p, 0); const g = readGroup(p, o ? o[1] : 0);
      if (!g) return;
      ctx.cites[g[0].trim()] = k + 1;
      items.push({ num: k + 1, key: g[0].trim(), c: parseInline(squash(p.slice(g[1]).trim()), ctx) });
    });
    out.push({ t: "bib", items });
  } else if (bare === "proof" || bare in THEOREMS || bare in ctx.theorems) {
    const proof = bare === "proof";
    const title = ctx.theorems[bare] ?? THEOREMS[bare] ?? (bare.charAt(0).toUpperCase() + bare.slice(1));
    let num = "";
    if (!proof && !starred) { ctx.counters[`t_${bare}`] = (ctx.counters[`t_${bare}`] ?? 0) + 1; num = String(ctx.counters[`t_${bare}`]); ctx.cur = num; }
    const o = readOpt(body, 0);
    out.push({ t: "thm", name: proof ? "Proof" : title, num, note: o ? parseInline(o[0], ctx) : null, c: parseBlocks(o ? body.slice(o[1]) : body, ctx), proof });
  } else if (bare === "document") out.push(...parseBlocks(body, ctx));
  else if (["minipage", "flushleft", "flushright", "samepage", "small", "footnotesize", "large", "tiny", "spacing", "singlespace", "doublespace"].includes(bare)) {
    out.push(...parseBlocks(bare === "minipage" ? body.replace(/^\s*(\[[^\]]*\])?\s*\{[^}]*\}/, "") : body, ctx));
  } else out.push(...parseBlocks(body, ctx));
  void src; void bodyStart;
}

/** Parse a LaTeX document. `math` typesets a formula to HTML (KaTeX in the viewer; a stub in tests). */
export function parseLatex(source: string, math: MathRenderer): LatexDoc {
  const mk = (known: Record<string, string>): Ctx => ({
    macros: {}, theorems: {}, counters: {}, cur: "", labels: {}, known, cites: {}, headings: [],
    meta: { title: [], author: [], date: [] }, math, steps: 0, errors: 0,
  });
  const run = (known: Record<string, string>): { blocks: Block[]; ctx: Ctx } => {
    const ctx = mk(known);
    let body = stripComments(source.replace(/\r\n?/g, "\n"));
    const d = body.indexOf("\\begin{document}");
    if (d >= 0) {
      const pre = body.slice(0, d);
      parseBlocks(pre, ctx);                                // the preamble: \title, \author, macros, theorems
      ctx.headings = []; ctx.counters = {}; ctx.cur = "";
      const e = body.indexOf("\\end{document}");
      body = body.slice(d + "\\begin{document}".length, e > d ? e : undefined);
    }
    // the title block's \maketitle needs the preamble's metadata, so the body is parsed after it
    try { return { blocks: parseBlocks(body, ctx), ctx }; } catch { ctx.errors++; return { blocks: [{ t: "pre", s: source }], ctx }; }
  };
  const first = run({});
  const second = run(first.ctx.labels);
  return { blocks: second.blocks, labels: second.ctx.labels, errors: second.ctx.errors, cites: second.ctx.cites };
}

/** The number a \ref or \cite shows. */
export function refText(doc: { labels: Record<string, string> }, label: string): string {
  return doc.labels[label] ?? "??";
}
