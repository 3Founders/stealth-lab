// Facts about the company that the legal pages can only state if someone supplies them. Nothing here is guessed: a value
// that is not set is simply not shown. Set them as build-time environment variables (they appear on public pages).
//   NEXT_PUBLIC_LEGAL_ENTITY   the name of the company that provides Kell
//   NEXT_PUBLIC_LEGAL_EMAIL    where people write about privacy, copyright, security and misuse
//   NEXT_PUBLIC_LEGAL_LAW      the governing law and where disputes are heard, as one sentence
const clean = (v: string | undefined) => (v ?? "").trim();

export const LEGAL_CONFIG = {
  entity: clean(process.env.NEXT_PUBLIC_LEGAL_ENTITY),
  email: clean(process.env.NEXT_PUBLIC_LEGAL_EMAIL),
  law: clean(process.env.NEXT_PUBLIC_LEGAL_LAW),
};

const esc = (t: string) => t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

/** The closing block of every legal page, built only from what is set. Empty string when nothing is. */
export function legalFooterHtml(cfg = LEGAL_CONFIG): string {
  const rows: string[] = [];
  if (cfg.entity) rows.push(`<p><b>Company.</b> Kell is provided by ${esc(cfg.entity)}.</p>`);
  if (cfg.email) rows.push(`<p><b>Contact.</b> Write to <a href="mailto:${esc(cfg.email)}" rel="noopener">${esc(cfg.email)}</a> about privacy, copyright, security and misuse.</p>`);
  if (cfg.law) rows.push(`<p><b>Governing law.</b> ${esc(cfg.law)}</p>`);
  return rows.length ? `<hr /><h2>Contact and company</h2>${rows.join("")}` : "";
}
