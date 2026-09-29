"""SWE-rebench OpenHands trajectories (nebius/SWE-rebench-openhands-trajectories) -> Kel knowledge.

Plan (docs/ingestion_sources_plan.md, rows A.1 and "Order" 1; build prompt steps 0-1):
  * one trajectory per (task, outcome): the best resolved run of each task, plus at most one failed run;
  * resolved runs become Procedures; failed runs become failure Claims, never Procedures;
  * held-out tasks and every task of a held-out repository are excluded, failing closed;
  * the license is the task repository's own (`license_name` in the parent nebius/SWE-rebench), decided per item;
    the trajectory text itself is nebius's CC-BY-4.0 work and is credited as such;
  * the trajectory enters through the same trace -> episode -> semantic-extraction path the Claude Code collector
    uses, so it is learned exactly the way a user's own session is.
"""
