"""The model client for ingestion: the model asked for is the model called.

One setting, `INGEST_MODEL`, names the model every pipeline uses (task-goal naming, trajectory extraction, verified-
solution extraction, skill compilation). Decided 2026-09-29: production uses Vertex `google/gemini-3.8-flash`.

  * `google/...` -> Vertex AI through the core client (`ingestion_jobs._vertex_oauth_client`: ADC, the `global` LLM
    location, and the Gemini 3.x reasoning-budget shaping). That wrapper always answers with the configured
    `VERTEX_MODEL`, so the requested model must BE `VERTEX_MODEL`; anything else is refused rather than silently
    answered by a different model.
  * anything else -> General Compute, called directly with that model.

`probe` proves before a run that the model answers.
"""
from __future__ import annotations

import os
from typing import Any


class ModelUnavailable(RuntimeError):
    pass


def ingest_model() -> str:
    from app.config import settings

    return (os.environ.get("INGEST_MODEL") or settings.trajectory_extraction_strong_model or "").strip()


def extraction_client(model: str | None = None) -> Any:
    from app.config import settings

    model = model or ingest_model()
    if not model:
        raise ModelUnavailable("INGEST_MODEL is not set")
    if model.startswith("google/"):
        if model != settings.vertex_model:
            raise ModelUnavailable(f"INGEST_MODEL={model} is a Vertex model but VERTEX_MODEL={settings.vertex_model}: "
                                   "the Vertex client only calls VERTEX_MODEL; set them to the same model")
        if not settings.vertex_project:
            raise ModelUnavailable("INGEST_MODEL is a Vertex model but VERTEX_PROJECT is not set")
        from app.services.ingestion_jobs import _vertex_oauth_client

        client = _vertex_oauth_client()
        if client is None:
            raise ModelUnavailable("Vertex credentials unavailable: run `gcloud auth application-default login`")
        return client
    if not settings.general_compute_api_key:
        raise ModelUnavailable("GENERAL_COMPUTE_API_KEY is not set: extraction has no model")
    from openai import OpenAI

    return OpenAI(api_key=settings.general_compute_api_key, base_url=settings.general_compute_base_url,
                  max_retries=2, timeout=180.0)


def probe(client: Any, models: list[str]) -> dict[str, str]:
    """A tiny call per model. Blocking. Raises ModelUnavailable naming every model that did not answer."""
    failed: dict[str, str] = {}
    for model in dict.fromkeys(models):
        try:
            resp = client.chat.completions.create(model=model, max_tokens=16, temperature=0,
                                                  messages=[{"role": "user", "content": "Reply with: ok"}])
            if not (resp.choices and resp.choices[0].message.content):
                failed[model] = "empty reply"
        except Exception as exc:  # noqa: BLE001 -- any failure means the run cannot use this model
            failed[model] = f"{type(exc).__name__}: {str(exc)[:160]}"
    if failed:
        raise ModelUnavailable(f"ingestion model not served: {failed}. Set INGEST_MODEL to a model the provider serves.")
    return {m: "ok" for m in dict.fromkeys(models)}
