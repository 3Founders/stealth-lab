// A Way's steps -> the plan's DAG (run.md's NODE deps=). The steps themselves carry no scheduling fields (a global
// Procedure's steps are planner-neutral, backend db/18_procedures.sql); the edges are made here, from:
//   1. data flow: B depends on A when B needs a name A gives (lib/way_extract.mjs, from the code of the past fix);
//   2. the same file: two steps in one file run one after the other (parallel results could not be merged);
//   3. no locator: a step that does not say where it is depends on every step before it (unsure -> in order).
// Everything else is independent, so it can run in parallel. Waves: the nodes whose deps are all in earlier waves.
// A cycle (it should not happen: way_extract orders the steps) falls back to the steps' own order, one after another.

export function buildDag(steps) {
  const nodes = steps.map((s, i) => ({ id: `N-${i + 1}`, step: s, deps: new Set() }));
  for (let b = 0; b < nodes.length; b++) {
    const B = nodes[b].step;
    for (let a = 0; a < nodes.length; a++) {
      if (a === b) continue;
      const A = nodes[a].step;
      const flows = (B.needs || []).some((n) => (A.gives || []).includes(n));
      const sameFile = a < b && (A.at || []).some((f) => (B.at || []).includes(f));
      const unsure = a < b && (!(A.at || []).length || !(B.at || []).length);
      if (flows || sameFile || unsure) nodes[b].deps.add(nodes[a].id);
    }
  }
  const waves = [];
  const placed = new Set();
  while (placed.size < nodes.length) {
    const wave = nodes.filter((n) => !placed.has(n.id) && [...n.deps].every((d) => placed.has(d)));
    if (!wave.length) {
      // a cycle: one after another, in the steps' order
      return { nodes: nodes.map((n, i) => ({ ...n, deps: i ? [nodes[i - 1].id] : [] })), waves: nodes.map((n) => [n.id]), cycle: true };
    }
    waves.push(wave.map((n) => n.id));
    for (const n of wave) placed.add(n.id);
  }
  return { nodes: nodes.map((n) => ({ ...n, deps: [...n.deps] })), waves, cycle: false };
}
