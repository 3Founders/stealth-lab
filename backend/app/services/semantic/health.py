"""`python -m app.ingestion.admin judge-health` -- is every semantic judge path actually answering?

Probes, with tiny synthetic inputs:
  * each configured provider (JEV, Gemini, Gemma) on a single identity judgment AND on an identity
    batch (the path goal placement / identity resolution use);
  * each General Compute key the Gemma fallback rotates through, on the model it will call.

Prints provider names, model ids, key POSITIONS and HTTP status / error class -- never a key, a
URL with credentials, or response text. Exit code 0 when every configured provider answers both
probes, 1 otherwise.
"""
from __future__ import annotations

import json
from typing import Any

_A = "Calculate row-wise percentages for numeric columns in a pandas DataFrame"
_B = [{"id": "0", "text": "Compute each category's share of the column total in a pandas DataFrame"},
      {"id": "1", "text": "Train a random forest classifier with scikit-learn"}]


async def probe() -> dict[str, Any]:
    from app.config import settings
    from app.services.identity_resolution import default_judge
    from app.services.semantic import providers as pv

    report: dict[str, Any] = {"providers": {}, "general_compute_keys": []}
    judge = default_judge()
    for p in getattr(judge, "providers", []):
        entry: dict[str, Any] = {"model": getattr(p, "model", None)}
        for label, call in (("identity", lambda p=p: p.identity("goal", _A, _B[0]["text"])),
                            ("identity_batch", lambda p=p: p.identity_batch("goal", _A, _B))):
            try:
                await call()
                entry[label] = "ok"
            except Exception as exc:  # noqa: BLE001 -- reported, never raised
                entry[label] = f"{type(exc).__name__}: {str(exc)[:160]}"
        report["providers"][p.name] = entry

    keys = pv._general_compute_keys(settings)
    if keys:
        from openai import AsyncOpenAI

        model = (getattr(settings, "general_compute_fallback_model", None)
                 or getattr(settings, "general_compute_judge_model", None) or "gemma-4-31b-it")
        for index, key in enumerate(keys):
            client = AsyncOpenAI(api_key=key, base_url=settings.general_compute_base_url, max_retries=0, timeout=60)
            try:
                await client.chat.completions.create(model=model, max_tokens=3, temperature=0,
                                                     messages=[{"role": "user", "content": "ok"}])
                status = "ok"
            except Exception as exc:  # noqa: BLE001
                status = f"{type(exc).__name__} (HTTP {getattr(exc, 'status_code', '?')})"
            report["general_compute_keys"].append({"position": index, "model": model, "status": status})
    report["healthy"] = all(v.get("identity") == "ok" and v.get("identity_batch") == "ok"
                            for v in report["providers"].values()) and bool(report["providers"])
    return report


async def run(_pool: Any, _args: Any) -> int:
    report = await probe()
    print(json.dumps(report, indent=2))
    return 0 if report["healthy"] else 1
