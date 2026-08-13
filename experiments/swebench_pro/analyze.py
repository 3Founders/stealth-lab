"""
Score the run. Paired tests only, and the pairing is the point.

Both arms solve the *same* instances, so every instance is its own control:
the difficulty of the bug, the size of the file, how well ansible happens to
suit this model -- all of it cancels within a pair. Unpaired tests throw that
away and need far more data to see the same effect.

WHICH TESTS, AND WHY NOT THE ONE ALREADY IN THE REPO

app/eval/statistics.py's welch_comparison is deliberately not used here.
Welch assumes two independent groups; these groups are the same 20 instances
measured twice. Using it would understate the evidence on tokens and
overstate it on accuracy. Its benjamini_hochberg IS reused -- FDR control is
about the family of hypotheses, not about how any one of them was tested.

  tokens, tool calls  Wilcoxon signed-rank. Token counts are heavily
                      right-skewed (a flailing episode costs 10x a clean
                      one), so a mean-based test would be driven by whichever
                      arm got the worst outlier.
  accuracy            Exact McNemar, i.e. a binomial test on the discordant
                      pairs. With ~20 instances the chi-square approximation
                      is not valid.

UNDERPOWER IS REPORTED, NOT HIDDEN. At n=20 a resolve-rate difference
smaller than roughly 20 points cannot reach significance no matter what is
true. So a null result on accuracy here means "this run could not tell",
which is a different claim from "there is no difference", and the output
says which one it is.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import sys

from scipy import stats

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", ".."))
try:
    from backend.app.eval.statistics import benjamini_hochberg
except ImportError:  # standalone use
    def benjamini_hochberg(p_values, alpha=0.05):
        m = len(p_values)
        if not m:
            return []
        indexed = sorted(enumerate(p_values), key=lambda kv: kv[1])
        max_k = -1
        for rank, (_, p) in enumerate(indexed, 1):
            if p <= (rank / m) * alpha:
                max_k = rank
        out = [False] * m
        if max_k > 0:
            for rank, (i, _) in enumerate(indexed, 1):
                if rank <= max_k:
                    out[i] = True
        return out

ARMS = ("no_memory", "memory")


def load(path: str) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def usable(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split into rows that can be scored and rows that cannot, with the
    reason preserved. An instance whose gold patch does not resolve is a
    broken instance; scoring it would charge both arms for a harness fault."""
    good, dropped = [], []
    for r in rows:
        if r.get("error"):
            dropped.append({**r, "_why": f"error: {r['error'][:80]}"})
        elif not r.get("gold", {}).get("resolved"):
            dropped.append({**r, "_why": "gold patch does not resolve"})
        elif not all(isinstance(r.get(a), dict) and "total_tokens" in r[a] for a in ARMS):
            dropped.append({**r, "_why": "an arm did not run"})
        elif any(r[a].get("stop_reason") == "api_error" for a in ARMS):
            # A truncated episode has a truncated token count. Keeping it
            # would put a fake saving on whichever arm died early.
            dropped.append({**r, "_why": "arm ended on an unrecovered API error"})
        else:
            good.append(r)
    return good, dropped


def wilcoxon(a: list[float], b: list[float]) -> tuple[float, float]:
    diffs = [y - x for x, y in zip(a, b)]
    if not any(diffs):
        return 0.0, 1.0
    try:
        res = stats.wilcoxon(a, b, zero_method="wilcox", alternative="two-sided")
        return float(res.statistic), float(res.pvalue)
    except ValueError:
        return 0.0, 1.0


def mcnemar_exact(pairs: list[tuple[bool, bool]]) -> tuple[int, int, float]:
    """b = baseline solved & memory didn't, c = the reverse. Only discordant
    pairs carry information about a difference."""
    b = sum(1 for x, y in pairs if x and not y)
    c = sum(1 for x, y in pairs if y and not x)
    if b + c == 0:
        return b, c, 1.0
    p = float(stats.binomtest(c, b + c, 0.5).pvalue)
    return b, c, p


def boot_ci(values: list[float], n: int = 20000, seed: int = 0) -> tuple[float, float]:
    rng = random.Random(seed)
    k = len(values)
    means = []
    for _ in range(n):
        means.append(sum(rng.choice(values) for _ in range(k)) / k)
    means.sort()
    return means[int(0.025 * n)], means[int(0.975 * n)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True)
    ap.add_argument("--retrieval-check", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "retrieval_check.json"))
    args = ap.parse_args()

    rows = load(args.results)
    good, dropped = usable(rows)

    print("=" * 94)
    print("SWE-bench Pro (ansible/ansible) -- knowledge-graph memory vs no memory")
    print("=" * 94)
    print(f"\ninstances attempted        : {len(rows)}")
    print(f"instances scored           : {len(good)}")
    if dropped:
        print(f"instances dropped          : {len(dropped)}")
        for d in dropped:
            print(f"    - {d['instance_id'][:56]:56s} {d['_why']}")
    if not good:
        print("\nnothing scoreable.")
        return

    model = good[0].get("model", "?")
    print(f"\nmodel                      : {model}")
    print(f"step budget                : {good[0].get('max_steps')}")
    # memory_meta, not memory: record["memory"] is the arm's result.
    print(f"memory corpus (per instance): "
          f"{good[0].get('memory_meta', {}).get('corpus_size')} earlier issues, strictly by date")
    budget = good[0].get("max_steps", 25)
    exhausted = sum(1 for r in good for a in ARMS
                    if r[a].get("stop_reason") == "step_budget")
    print(f"episodes that hit the step cap: {exhausted}/{len(good) * 2}")

    # --- per-instance table ---
    print("\n" + "-" * 94)
    print(f"{'instance':<44s} {'no-mem':>14s} {'memory':>14s} {'Δtokens':>12s}")
    print("-" * 94)
    for r in good:
        a, b = r["no_memory"], r["memory"]
        d = b["total_tokens"] - a["total_tokens"]
        print(f"{r['title'][:44]:<44s} "
              f"{('R ' if a['resolved'] else '. ') + format(a['total_tokens'], ','):>14s} "
              f"{('R ' if b['resolved'] else '. ') + format(b['total_tokens'], ','):>14s} "
              f"{d:+,}".rjust(12))

    # --- accuracy ---
    pairs = [(r["no_memory"]["resolved"], r["memory"]["resolved"]) for r in good]
    n = len(pairs)
    acc_a = sum(1 for x, _ in pairs if x)
    acc_b = sum(1 for _, y in pairs if y)
    b_only, c_only, p_acc = mcnemar_exact(pairs)

    print("\n" + "=" * 94)
    print("ACCURACY (resolved = every FAIL_TO_PASS passes and no PASS_TO_PASS breaks)")
    print("=" * 94)
    print(f"  no memory : {acc_a}/{n}  ({acc_a / n:.0%})")
    print(f"  memory    : {acc_b}/{n}  ({acc_b / n:.0%})")
    print(f"  discordant: memory-only wins={c_only}  baseline-only wins={b_only}")
    print(f"  exact McNemar p = {p_acc:.3f}")

    # --- efficiency ---
    tok_a = [float(r["no_memory"]["total_tokens"]) for r in good]
    tok_b = [float(r["memory"]["total_tokens"]) for r in good]
    tool_a = [float(r["no_memory"]["n_tool_calls"]) for r in good]
    tool_b = [float(r["memory"]["n_tool_calls"]) for r in good]

    _, p_tok = wilcoxon(tok_a, tok_b)
    _, p_tool = wilcoxon(tool_a, tool_b)

    rel = [(y - x) / x * 100 for x, y in zip(tok_a, tok_b) if x]
    lo, hi = boot_ci(rel)

    print("\n" + "=" * 94)
    print("TOKEN COST (prompt + completion, summed over every LLM call in an episode)")
    print("=" * 94)
    print(f"  no memory : total {sum(tok_a):>12,.0f}   median {statistics.median(tok_a):>10,.0f}"
          f"   mean {statistics.mean(tok_a):>10,.0f}")
    print(f"  memory    : total {sum(tok_b):>12,.0f}   median {statistics.median(tok_b):>10,.0f}"
          f"   mean {statistics.mean(tok_b):>10,.0f}")
    change = (sum(tok_b) - sum(tok_a)) / sum(tok_a) * 100
    print(f"  aggregate change        : {change:+.1f}%  "
          f"({'reduction' if change < 0 else 'increase'})")
    print(f"  per-instance mean change: {statistics.mean(rel):+.1f}%  "
          f"[95% bootstrap CI {lo:+.1f}%, {hi:+.1f}%]")
    print(f"  Wilcoxon signed-rank p  = {p_tok:.4f}")

    print(f"\n  tool calls: no-mem median {statistics.median(tool_a):.0f}, "
          f"memory median {statistics.median(tool_b):.0f}, Wilcoxon p = {p_tool:.4f}")

    # --- multiplicity ---
    fam = [("accuracy", p_acc), ("tokens", p_tok), ("tool_calls", p_tool)]
    flags = benjamini_hochberg([p for _, p in fam], alpha=0.05)
    print("\n" + "=" * 94)
    print("MULTIPLICITY (Benjamini-Hochberg, alpha=0.05, family of 3)")
    print("=" * 94)
    for (name, p), keep in zip(fam, flags):
        print(f"  {name:<12s} p={p:.4f}  {'significant' if keep else 'not significant'}")

    # --- where did the tokens actually go? ---
    # A bare "+12% tokens" is not actionable. The memory arm pays a fixed
    # tax (the retrieved block sits in the conversation prefix, so it is
    # re-sent on every call of the episode) against a variable saving (fewer
    # exploration steps). Splitting them says whether the idea is wrong or
    # merely delivered in the wrong place, which are different fixes.
    taxes, savings = [], []
    for r in good:
        a, b = r["no_memory"], r["memory"]
        block_tokens = r.get("memory_meta", {}).get("block_chars", 0) / 4.0
        taxes.append(block_tokens * b["llm_calls"])
        # Value one avoided step at the baseline's own average step cost.
        per_step = a["total_tokens"] / max(1, a["llm_calls"])
        savings.append((a["llm_calls"] - b["llm_calls"]) * per_step)

    # Everything below is signed as a CHANGE IN TOKENS: positive = the memory
    # arm spent more. The tax is a cost (+) and the step saving is a credit
    # (-). An earlier version printed the prediction as a saving and the
    # observation as a change, so the two had opposite signs and looked
    # wildly inconsistent when they actually agree.
    mean_tax = statistics.mean(taxes)
    mean_saving = statistics.mean(savings)
    predicted = mean_tax - mean_saving
    observed = statistics.mean([y - x for x, y in zip(tok_a, tok_b)])

    print("\n" + "=" * 94)
    print("TOKEN DECOMPOSITION (all figures are change in tokens; + = memory arm spent more)")
    print("=" * 94)
    print(f"  prefix tax   : block re-sent every call      {mean_tax:>+10,.0f} tok/instance")
    print(f"  step saving  : steps avoided x baseline cost {-mean_saving:>+10,.0f} tok/instance")
    print(f"  net predicted: {predicted:>+10,.0f} tok/instance")
    print(f"  net observed : {observed:>+10,.0f} tok/instance")
    if observed:
        print(f"  -> the prefix tax alone accounts for {mean_tax / observed:.0%} "
              f"of the observed change")
    steps_saved = statistics.mean(
        [r["no_memory"]["llm_calls"] - r["memory"]["llm_calls"] for r in good])
    print(f"\n  mean steps avoided by memory: {steps_saved:+.2f} of "
          f"{statistics.mean([r['no_memory']['llm_calls'] for r in good]):.1f}")

    # --- did the mechanism do what it claims? ---
    if os.path.exists(args.retrieval_check):
        with open(args.retrieval_check, encoding="utf-8") as f:
            rc = {r["instance_id"]: r for r in json.load(f)}
        hit, miss = [], []
        for r in good:
            k = rc.get(r["instance_id"])
            if not k:
                continue
            (hit if k.get("recall@5", 0) > 0 else miss).append(
                (r["memory"]["total_tokens"] - r["no_memory"]["total_tokens"])
                / max(1, r["no_memory"]["total_tokens"]) * 100)
        print("\n" + "=" * 94)
        print("MECHANISM CHECK -- token change split by whether retrieval found a real file")
        print("=" * 94)
        print("  If memory helps by pointing at the right code, the saving should sit")
        print("  with the instances where retrieval actually hit. If both groups move")
        print("  together, something other than the mechanism is doing the work.")
        if hit:
            print(f"  retrieval hit  (n={len(hit):2d}): mean token change {statistics.mean(hit):+.1f}%")
        if miss:
            print(f"  retrieval miss (n={len(miss):2d}): mean token change {statistics.mean(miss):+.1f}%")

    # --- power ---
    print("\n" + "=" * 94)
    print("POWER")
    print("=" * 94)
    print(f"  With n={n} paired instances, exact McNemar needs roughly 6+ discordant")
    print(f"  pairs in one direction to reach p<0.05; this run has {b_only + c_only}.")
    if p_acc >= 0.05:
        print("  The accuracy comparison is therefore UNDERPOWERED, not null: this run")
        print("  cannot distinguish 'no accuracy effect' from 'an effect it cannot see'.")
    print("  Token cost is a paired continuous measure and is far better powered at")
    print("  this n, which is why it is the metric this pilot can actually speak to.")


if __name__ == "__main__":
    main()
