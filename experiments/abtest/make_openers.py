"""make_openers: the opener (and follow-ups) of each issue of a series, written ONCE, before the arms start.

A split run gives each arm its own container: each wrote its own opener with glm-5.3-flash (multiturn.make_opener caches
per task, but per container), and the model does not write the same text twice -- so A, C and C0 were told different
things (ct-series-v5: jsonargparse C's opener was dispatched as a code task, C0's was guarded as a question). The series'
prepare step runs this into the run's shared folder on the volume, and every arm copies it in before its first task.

    python experiments/abtest/make_openers.py <series_module> <repo> <out_dir> <gc_key_name> <task_id> [<task_id> ...]
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def main(argv: list[str]) -> int:
    if len(argv) < 5:
        print(__doc__)
        return 2
    module, repo, out_dir, gc_key_name, ids = argv[0], argv[1], Path(argv[2]), argv[3], argv[4:]
    import multiturn
    import runner
    mod = importlib.import_module(module)
    tasks = {t["task_id"]: t for t in mod.series(repo)}
    missing = [i for i in ids if i not in tasks]
    if missing:
        print(f"not in series {repo}: {', '.join(missing)}", file=sys.stderr)
        return 2
    llm = runner.sim_llm({"zai": runner.secret_value("Z_AI_KEY"), "gc": runner.secret_value(gc_key_name)})
    out_dir.mkdir(parents=True, exist_ok=True)
    for i in ids:
        t = tasks[i]
        issue = mod.issue_text(t) if hasattr(mod, "issue_text") else mod.prompt(t)
        op = multiturn.make_opener(i, issue, llm, out_dir)          # cached: an issue that has one keeps it
        print(f"{i}: {op['opener'][:100]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
