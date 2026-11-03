// @vitest-environment node
import katex from "katex";
import { describe, expect, it } from "vitest";
import { parseLatex, stripComments, type Block, type Inline } from "@/lib/chat/latex";

const math = (src: string, display: boolean, macros: Record<string, string>) =>
  katex.renderToString(src, { displayMode: display, throwOnError: false, strict: "ignore", trust: false, macros: { ...macros } });

const DOC = String.raw`
\documentclass{article}
\usepackage{amsmath}
\newtheorem{theorem}{Theorem}
\newcommand{\R}{\mathbb{R}}
\title{On \emph{Sums}}
\author{A. Author}
\date{1 January 2026}
\begin{document}
\maketitle
\begin{abstract}
We sum things. % a comment
\end{abstract}
\section{Introduction}\label{sec:intro}
Let $x \in \R$. This is \textbf{bold}, \emph{emph} and 50\% done -- see Section~\ref{sec:intro}.

\begin{itemize}
  \item first
  \item second with $a^2$
  \begin{enumerate}
    \item nested one
  \end{enumerate}
\end{itemize}
\begin{theorem}[Gauss]\label{thm:g}
$\sum_{i=1}^n i = \frac{n(n+1)}{2}$.
\end{theorem}
\begin{proof}
Induction. \qedhere
\end{proof}
\begin{equation}\label{eq:one} e^{i\pi}+1=0 \end{equation}
As in \eqref{eq:one} and Theorem~\ref{thm:g}; see \cite{knuth}.
\begin{align}
a &= b \\
c &= d \nonumber
\end{align}
\subsection{A table}
\begin{table}[h]\centering
\begin{tabular}{|l|c|r|}
\hline
Name & Qty & Price \\ \hline
Tea & 2 & 3.5 \\
\multicolumn{2}{|c|}{Total} & 7 \\ \hline
\end{tabular}
\caption{Prices.}\label{tab:p}
\end{table}
\begin{verbatim}
x = 1  % not a comment
\end{verbatim}
\begin{thebibliography}{9}
\bibitem{knuth} D. Knuth, \emph{The TeXbook}.
\end{thebibliography}
\end{document}
`;

const kinds = (bs: Block[]) => bs.map((b) => b.t);
const text = (c: Inline[]): string => c.map((n) => ("s" in n && n.t === "text" ? n.s : "c" in n ? text(n.c as Inline[]) : n.t === "math" ? `$${n.s}$` : n.t === "br" ? "\n" : "")).join("");

describe("LaTeX reader", () => {
  const doc = parseLatex(DOC, math);

  it("builds the title block, the abstract, sections and lists in order", () => {
    expect(kinds(doc.blocks)).toEqual(["title", "abstract", "h", "p", "list", "thm", "thm", "math", "p", "math", "h", "float", "pre", "bib"]);
    const title = doc.blocks[0] as Extract<Block, { t: "title" }>;
    expect(text(title.title)).toBe("On Sums");
    expect(text(title.author)).toBe("A. Author");
    expect(text(title.date)).toBe("1 January 2026");
  });

  it("numbers sections, theorems and equations, and resolves \\ref, \\eqref and \\cite", () => {
    expect(doc.labels["sec:intro"]).toBe("1");
    expect(doc.labels["thm:g"]).toBe("1");
    expect(doc.labels["eq:one"]).toBe("1");
    expect(doc.labels["tab:p"]).toBe("1");
    expect(doc.cites.knuth).toBe(1);
    const h = doc.blocks[2] as Extract<Block, { t: "h" }>;
    expect(h.num).toBe("1");
  });

  it("keeps maths for KaTeX, expands a simple \\newcommand into it, and drops comments and \\label", () => {
    const p = doc.blocks[3] as Extract<Block, { t: "p" }>;
    const m = p.c.find((n) => n.t === "math") as { t: "math"; s: string };
    expect(m.s).toBe(String.raw`x \in \R`);
    expect(text(p.c)).toContain("50% done – see Section");
    const eq = doc.blocks[7] as Extract<Block, { t: "math" }>;
    expect(eq.html).toContain("katex") && expect(eq.html).not.toContain("label");
  });

  it("lists nest and tables keep their rules, spans and alignment", () => {
    const list = doc.blocks[4] as Extract<Block, { t: "list" }>;
    expect(list.kind).toBe("ul");
    expect(list.items).toHaveLength(2);
    expect(kinds(list.items[1].c)).toEqual(["p", "list"]);
    const float = doc.blocks[11] as Extract<Block, { t: "float" }>;
    expect(float.kind).toBe("table");
    expect(text(float.caption ?? [])).toBe("Prices.");
    const table = float.c[0] as Extract<Block, { t: "table" }>;
    expect(table.t).toBe("table");
    expect(table.rows).toHaveLength(3);
    expect(table.rows[0][0].top).toBe(true);
    expect(table.rows[2][0].span).toBe(2);
    expect(table.rows[1].map((c) => c.align)).toEqual(["l", "c", "r"]);
  });

  it("shows verbatim text untouched, including a % in it", () => {
    const pre = doc.blocks[12] as Extract<Block, { t: "pre" }>;
    expect(pre.s).toBe("x = 1  % not a comment");
  });

  it("align rows are numbered one by one except \\nonumber", () => {
    const al = doc.blocks[9] as Extract<Block, { t: "math" }>;
    expect(al.html).toContain("katex");
    expect(doc.labels).not.toHaveProperty("undefined");
  });
});

describe("robustness", () => {
  it("never throws on broken or hostile input and always ends", () => {
    for (const src of ["\\begin{itemize} \\item", "\\section{", "$$ unclosed", "{{{{{{{", "\\begin{tabular}{ll} a & b", "\\\\\\\\\\\\", "\\end{x}", "\\begin{theorem}", "%", "", "\\newcommand", "\\ref{", "\\href{a}"]) {
      expect(() => parseLatex(src, math)).not.toThrow();
    }
    const big = "\\section{A}\n" + "word ".repeat(50_000);
    expect(() => parseLatex(big, math)).not.toThrow();
  });

  it("a document without \\begin{document} is read as a body", () => {
    const d = parseLatex("Hello $x^2$ and \\textit{world}.\n\n\\section*{Plain}", math);
    expect(kinds(d.blocks)).toEqual(["p", "h"]);
    expect((d.blocks[1] as Extract<Block, { t: "h" }>).num).toBe("");
  });

  it("comments: an escaped % stays", () => {
    expect(stripComments("50\\% sure % gone\nnext")).toBe("50\\% sure \nnext");
  });

  it("maths that KaTeX cannot read does not break the page", () => {
    const d = parseLatex("\\[ \\frac{1}{ \\]", math);
    expect(d.blocks[0].t).toBe("math");
  });
});
