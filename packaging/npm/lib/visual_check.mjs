// Visual check for a task built on a screenshot ("recreate the page in screenshot.png"): the cheap answer's page is
// rendered and a model that can see compares it with the screenshot. A text-only check let wrong layouts through
// (2 of 3 GLM pages, 2026-10-14); this one judges what the task is about -- does the page look like the picture.
//
// Fail-closed: no verdict (no browser to render with, no vision model, an error) rejects the answer, so the session's
// model does the task -- an image task is never accepted on the text check alone. STEALTHLAB_DISPATCH_VISUAL_CHECK=off.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { readExecConfig } from "./exec/store.mjs";
import { profileKey } from "./executors/profile.mjs";
import { launchOrThrow, scrubEnv } from "./executors/common.mjs";
import { spawn } from "node:child_process";

export const visualCheckEnabled = (env = process.env) => (env.STEALTHLAB_DISPATCH_VISUAL_CHECK || "on").toLowerCase() !== "off";

export const VISUAL_PROMPT = [
  "Image 1 is the target screenshot of a web page. Image 2 is a recreation of it, at the same width. Decide whether",
  "the recreation matches the target well enough to hand to the person who asked for it. Check each of these and say",
  "MISMATCH if any fails: (1) every section and block of text in the target is present; (2) each sits at about the",
  "same position -- the same column, and roughly the same height down the page relative to the others; (3) blocks",
  "have roughly the same size and the page roughly the same overall height; (4) the colour scheme matches. Fonts,",
  "small spacing differences and placeholder images in place of photos are fine; moved, resized or missing blocks are not.",
  "Reply with one line: MATCH or MISMATCH, then a colon and at most 20 words why.",
].join(" ");

const RENDER_PY = `import sys
from playwright.sync_api import sync_playwright
html, out = sys.argv[1], sys.argv[2]
with sync_playwright() as p:
    b = p.chromium.launch()
    try:
        pg = b.new_page(viewport={"width": 1280, "height": 1024})
        pg.goto("file:///" + html.replace("\\\\", "/"), wait_until="load", timeout=60000)
        try:
            pg.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass
        pg.screenshot(path=out, full_page=True)
    finally:
        b.close()
`;

// both images go to the model at the same, modest size: full-page screenshots of long pages are too big to send
const SHRINK_PY = `import sys
from PIL import Image
src, out = sys.argv[1], sys.argv[2]
im = Image.open(src).convert("RGB")
w = 800
h = max(1, round(im.height * w / im.width))
im = im.resize((w, h))
if h > 2400:
    im = im.crop((0, 0, w, 2400))
im.save(out, "JPEG", quality=80)
`;

// -> a shrunk JPEG copy of an image, or null
export function shrinkImage(file, { python = "python" } = {}) {
  try {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "stealth-shrink-"));
    const script = path.join(dir, "shrink.py");
    const out = path.join(dir, path.basename(file).replace(/\.\w+$/, "") + ".jpg");
    fs.writeFileSync(script, SHRINK_PY);
    const r = spawnSync(python, [script, file, out], { encoding: "utf8", timeout: 60000, windowsHide: true });
    return r.status === 0 && fs.existsSync(out) ? out : null;
  } catch {
    return null;
  }
}

// -> the rendered page as a PNG file path, or null
export function renderPage(htmlFile, { python = "python" } = {}) {
  try {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "stealth-visual-"));
    const script = path.join(dir, "render.py");
    const out = path.join(dir, "page.png");
    fs.writeFileSync(script, RENDER_PY);
    const r = spawnSync(python, [script, htmlFile, out], { encoding: "utf8", timeout: 90000, windowsHide: true });
    return r.status === 0 && fs.existsSync(out) ? out : null;
  } catch {
    return null;
  }
}

const dataUrl = (file) => {
  const ext = path.extname(file).slice(1).toLowerCase().replace("jpg", "jpeg");
  return `data:image/${ext || "png"};base64,${fs.readFileSync(file).toString("base64")}`;
};

// -> {match: boolean, why} | null (no verdict). target: the screenshot the task names; page: the rendered answer.
export async function compareImages(target, page, { env = process.env, fetchImpl = globalThis.fetch, log = () => {} } = {}) {
  try {
    const profiles = readExecConfig(env).profiles || {};
    const name = String(env.STEALTHLAB_DISPATCH_VISUAL_MODEL || "").trim()
      || Object.keys(profiles).find((k) => profiles[k]?.vision === true && profiles[k]?.openai_base_url);
    const p = name ? profiles[name] : null;
    if (!p?.openai_base_url) { log("visual check: no model that can see"); return null; }
    const res = await fetchImpl(`${p.openai_base_url.replace(/\/+$/, "")}/chat/completions`, {
      method: "POST", redirect: "error", signal: AbortSignal.timeout(90000),
      headers: { "content-type": "application/json", authorization: `Bearer ${profileKey(p, env)}` },
      body: JSON.stringify({ model: p.model_id, max_tokens: 2000, temperature: 0, ...(p.request_extras || {}), messages: [{ role: "user", content: [
        { type: "text", text: VISUAL_PROMPT },
        { type: "image_url", image_url: { url: dataUrl(target) } },
        { type: "image_url", image_url: { url: dataUrl(page) } }] }] }),
    });
    if (!res.ok) { log(`visual check: HTTP ${res.status}`); return null; }
    const body = await res.json();
    const text = String(body?.choices?.[0]?.message?.content || "").trim();
    const m = /^\W*(MATCH|MISMATCH)\b\W*(.*)$/is.exec(text);
    if (!m) { log("visual check: no verdict in the reply"); return null; }
    return { match: m[1].toUpperCase() === "MATCH", why: m[2].trim().slice(0, 200) };
  } catch (err) {
    log(`visual check: ${err.message}`);
    return null;
  }
}

// The judge on a Claude model (default: Sonnet) through Claude Code, which reads the two images with its Read tool.
// GLM-5.3-flash as judge agreed with the layout grader on 5 of 9 known pages and passed both wrong GLM pages
// (2026-10-14); a Claude judge costs ~1-2 cents a page against ~20 for the session's model building it.
export async function compareImagesClaude(target, page, model, { env = process.env, log = () => {}, run } = {}) {
  const prompt = `${VISUAL_PROMPT}\n\nImage 1 (the target): ${target}\nImage 2 (the recreation): ${page}\n` +
    "Read both image files, then reply with the one line.";
  const args = ["-p", "--model", model, "--allowedTools", "Read", "--setting-sources", "user", "--strict-mcp-config",
    "--output-format", "json"];
  const exec = run || ((a, input) => new Promise((resolve) => {
    let launch;
    try { launch = launchOrThrow("claude", { env }); } catch (err) { resolve({ status: null, stdout: "", stderr: err.message }); return; }
    const c = spawn(launch.cmd, [...launch.prefix, ...a], { windowsHide: true, env: scrubEnv(env) });
    let stdout = "";
    const timer = setTimeout(() => c.kill(), 180000);
    c.stdout.on("data", (d) => { stdout += d; });
    c.on("error", () => {});
    c.on("close", (status) => { clearTimeout(timer); resolve({ status, stdout }); });
    c.stdin.end(input);
  }));
  try {
    const r = await exec(args, prompt);
    const j = JSON.parse(String(r.stdout || "").trim().split(/\r?\n/).pop());
    try {
      fs.appendFileSync(path.join(env.STEALTHLAB_HOME || path.join(os.homedir(), ".stealthlab"), "dispatch_costs.jsonl"),
        JSON.stringify({ at: new Date().toISOString(), what: "visual-check", model, cost_usd: j.total_cost_usd ?? null }) + "\n");
    } catch { /* the ledger is best-effort */ }
    const m = /^\W*(MATCH|MISMATCH)\b\W*(.*)$/ims.exec(String(j.result || "").trim().split(/\r?\n/).filter((l) => /MATCH/i.test(l)).pop() || "");
    if (!m) { log("visual check: no verdict in the reply"); return null; }
    return { match: m[1].toUpperCase() === "MATCH", why: m[2].trim().slice(0, 200) };
  } catch (err) {
    log(`visual check: ${err.message}`);
    return null;
  }
}

// The whole check for one passing answer -> {match, why} | null. worktree: the answer's checkout; files: what it changed.
export async function visualCheck({ worktree, files = [], images = [], repo, env = process.env, fetchImpl, log = () => {}, python = "python" }) {
  const html = files.find((f) => /\.html?$/i.test(f));
  if (!worktree || !html || !images.length) { log("visual check: nothing to render"); return null; }
  const target = path.resolve(repo, images[0]);
  if (!fs.existsSync(target)) return null;
  const shot = renderPage(path.join(worktree, html), { python });
  if (!shot) { log("visual check: the page could not be rendered (needs python with playwright)"); return null; }
  const a = shrinkImage(target, { python }), b = shrinkImage(shot, { python });
  if (!a || !b) { log("visual check: the images could not be resized (needs python with Pillow)"); return null; }
  // the judge: STEALTHLAB_DISPATCH_VISUAL_MODEL -- a Claude model (default Sonnet) or an exec.json profile that can see
  const judge = String(env.STEALTHLAB_DISPATCH_VISUAL_MODEL || "claude-sonnet-5-5").trim();
  return judge.includes("claude") ? compareImagesClaude(a, b, judge, { env, log })
    : compareImages(a, b, { env: { ...env, STEALTHLAB_DISPATCH_VISUAL_MODEL: judge }, fetchImpl, log });
}
