# Deviations -- SWE-rebench variant

Protocol: docs/knowledge_side_improvements.md (SWE-bench section), with the dataset and split in
experiments/swebench_rebench/experiment.json. Shared machinery deviations are in experiments/swebench/DEVIATIONS.md.

## 2026-09-27 -- before calibration (nothing scored)

1. **Dataset and split.** nebius/SWE-rebench (rev 89cdfbab), tasks with a prebuilt image only. Held-out = created
   on/after the model's cutoff (2024-07-01), train = before it; repos with >= 8 held-out and >= 10 train tasks;
   caps of 40 per repo. Design: 13 repos, 317 train / 193 test / 12 calibration (sha256 4919f825...).
2. **Grading dataset rendered by SWE-rebench's own harness fork** (commit e4907b7a) from each task's
   install_config: eval_script, log_parser = install_config.log_parser, image = docker_image. The fork's
   TractoAI backend (unused) is stubbed at import in the render process only.
3. **Modal image fix for SWE-rebench images** (modal_compat v3): the images install the repo editable from
   /<name> but ship it at /testbed, so on Modal the package did not import (gold smoke 0/3, ModuleNotFoundError).
   Missing editable roots are symlinked to /testbed at image build. Gold smoke after the fix: 3/3 resolved
   (beeware__briefcase-1972, dask__dask-11233, PennyLaneAI__pennylane-5926).
4. **Every train/test/calibration task is gold-checked** (not only calibration), because SWE-rebench is validated
   automatically rather than by humans. Tasks whose gold patch does not resolve are dropped from every arm and
   listed here.
