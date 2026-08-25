"""
Run the extraction error-floor measurement (board MEASURE item 4).

Loads the hand-gold excerpts (fixtures/error_floor/), obtains predictions
from EITHER a dotted-path adapter resolved in THIS process's environment
(`--adapter pkg.module:func`; put backend on PYTHONPATH yourself — the
harness never imports backend/**, lane rule) OR a predictions JSONL
(one {"excerpt_id": ..., "observations": [...]} per line, from any
out-of-process extractor run), grades them under the rubric
(fixtures/error_floor/_rubric.md), prints every discrepancy plus the
precision/recall section via scoreboard.format_error_floor, and writes
results + detail files for the scoreboard to re-consume later.

Exit code 1 iff any excerpt's adapter raised — a partial floor is not a
floor.
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
import traceback
from pathlib import Path

import error_floor
import scoreboard

HERE = Path(__file__).resolve().parent


def resolve_adapter(spec: str):
    """'pkg.module:func' -> callable. Import cost belongs to the caller."""
    if ":" not in spec:
        raise ValueError(f"--adapter wants 'module:function', got {spec!r}")
    mod_name, _, func_name = spec.partition(":")
    mod = importlib.import_module(mod_name)
    fn = getattr(mod, func_name, None)
    if fn is None or not callable(fn):
        raise ValueError(f"{mod_name} has no callable {func_name!r}")
    return fn


def load_predictions(path: Path) -> dict[str, list[dict]]:
    by_id: dict[str, list[dict]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        by_id[row["excerpt_id"]] = row.get("observations", [])
    return by_id


def run(excerpts: list[dict], get_preds, extractor_name: str):
    """get_preds: excerpt dict -> prediction list (adapter or lookup)."""
    graded, errors = [], []
    for ex in excerpts:
        try:
            preds = get_preds(ex)
            if not isinstance(preds, list):
                raise TypeError(
                    f"adapter returned {type(preds).__name__}, want list")
            graded.append(error_floor.grade_excerpt(ex, preds))
        except Exception as exc:  # noqa: BLE001 - instrument reports, never dies mid-run
            errors.append({
                "excerpt_id": ex["excerpt_id"],
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(limit=3),
            })
    summary = error_floor.summarize(graded, n_errors=len(errors),
                                    extractor_name=extractor_name)
    return graded, errors, summary


def render_discrepancies(graded: list[dict], errors: list[dict]) -> list[str]:
    lines: list[str] = []
    if errors:
        lines.append("ADAPTER ERRORS (excerpt skipped, exit will be 1):")
        lines.extend(f"  {e['excerpt_id']}: {e['error']}" for e in errors)
    for g in graded:
        if not (g["fp"] or g["fn"]):
            continue
        lines.append(f"{g['excerpt_id']}:")
        lines.extend(f"  FP {rec['observation_type']} key={rec['key']!r} "
                     f"[{rec['reason']}]" for rec in g["fp_detail"])
        lines.extend(f"  FN {rec['observation_type']} key={rec['key']!r} "
                     f"[{rec['reason']}]" for rec in g["fn_detail"])
    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fixtures-dir", default=str(error_floor.DEFAULT_FIXTURES_DIR))
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--adapter", help="dotted 'module:function' extractor")
    src.add_argument("--predictions", help="predictions JSONL from an offline run")
    ap.add_argument("--out", default=str(HERE / "error_floor_results.jsonl"))
    args = ap.parse_args(argv)

    excerpts = error_floor.load_excerpts(Path(args.fixtures_dir))

    if args.adapter:
        adapter = resolve_adapter(args.adapter)
        get_preds = lambda ex: adapter(ex["trace_event"])  # noqa: E731 - thin seam
        extractor_name = args.adapter
    else:
        by_id = load_predictions(Path(args.predictions))
        missing = [ex["excerpt_id"] for ex in excerpts if ex["excerpt_id"] not in by_id]
        if missing:
            print(f"predictions file missing excerpt_ids: {missing[:5]} "
                  f"({len(missing)} total)", file=sys.stderr)
            return 2
        get_preds = lambda ex: by_id[ex["excerpt_id"]]  # noqa: E731
        extractor_name = f"predictions:{args.predictions}"

    graded, errors, summary = run(excerpts, get_preds, extractor_name)

    out_path = Path(args.out)
    body = "".join(json.dumps(g) + "\n" for g in graded)
    out_path.write_text(body, encoding="utf-8")
    detail_path = out_path.with_name(out_path.stem + "_detail.json")
    detail_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print("EXTRACTION ERROR FLOOR — per-excerpt discrepancies")
    print("\n".join(render_discrepancies(graded, errors)) or "(none)")
    print()
    print("\n".join(scoreboard.format_error_floor(summary)))
    print(f"[detail -> {detail_path}]")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
