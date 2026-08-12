# Changes — 2026-08-12 (after morning pull)

- **Resolved the merge conflict** from `git pull` (5 add/add conflicts in `experiments/swebench_pro/`). Diffed both sides fully; the incoming commit was a teammate's older, pre-fix snapshot with nothing unique — kept our side throughout. Removed 3 dead duplicate files it introduced (`code_index.py`, `patch_format.py`, `experiments/after/embed_cache.py`).
- **Verified Fix C** (replan-on-exhaustion in `htn_agent.py`) with new tests — passed. Found and rewrote 2 pre-existing tests that still asserted the old pre-Fix-C behavior; added a 3rd for the no-alternative-available branch.
- **Fix D**: `HTNAgent.run()` now discards the patch and sets `stop_reason="discarded_incomplete_plan"` when zero subgoals ever completed, instead of shipping destructive partial edits (the cause of the earlier `p2p_broke: 25` regression). 3 new tests.
- **Added `--steps-per-subgoal` CLI flag** to `run_graph_experiment.py`, needed for the 200/20 turn budget. Extracted `build_arg_parser()` so it's directly testable. 2 new tests.
- **Full suite: 530 passed, 0 failed** (up from 522).
- Stopped a background Ollama instance running with no model loaded.

**Not done yet:** the gated 200/20 ansible smoke test and the 19-instance sweep — handed off for you to run.
