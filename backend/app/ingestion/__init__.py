"""Provider-neutral distributed ingestion (docs/distributed_ingestion.md).

    python -m app.ingestion.enqueue   ...   put a job on the durable queue (idempotent)
    python -m app.ingestion.worker    ...   lease -> run handler -> complete/fail (any host)
    python -m app.ingestion.admin     ...   status / retry / shards / projections / verify

The queue is the existing ``ingestion_jobs`` table (extended by migration 95:
lease, idempotency key, retry state, scope). Cloud Run, GitHub Actions, Oracle
and local machines all run the SAME worker command; nothing in this package
knows which provider it is running on.
"""
