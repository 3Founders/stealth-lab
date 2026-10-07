/**
 * Client-side CSV export for dashboard tables. Values are escaped per RFC 4180, and cells that a spreadsheet would
 * evaluate as a formula (leading = + - @ tab CR) are prefixed with an apostrophe so exported attacker-controlled text
 * (a model name, a note) can't run as a formula when an admin opens the file (CSV injection).
 */
export type Cell = string | number | bigint | boolean | null | undefined;

export function csvCell(value: Cell): string {
  if (value == null) return "";
  let s = String(value);
  if (/^[=+\-@\t\r]/.test(s) && !/^-?\d+(\.\d+)?$/.test(s)) s = `'${s}`; // plain negative numbers stay numbers
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export function toCsv(header: string[], rows: Cell[][]): string {
  return [header, ...rows].map((r) => r.map(csvCell).join(",")).join("\r\n") + "\r\n";
}

export function downloadCsv(filename: string, header: string[], rows: Cell[][]): void {
  const blob = new Blob(["﻿" + toCsv(header, rows)], { type: "text/csv;charset=utf-8" }); // BOM so Excel reads UTF-8
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}
