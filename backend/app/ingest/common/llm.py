"""The model client for ingestion extraction: the model asked for is the model called.

The core's rotating client (`ingestion_jobs._general_compute_client`) puts Vertex first when it is configured, and
the Vertex wrapper answers with its own configured model whatever model the caller names. For extraction that is
a silent substitution: `extraction_routing` escalates a hard trajectory to the strong model and a different model
answers. Ingestion therefore calls General Compute directly, where the cheap and strong extraction models live,
and `probe` proves before the run starts that every model the run may use actually answers.
"""
from __future__ import annotations

from typing import Any


class ModelUnavailable(RuntimeError):
    pass


def extraction_client() -> Any:
    from openai import OpenAI

    from app.config import settings

    if not settings.general_compute_api_key:
        raise ModelUnavailable("GENERAL_COMPUTE_API_KEY is not set: extraction has no model")
    return OpenAI(api_key=settings.general_compute_api_key, base_url=settings.general_compute_base_url,
                  max_retries=2, timeout=180.0)


def probe(client: Any, models: list[str]) -> dict[str, str]:
    """A one-token call per model. Blocking. Raises ModelUnavailable naming every model that did not answer."""
    failed: dict[str, str] = {}
    for model in dict.fromkeys(models):
        try:
            client.chat.completions.create(model=model, max_tokens=1, temperature=0,
                                           messages=[{"role": "user", "content": "ok"}])
        except Exception as exc:  # noqa: BLE001 -- any failure means the run cannot use this model
            failed[model] = f"{type(exc).__name__}: {str(exc)[:160]}"
    if failed:
        raise ModelUnavailable(f"extraction model(s) not served by the configured provider: {failed}. Set "
                               "TRAJECTORY_EXTRACTION_CHEAP_MODEL / TRAJECTORY_EXTRACTION_STRONG_MODEL (or the skill "
                               "extraction model) to models the provider serves.")
    return {m: "ok" for m in dict.fromkeys(models)}
