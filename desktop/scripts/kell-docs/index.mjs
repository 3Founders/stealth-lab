// kell-docs: real Office and PDF files for the desktop engine (docs/desktop_requirements_2026-11.md, P0 #2, 2026-11-17).
//
//   kell-docs make <spec.json>     write a .docx, .xlsx, .pptx, .pdf, .csv, .md or .html file from a JSON spec
//   kell-docs read <file>          print the text of a .docx, .xlsx, .pptx, .csv or .md file (for reading attachments)
//   kell-docs spec                 print the spec format
//
// Bundled into vendor/kell-docs/kell-docs.mjs by scripts/bundle-cli.mjs and run through the app's own runtime (lib/docs.ts puts a
// `kell-docs` command on the engine's PATH). Every file it writes is logged to ~/.stealthlab/desktop/docs.jsonl, which is how the
// app shows a card for it beside the chat; a file it replaces is kept first under .kell-versions/ next to it, so a change can be
// undone from the preview's version list.
import fs from "node:fs"
import os from "node:os"
import path from "node:path"
import { marked } from "marked"
import * as docx from "docx"
import PptxGenJS from "pptxgenjs"
import ExcelJS from "exceljs"
import PDFDocument from "pdfkit"
import mammoth from "mammoth"
import JSZip from "jszip"

const SPEC = `kell-docs make <spec.json>   (or: kell-docs make - < spec.json)

{ "path": "Quarterly report.docx",          // where to write it; the extension picks the format:
                                            //   .docx .pdf .md .html  -> "markdown"
                                            //   .xlsx .csv            -> "sheets"
                                            //   .pptx                 -> "slides"
  "title": "Quarterly report",
  "markdown": "# Heading\\n\\nText with **bold** and *italic*.\\n\\n- a list\\n\\n| a | b |\\n|---|---|\\n| 1 | 2 |",
  "sheets": [ { "name": "Sales", "columns": ["Month", "Revenue", "Cost", "Profit"],
                "rows": [["Jan", 1200, 800, "=B2-C2"], ["Feb", 1500, 900, "=B3-C3"]],
                "widths": [14, 12, 12, 12], "totals": true } ],         // a cell starting with "=" is a formula
  "slides": [ { "title": "Q3 at a glance", "subtitle": "optional, first slide only",
                "bullets": ["Revenue up 12%", "Two new customers"], "notes": "speaker notes",
                "table": { "columns": ["Region", "Sales"], "rows": [["EU", 10], ["US", 14]] },
                "chart": { "type": "bar", "title": "Revenue", "labels": ["Jan", "Feb"],      // bar | line | pie | doughnut | area
                           "series": [{ "name": "Revenue", "values": [1200, 1500] }] },
                "image": "chart.png" } ],
  "theme": { "font": "Calibri", "color": "1F4E79" } }

In markdown (docx, pdf), a fenced block of language "chart" holding the chart JSON above becomes a chart: a table of its values in
.docx, bars drawn in .pdf. Use .pptx for native, editable charts.`

const home = process.env.STEALTHLAB_HOME || path.join(os.homedir(), ".stealthlab")
const logFile = path.join(home, "desktop", "docs.jsonl")

function log(file, kind) {
  try {
    fs.mkdirSync(path.dirname(logFile), { recursive: true })
    fs.appendFileSync(logFile, JSON.stringify({ path: file, kind, at: Date.now(), cwd: process.cwd() }) + "\n")
  } catch { /* the file is made either way */ }
}

/** Keep the file this write replaces: <dir>/.kell-versions/<name>/<stamp><ext>. */
function keepVersion(file) {
  if (!fs.existsSync(file)) return
  const dir = path.join(path.dirname(file), ".kell-versions", path.basename(file))
  fs.mkdirSync(dir, { recursive: true })
  const stamp = new Date(fs.statSync(file).mtimeMs).toISOString().replace(/[:.]/g, "-")
  fs.copyFileSync(file, path.join(dir, stamp + path.extname(file)))
}

const hex = (c, d) => String(c || d).replace(/^#/, "").slice(0, 6)

// ---- markdown -> docx ----------------------------------------------------------------------------------------------
function runs(tokens, style = {}) {
  const out = []
  for (const t of tokens || []) {
    if (t.type === "strong") out.push(...runs(t.tokens, { ...style, bold: true }))
    else if (t.type === "em") out.push(...runs(t.tokens, { ...style, italics: true }))
    else if (t.type === "del") out.push(...runs(t.tokens, { ...style, strike: true }))
    else if (t.type === "codespan") out.push(new docx.TextRun({ text: t.text, font: "Consolas", ...style }))
    else if (t.type === "link") out.push(new docx.ExternalHyperlink({ link: t.href, children: [new docx.TextRun({ text: t.text, style: "Hyperlink", ...style })] }))
    else if (t.type === "br") out.push(new docx.TextRun({ break: 1 }))
    else if (t.tokens?.length) out.push(...runs(t.tokens, style))
    else out.push(new docx.TextRun({ text: decode(t.text ?? t.raw ?? ""), ...style }))
  }
  return out
}
const decode = (s) => String(s).replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#39;/g, "'")

function docxTable(header, rows) {
  const cell = (content, bold) => new docx.TableCell({
    children: [new docx.Paragraph({ children: typeof content === "string" ? [new docx.TextRun({ text: content, bold })] : runs(content, bold ? { bold: true } : {}) })],
    margins: { top: 60, bottom: 60, left: 100, right: 100 },
  })
  return new docx.Table({
    width: { size: 100, type: docx.WidthType.PERCENTAGE },
    rows: [
      new docx.TableRow({ tableHeader: true, children: header.map((h) => cell(h, true)) }),
      ...rows.map((r) => new docx.TableRow({ children: r.map((c) => cell(c, false)) })),
    ],
  })
}

function chartAsTable(spec) {
  const header = ["", ...(spec.series || []).map((s) => String(s.name || ""))]
  const rows = (spec.labels || []).map((l, i) => [String(l), ...(spec.series || []).map((s) => String(s.values?.[i] ?? ""))])
  return [new docx.Paragraph({ children: [new docx.TextRun({ text: spec.title || "Chart", bold: true })] }), docxTable(header, rows)]
}

function docxBlocks(tokens, list = null, level = 0) {
  const out = []
  for (const t of tokens) {
    if (t.type === "heading") out.push(new docx.Paragraph({ heading: [docx.HeadingLevel.HEADING_1, docx.HeadingLevel.HEADING_2, docx.HeadingLevel.HEADING_3, docx.HeadingLevel.HEADING_4][Math.min(t.depth, 4) - 1], children: runs(t.tokens) }))
    else if (t.type === "paragraph") out.push(new docx.Paragraph({ children: runs(t.tokens), spacing: { after: 120 } }))
    else if (t.type === "text") out.push(new docx.Paragraph({ children: runs(t.tokens || [{ type: "text", text: t.text }]), ...(list ? (list.ordered ? { numbering: { reference: "num", level } } : { bullet: { level } }) : {}) }))
    else if (t.type === "list") {
      for (const it of t.items) {
        const [first, ...rest] = it.tokens
        const firstRuns = first?.type === "text" || first?.type === "paragraph" ? runs(first.tokens || [{ type: "text", text: first.text }]) : []
        out.push(new docx.Paragraph({ children: firstRuns, ...(t.ordered ? { numbering: { reference: "num", level } } : { bullet: { level } }) }))
        for (const r of rest) out.push(...(r.type === "list" ? docxBlocks([r], t, level + 1) : docxBlocks([r])))
      }
    }
    else if (t.type === "table") out.push(docxTable(t.header.map((h) => h.tokens), t.rows.map((r) => r.map((c) => c.tokens))), new docx.Paragraph({ text: "" }))
    else if (t.type === "code" && t.lang === "chart") { try { out.push(...chartAsTable(JSON.parse(t.text)), new docx.Paragraph({ text: "" })) } catch { out.push(new docx.Paragraph({ text: t.text })) } }
    else if (t.type === "code") out.push(...t.text.split("\n").map((line) => new docx.Paragraph({ children: [new docx.TextRun({ text: line, font: "Consolas", size: 19 })], shading: { type: docx.ShadingType.CLEAR, fill: "F3F3F3" } })))
    else if (t.type === "blockquote") out.push(...docxBlocks(t.tokens).map((p) => p))
    else if (t.type === "hr") out.push(new docx.Paragraph({ thematicBreak: true, text: "" }))
  }
  return out
}

async function makeDocx(spec, file) {
  const font = spec.theme?.font || "Calibri"
  const tokens = marked.lexer(String(spec.markdown || ""))
  const children = []
  if (spec.title && !(tokens[0]?.type === "heading" && tokens[0].depth === 1)) children.push(new docx.Paragraph({ heading: docx.HeadingLevel.TITLE, children: [new docx.TextRun(spec.title)] }))
  children.push(...docxBlocks(tokens))
  const doc = new docx.Document({
    creator: "kell", title: spec.title || path.basename(file, ".docx"),
    styles: { default: { document: { run: { font, size: 22 } } } },
    numbering: { config: [{ reference: "num", levels: [0, 1, 2, 3].map((l) => ({ level: l, format: docx.LevelFormat.DECIMAL, text: `%${l + 1}.`, alignment: docx.AlignmentType.START, style: { paragraph: { indent: { left: 720 * (l + 1), hanging: 360 } } } })) }] },
    sections: [{ children }],
  })
  fs.writeFileSync(file, await docx.Packer.toBuffer(doc))
}

// ---- xlsx / csv ---------------------------------------------------------------------------------------------------------
const colLetter = (n) => { let s = ""; n++; while (n > 0) { const m = (n - 1) % 26; s = String.fromCharCode(65 + m) + s; n = Math.floor((n - 1) / 26) } return s }

// Works out simple formulas (cell refs, + - * / ( ), SUM/AVERAGE/MIN/MAX/COUNT over ranges) and stores the result with the formula, so a
// preview or a reader that does not recalculate shows numbers. Anything it cannot work out is left for Excel to calculate on open.
function computeFormulas(ws) {
  const memo = new Map()
  const colNum = (s) => s.split("").reduce((n, ch) => n * 26 + ch.charCodeAt(0) - 64, 0)
  const value = (ref, depth = 0) => {
    if (memo.has(ref)) return memo.get(ref)
    if (depth > 50) return NaN
    const cell = ws.getCell(ref)
    const v = cell.value
    let out
    if (v && typeof v === "object" && v.formula) out = evaluate(v.formula, depth + 1)
    else out = typeof v === "number" ? v : v == null || v === "" ? 0 : Number(v)
    memo.set(ref, out)
    return out
  }
  const range = (a, b, depth) => {
    const [, c1, r1] = a.match(/([A-Z]+)(\d+)/), [, c2, r2] = b.match(/([A-Z]+)(\d+)/)
    const out = []
    for (let r = Math.min(+r1, +r2); r <= Math.max(+r1, +r2); r++)
      for (let c = Math.min(colNum(c1), colNum(c2)); c <= Math.max(colNum(c1), colNum(c2)); c++) {
        const v = ws.getCell(r, c).value
        if (v != null && v !== "") out.push(value(`${colLetter(c - 1)}${r}`, depth))
      }
    return out.filter((x) => Number.isFinite(x))
  }
  const FN = { SUM: (a) => a.reduce((x, y) => x + y, 0), AVERAGE: (a) => (a.length ? a.reduce((x, y) => x + y, 0) / a.length : NaN),
    MIN: (a) => Math.min(...a), MAX: (a) => Math.max(...a), COUNT: (a) => a.length }
  function evaluate(f, depth) {
    let e = String(f).toUpperCase().replace(/\$/g, "")
    e = e.replace(/(SUM|AVERAGE|MIN|MAX|COUNT)\(([A-Z]+\d+):([A-Z]+\d+)\)/g, (_m, fn, a, b) => String(FN[fn](range(a, b, depth))))
    e = e.replace(/\b([A-Z]{1,3}\d+)\b/g, (_m, ref) => String(value(ref, depth)))
    if (!/^[\d\s.+\-*/()eE]+$/.test(e)) return NaN
    try { const r = Function(`"use strict";return (${e})`)(); return typeof r === "number" ? r : NaN } catch { return NaN }
  }
  ws.eachRow((row) => row.eachCell((cell) => {
    const v = cell.value
    if (v && typeof v === "object" && v.formula) {
      const r = value(cell.address)
      if (Number.isFinite(r)) cell.value = { formula: v.formula, result: Math.round(r * 1e10) / 1e10 }
    }
  }))
}

async function makeXlsx(spec, file) {
  const wb = new ExcelJS.Workbook()
  wb.creator = "kell"
  const color = hex(spec.theme?.color, "1F4E79")
  for (const [k, sh] of (spec.sheets || []).entries()) {
    const ws = wb.addWorksheet(String(sh.name || `Sheet${k + 1}`).slice(0, 31))
    const cols = sh.columns || []
    if (cols.length) {
      const head = ws.addRow(cols)
      head.font = { bold: true, color: { argb: "FFFFFFFF" } }
      head.eachCell((c) => { c.fill = { type: "pattern", pattern: "solid", fgColor: { argb: "FF" + color } } })
      ws.views = [{ state: "frozen", ySplit: 1 }]
    }
    for (const r of sh.rows || []) {
      ws.addRow(r.map((v) => (typeof v === "string" && v.startsWith("=") ? { formula: v.slice(1) } : v)))
    }
    if (sh.totals && cols.length && (sh.rows || []).length) {
      const first = cols.length ? 2 : 1, last = first + sh.rows.length - 1
      const row = cols.map((_, c) => {
        if (c === 0) return "Total"
        const numeric = sh.rows.some((r) => typeof r[c] === "number" || (typeof r[c] === "string" && r[c].startsWith("=")))
        return numeric ? { formula: `SUM(${colLetter(c)}${first}:${colLetter(c)}${last})` } : ""
      })
      ws.addRow(row).font = { bold: true }
    }
    computeFormulas(ws)
    ws.columns.forEach((c, i) => {
      const w = sh.widths?.[i]
      if (w) { c.width = w; return }
      let max = 8
      c.eachCell?.({ includeEmpty: false }, (cell) => { max = Math.max(max, String(cell.value?.formula ? "000000000" : cell.value ?? "").length + 2) })
      c.width = Math.min(60, max)
    })
    if (cols.length) ws.autoFilter = { from: "A1", to: `${colLetter(cols.length - 1)}1` }
  }
  if (!(spec.sheets || []).length) wb.addWorksheet("Sheet1")
  await wb.xlsx.writeFile(file)
}

function makeCsv(spec, file) {
  const sh = (spec.sheets || [])[0] || { rows: [] }
  const q = (v) => { const s = String(v ?? ""); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s }
  const lines = [...(sh.columns ? [sh.columns] : []), ...(sh.rows || [])].map((r) => r.map(q).join(","))
  fs.writeFileSync(file, lines.join("\r\n") + "\r\n")
}

// ---- pptx ----------------------------------------------------------------------------------------------------------------
async function makePptx(spec, file) {
  const p = new PptxGenJS()
  p.layout = "LAYOUT_WIDE"
  p.title = spec.title || path.basename(file, ".pptx")
  const font = spec.theme?.font || "Calibri"
  const color = hex(spec.theme?.color, "1F4E79")
  const slides = spec.slides?.length ? spec.slides : [{ title: spec.title || "Untitled" }]
  slides.forEach((s, i) => {
    const sl = p.addSlide()
    if (i === 0 && !s.bullets?.length && !s.chart && !s.table && !s.text) {
      sl.background = { color }
      sl.addText(s.title || spec.title || "", { x: 0.7, y: 2.4, w: 11.9, h: 1.4, fontFace: font, fontSize: 40, bold: true, color: "FFFFFF" })
      if (s.subtitle) sl.addText(s.subtitle, { x: 0.7, y: 3.8, w: 11.9, h: 0.8, fontFace: font, fontSize: 20, color: "E8EEF5" })
      if (s.notes) sl.addNotes(s.notes)
      return
    }
    sl.addShape(p.ShapeType.rect, { x: 0, y: 0, w: 13.33, h: 0.12, fill: { color } })
    sl.addText(s.title || "", { x: 0.6, y: 0.35, w: 12.1, h: 0.9, fontFace: font, fontSize: 28, bold: true, color: "1A1A1A" })
    const hasSide = !!(s.chart || s.table || s.image)
    const textW = hasSide ? 5.6 : 12.1
    if (s.bullets?.length) sl.addText(s.bullets.map((b) => ({ text: String(b), options: { bullet: true, breakLine: true } })), { x: 0.6, y: 1.45, w: textW, h: 5.4, fontFace: font, fontSize: 18, valign: "top", color: "333333", paraSpaceAfter: 8 })
    else if (s.text) sl.addText(String(s.text), { x: 0.6, y: 1.45, w: textW, h: 5.4, fontFace: font, fontSize: 18, valign: "top", color: "333333" })
    const sx = hasSide && (s.bullets?.length || s.text) ? 6.5 : 0.6, sw = hasSide && (s.bullets?.length || s.text) ? 6.2 : 12.1
    if (s.chart) {
      const type = { bar: p.ChartType.bar, line: p.ChartType.line, pie: p.ChartType.pie, doughnut: p.ChartType.doughnut, area: p.ChartType.area, scatter: p.ChartType.scatter }[s.chart.type] || p.ChartType.bar
      const data = (s.chart.series || []).map((se) => ({ name: String(se.name || ""), labels: (s.chart.labels || []).map(String), values: (se.values || []).map(Number) }))
      sl.addChart(type, data, { x: sx, y: 1.45, w: sw, h: 5.4, showLegend: data.length > 1 || ["pie", "doughnut"].includes(s.chart.type), legendPos: "b",
        showTitle: !!s.chart.title, title: s.chart.title, titleFontFace: font, catAxisLabelFontFace: font, valAxisLabelFontFace: font,
        showPercent: ["pie", "doughnut"].includes(s.chart.type), chartColors: ["1F4E79", "2E86AB", "F18F01", "C73E1D", "3B1F2B", "6A994E"] })
    } else if (s.table) {
      const rows = [(s.table.columns || []).map((c) => ({ text: String(c), options: { bold: true, color: "FFFFFF", fill: { color } } })), ...(s.table.rows || []).map((r) => r.map((c) => ({ text: String(c ?? "") })))]
      sl.addTable(rows, { x: sx, y: 1.45, w: sw, fontFace: font, fontSize: 14, border: { type: "solid", color: "CCCCCC", pt: 0.75 }, autoPage: false })
    } else if (s.image) {
      const img = path.resolve(String(s.image))
      if (fs.existsSync(img)) sl.addImage({ path: img, x: sx, y: 1.45, w: sw, h: 5.4, sizing: { type: "contain", w: sw, h: 5.4 } })
    }
    if (s.notes) sl.addNotes(String(s.notes))
  })
  await p.writeFile({ fileName: file })
}

// ---- pdf -----------------------------------------------------------------------------------------------------------------
function makePdf(spec, file) {
  return new Promise((resolve, reject) => {
    const doc = new PDFDocument({ size: "A4", margins: { top: 64, bottom: 64, left: 64, right: 64 }, info: { Title: spec.title || "", Creator: "kell" } })
    const out = fs.createWriteStream(file)
    doc.pipe(out)
    out.on("finish", resolve); out.on("error", reject)
    const W = doc.page.width - 128
    const plain = (tokens) => (tokens || []).map((t) => (t.tokens ? plain(t.tokens) : decode(t.text ?? t.raw ?? ""))).join("")
    const rich = (tokens, opts = {}) => {
      const parts = []
      const walk = (ts, st) => { for (const t of ts || []) {
        if (t.type === "strong") walk(t.tokens, { ...st, b: true })
        else if (t.type === "em") walk(t.tokens, { ...st, i: true })
        else if (t.type === "codespan") parts.push({ text: t.text, font: "Courier" })
        else if (t.tokens?.length) walk(t.tokens, st)
        else parts.push({ text: decode(t.text ?? t.raw ?? ""), font: st.b && st.i ? "Helvetica-BoldOblique" : st.b ? "Helvetica-Bold" : st.i ? "Helvetica-Oblique" : "Helvetica" })
      } }
      walk(tokens, {})
      parts.forEach((p, k) => doc.font(p.font).text(p.text, { continued: k < parts.length - 1, ...opts }))
      if (!parts.length) doc.text("", opts)
    }
    const table = (header, rows) => {
      const n = header.length || 1, cw = W / n
      const draw = (cells, bold) => {
        const h = Math.max(...cells.map((c) => doc.font(bold ? "Helvetica-Bold" : "Helvetica").fontSize(10).heightOfString(c, { width: cw - 8 }))) + 8
        if (doc.y + h > doc.page.height - 64) doc.addPage()
        const y = doc.y
        cells.forEach((c, k) => {
          if (bold) doc.rect(64 + k * cw, y, cw, h).fill("#E8EEF5").fillColor("#000")
          doc.rect(64 + k * cw, y, cw, h).stroke("#BBBBBB")
          doc.fillColor("#000").font(bold ? "Helvetica-Bold" : "Helvetica").fontSize(10).text(c, 68 + k * cw, y + 4, { width: cw - 8 })
        })
        doc.x = 64; doc.y = y + h
      }
      draw(header, true); rows.forEach((r) => draw(r, false)); doc.moveDown(0.6); doc.fontSize(11)
    }
    const chart = (c) => {
      const values = (c.series?.[0]?.values || []).map(Number), labels = (c.labels || []).map(String)
      const max = Math.max(1, ...values), barH = 16
      doc.font("Helvetica-Bold").fontSize(12).text(c.title || "Chart"); doc.moveDown(0.3)
      labels.forEach((l, k) => {
        if (doc.y + barH + 6 > doc.page.height - 64) doc.addPage()
        const y = doc.y
        doc.font("Helvetica").fontSize(9).fillColor("#000").text(l, 64, y + 3, { width: 110 })
        doc.rect(180, y, ((W - 170) * (values[k] || 0)) / max, barH).fill("#1F4E79")
        doc.fillColor("#000").text(String(values[k] ?? ""), 184 + ((W - 170) * (values[k] || 0)) / max, y + 3)
        doc.x = 64; doc.y = y + barH + 6
      })
      doc.moveDown(0.6); doc.fontSize(11)
    }
    const tokens = marked.lexer(String(spec.markdown || ""))
    if (spec.title && !(tokens[0]?.type === "heading" && tokens[0].depth === 1)) { doc.font("Helvetica-Bold").fontSize(22).text(spec.title); doc.moveDown(0.6) }
    const blocks = (ts, indent = 0) => { for (const t of ts) {
      doc.fillColor("#000").fontSize(11)
      if (t.type === "heading") { doc.moveDown(0.4); doc.font("Helvetica-Bold").fontSize([20, 16, 13, 12][Math.min(t.depth, 4) - 1]).text(plain(t.tokens)); doc.moveDown(0.3) }
      else if (t.type === "paragraph" || t.type === "text") { rich(t.tokens || [{ type: "text", text: t.text }], { indent, align: "left" }); doc.moveDown(0.5) }
      else if (t.type === "list") { t.items.forEach((it, k) => { doc.font("Helvetica").text(`${t.ordered ? `${(t.start || 1) + k}.` : "•"} ${plain(it.tokens.filter((x) => x.type !== "list"))}`, { indent: indent + 12 }); const sub = it.tokens.filter((x) => x.type === "list"); if (sub.length) blocks(sub, indent + 18) }); doc.moveDown(0.5) }
      else if (t.type === "table") table(t.header.map((h) => plain(h.tokens)), t.rows.map((r) => r.map((c) => plain(c.tokens))))
      else if (t.type === "code" && t.lang === "chart") { try { chart(JSON.parse(t.text)) } catch { doc.font("Courier").text(t.text) } }
      else if (t.type === "code") { doc.font("Courier").fontSize(9).text(t.text, { indent: 8 }); doc.moveDown(0.5) }
      else if (t.type === "blockquote") { doc.fillColor("#555"); blocks(t.tokens, indent + 14) }
      else if (t.type === "hr") { doc.moveTo(64, doc.y).lineTo(64 + W, doc.y).stroke("#CCCCCC"); doc.moveDown(0.6) }
    } }
    blocks(tokens)
    doc.end()
  })
}

function makeHtml(spec, file) {
  const body = marked.parse(String(spec.markdown || ""))
  fs.writeFileSync(file, `<!doctype html><html><head><meta charset="utf-8"><title>${(spec.title || "").replace(/</g, "&lt;")}</title>
<style>body{font:16px/1.55 system-ui,sans-serif;max-width:760px;margin:40px auto;padding:0 16px;color:#1a1a1a}table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:6px 10px}th{background:#eef2f7}pre{background:#f4f4f4;padding:12px;overflow:auto}</style>
</head><body>${body}</body></html>`)
}

// ---- read ----------------------------------------------------------------------------------------------------------------
export async function readText(file) {
  const ext = path.extname(file).toLowerCase()
  if (ext === ".docx") return (await mammoth.extractRawText({ path: file })).value
  if (ext === ".xlsx") {
    const wb = new ExcelJS.Workbook(); await wb.xlsx.readFile(file)
    const parts = []
    wb.eachSheet((ws) => {
      parts.push(`## ${ws.name}`)
      ws.eachRow({ includeEmpty: false }, (row) => parts.push(row.values.slice(1).map((v) => (v && typeof v === "object" ? (v.result ?? v.text ?? (v.formula ? `=${v.formula}` : JSON.stringify(v))) : v ?? "")).join("\t")))
    })
    return parts.join("\n")
  }
  if (ext === ".pptx") {
    const zip = await JSZip.loadAsync(fs.readFileSync(file))
    const names = Object.keys(zip.files).filter((n) => /^ppt\/slides\/slide\d+\.xml$/.test(n)).sort((a, b) => Number(a.match(/\d+/g).pop()) - Number(b.match(/\d+/g).pop()))
    const out = []
    for (const [k, n] of names.entries()) {
      const xml = await zip.file(n).async("string")
      const paras = xml.split(/<\/a:p>/).map((p) => [...p.matchAll(/<a:t>([^<]*)<\/a:t>/g)].map((m) => decode(m[1])).join("")).filter(Boolean)
      out.push(`## Slide ${k + 1}\n${paras.join("\n")}`)
    }
    return out.join("\n\n")
  }
  return fs.readFileSync(file, "utf8")
}

// ---- html preview (the app's document pane) ----------------------------------------------------------------------------
const esc = (s) => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
const PREVIEW_CSS = `body{font:15px/1.5 system-ui,Segoe UI,sans-serif;margin:0;padding:24px;color:#1a1a1a;background:#fff}
table{border-collapse:collapse;margin:8px 0 20px;font-size:13px}td,th{border:1px solid #d4d4d4;padding:4px 8px;text-align:left;vertical-align:top}
th{background:#eef2f7;font-weight:600}h2.sheet{font-size:14px;margin:18px 0 4px;color:#444}.slide{border:1px solid #ddd;border-radius:8px;
padding:18px 22px;margin:0 0 16px;aspect-ratio:16/9;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.08)}.slide h3{margin:0 0 10px;font-size:20px}
.slide .n{float:right;color:#999;font-size:12px}.f{color:#888;font-style:italic}img{max-width:100%}`

function cellText(v) {
  if (v == null) return ""
  if (typeof v === "object") {
    if ("result" in v && v.result != null) return String(v.result)
    if (v.formula) return "=" + v.formula
    if (v.richText) return v.richText.map((r) => r.text).join("")
    if (v.text) return String(v.text)
    if (v instanceof Date) return v.toISOString().slice(0, 10)
    return JSON.stringify(v)
  }
  return String(v)
}

export async function toHtml(file) {
  const ext = path.extname(file).toLowerCase()
  let body = ""
  if (ext === ".docx") body = (await mammoth.convertToHtml({ path: file })).value
  else if (ext === ".xlsx") {
    const wb = new ExcelJS.Workbook(); await wb.xlsx.readFile(file)
    wb.eachSheet((ws) => {
      body += `<h2 class="sheet">${esc(ws.name)}</h2><table>`
      ws.eachRow({ includeEmpty: false }, (row, n) => {
        const cells = row.values.slice(1)
        body += "<tr>" + cells.map((v) => { const t = cellText(v); const f = v && typeof v === "object" && v.formula && v.result == null; return n === 1 ? `<th>${esc(t)}</th>` : `<td${f ? ' class="f" title="formula: Excel works it out when the file opens"' : ""}>${esc(t)}</td>` }).join("") + "</tr>"
      })
      body += "</table>"
    })
  } else if (ext === ".pptx") {
    const zip = await JSZip.loadAsync(fs.readFileSync(file))
    const names = Object.keys(zip.files).filter((n) => /^ppt\/slides\/slide\d+\.xml$/.test(n)).sort((a, b) => Number(a.match(/\d+/g).pop()) - Number(b.match(/\d+/g).pop()))
    for (const [k, n] of names.entries()) {
      const xml = await zip.file(n).async("string")
      // tables are drawn as tables, not as one bullet per cell
      let tableHtml = ""
      for (const t of xml.match(/<a:tbl>[\s\S]*?<\/a:tbl>/g) || []) {
        const rows = (t.match(/<a:tr[\s\S]*?<\/a:tr>/g) || []).map((r) => (r.match(/<a:tc[\s\S]*?<\/a:tc>/g) || []).map((c) => [...c.matchAll(/<a:t>([^<]*)<\/a:t>/g)].map((m) => decode(m[1])).join("")))
        tableHtml += "<table>" + rows.map((r, i) => "<tr>" + r.map((c) => (i ? `<td>${esc(c)}</td>` : `<th>${esc(c)}</th>`)).join("") + "</tr>").join("") + "</table>"
      }
      const paras = xml.replace(/<a:tbl>[\s\S]*?<\/a:tbl>/g, "").split(/<\/a:p>/).map((p) => [...p.matchAll(/<a:t>([^<]*)<\/a:t>/g)].map((m) => decode(m[1])).join("")).filter(Boolean)
      const rels = await zip.file(n.replace("slides/", "slides/_rels/") + ".rels")?.async("string") || ""
      const charts = [...rels.matchAll(/Target="(?:\.\.|\/ppt)\/charts\/(chart\d+\.xml)"/g)].map((m) => m[1])
      let chartHtml = ""
      for (const c of charts) {
        const cx = await zip.file(`ppt/charts/${c}`)?.async("string") || ""
        const part = (tag) => (cx.split(`<c:${tag}>`)[1] || "").split(`</c:${tag}>`)[0]
        const cats = [...part("cat").matchAll(/<c:v>([^<]*)<\/c:v>/g)].map((m) => decode(m[1]))
        const vals = [...part("val").matchAll(/<c:v>([^<]*)<\/c:v>/g)].map((m) => Number(m[1]))
        const max = Math.max(1, ...vals)
        chartHtml += `<div style="margin-top:8px">${cats.map((c, i) => `<div style="display:flex;gap:8px;align-items:center;font-size:12px"><span style="width:90px">${esc(c)}</span><span style="background:#1F4E79;height:12px;width:${Math.round(260 * (vals[i] || 0) / max)}px"></span><span>${vals[i] ?? ""}</span></div>`).join("")}</div>`
      }
      const [title, ...rest] = paras
      body += `<div class="slide"><span class="n">${k + 1}</span><h3>${esc(title || "")}</h3>${rest.length ? `<ul>${rest.map((p) => `<li>${esc(p)}</li>`).join("")}</ul>` : ""}${tableHtml}${chartHtml}</div>`
    }
  } else if (ext === ".csv") {
    const rows = fs.readFileSync(file, "utf8").split(/\r?\n/).filter(Boolean).slice(0, 2000).map((l) => l.match(/("([^"]|"")*"|[^,]*)(,|$)/g).filter((x, i, a) => i < a.length - 1 || x).map((c) => c.replace(/,$/, "").replace(/^"|"$/g, "").replace(/""/g, '"')))
    body = "<table>" + rows.map((r, i) => "<tr>" + r.map((c) => (i ? `<td>${esc(c)}</td>` : `<th>${esc(c)}</th>`)).join("") + "</tr>").join("") + "</table>"
  } else if (ext === ".md") body = marked.parse(fs.readFileSync(file, "utf8"))
  else if (ext === ".html" || ext === ".htm") return fs.readFileSync(file, "utf8")
  else body = `<pre>${esc(fs.readFileSync(file, "utf8").slice(0, 200000))}</pre>`
  return `<!doctype html><html><head><meta charset="utf-8"><style>${PREVIEW_CSS}</style></head><body>${body}</body></html>`
}

// ---- main ----------------------------------------------------------------------------------------------------------------
async function make(specPath) {
  const raw = specPath === "-" || !specPath ? fs.readFileSync(0, "utf8") : fs.readFileSync(specPath, "utf8")
  const spec = JSON.parse(raw.replace(/^﻿/, ""))
  if (!spec.path) throw new Error('the spec needs a "path", e.g. "report.docx"')
  const file = path.resolve(String(spec.path))
  const ext = path.extname(file).toLowerCase()
  fs.mkdirSync(path.dirname(file), { recursive: true })
  keepVersion(file)
  if (ext === ".docx") await makeDocx(spec, file)
  else if (ext === ".xlsx") await makeXlsx(spec, file)
  else if (ext === ".csv") makeCsv(spec, file)
  else if (ext === ".pptx") await makePptx(spec, file)
  else if (ext === ".pdf") await makePdf(spec, file)
  else if (ext === ".md") fs.writeFileSync(file, String(spec.markdown || ""))
  else if (ext === ".html" || ext === ".htm") makeHtml(spec, file)
  else throw new Error(`kell-docs makes .docx .xlsx .pptx .pdf .csv .md .html, not ${ext || "a file with no extension"}`)
  log(file, ext.slice(1))
  console.log(`made ${file} (${fs.statSync(file).size} bytes). kell shows it beside the chat; tell the person its name.`)
}

const [cmd, arg] = process.argv.slice(2)
try {
  if (cmd === "make") await make(arg)
  else if (cmd === "read" && arg) console.log(await readText(path.resolve(arg)))
  else if (cmd === "html" && arg) process.stdout.write(await toHtml(path.resolve(arg)))
  else console.log(SPEC)
} catch (e) {
  console.error(`kell-docs: ${e?.message || e}`)
  process.exit(1)
}
