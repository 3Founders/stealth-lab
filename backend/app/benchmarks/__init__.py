"""Benchmark ingestion: public benchmark tasks become Goals (placed in the hierarchy
under domain Goals), each with a FROZEN benchmark built from the task's tests.

    tasks.py          BenchmarkTask (source-neutral) + deterministic fit/held-out split
    bigcodebench.py   adapter: BigCodeBench rows -> BenchmarkTask
    importer.py       idempotent import into Kel (Goals, accepted edges, frozen benchmarks)
    admin_cli.py      `python -m app.ingestion.admin benchmark-import ...`

Reference solutions are never stored (only their hash): benchmark answers must not
leak into the knowledge agents retrieve from. Each task's tests are split into a
VISIBLE subset (the runtime check a real agent would run between attempts) and the
full suite (the gold grade), so evaluations cannot grade with the answer key twice.
"""
