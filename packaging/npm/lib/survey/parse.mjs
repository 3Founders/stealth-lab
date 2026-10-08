// Small, dependency-free parsers for the manifest formats the survey reads. Each one returns data plus
// the 1-based line a value came from, because every scanner fact cites `path:line`.
//
// They cover what manifests actually use, not every corner of each spec: a value they cannot parse is
// skipped (never guessed), and the fact that would have cited it is simply not emitted.

// ---------------------------------------------------------------------------------------------
// TOML (Cargo.toml, pyproject.toml, uv/pdm/hatch sections, pants.toml, rust-toolchain.toml)
// ---------------------------------------------------------------------------------------------
export function parseToml(text) {
  const data = {};
  const lineOf = new Map(); // "a.b.c" -> line
  let cur = data;
  let curPath = [];
  const lines = text.split(/\r?\n/);
  let i = 0;

  const setPath = (obj, keys, value) => {
    let o = obj;
    for (let k = 0; k < keys.length - 1; k++) {
      if (typeof o[keys[k]] !== "object" || o[keys[k]] === null) o[keys[k]] = {};
      o = o[keys[k]];
      if (Array.isArray(o)) o = o[o.length - 1];
    }
    o[keys[keys.length - 1]] = value;
  };

  while (i < lines.length) {
    const lineNo = i + 1;
    let line = stripTomlComment(lines[i]).trim();
    i++;
    if (!line) continue;
    const arrTable = line.match(/^\[\[\s*(.+?)\s*\]\]$/);
    const table = !arrTable && line.match(/^\[\s*(.+?)\s*\]$/);
    if (arrTable || table) {
      const keys = splitDottedKey((arrTable || table)[1]);
      if (!keys) continue;
      let o = data;
      for (let k = 0; k < keys.length - 1; k++) {
        if (typeof o[keys[k]] !== "object" || o[keys[k]] === null) o[keys[k]] = {};
        o = o[keys[k]];
        if (Array.isArray(o)) o = o[o.length - 1];
      }
      const last = keys[keys.length - 1];
      if (arrTable) {
        if (!Array.isArray(o[last])) o[last] = [];
        o[last].push({});
        cur = o[last][o[last].length - 1];
      } else {
        if (typeof o[last] !== "object" || o[last] === null || Array.isArray(o[last])) o[last] = o[last] && !Array.isArray(o[last]) ? o[last] : {};
        cur = o[last];
      }
      curPath = keys;
      lineOf.set(keys.join("."), lineNo);
      continue;
    }
    const eq = findTopLevelEq(line);
    if (eq < 0) continue;
    const keys = splitDottedKey(line.slice(0, eq).trim());
    if (!keys) continue;
    let rest = line.slice(eq + 1).trim();
    // Multi-line values: keep reading until the value is complete.
    while (!valueComplete(rest) && i < lines.length) {
      rest += "\n" + (rest.startsWith('"""') || rest.startsWith("'''") ? lines[i] : stripTomlComment(lines[i]));
      i++;
    }
    let value;
    try { value = parseTomlValue(rest); } catch { continue; }
    setPath(cur, keys, value);
    lineOf.set([...curPath, ...keys].join("."), lineNo);
  }
  return { data, lineOf };
}

function stripTomlComment(line) {
  let inS = null;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (inS) {
      if (c === "\\" && inS === '"') { i++; continue; }
      if (c === inS) inS = null;
    } else if (c === '"' || c === "'") inS = c;
    else if (c === "#") return line.slice(0, i);
  }
  return line;
}

function findTopLevelEq(line) {
  let inS = null;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (inS) { if (c === inS) inS = null; continue; }
    if (c === '"' || c === "'") inS = c;
    else if (c === "=") return i;
  }
  return -1;
}

function splitDottedKey(s) {
  const keys = [];
  let i = 0;
  while (i < s.length) {
    while (s[i] === " " || s[i] === "\t") i++;
    if (s[i] === '"' || s[i] === "'") {
      const q = s[i];
      const end = s.indexOf(q, i + 1);
      if (end < 0) return null;
      keys.push(s.slice(i + 1, end));
      i = end + 1;
    } else {
      const m = s.slice(i).match(/^[A-Za-z0-9_-]+/);
      if (!m) return null;
      keys.push(m[0]);
      i += m[0].length;
    }
    while (s[i] === " " || s[i] === "\t") i++;
    if (i < s.length) {
      if (s[i] !== ".") return null;
      i++;
    }
  }
  return keys.length ? keys : null;
}

function valueComplete(s) {
  if (s.startsWith('"""')) return s.length >= 6 && s.slice(3).includes('"""');
  if (s.startsWith("'''")) return s.length >= 6 && s.slice(3).includes("'''");
  let depth = 0;
  let inS = null;
  for (let i = 0; i < s.length; i++) {
    const c = s[i];
    if (inS) {
      if (c === "\\" && inS === '"') { i++; continue; }
      if (c === inS) inS = null;
      continue;
    }
    if (c === '"' || c === "'") inS = c;
    else if (c === "[" || c === "{") depth++;
    else if (c === "]" || c === "}") depth--;
  }
  return depth <= 0 && !inS;
}

function parseTomlValue(src) {
  let i = 0;
  const ws = () => {
    for (;;) {
      while (i < src.length && /\s/.test(src[i])) i++;
      if (src[i] === "#") { while (i < src.length && src[i] !== "\n") i++; continue; }
      break;
    }
  };
  const val = () => {
    ws();
    const c = src[i];
    if (src.startsWith('"""', i)) {
      const end = src.indexOf('"""', i + 3);
      const s = src.slice(i + 3, end).replace(/^\n/, "");
      i = end + 3;
      return unescape(s);
    }
    if (src.startsWith("'''", i)) {
      const end = src.indexOf("'''", i + 3);
      const s = src.slice(i + 3, end).replace(/^\n/, "");
      i = end + 3;
      return s;
    }
    if (c === '"') {
      let j = i + 1;
      while (j < src.length && src[j] !== '"') j += src[j] === "\\" ? 2 : 1;
      const s = src.slice(i + 1, j);
      i = j + 1;
      return unescape(s);
    }
    if (c === "'") {
      const j = src.indexOf("'", i + 1);
      const s = src.slice(i + 1, j);
      i = j + 1;
      return s;
    }
    if (c === "[") {
      i++;
      const arr = [];
      for (;;) {
        ws();
        if (src[i] === "]") { i++; return arr; }
        arr.push(val());
        ws();
        if (src[i] === ",") i++;
        else if (src[i] === "]") { i++; return arr; }
        else throw new Error("bad array");
      }
    }
    if (c === "{") {
      i++;
      const obj = {};
      for (;;) {
        ws();
        if (src[i] === "}") { i++; return obj; }
        const eq = src.indexOf("=", i);
        const keys = splitDottedKey(src.slice(i, eq).trim());
        if (!keys) throw new Error("bad inline key");
        i = eq + 1;
        const v = val();
        let o = obj;
        for (let k = 0; k < keys.length - 1; k++) o = o[keys[k]] ??= {};
        o[keys[keys.length - 1]] = v;
        ws();
        if (src[i] === ",") i++;
        else if (src[i] === "}") { i++; return obj; }
        else throw new Error("bad inline table");
      }
    }
    const m = src.slice(i).match(/^[^,\]\}\s#]+/);
    if (!m) throw new Error("bad value");
    i += m[0].length;
    const t = m[0];
    if (t === "true") return true;
    if (t === "false") return false;
    if (/^[+-]?\d[\d_]*$/.test(t)) return Number(t.replace(/_/g, ""));
    if (/^[+-]?\d[\d_]*\.\d+([eE][+-]?\d+)?$/.test(t)) return Number(t.replace(/_/g, ""));
    return t; // dates, times, inf/nan: kept as text
  };
  const v = val();
  return v;
}

function unescape(s) {
  return s.replace(/\\(["\\bfnrt]|u[0-9a-fA-F]{4})/g, (_, e) => {
    switch (e[0]) {
      case "n": return "\n";
      case "t": return "\t";
      case "r": return "\r";
      case "b": return "\b";
      case "f": return "\f";
      case "u": return String.fromCharCode(parseInt(e.slice(1), 16));
      default: return e;
    }
  });
}

export function tomlGet(obj, dotted) {
  let o = obj;
  for (const k of dotted.split(".")) {
    if (o == null || typeof o !== "object") return undefined;
    o = o[k];
  }
  return o;
}

// ---------------------------------------------------------------------------------------------
// JSON with line numbers (package.json, lerna.json, rush.json (JSONC), nx project.json, deno.json)
// ---------------------------------------------------------------------------------------------
export function parseJsonLoose(text) {
  try { return JSON.parse(text); } catch { /* fall through to JSONC */ }
  try { return JSON.parse(stripJsonComments(text)); } catch { return null; }
}

export function stripJsonComments(text) {
  let out = "";
  let inS = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (inS) {
      out += c;
      if (c === "\\") { out += text[++i] ?? ""; continue; }
      if (c === '"') inS = false;
      continue;
    }
    if (c === '"') { inS = true; out += c; continue; }
    if (c === "/" && text[i + 1] === "/") { while (i < text.length && text[i] !== "\n") i++; out += "\n"; continue; }
    if (c === "/" && text[i + 1] === "*") {
      const end = text.indexOf("*/", i + 2);
      out += text.slice(i, end < 0 ? text.length : end + 2).replace(/[^\n]/g, " ");
      i = end < 0 ? text.length : end + 1;
      continue;
    }
    out += c;
  }
  return out.replace(/,(\s*[}\]])/g, "$1");
}

/** The 1-based line of a nested key (`["scripts","test"]`) in JSON text, found key by key in order. */
export function jsonKeyLine(text, keys) {
  // Depth-aware: keys[0] must be a key of the top-level object, keys[1] a key of that value, and so on --
  // a nested "type" inside "funding" is not the package's "type".
  let depth = 0;
  let want = 0;
  let line = 1;
  let i = 0;
  const n = text.length;
  while (i < n) {
    const c = text[i];
    if (c === "\n") { line++; i++; continue; }
    if (c === '"') {
      let j = i + 1;
      while (j < n && text[j] !== '"') j += text[j] === "\\" ? 2 : 1;
      const str = text.slice(i + 1, j);
      let k = j + 1;
      while (k < n && /[ \t\r\n]/.test(text[k])) k++;
      if (text[k] === ":" && depth === want + 1) {
        let key;
        try { key = JSON.parse(`"${str}"`); } catch { key = str; }
        if (key === keys[want]) {
          if (want === keys.length - 1) return line;
          want++;
        }
      }
      i = j + 1;
      continue;
    }
    if (c === "{" || c === "[") depth++;
    else if (c === "}" || c === "]") {
      depth--;
      if (depth < want) return 0; // left the object holding the key we wanted without finding it
    }
    i++;
  }
  return 0;
}

// ---------------------------------------------------------------------------------------------
// Line-oriented helpers
// ---------------------------------------------------------------------------------------------
export const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\/]/g, "\\$&");

export function lineAt(text, index) {
  let n = 1;
  for (let i = 0; i < index && i < text.length; i++) if (text.charCodeAt(i) === 10) n++;
  return n;
}

/** First line (1-based) whose text matches `re`, or 0. */
export function findLine(text, re) {
  const lines = text.split(/\r?\n/);
  for (let i = 0; i < lines.length; i++) if (re.test(lines[i])) return i + 1;
  return 0;
}

const unquote = (s) => { s = s.trim(); return s.length >= 2 && (s[0] === '"' || s[0] === "'") && s[s.length - 1] === s[0] ? s.slice(1, -1) : s; };

/** A top-level YAML list under `key:` (pnpm-workspace.yaml `packages:`, melos.yaml `packages:`). */
export function yamlTopList(text, key) {
  const lines = text.split(/\r?\n/);
  const out = [];
  let inKey = false;
  let keyLine = 0;
  for (let i = 0; i < lines.length; i++) {
    const raw = lines[i].replace(/\s+#.*$/, "");
    if (!raw.trim() || raw.trim().startsWith("#")) continue;
    const top = raw.match(/^([A-Za-z0-9_.-]+)\s*:\s*(.*)$/);
    if (top) {
      inKey = top[1] === key;
      if (inKey) {
        keyLine = i + 1;
        const inline = top[2].trim();
        if (inline.startsWith("[")) {
          for (const v of inline.replace(/^\[|\]$/g, "").split(",")) if (v.trim()) out.push({ value: unquote(v), line: i + 1 });
          inKey = false;
        }
      }
      continue;
    }
    if (inKey) {
      const item = raw.match(/^\s*-\s*(.+)$/);
      if (item) out.push({ value: unquote(item[1]), line: i + 1 });
    }
  }
  return { items: out, line: keyLine };
}

/** A scalar YAML key at any indent (`apps_path: "apps"`, `name: foo`). */
export function yamlScalar(text, key) {
  const re = new RegExp(`^\\s*${escapeRe(key)}\\s*:\\s*(.+?)\\s*$`);
  const lines = text.split(/\r?\n/);
  for (let i = 0; i < lines.length; i++) {
    const m = lines[i].match(re);
    if (m && !m[1].startsWith("#")) return { value: unquote(m[1].replace(/\s+#.*$/, "")), line: i + 1 };
  }
  return null;
}

/**
 * GitHub Actions / GitLab CI / generic YAML CI: every shell command with its line, its job, its
 * working directory (GitHub step `working-directory:` or job `defaults.run.working-directory`), and the
 * setup actions' versions (`actions/setup-node` with `node-version: 20`).
 */
export function ciCommands(text, kind = "github") {
  const lines = text.split(/\r?\n/);
  const cmds = [];
  const setups = [];
  let job = null;
  let jobIndent = -1;
  let inJobs = kind !== "github";
  let block = null; // {indent, lines:[{text,line}], step}
  let step = null;  // the current GitHub step: {wd}
  let stepIndent = -1;
  let jobWd = null;
  const push = (cmd, line, st) => cmds.push({ job, cmd, line, step: st, jobWdRef: () => jobWd });
  const flush = () => {
    if (block) for (const c of logicalCommands(block.lines)) push(c.text, c.line, block.step);
    block = null;
  };
  for (let i = 0; i < lines.length; i++) {
    const raw = lines[i];
    const indent = raw.match(/^\s*/)[0].length;
    if (block) {
      if (!raw.trim() || indent >= block.indent) { block.lines.push({ text: raw.trim(), line: i + 1 }); continue; }
      flush();
    }
    const t = raw.trim();
    if (!t || t.startsWith("#")) continue;
    if (kind === "github") {
      if (indent === 0) { inJobs = /^jobs\s*:/.test(t); job = null; continue; }
      if (inJobs && (jobIndent < 0 || indent <= jobIndent) && /^[A-Za-z0-9_.-]+\s*:\s*$/.test(t)) {
        jobIndent = indent;
        job = t.replace(/\s*:\s*$/, "");
        step = null; stepIndent = -1; jobWd = null;
        continue;
      }
      if (inJobs && job && t.startsWith("- ") && (stepIndent < 0 || indent <= stepIndent)) { stepIndent = indent; step = { wd: null }; }
      const wd = t.match(/^-?\s*working-directory\s*:\s*(.+)$/);
      if (wd) {
        const v = wd[1].replace(/\s+#.*$/, "").replace(/^["']|["']$/g, "");
        if (step) step.wd = v; else jobWd = v;
        continue;
      }
    } else if (indent === 0 && /^[A-Za-z0-9_. -]+\s*:\s*$/.test(t) && !t.startsWith(".")) {
      job = t.replace(/\s*:\s*$/, "");
      continue;
    }
    // GitHub Actions runs shell only in `run:`; a `script:` there is actions/github-script's JavaScript input.
    const runKeys = kind === "github" ? /^-?\s*(run)\s*:\s*(.*)$/ : /^-?\s*(run|script|before_script|after_script|command)\s*:\s*(.*)$/;
    const run = t.match(runKeys);
    if (run) {
      const v = run[2].trim();
      if (/^[|>][-+]?$/.test(v)) {
        block = { indent: indent + 1, lines: [], step };
      } else if (v === "") {
        // GitLab / generic list form: `script:` then `- cmd` lines
        for (let j = i + 1; j < lines.length; j++) {
          const lj = lines[j];
          const ij = lj.match(/^\s*/)[0].length;
          if (!lj.trim()) continue;
          const it = lj.trim().match(/^-\s*(.+)$/);
          if (!it || ij < indent) break;
          push(unquote(it[1]), j + 1, step);
          i = j;
        }
      } else push(unquote(v), i + 1, step);
      continue;
    }
    const uses = t.match(/^-?\s*uses\s*:\s*([^\s#]+)/);
    if (uses) setups.push({ job, action: uses[1].replace(/["']/g, ""), line: i + 1, with: {} });
    const w = t.match(/^([a-z-]+-version|toolchain)\s*:\s*(.+)$/);
    if (w && setups.length) setups[setups.length - 1].with[w[1]] = { value: unquote(w[2].replace(/\s+#.*$/, "")), line: i + 1 };
  }
  flush();
  for (const c of cmds) {
    c.wd = c.step?.wd || c.jobWdRef() || null;
    delete c.step;
    delete c.jobWdRef;
  }
  return { cmds, setups };
}


/**
 * The shell commands in a `run: |` block, one per logical command: backslash continuations are joined;
 * heredoc bodies, lines inside a quote left open (a multi-line jq or JSON string), continuation-looking
 * lines (`| x`, `&& y`, `)`, `-flag`) and bare assignments (`pkg=$(ls)`) are not commands.
 */
export function logicalCommands(lines) {
  const out = [];
  let heredoc = null;
  let openQuote = null;
  let cur = null;
  const quoteState = (s, q) => {
    for (let i = 0; i < s.length; i++) {
      const ch = s[i];
      if (ch === "\\" && q !== "'") { i++; continue; }
      if (q) { if (ch === q) q = null; }
      else if (ch === "'" || ch === '"') q = ch;
      else if (ch === "#" && (i === 0 || /\s/.test(s[i - 1]))) break; // comment
    }
    return q;
  };
  for (const { text, line } of lines) {
    const t = text.trim();
    if (heredoc) { if (t === heredoc) heredoc = null; continue; }
    if (openQuote) { openQuote = quoteState(t, openQuote); continue; }
    if (cur) {
      cur.text += " " + t.replace(/\\$/, "").trim();
      if (!t.endsWith("\\")) { out.push(cur); cur = null; }
      continue;
    }
    if (!t || t.startsWith("#")) continue;
    if (/^([|&;)(}{\]"'<>-]|\|\||&&|then\b|else\b|elif\b|fi\b|do\b|done\b|esac\b)/.test(t)) continue;
    // An assignment, not a command: `x=1`, `x=$(ls)`, `title="Update docs for ${name}"` (held-out ruff CI).
    // A heredoc opened on such a line (`body=$(cat <<EOF`) still has a body that is text, not commands
    // (held-out 3, pydantic-ai docs-navigation.yml).
    const hd = t.match(/<<-?\s*['"]?(\w+)['"]?/);
    if (/^[A-Za-z_]\w*=("[^"]*"|'[^']*'|\S*|\$\(.*)\s*(#.*)?$/.test(t)) { if (hd) heredoc = hd[1]; continue; }
    const q = quoteState(t, null);
    if (q) { openQuote = q; continue; } // the command continues inside a string: never report half of it
    if (hd) heredoc = hd[1];
    if (t.endsWith("\\")) { cur = { text: t.replace(/\\$/, "").trim(), line }; continue; }
    out.push({ text: t, line });
  }
  if (cur) out.push(cur);
  return out;
}

/** Makefile / justfile targets with their lines. */
export function makeTargets(text) {
  const out = [];
  const lines = text.split(/\r?\n/);
  for (let i = 0; i < lines.length; i++) {
    const m = lines[i].match(/^([A-Za-z0-9][A-Za-z0-9_.\-/]*)\s*:(?!=)(.*)$/);
    if (!m || m[1].includes("%") || m[1].startsWith(".")) continue;
    // `test-bench: ARGS=-run=x` is a target-specific variable, not a rule (the rule is elsewhere).
    if (/^\s*(export\s+|override\s+)?[A-Za-z_][A-Za-z0-9_]*\s*(:{1,3}|\+|\?|!)?=/.test(m[2])) continue;
    if (!out.some((x) => x.target === m[1])) out.push({ target: m[1], line: i + 1 });
  }
  return out;
}

// ---------------------------------------------------------------------------------------------
// Build-system declarations
// ---------------------------------------------------------------------------------------------

/** go.work `use` directives (single-line or block). */
export function goWorkUses(text) {
  const out = [];
  const lines = text.split(/\r?\n/);
  let block = false;
  for (let i = 0; i < lines.length; i++) {
    const t = lines[i].replace(/\/\/.*$/, "").trim();
    if (block) {
      if (t === ")") { block = false; continue; }
      if (t) out.push({ value: unquote(t), line: i + 1 });
      continue;
    }
    if (/^use\s*\($/.test(t)) { block = true; continue; }
    const m = t.match(/^use\s+(.+)$/);
    if (m) out.push({ value: unquote(m[1]), line: i + 1 });
  }
  return out;
}

/** Gradle settings: include(":a:b", "c"), include ':x', project(':x').projectDir = file('y'), includeBuild('z'). */
export function gradleSettings(text) {
  const includes = [];
  const builds = [];
  const dirs = new Map();
  const lines = text.split(/\r?\n/);
  for (let i = 0; i < lines.length; i++) {
    const t = lines[i].replace(/\/\/.*$/, "");
    // `include(":a")` / `include ':a', ':b'` -- not includeBuild, includeGroupByRegex, includeModule, ...
    const inc = t.match(/^\s*include(?=\s*\(|\s+["'])\s*\(?\s*(.+?)\)?\s*$/);
    if (inc && !/^\s*includeBuild/.test(t)) {
      for (const m of inc[1].matchAll(/["']([^"']+)["']/g)) includes.push({ value: m[1], line: i + 1 });
    }
    const ib = t.match(/^\s*includeBuild\s*\(?\s*["']([^"']+)["']/);
    if (ib) builds.push({ value: ib[1], line: i + 1 });
    const pd = t.match(/project\(\s*["']([^"']+)["']\s*\)\.projectDir\s*=\s*(?:file\(\s*)?(?:new File\([^,]+,\s*)?["']([^"']+)["']/);
    if (pd) dirs.set(pd[1], pd[2]);
  }
  return {
    projects: includes.map(({ value, line }) => {
      const name = value.startsWith(":") ? value : ":" + value;
      const dir = dirs.get(name) ?? name.slice(1).replace(/:/g, "/");
      return { name, dir, line };
    }),
    builds,
  };
}

/** Maven `<modules><module>x</module></modules>` (profiles included). */
export function mavenModules(text) {
  const out = [];
  const re = /<module>\s*([^<]+?)\s*<\/module>/g;
  for (const m of text.matchAll(re)) out.push({ value: m[1], line: lineAt(text, m.index) });
  return out;
}

export function xmlTag(text, tag) {
  const m = text.match(new RegExp(`<${tag}>\\s*([^<]+?)\\s*</${tag}>`));
  return m ? { value: m[1], line: lineAt(text, m.index) } : null;
}

/** .sln `Project("{...}") = "Name", "path\to\x.csproj", "{...}"` */
export function slnProjects(text) {
  const out = [];
  // .slnx (XML): <Project Path="src/Api/Api.csproj" />
  if (/<Solution\b/.test(text)) {
    for (const m of text.matchAll(/<Project\s+[^>]*Path="([^"]+)"/g)) {
      const p = m[1].replace(/\\/g, "/");
      if (/\.(cs|fs|vb|vcx|sql)proj$/i.test(p)) out.push({ name: p.replace(/^.*\/|\.\w+$/g, ""), value: p, line: lineAt(text, m.index) });
    }
    return out;
  }
  const re = /^Project\("\{[^}]+\}"\)\s*=\s*"([^"]+)",\s*"([^"]+)"/gm;
  for (const m of text.matchAll(re)) {
    const p = m[2].replace(/\\/g, "/");
    if (/\.(cs|fs|vb|vcx|sql)proj$/i.test(p)) out.push({ name: m[1], value: p, line: lineAt(text, m.index) });
  }
  return out;
}

/** CMake `add_subdirectory(x)` and whether this CMakeLists defines a target or a project. */
export function cmakeInfo(text) {
  const clean = text.replace(/#.*$/gm, "");
  const subdirs = [];
  for (const m of clean.matchAll(/add_subdirectory\s*\(\s*["']?([^\s)"']+)/gi)) subdirs.push({ value: m[1], line: lineAt(clean, m.index) });
  return {
    subdirs,
    project: /\bproject\s*\(/i.test(clean),
    targets: /\badd_(library|executable)\s*\(/i.test(clean),
  };
}

export function mesonSubdirs(text) {
  const out = [];
  for (const m of text.matchAll(/\bsubdir\s*\(\s*['"]([^'"]+)['"]/g)) out.push({ value: m[1], line: lineAt(text, m.index) });
  return out;
}

/** Elixir umbrella: `apps_path: "apps"` in the root mix.exs. */
export function mixAppsPath(text) {
  const m = text.match(/apps_path:\s*["']([^"']+)["']/);
  return m ? { value: m[1], line: lineAt(text, m.index) } : null;
}

/** .gitmodules paths. */
export function gitmodulesPaths(text) {
  const out = [];
  for (const m of text.matchAll(/^\s*path\s*=\s*(.+?)\s*$/gm)) out.push(m[1]);
  return out;
}

/** .gitattributes `linguist-vendored` / `linguist-generated` patterns. */
export function linguistPatterns(text) {
  const vendored = [];
  const generated = [];
  for (const line of text.split(/\r?\n/)) {
    const t = line.trim();
    if (!t || t.startsWith("#")) continue;
    const [pat, ...attrs] = t.split(/\s+/);
    for (const a of attrs) {
      if (/^linguist-vendored(=true)?$/.test(a)) vendored.push(pat);
      if (/^linguist-generated(=true)?$/.test(a)) generated.push(pat);
    }
  }
  return { vendored, generated };
}

/**
 * sbt projects a build.sbt declares, by directory: `project.in(file("core"))`, `(project in file("core"))`,
 * `crossProject(JVMPlatform, JSPlatform).in(file("streams"))`, `Project("id", file("x"))`. Comments are ignored.
 * @returns {{value: string, line: number}[]} directories as written (relative to the build), first line of each
 */
export function sbtProjects(text) {
  const out = [];
  const seen = new Set();
  const lines = String(text || "").split(/\r?\n/);
  let inBlock = false;
  for (let i = 0; i < lines.length; i++) {
    let t = lines[i];
    if (inBlock) { const e = t.indexOf("*/"); if (e < 0) continue; t = t.slice(e + 2); inBlock = false; }
    t = t.replace(/\/\*.*?\*\//g, "");
    const b = t.indexOf("/*");
    if (b >= 0) { t = t.slice(0, b); inBlock = true; }
    t = t.replace(/\/\/.*$/, "");
    for (const m of t.matchAll(/(?:\.in\s*\(\s*|\bin\s+|Project\s*\(\s*"[^"]*"\s*,\s*)file\(\s*"([^"]+)"\s*\)/g)) {
      const v = m[1].replace(/^\.\//, "").replace(/\/+$/, "") || ".";
      if (!seen.has(v)) { seen.add(v); out.push({ value: v, line: i + 1 }); }
    }
  }
  return out;
}

/** `key=value` from a Java properties file (project/build.properties, gradle-wrapper.properties). */
export function propertiesGet(text, key) {
  const lines = String(text || "").split(/\r?\n/);
  for (let i = 0; i < lines.length; i++) {
    const m = lines[i].match(/^\s*([^#!=:\s]+)\s*[=:]\s*(.*?)\s*$/);
    if (m && m[1] === key) return { value: m[2].replace(/\:/g, ":"), line: i + 1 };
  }
  return null;
}
