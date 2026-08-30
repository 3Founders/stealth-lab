"""
The local half of the local/global runtime split (architecture audit +
this session's Phase 1 plan). Everything under this package MUST have
zero database dependency -- see local_agent/runner.py's own docstring and
tests/test_local_agent_runner_offline.py's structural assertion for why
that boundary is proven, not just conventional.
"""
