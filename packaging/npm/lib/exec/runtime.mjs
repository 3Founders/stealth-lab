// The executor runtime behind the stealthlab-exec MCP tools (spec section 4).
//
// A run = one achieve() call. It has 1 attempt, or 2 when racing -- plus, with escalate=n, up to n more
// attempts, one at a time, each on the next untried rung of the ladder after the previous attempt(s) failed
// verification (check-and-escalate: a cheap model first, a stronger one only when the check says so). Each
// escalated rung is verified by the same checks and reports its own outcome. Each attempt gets its own detached
// worktree, runs one adapter's headless command there (auto-approve flags are only ever used inside that
// worktree), is verified by the caller's checks + scope, and ends in exactly one report_model_run
// (hosted, or the outbox). The user's checkout is only read -- until apply_run, which refuses unless the
// run is verified and every target file still has the blob the run started from.
//
// States: queued -> running -> verifying -> verified | failed | timed_out | cancelled.
// `verified` = every check exited 0 AND no scope violation AND a non-empty diff (unless the task says
// "no change expected"). The executor's own claims, and its exit code, never set it.
import fs from "node:fs";
import path from "node:path";
import { reportModelRun } from "./evidence.mjs";
import { hostedSettings } from "./hosted.mjs";
import { childEnv, spawnManaged } from "./proc.mjs";
import { redact } from "./redact.mjs";
import { Registry, selectUnits } from "./select.mjs";
import {
  appendPrivate, ensureDir, newRunId, readExecConfig, readJson, redactingLog, RUN_ID_RE, runDir, runsDir, writePrivate,
} from "./store.mjs";
import { NO_CHANGE_RE, parseNodeLines } from "./task.mjs";
import { runChecks, scopeViolations } from "./verify.mjs";
import {
  applyToCheckout, assertNoMergeState, collectDiff, createWorktree, defaultWorktreeRoot, headCommit, removeWorktree,
  repoTopLevel, workingTreeDiff,
} from "./worktree.mjs";

export const TERMINAL = new Set(["verified", "failed", "timed_out", "cancelled"]);
const ORDER = { queued: 0, running: 1, verifying: 2 };
const KEEP_MS = 7 * 24 * 3600 * 1000;
const OUT_KEEP = 1024 * 1024;

const clampInt = (v, lo, hi, dflt) => {
  if (v === undefined || v === null || v === "") return dflt;
  const n = Number(v);
  if (!Number.isFinite(n)) throw new Error(`not a number: ${v}`);
  return Math.min(hi, Math.max(lo, n));
};
const cut = (s, n) => (s.length <= n ? s : s.slice(0, n - 14) + " [...trimmed]");
const iso = (ms) => (ms ? new Date(ms).toISOString() : null);

export class ExecRuntime {
  constructor({ env = process.env, adapters, fetchImpl, worktreeRoot } = {}) {
    this.env = env;
    this.fetchImpl = fetchImpl;
    this.registry = new Registry({ adapters, env });
    this.worktreeRoot = worktreeRoot || defaultWorktreeRoot(env);
    this.runs = new Map();
    const { token } = hostedSettings(env);
    this.secrets = token ? [token] : [];
  }

  red(text) { return redact(text, { secrets: this.secrets }); }

  // ---------------------------------------------------------------- tools
  async listExecutors() {
    const list = await this.registry.list();
    if (!list.length && this.registry.loadError) throw new Error(this.registry.loadError);
    const cfg = readExecConfig(this.env);
    return list.map((p) => ({
      id: p.id, installed: p.installed, version: p.version, verified_flags: p.verified_flags, healthy: p.healthy,
      last_health_check: p.last_health_check, notes: p.notes || "",
      runnable: p.runnable, models: cfg.executors?.[p.id]?.models || [],
    }));
  }

  async achieve(input = {}) {
    const cfg = readExecConfig(this.env);
    if (typeof input.repo_path !== "string" || !input.repo_path) throw new Error("repo_path is required");
    if (typeof input.task !== "string" || !input.task.trim()) throw new Error("task is required");
    if (input.task.length > 2000) throw new Error(`task is ${input.task.length} chars; the limit is 2,000 (objective + NODE lines + claims)`);
    const nodes = parseNodeLines(input.task);
    let checks = Array.isArray(input.checks) ? input.checks.map(String).filter((c) => c.trim()) : [];
    if (!checks.length) checks = nodes.map((n) => n.check).filter(Boolean);
    if (!checks.length) throw new Error("checks are required (or NODE lines with check=): verification is the point of achieve");
    const scope = Array.isArray(input.scope) ? input.scope.map(String).filter((s) => s.trim()) : [];
    if (!scope.length) throw new Error("scope is required: the globs this run may modify");
    const timeoutS = clampInt(input.timeout_s, 1, 3600, 900);
    const hangS = clampInt(input.hang_s, 1, 3600, cfg.hang_s || 180);
    const checkTimeoutS = clampInt(input.check_timeout_s, 1, 3600, 600);
    const race = input.race === undefined || input.race === null ? undefined : Number(input.race);
    if (race !== undefined && race !== 1 && race !== 2) throw new Error("race must be 1 or 2");
    const escalate = clampInt(input.escalate, 0, 3, 0);
    const base = input.base || "HEAD";
    if (base !== "HEAD" && base !== "working-tree") throw new Error('base must be "HEAD" or "working-tree"');
    if (input.executor) await this.registry.get(String(input.executor));
    const active = [...this.runs.values()].filter((r) => !TERMINAL.has(this.stateOf(r))).length;
    if (active >= cfg.max_concurrent_runs) throw new Error(`${active} runs already active (max_concurrent_runs=${cfg.max_concurrent_runs})`);

    const repo = await repoTopLevel(path.resolve(input.repo_path), this.env);
    await assertNoMergeState(repo, this.env);
    const commit = await headCommit(repo, this.env);
    const patch = base === "working-tree" ? await workingTreeDiff(repo, this.env) : null;

    const id = newRunId();
    const stepOrder = input.step_order ?? (nodes.length === 1 ? nodes[0].step?.order ?? null : null);
    const run = {
      id, repo, commit, base, patch, createdAt: Date.now(), finishedAt: null,
      input: {
        task: input.task, checks, scope, goal_id: input.goal_id || null, procedure_id: input.procedure_id || null,
        step_order: stepOrder, executor: input.executor || null, model: input.model || null, timeoutS, hangS,
        checkTimeoutS, race, escalate, base, noChangeExpected: NO_CHANGE_RE.test(input.task),
      },
      attempts: [], winner: null, error: null, selection: null, applied: null, discarded: false,
      cancelRequested: false, lastEvent: "queued", waiters: new Set(),
    };
    this.runs.set(id, run);
    ensureDir(runDir(this.env, id));
    this.event(run, { event: "queued" });
    this.persist(run);
    run.driving = this.drive(run).catch((err) => {
      run.error = run.error || err.message;
      this.event(run, { event: "error", error: this.red(err.message) });
    }).finally(() => this.settle(run));
    return {
      run_id: id, state: "queued", executor: run.input.executor, model: run.input.model,
      started_at: iso(run.createdAt), worktree: path.join(this.worktreeRoot, `${id}-a1`),
    };
  }

  async runStatus(runId) {
    const run = this.load(runId);
    const a = this.primary(run);
    return {
      run_id: run.id, state: this.stateOf(run), elapsed_s: this.elapsed(run), last_event: run.lastEvent,
      executor: a?.executor ?? run.input.executor, model: a?.model ?? run.input.model,
      ...(run.attempts.length > 1 ? { attempts: run.attempts.map((x) => ({ executor: x.executor, model: x.model, state: x.state })) } : {}),
    };
  }

  async runResult(runId, waitS = 0) {
    const run = this.load(runId);
    const wait = clampInt(waitS, 0, 55, 0);
    if (!this.isDone(run) && wait > 0) {
      await new Promise((resolve) => {
        const t = setTimeout(done, wait * 1000);
        function done() { clearTimeout(t); run.waiters.delete(done); resolve(); }
        run.waiters.add(done);
      });
    }
    if (!this.isDone(run)) return { ...(await this.runStatus(runId)), done: false };
    return this.contract(run);
  }

  async applyRun(runId) {
    const run = this.load(runId);
    if (!this.isDone(run)) throw new Error(`refusing: run ${runId} is still ${this.stateOf(run)}`);
    if (run.applied) throw new Error(`refusing: run ${runId} was already applied at ${run.applied.at}`);
    const a = run.winner !== null ? run.attempts[run.winner] : null;
    if (this.stateOf(run) !== "verified" || !a || a.state !== "verified") {
      throw new Error(`refusing: run ${runId} is ${this.stateOf(run)}, not verified; only verified work is applied`);
    }
    if (!a.worktree || !fs.existsSync(a.worktree)) throw new Error(`refusing: the run's worktree is gone (${a.worktree})`);
    await assertNoMergeState(run.repo, this.env);
    const applied = await applyToCheckout({ repo: run.repo, dir: a.worktree, baseTree: a.baseTree, files: a.diff.files, env: this.env });
    run.applied = { at: new Date().toISOString(), files: applied.map((x) => x.path) };
    this.event(run, { event: "applied", files: run.applied.files });
    await removeWorktree({ repo: run.repo, dir: a.worktree, env: this.env });
    a.worktreeRemoved = true;
    this.persist(run);
    return { run_id: run.id, applied: true, files: applied.map((x) => x.path), actions: applied, repo: run.repo };
  }

  async cancelRun(runId) {
    const run = this.load(runId);
    if (run.applied) throw new Error(`refusing: run ${runId} was already applied`);
    run.cancelRequested = true;
    for (const a of run.attempts) this.cancelAttempt(a);
    if (run.driving) {
      await Promise.race([run.driving, new Promise((r) => setTimeout(r, 15000).unref())]);
    }
    if (this.isDone(run) && !run.discarded) {
      run.discarded = true;
      for (const a of run.attempts) {
        if (a.worktree && !a.worktreeRemoved) {
          await removeWorktree({ repo: run.repo, dir: a.worktree, env: this.env });
          a.worktreeRemoved = true;
        }
      }
      this.event(run, { event: "cancelled" });
      this.persist(run);
    }
    return { run_id: run.id, state: this.stateOf(run), worktrees_removed: run.attempts.every((a) => !a.worktree || a.worktreeRemoved) };
  }

  // Remove worktrees of kept runs (verified-unapplied, failed-with-diff) older than 7 days.
  async gc({ now = Date.now() } = {}) {
    let removed = 0;
    let names = [];
    try { names = fs.readdirSync(runsDir(this.env)); } catch { return { removed }; }
    for (const name of names) {
      if (!RUN_ID_RE.test(name) || this.runs.has(name)) continue;
      let rec;
      try { rec = readJson(path.join(runDir(this.env, name), "run.json")); } catch { continue; }
      if (!rec || !rec.finished_at || now - Date.parse(rec.finished_at) < KEEP_MS) continue;
      let changed = false;
      for (const a of rec.attempts || []) {
        if (a.worktree && !a.worktree_removed && fs.existsSync(a.worktree)) {
          await removeWorktree({ repo: rec.repo, dir: a.worktree, env: this.env });
          a.worktree_removed = true;
          changed = true;
          removed++;
        }
      }
      if (changed) writePrivate(path.join(runDir(this.env, name), "run.json"), JSON.stringify(rec, null, 2) + "\n");
    }
    return { removed };
  }

  // ---------------------------------------------------------------- driving
  async drive(run) {
    const cfg = readExecConfig(this.env);
    const sel = await selectUnits({
      registry: this.registry, config: cfg, executor: run.input.executor, model: run.input.model, race: run.input.race,
      goal_id: run.input.goal_id, procedure_id: run.input.procedure_id, step_order: run.input.step_order,
      env: this.env, fetchImpl: this.fetchImpl,
    });
    run.selection = { source: sel.source, race: sel.race, note: sel.note, recommendation: sel.recommendation };
    run.instanceKey = sel.recommendation?.instance_key || `stealth-${run.id}`;
    run.attempts = sel.units.map((u, i) => this.newAttempt(run, u, i));
    this.event(run, { event: "selected", source: sel.source, race: sel.race, units: sel.units, note: sel.note });
    this.persist(run);
    if (run.cancelRequested) for (const a of run.attempts) a.cancelRequested = true;
    await Promise.all(run.attempts.map((a) => this.runGuarded(run, a)));

    // Check-and-escalate: while nothing is verified, climb to the next untried rung, one at a time.
    const tried = new Set(run.attempts.map((a) => `${a.model}|${a.executor}`));
    const rungs = (sel.ladder || []).filter((u) => !tried.has(`${u.model}|${u.executor}`));
    for (let i = 0; i < (run.input.escalate || 0) && i < rungs.length; i++) {
      if (run.winner !== null || run.cancelRequested) break;
      const failed = run.attempts.map((a) => ({ executor: a.executor, model: a.model, state: a.state }));
      const a = this.newAttempt(run, rungs[i], run.attempts.length);
      a.escalated = true;
      run.attempts.push(a);
      this.event(run, { event: "escalate", attempt: a.index + 1, executor: a.executor, model: a.model, after: failed });
      this.persist(run);
      await this.runGuarded(run, a);
    }
  }

  newAttempt(run, u, i) {
    return {
      index: i, executor: u.executor, model: u.model, state: "queued",
      worktree: path.join(this.worktreeRoot, `${run.id}-a${i + 1}`), startedAt: null, finishedAt: null,
      checks: [], scopeViolations: [], diff: null, summary: "", learned: [], tokens: { in: null, out: null },
      costUsd: null, exitCode: null, reason: null, error: null, evidence: null, handle: null, checkHandle: null,
      cancelRequested: !!run.cancelRequested, worktreeRemoved: false,
    };
  }

  runGuarded(run, a) {
    return this.runAttempt(run, a).catch((err) => {
      a.error = this.red(err.message);
      if (!TERMINAL.has(a.state)) this.setState(run, a, a.cancelRequested ? "cancelled" : "failed");
    });
  }

  setState(run, a, state, extra = {}) {
    a.state = state;
    this.event(run, { event: state, attempt: a.index + 1, executor: a.executor, model: a.model, ...extra });
    if (state === "verified" && run.winner === null) {
      run.winner = a.index;
      for (const other of run.attempts) {
        if (other !== a && !TERMINAL.has(other.state)) {
          this.event(run, { event: "race_lost", attempt: other.index + 1 });
          this.cancelAttempt(other);
        }
      }
    }
    this.persist(run);
  }

  cancelAttempt(a) {
    a.cancelRequested = true;
    a.handle?.kill("cancelled");
    a.checkHandle?.kill("cancelled");
  }

  async runAttempt(run, a) {
    const env = this.env;
    const dir = runDir(env, run.id);
    const tag = `a${a.index + 1}`;
    a.startedAt = Date.now();
    try {
      if (a.cancelRequested) return this.setState(run, a, "cancelled");
      const adapter = await this.registry.get(a.executor);
      const probe = await this.registry.probe(a.executor);
      if (probe.refusal || !probe.runnable) {
        a.error = probe.refusal || `executor not healthy: ${probe.notes}`;
        a.didNotRun = true;
        return this.setState(run, a, "failed", { error: a.error });
      }
      const wt = await createWorktree({ repo: run.repo, commit: run.commit, dir: a.worktree, patch: run.patch, env });
      a.baseTree = wt.baseTree;
      this.event(run, { event: "worktree", attempt: a.index + 1, path: a.worktree });
      if (a.cancelRequested) return this.setState(run, a, "cancelled");

      // An open-model profile for this model (exec.json "profiles"), if any. Adapters that cannot use one ignore it.
      const profile = readExecConfig(env).profiles[a.model] || undefined;
      const spec = adapter.buildCommand({ task: run.input.task, model: a.model, worktree: a.worktree, timeoutS: run.input.timeoutS,
                                          env, bin: probe.bin || undefined, profile });
      if (!spec || !spec.cmd) throw new Error(`adapter "${a.executor}" returned no command`);
      const outLog = redactingLog(path.join(dir, `${tag}.stdout.log`), (s) => this.red(s));
      const errLog = redactingLog(path.join(dir, `${tag}.stderr.log`), (s) => this.red(s));
      let stdout = "";
      let stderr = "";
      this.setState(run, a, "running");
      const h = spawnManaged({
        cmd: spec.cmd, args: spec.args || [], cwd: a.worktree, env: childEnv(env, spec.env), stdinText: spec.stdinText,
        hangS: run.input.hangS, timeoutS: run.input.timeoutS,
        onStdout: (d) => { stdout = (stdout + d).slice(-OUT_KEEP); outLog.write(d); },
        onStderr: (d) => { stderr = (stderr + d).slice(-OUT_KEEP); errLog.write(d); },
      });
      a.handle = h;
      a.spawned = !!h.pid;
      if (a.cancelRequested) h.kill("cancelled");
      const r = await h.done;
      a.handle = null;
      outLog.close();
      errLog.close();
      a.exitCode = r.exitCode;
      a.reason = r.reason;
      this.event(run, { event: "executor_exit", attempt: a.index + 1, exit: r.exitCode, reason: r.reason, error: r.error ? this.red(r.error) : undefined });

      let parsed = {};
      try {
        parsed = adapter.parseOutput({ stdout, stderr, exitCode: r.exitCode }) || {};
      } catch (err) {
        parsed = { finalMessage: stdout.split(/\r?\n/).slice(-20).join("\n"), learned: [], parseError: err.message };
      }
      a.summary = cut(this.red(String(parsed.finalMessage || "").trim()), 1200);
      a.learned = (Array.isArray(parsed.learned) ? parsed.learned : []).slice(0, 5).map((s) => cut(this.red(String(s)), 240));
      if (parsed.tokens) a.tokens = { in: parsed.tokens.in ?? null, out: parsed.tokens.out ?? null };
      a.costUsd = parsed.costUsd ?? parsed.cost ?? null;

      let terminal = null;
      if (a.cancelRequested || r.reason === "cancelled") terminal = "cancelled";
      else if (r.reason === "hang" || r.reason === "timeout") terminal = "timed_out";
      else if (r.reason === "spawn_error") { terminal = "failed"; a.error = this.red(`could not start executor: ${r.error}`); }

      if (terminal !== "cancelled") {
        const d = await collectDiff({ dir: a.worktree, baseTree: a.baseTree, env });
        const patchPath = path.join(dir, `${tag}.patch`);
        writePrivate(patchPath, d.patch);
        a.diff = { files: d.files, stat: d.stat, worktree: a.worktree, patch_path: patchPath };
        a.scopeViolations = scopeViolations(d.files, run.input.scope);
      }
      if (terminal) return this.setState(run, a, terminal, { reason: r.reason });

      this.setState(run, a, "verifying");
      a.checks = await runChecks({
        cwd: a.worktree, checks: run.input.checks, timeoutS: run.input.checkTimeoutS, env, secrets: this.secrets,
        onEvent: (e) => this.event(run, { ...e, attempt: a.index + 1 }),
        onSpawn: (ch) => { a.checkHandle = ch; if (ch && a.cancelRequested) ch.kill("cancelled"); },
        shouldStop: () => a.cancelRequested,
      });
      if (a.cancelRequested) return this.setState(run, a, "cancelled");
      const allPass = a.checks.length === run.input.checks.length && a.checks.every((c) => c.exit === 0);
      const nonEmpty = a.diff.files.length > 0 || run.input.noChangeExpected;
      const verified = allPass && a.scopeViolations.length === 0 && nonEmpty;
      if (!verified) {
        a.error = [!allPass && "a check failed", a.scopeViolations.length && "scope violation",
          !nonEmpty && "empty diff"].filter(Boolean).join("; ");
      }
      return this.setState(run, a, verified ? "verified" : "failed");
    } finally {
      a.finishedAt = Date.now();
      await this.finishAttempt(run, a);
    }
  }

  async finishAttempt(run, a) {
    if (a.spawned) {
      a.evidence = await reportModelRun({
        model: a.model, scaffold: a.executor, accepted: a.state === "verified", instance_key: run.instanceKey,
        goal_id: run.input.goal_id, procedure_id: run.input.procedure_id, step_order: run.input.step_order,
        check_kind: a.checks.length ? "tests" : "self_report", latency_ms: a.finishedAt - a.startedAt,
        tokens_in: a.tokens.in, tokens_out: a.tokens.out, cost_usd: a.costUsd,
        recommendation_id: run.selection?.recommendation?.recommendation_id, attempt_index: a.index,
      }, { env: this.env, fetchImpl: this.fetchImpl }).catch((err) => ({ reported: false, queued: false, error: this.red(err.message) }));
    } else {
      a.evidence = { reported: false, queued: false, skipped: "the executor never ran" };
    }
    this.event(run, { event: "evidence", attempt: a.index + 1, reported: a.evidence.reported, queued: a.evidence.queued });
    const emptyDiff = !a.diff || a.diff.files.length === 0;
    const remove = a.state === "cancelled" || (a.state !== "verified" && emptyDiff);
    if (remove && a.worktree && fs.existsSync(a.worktree)) {
      await removeWorktree({ repo: run.repo, dir: a.worktree, env: this.env });
      a.worktreeRemoved = true;
    } else if (!fs.existsSync(a.worktree)) {
      a.worktreeRemoved = true;
    }
    this.persist(run);
  }

  settle(run) {
    run.finishedAt = Date.now();
    run.settled = true;
    run.patch = null;
    this.event(run, { event: "done", state: this.stateOf(run) });
    this.persist(run);
    for (const w of [...run.waiters]) w();
  }

  // ---------------------------------------------------------------- views
  isDone(run) {
    return run.settled === true;
  }

  stateOf(run) {
    if (run.discarded) return "cancelled";
    if (!run.attempts.length) {
      if (run.settled) return run.cancelRequested ? "cancelled" : "failed";
      return "queued";
    }
    if (run.winner !== null && run.attempts[run.winner]?.state === "verified" && run.settled) return "verified";
    if (run.settled || run.attempts.every((a) => TERMINAL.has(a.state))) {
      if (run.winner !== null) return "verified";
      return this.primary(run).state;
    }
    let best = "queued";
    for (const a of run.attempts) if (!TERMINAL.has(a.state) && ORDER[a.state] > ORDER[best]) best = a.state;
    if (run.winner !== null) best = "verifying";
    return best;
  }

  primary(run) {
    if (!run.attempts.length) return null;
    if (run.winner !== null) return run.attempts[run.winner];
    const rank = { failed: 0, timed_out: 1, cancelled: 2 };
    const done = run.attempts.filter((a) => TERMINAL.has(a.state));
    if (done.length === run.attempts.length && run.attempts.length > 1) {
      return [...run.attempts].sort((x, y) => (rank[x.state] ?? 9) - (rank[y.state] ?? 9) || x.index - y.index)[0];
    }
    return run.attempts[0];
  }

  elapsed(run) {
    return Math.round(((run.finishedAt || Date.now()) - run.createdAt) / 100) / 10;
  }

  attemptView(run, a) {
    return {
      run_id: run.id, state: a.state, verified: a.state === "verified",
      checks: a.checks, scope_violations: a.scopeViolations, summary: a.summary, learned: a.learned,
      diff: a.diff ? { files: a.diff.files, stat: a.diff.stat, worktree: a.worktreeRemoved ? null : a.worktree, patch_path: a.diff.patch_path }
        : { files: [], stat: "", worktree: null, patch_path: null },
      executor: a.executor, model: a.model, attempt: a.index + 1,
      duration_s: a.finishedAt && a.startedAt ? Math.round((a.finishedAt - a.startedAt) / 100) / 10 : null,
      tokens: a.tokens, cost_usd: a.costUsd,
      evidence: { ...(a.evidence || { reported: false, queued: false }), instance_key: run.instanceKey || null },
      executor_exit: a.exitCode, ...(a.reason && a.reason !== "exit" ? { stop_reason: a.reason } : {}),
      ...(a.error ? { error: a.error } : {}),
    };
  }

  contract(run) {
    const a = this.primary(run);
    if (!a) {
      return {
        run_id: run.id, state: this.stateOf(run), verified: false, checks: [], scope_violations: [], summary: "",
        learned: [], diff: { files: [], stat: "", worktree: null, patch_path: null }, executor: run.input.executor,
        model: run.input.model, attempt: 0, duration_s: this.elapsed(run), tokens: { in: null, out: null }, cost_usd: null,
        evidence: { reported: false, queued: false, instance_key: run.instanceKey || null },
        error: this.red(run.error || "run did not start"),
      };
    }
    const view = { ...this.attemptView(run, a), state: this.stateOf(run), verified: this.stateOf(run) === "verified" };
    if (run.attempts.length > 1) {
      view.race = run.attempts.map((x) => ({
        executor: x.executor, model: x.model, state: x.state, verified: x.state === "verified", attempt: x.index + 1,
        ...(x.escalated ? { escalated: true } : {}),
        evidence: { ...(x.evidence || {}), instance_key: run.instanceKey || null },
      }));
      const n = run.attempts.filter((x) => x.escalated).length;
      if (n) view.escalated = n;
    }
    if (run.selection) view.selection = { source: run.selection.source, note: run.selection.note || undefined };
    if (run.applied) view.applied = run.applied;
    return view;
  }

  // ---------------------------------------------------------------- persistence
  event(run, e) {
    const rec = { ts: new Date().toISOString(), ...e };
    run.lastEvent = e.event + (e.attempt ? ` (attempt ${e.attempt})` : "");
    try { appendPrivate(path.join(runDir(this.env, run.id), "events.jsonl"), this.red(JSON.stringify(rec)) + "\n"); } catch { /* disk full etc. */ }
  }

  persist(run) {
    const rec = {
      run_id: run.id, repo: run.repo, commit: run.commit, base: run.base,
      created_at: iso(run.createdAt), finished_at: iso(run.finishedAt), settled: !!run.settled,
      state: this.stateOf(run), winner: run.winner, applied: run.applied, discarded: run.discarded, error: run.error ? this.red(run.error) : null,
      selection: run.selection, instance_key: run.instanceKey || null,
      input: { ...run.input, task: this.red(run.input.task), checks: run.input.checks.map((c) => this.red(c)) },
      attempts: run.attempts.map((a) => ({
        ...this.attemptView(run, a), base_tree: a.baseTree || null, worktree: a.worktree,
        worktree_removed: !!a.worktreeRemoved, did_not_run: !a.spawned, escalated: !!a.escalated,
        started_at: iso(a.startedAt), finished_at: iso(a.finishedAt),
      })),
    };
    try { writePrivate(path.join(runDir(this.env, run.id), "run.json"), JSON.stringify(rec, null, 2) + "\n"); } catch { /* best effort */ }
  }

  // A run from an earlier server process: rebuilt read-only from run.json. A run that was still active
  // when that process died is reported as failed (its executor is gone with it).
  load(runId) {
    if (typeof runId !== "string" || !RUN_ID_RE.test(runId)) throw new Error(`not a run id: ${runId}`);
    const live = this.runs.get(runId);
    if (live) return live;
    const rec = readJson(path.join(runDir(this.env, runId), "run.json"));
    if (!rec) throw new Error(`unknown run: ${runId}`);
    const run = {
      id: rec.run_id, repo: rec.repo, commit: rec.commit, base: rec.base, patch: null,
      createdAt: Date.parse(rec.created_at), finishedAt: rec.finished_at ? Date.parse(rec.finished_at) : Date.now(),
      input: { ...rec.input }, selection: rec.selection, instanceKey: rec.instance_key, winner: rec.winner,
      applied: rec.applied, discarded: rec.discarded, error: rec.error, settled: true, waiters: new Set(),
      lastEvent: "loaded from disk",
      attempts: (rec.attempts || []).map((x, i) => ({
        index: i, executor: x.executor, model: x.model,
        state: TERMINAL.has(x.state) ? x.state : "failed",
        spawned: !x.did_not_run, worktree: x.worktree, worktreeRemoved: x.worktree_removed, baseTree: x.base_tree, checks: x.checks || [],
        scopeViolations: x.scope_violations || [], diff: x.diff && x.diff.files ? { ...x.diff, worktree: x.worktree } : null,
        summary: x.summary, learned: x.learned || [], tokens: x.tokens || { in: null, out: null }, costUsd: x.cost_usd,
        exitCode: x.executor_exit, reason: x.stop_reason || null,
        error: TERMINAL.has(x.state) ? x.error : "the runtime exited before this attempt finished",
        evidence: x.evidence, startedAt: Date.parse(x.started_at) || null, finishedAt: Date.parse(x.finished_at) || null,
      })),
    };
    if (!rec.settled) run.error = run.error || "the runtime exited before this run finished";
    this.runs.set(runId, run);
    return run;
  }
}
