"""
§40 scoreboard: pass-rate / cost / false-reuse / stale-refusal per arm,
pairwise exact-McNemar comparisons, and a POWER-ANALYSIS FOOTER whose every
p-value carries its discordant-pair counts (board rule: never bare point
estimates).

Reads the harness JSONL (one task per line, per-arm episode records nested
under each arm key — same shape discipline as the swebench_pro results
files), classifies via scoring.py, aggregates over the USABLE subset only:
a task contributes if EVERY arm produced a valid episode on it, because a
task where one arm died cannot contribute a paired comparison and silently
dropping just that arm would compare different task sets to each other
(reference summarise() discipline).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import mcnemar_power
import scoring

HERE = Path(__file__).resolve().parent

ARMS = ("A", "B", "C")
ARM_LABELS = {
    "A": "frontier solo",
    "B": "+ conventional memory",
    "C": "+ verified procedures",
}


def load_rows(path: Path | str) -> list[dict]:
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # torn final line from an interrupted append
    return out


def classify_rows(rows: list[dict],
                  procedures_by_id: dict[str, dict]) -> list[dict]:
    """-> [{task_id, <arm>: classified-metrics-or-None}]"""
    out = []
    for r in rows:
        entry: dict = {"task_id": r.get("task_id")}
        for arm in ARMS:
            ep = r.get(arm)
            entry[arm] = None if not ep else scoring.classify(ep, procedures_by_id)
        out.append(entry)
    return out


def usable_tasks(classified: list[dict], arms=ARMS) -> list[dict]:
    """Tasks with a VALID classified episode from every requested arm."""
    return [c for c in classified
            if all(c.get(a) and c[a]["valid"] for a in arms)]


def per_arm(usable: list[dict], arms=ARMS) -> dict[str, dict]:
    res: dict[str, dict] = {}
    n = len(usable)
    for arm in arms:
        rows = [c[arm] for c in usable]
        passes = sum(1 for m in rows if m["pass"])
        reused = sum(1 for m in rows if m["reused"])
        false_reuse = sum(1 for m in rows if m["false_reuse"])
        opps = sum(1 for m in rows if m["stale_offer_opportunity"])
        correct_refusals = sum(
            1 for m in rows
            if m["stale_offer_opportunity"] and m["stale_refusal_correct"])
        missed = sum(1 for m in rows if m["stale_refusal_missed"])
        unseen_rows = [m for m in rows if m["unseen_task"]]
        unseen_pass = sum(1 for m in unseen_rows if m["pass"])
        transfers = sum(1 for m in rows if m["transfer_success"])
        total_cost = round(sum(m["cost_usd"] for m in rows), 4)
        res[arm] = {
            "n": n,
            # Every rate ships with its numerator/denominator — no bare rates.
            "passes": passes,
            "pass_rate": round(passes / n, 3) if n else None,
            "total_cost_usd": total_cost,
            "mean_cost_usd": round(total_cost / n, 4) if n else None,
            "tokens_total": sum(m["tokens_in"] + m["tokens_out"] for m in rows),
            "reuse_count": reused,
            "reuse_rate": round(reused / n, 3) if n else None,
            "false_reuse_count": false_reuse,
            "false_reuse_rate": round(false_reuse / n, 3) if n else None,
            "stale_opportunities": opps,
            "stale_refusals_correct": correct_refusals,
            "stale_refusals_missed": missed,
            "unseen_n": len(unseen_rows),
            "unseen_passes": unseen_pass,
            "transfers": transfers,
            "mean_latency_s": (
                round(sum(m["latency_seconds"] for m in rows) / n, 1)
                if n else None),
            "human_interventions": sum(
                m["human_interventions"] for m in rows),
        }
    return res


def pairwise_comparisons(usable: list[dict], arms=ARMS,
                         alpha: float = 0.05,
                         target_power: float = 0.8) -> list[str]:
    comps, lines = [], []
    for i, x in enumerate(arms):
        for y in arms[i + 1:]:
            fo, so = mcnemar_power.discordant_counts(usable, x, y)
            comps.append((x, y, fo, so))
    lines.append("PAIRWISE (exact McNemar on pass/fail discordant pairs)")
    lines.append(mcnemar_power.format_footer(comps, alpha, target_power))
    return lines


def format_error_floor(summary: dict) -> list[str]:
    """Extraction error-floor section (board MEASURE item 4).

    Takes error_floor.summarize()'s detail dict; prints overall and
    per-type precision/recall WITH numerator/denominator — same
    no-bare-rates discipline as the arm table. Rendered '-' for a null
    rate (zero denominator), never a fake 0.0/1.0.
    """
    def rate(num: int, den: int | None, val: float | None) -> str:
        if den is None or val is None:
            return f"{num}/{'?' if den is None else den} (-)"
        return f"{num}/{den} ({val:.3f})"

    lines = [
        "EXTRACTION ERROR FLOOR (observation extraction vs hand-gold)",
        (
            f"excerpts={summary['n_excerpts']} "
            f"adapter_errors={summary['n_error_excerpts']} "
            f"golds={summary['n_gold']} predictions={summary['n_predictions']} "
            f"TP={summary['tp']} FP={summary['fp']} FN={summary['fn']}"
        ),
        (
            f"precision {rate(summary['tp'], summary['tp'] + summary['fp'], summary['precision'])}  "
            f"recall {rate(summary['tp'], summary['tp'] + summary['fn'], summary['recall'])}  "
            f"f1 ({summary['f1'] if summary['f1'] is not None else '-'})"
        ),
        f"{'type':<18}{'gold':>5} {'pred':>5} {'TP':>4} {'FP':>4} {'FN':>4} "
        f"{'precision':>14} {'recall':>14}",
    ]
    for otype, m in summary["per_type"].items():
        lines.append(
            f"{otype:<18}{m['gold']:>5} {m['predictions']:>5} {m['tp']:>4} "
            f"{m['fp']:>4} {m['fn']:>4} "
            f"{rate(m['tp'], m['predictions'], m['precision']):>14} "
            f"{rate(m['tp'], m['gold'], m['recall']):>14}")
    return lines


def render(summary_arms: dict[str, dict], pairwise_lines: list[str],
           n_tasks_total: int, n_usable: int, banner: str = "") -> str:
    out = []
    out.append("=" * 78)
    out.append(banner or "SPEC 40 SCOREBOARD")
    out.append(f"tasks total={n_tasks_total} usable(all arms valid)={n_usable}")
    hdr = (f"{'arm':<4}{'n':>4} {'pass':>11} {'cost tot/mean $':>20} "
           f"{'tokens':>10} {'reuse':>12} {'false_reuse':>13} "
           f"{'stale_refusal':>17} {'unseen':>9}")
    out.append(hdr)
    out.append("-" * len(hdr))
    for arm, m in summary_arms.items():
        pr = ("-" if m["pass_rate"] is None
              else f"{m['passes']}/{m['n']} ({m['pass_rate']:.2f})")
        ru = ("-" if m["reuse_rate"] is None
              else f"{m['reuse_count']}/{m['n']} ({m['reuse_rate']:.2f})")
        fr = ("-" if m["false_reuse_rate"] is None
              else f"{m['false_reuse_count']}/{m['n']} ({m['false_reuse_rate']:.2f})")
        sr = ("no offers" if m["stale_opportunities"] == 0 else
              f"{m['stale_refusals_correct']}/{m['stale_opportunities']}"
              f"(missed {m['stale_refusals_missed']})")
        un = f"{m['unseen_passes']}/{m['unseen_n']}" if m["unseen_n"] else "-"
        out.append(
            f"{arm:<4}{m['n']:>4} {pr:>11} "
            f"{m['total_cost_usd']:>9}/{m['mean_cost_usd']:<9} "
            f"{m['tokens_total']:>10,} {ru:>12} {fr:>13} {sr:>17} {un:>9}")
    out.append(f"({' | '.join(a + ': ' + ARM_LABELS[a] for a in summary_arms)})")
    out.append("")
    out += pairwise_lines
    out.append("=" * 78)
    return "\n".join(out)


def build_summary(jsonl_paths: list[Path | str], fixtures_dir: Path | str,
                  arms=ARMS, alpha: float = 0.05,
                  target_power: float = 0.8,
                  banner: str = "") -> tuple[str, dict]:
    procedures_by_id = scoring.load_procedures(fixtures_dir)
    rows: list[dict] = []
    for p in jsonl_paths:
        rows.extend(load_rows(p))
    classified = classify_rows(rows, procedures_by_id)
    usable = usable_tasks(classified, arms)
    arms_stats = per_arm(usable, arms)
    pw_lines = pairwise_comparisons(usable, arms, alpha, target_power)
    text = render(arms_stats, pw_lines, n_tasks_total=len(classified),
                  n_usable=len(usable), banner=banner)
    detail = {
        "n_tasks_total": len(classified),
        "n_usable": len(usable),
        "arms": arms_stats,
        "comparisons": [
            {"first": f, "second": s,
             "discordant_first_only": fo, "discordant_second_only": so}
            for f, s, fo, so in _extract_comparisons(usable, arms)],
    }
    return text, detail


def _extract_comparisons(usable: list[dict], arms):
    for i, x in enumerate(arms):
        for y in arms[i + 1:]:
            fo, so = mcnemar_power.discordant_counts(usable, x, y)
            yield x, y, fo, so


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("jsonl", nargs="+", help="harness result JSONL file(s)")
    ap.add_argument("--fixtures-dir", default=str(HERE / "fixtures"))
    ap.add_argument("--alpha", type=float, default=0.05)
    ap.add_argument("--target-power", type=float, default=0.80)
    ap.add_argument("--banner", default="SPEC 40 SCOREBOARD (synthetic fixtures)")
    ap.add_argument("--error-floor-results", default=None,
                    help="error_floor detail JSON; prints the extraction "
                         "precision/recall section after the arm table")
    args = ap.parse_args(argv)

    text, detail = build_summary(
        [Path(p) for p in args.jsonl], Path(args.fixtures_dir),
        alpha=args.alpha, target_power=args.target_power, banner=args.banner)
    if args.error_floor_results:
        ef = json.loads(Path(args.error_floor_results).read_text(encoding="utf-8"))
        ef_lines = format_error_floor(ef)
        text = text.rstrip("\n") + "\n" + "\n".join(ef_lines) + "\n" + "=" * 78 + "\n"
        detail["error_floor"] = ef
    print(text)
    out_path = Path(args.jsonl[-1]).with_suffix("").with_name(
        Path(args.jsonl[-1]).stem + "_scoreboard.json")
    out_path.write_text(json.dumps(detail, indent=2), encoding="utf-8")
    print(f"[scoreboard detail -> {out_path}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
