"""
Diagnostic for the suspicious zero-stdev result on the LLM cells: were
the 5 repetitions actually independent calls, or is something
(provider-side caching of identical prompts, most likely) returning
the same cached response every time?

NOTE: this only works if final_code was saved per-repetition -- the
run that already produced task_b_repeated_results.json predates that
fix, so this will report "final_code not saved in this run" if so.
Re-run run_task_b_repeated.py after updating run_task_b_2x2.py to get
data this script can actually check.

Run from backend/:
    python scripts/synthetic_tasks/check_llm_variance.py
"""
import json
from pathlib import Path

HERE = Path(__file__).parent
results = json.loads((HERE / "task_b_repeated_results.json").read_text())

for label, runs in results["per_repetition"].items():
    if "llm" not in label.lower() and "LLM" not in label:
        continue
    print(f"\n{label}:")
    if not runs or "final_code" not in runs[0]:
        print("  final_code not saved in this run -- re-run after updating "
              "run_task_b_2x2.py to get real per-repetition code to compare")
        continue
    codes = [r["final_code"] for r in runs]
    unique_codes = set(codes)
    print(f"  {len(runs)} repetitions, {len(unique_codes)} unique code text(s)")
    if len(unique_codes) == 1:
        print("  ALL 5 REPETITIONS PRODUCED BYTE-IDENTICAL CODE -- strong evidence "
              "of provider-side caching on identical prompts, not 5 independent generations")
    else:
        print("  code differs across repetitions despite identical token counts -- "
              "genuinely surprising if so, worth a closer look at *why* token counts "
              "matched despite different content")
