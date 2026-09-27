// Parse the plan_and_run node format (backend/app/mcp_server/prompts.py RUN_MD_FORMAT):
//   NODE|<node_id>|<status>|<what to do>|step=<P-n>:<order>|claims=<R-a..R-b,...>|deps=<ids or ->|check=<how to tell it worked>
// The key=value fields are recognised by their prefix, so a "|" inside <what to do> or check= does not
// break the parse: fields after position 3 that do not start with a known key are glued back onto the
// previous field.
const KEYS = ["step", "claims", "deps", "check"];

export function parseNodeLines(text) {
  const nodes = [];
  for (const raw of String(text || "").split(/\r?\n/)) {
    const line = raw.trim().replace(/^[-*]\s+/, "").replace(/^`|`$/g, "");
    if (!line.startsWith("NODE|")) continue;
    const parts = line.split("|");
    const node = { id: parts[1] || "", status: parts[2] || "", what: "", step: null, claims: "", deps: "", check: "" };
    let current = "what";
    const fields = { what: [] };
    for (const p of parts.slice(3)) {
      const m = p.match(/^(step|claims|deps|check)=(.*)$/s);
      if (m && KEYS.includes(m[1]) && !(m[1] in fields)) {
        current = m[1];
        fields[current] = [m[2]];
      } else {
        (fields[current] ||= []).push(p);
      }
    }
    node.what = (fields.what || []).join("|").trim();
    node.claims = (fields.claims || []).join("|").trim();
    node.deps = (fields.deps || []).join("|").trim();
    node.check = (fields.check || []).join("|").trim();
    const step = (fields.step || []).join("|").trim();
    const sm = step.match(/^([^:]+):(\d+)$/);
    node.step = step ? { procedure: sm ? sm[1] : step, order: sm ? Number(sm[2]) : null } : null;
    nodes.push(node);
  }
  return nodes;
}

export const NO_CHANGE_RE = /\bno change(s)? (is )?expected\b/i;
