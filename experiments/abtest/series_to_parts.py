"""series_to_parts: the cheap answers of a series run, as survey stage 3 parts, so survey stage 4 grades each of them with
the hidden tests (and the project's existing tests) -- whether an accepted answer was right, and whether one the rule
did not accept would have been.

    python experiments/abtest/series_to_parts.py <out_dir> <results.jsonl> [<results.jsonl> ...]
    python -m modal volume put stealthlab-series <out_dir> survey/<run>/stage3.parts
    python -m modal run --detach experiments/abtest/modal_survey.py --stage 4 --run <run>-s4 --src-run <run> --workers 4 --ids "<ids>"

Each answer is one "agent" named <arm>.<model>.<n> (n: its order among that arm's tries of the issue). Only tries that
left a patch are kept; the results' attempts carry it since 2026-10-17 (runner.py open_model_runs).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def parts(results_files: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for f in results_files:
        for line in Path(f).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            iid, arm = r["task_id"], r.get("arm", "?")
            rec = out.setdefault(iid, {"instance_id": iid, "agents": {}})
            for n, a in enumerate(r.get("attempts") or [], 1):
                if not (a.get("patch") or "").strip() or a.get("role") != "attempt":
                    continue
                rec["agents"][f"{arm}.{a.get('model')}.{n}"] = {"patch": a["patch"], "state": a.get("state"), "files": [],
                                                                 "cost_usd": a.get("cost_usd")}
    return out


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    out = Path(argv[0])
    out.mkdir(parents=True, exist_ok=True)
    got = parts(argv[1:])
    for iid, rec in got.items():
        (out / f"{iid}.json").write_text(json.dumps(rec), encoding="utf-8")
    print(f"{len(got)} issues, {sum(len(r['agents']) for r in got.values())} answers -> {out}; ids: {','.join(sorted(got))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
