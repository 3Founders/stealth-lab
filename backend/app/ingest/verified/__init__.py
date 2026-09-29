"""Verified task solutions -> the task's Goal, a Procedure carrying the gold patch, and the task's Benchmark.

Plan (docs/ingestion_sources_plan.md, "Order" 2; build prompt step 2): SWE-rebench, SWE-rebench-V2, SWE-bench-extra and
SWE-Gym as `verified_solution` knowledge -- issue -> patch -> tests. Each task:
  * lands on the ONE Goal every pipeline shares for that task (migration 129), so agent runs of the same task
    (OpenHands trajectories) and this verified solution meet;
  * gets one Procedure written from the issue, the gold patch and its tests: steps with their role and a concrete
    check, preconditions, pitfalls, and short natural-language facts -- the gold patch attached as its verified
    solution;
  * gets a frozen Benchmark when the task has a runnable environment (docker image), so Kel can evaluate against it;
  * records the dataset's test result as testimony, never as Kel verification.
One source writes a task once: SWE-rebench and SWE-bench-extra share 4,568 tasks, SWE-Gym overlaps SWE-rebench on 196.
"""
