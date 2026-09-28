// Executor registry + selection.
//
// Adapters come from lib/executors/index.mjs (Builder B), imported lazily so the runtime can be tested
// with injected stub adapters and so nothing loads until `stealthlab-mcp exec` actually runs. The `fake`
// adapter is selectable only when STEALTHLAB_EXEC_ALLOW_FAKE=1 (tests), never in production config.
//
// The VERIFIED_WITH gate lives here and is enforced by the runtime before any spawn: an adapter with no
// VERIFIED_WITH, an agent that is not installed, or an installed major version different from the one the
// flags were verified against is REFUSED -- it never runs, whatever the caller asks for.
import { callHostedTool } from "./hosted.mjs";

const PROBE_TTL_MS = 5 * 60 * 1000;

export async function loadAdapters({ adapters, env = process.env } = {}) {
  if (adapters) return adapters;
  const mod = await import("../executors/index.mjs");
  const all = { ...(mod.ADAPTERS || {}) };
  if (env.STEALTHLAB_EXEC_ALLOW_FAKE !== "1") delete all.fake;
  return all;
}

export function majorOf(v) {
  const m = String(v ?? "").match(/(\d+)/);
  return m ? Number(m[1]) : null;
}

export function refusalReason(adapter, detected) {
  const vw = adapter?.VERIFIED_WITH;
  if (!vw || !vw.version || !vw.date || !vw.source) {
    return `adapter "${adapter?.id}" has no VERIFIED_WITH {version,date,source}: its headless flags were never verified, so it refuses to run`;
  }
  if (!detected?.installed) return `"${adapter.id}" is not installed on this machine`;
  const want = majorOf(vw.version);
  const got = majorOf(detected.version);
  if (got === null) return `"${adapter.id}" reported no parseable version; flags were verified against ${vw.version}, refusing`;
  if (want !== got) {
    return `"${adapter.id}" installed major version ${got} (${detected.version}) differs from the verified ${vw.version}: flags unverified, refusing`;
  }
  return null;
}

export class Registry {
  constructor({ adapters, env = process.env, now = () => Date.now() } = {}) {
    this.injected = adapters;
    this.env = env;
    this.now = now;
    this.cache = new Map();
    this.adapters = null;
    this.loadError = null;
  }

  async all() {
    if (this.adapters) return this.adapters;
    try {
      this.adapters = await loadAdapters({ adapters: this.injected, env: this.env });
    } catch (err) {
      this.loadError = `executor adapters unavailable: ${err.message}`;
      this.adapters = {};
    }
    return this.adapters;
  }

  async get(id) {
    const all = await this.all();
    const a = all[id];
    if (!a) {
      const known = Object.keys(all).join(", ") || "none";
      throw new Error(`unknown or unavailable executor "${id}" (available: ${known})${this.loadError ? `; ${this.loadError}` : ""}`);
    }
    return a;
  }

  async probe(id, { fresh = false } = {}) {
    const hit = this.cache.get(id);
    if (!fresh && hit && this.now() - hit.at < PROBE_TTL_MS) return hit.value;
    const adapter = await this.get(id);
    let detected = { installed: false, version: null, bin: null };
    let health = { healthy: false, detail: "not checked" };
    const notes = [];
    try { detected = (await adapter.detect({ env: this.env })) || detected; } catch (err) { notes.push(`detect failed: ${err.message}`); }
    if (detected.installed) {
      try { health = (await adapter.health({ env: this.env })) || health; } catch (err) { health = { healthy: false, detail: err.message }; }
    } else {
      health = { healthy: false, detail: "not installed" };
    }
    const refusal = refusalReason(adapter, detected);
    if (refusal) notes.push(refusal);
    if (health.detail) notes.push(String(health.detail));
    const value = {
      id, installed: !!detected.installed, version: detected.version ?? null, bin: detected.bin ?? null,
      verified_flags: !!(adapter.VERIFIED_WITH && adapter.VERIFIED_WITH.version),
      verified_with: adapter.VERIFIED_WITH || null,
      healthy: !!health.healthy, last_health_check: new Date(this.now()).toISOString(),
      runnable: !refusal && !!health.healthy, refusal,
      notes: notes.join("; "),
    };
    this.cache.set(id, { at: this.now(), value });
    return value;
  }

  async list() {
    const all = await this.all();
    return Promise.all(Object.keys(all).map((id) => this.probe(id)));
  }
}

const unitId = (u) => `${u.model}|${u.executor}`;
function parseUnit(s) {
  const i = String(s).lastIndexOf("|");
  if (i <= 0) return null;
  return { model: s.slice(0, i), executor: s.slice(i + 1) };
}

// selectUnits -> { units: [{executor, model}], race, source, recommendation, note }
export async function selectUnits({ registry, config, executor, model, race, goal_id, procedure_id, step_order,
                                    env = process.env, fetchImpl, recommendTimeoutMs = 8000 }) {
  const candidates = [];
  const explicitRace = race !== undefined && race !== null;
  if (executor) {
    const probe = await registry.probe(executor);
    if (!probe.runnable) throw new Error(probe.refusal || `executor "${executor}" is not healthy: ${probe.notes}`);
    if (Number(race || 1) !== 2) {
      return { units: [{ executor, model: model || (config.executors?.[executor]?.models?.[0] ?? null) }], race: 1,
               source: "explicit", recommendation: null, note: "" };
    }
  }
  for (const [id, spec] of Object.entries(config.executors || {})) {
    let probe;
    try { probe = await registry.probe(id); } catch { continue; }
    if (!probe.runnable) continue;
    const models = Array.isArray(spec?.models) && spec.models.length ? spec.models.map(String) : [];
    for (const m of models) candidates.push({ executor: id, model: m });
  }

  let ranked = [];
  let source = "config_order";
  let recommendation = null;
  let note = "";
  if (candidates.length && (goal_id || procedure_id)) {
    try {
      const text = await callHostedTool("recommend_models", {
        candidates: candidates.map(unitId),
        ...(goal_id ? { goal_id } : {}), ...(procedure_id ? { procedure_id } : {}),
        ...(step_order !== undefined && step_order !== null ? { step_order } : {}),
        check_kind: "tests",
      }, { env, fetchImpl, timeoutMs: recommendTimeoutMs });
      const rec = JSON.parse(text);
      if (rec.status !== "ok" || !Array.isArray(rec.recommended?.ladder)) {
        throw new Error(`recommend_models status ${rec.status || "unknown"}${rec.reason ? `: ${rec.reason}` : ""}`);
      }
      const known = new Set(candidates.map(unitId));
      const seen = new Set();
      for (const u of rec.recommended.ladder) {
        if (known.has(u) && !seen.has(u)) { seen.add(u); ranked.push(parseUnit(u)); }
      }
      if (!ranked.length) throw new Error("recommend_models ladder named none of our candidates");
      const q05 = Number(rec.recommended.p_success_q05);
      const q95 = Number(rec.recommended.p_success_q95);
      recommendation = {
        recommendation_id: rec.recommendation_id || null, instance_key: rec.instance_key || null,
        p_success: rec.recommended.p_success ?? null,
        interval: Number.isFinite(q05) && Number.isFinite(q95) ? [q05, q95] : null,
      };
      source = "recommend_models";
    } catch (err) {
      note = `recommend_models unavailable (${err.message}); fell back to default_order`;
      ranked = [];
    }
  } else if (candidates.length) {
    note = "no goal_id/procedure_id: recommend_models not called; used default_order";
  }
  if (source !== "recommend_models") {
    const known = new Map(candidates.map((c) => [unitId(c), c]));
    for (const u of config.default_order || []) {
      if (known.has(u) && !ranked.some((r) => unitId(r) === u)) ranked.push(known.get(u));
    }
    source = ranked.length ? "default_order" : "config_order";
  }
  for (const c of candidates) if (!ranked.some((r) => unitId(r) === unitId(c))) ranked.push(c);

  let units = ranked;
  if (executor) {
    const mine = model ? [{ executor, model }] : ranked.filter((r) => r.executor === executor);
    const first = mine[0] || { executor, model: model || (config.executors?.[executor]?.models?.[0] ?? null) };
    units = [first, ...ranked.filter((r) => unitId(r) !== unitId(first))];
    source = "explicit";
  } else if (model) {
    units = ranked.filter((r) => r.model === model).concat(ranked.filter((r) => r.model !== model));
  }
  if (!units.length) {
    throw new Error("no runnable executor: configure ~/.stealthlab/exec.json {\"executors\": {\"<id>\": {\"models\": [...]}}} " +
      "with an installed, healthy, flag-verified agent (see list_executors)");
  }

  let raceN = explicitRace ? Number(race) : 1;
  if (!explicitRace && recommendation?.interval) {
    const width = recommendation.interval[1] - recommendation.interval[0];
    if (width >= (config.race_interval_width ?? 0.5)) {
      raceN = 2;
      note = `${note ? note + "; " : ""}racing the top two rungs: P(success) interval ${recommendation.interval.map((x) => x.toFixed(2)).join("-")} is wide`;
    }
  }
  if (raceN === 2 && units.length < 2) {
    raceN = 1;
    note = `${note ? note + "; " : ""}race=2 requested but only one runnable unit`;
  }
  // ladder: every runnable unit in rank order -- the rungs achieve(escalate=n) climbs after a failed attempt.
  return { units: units.slice(0, raceN), ladder: units, race: raceN, source, recommendation, note };
}
