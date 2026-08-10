"""
List and smoke-test hosted models available for the SLM arm.

Model IDs are discovered from each provider rather than hardcoded: these
catalogs change, and a stale ID fails as a 404 mid-run rather than at
setup, after the expensive part has already been paid for.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from openai import AsyncOpenAI

from app.config import settings


async def probe(label: str, key: str | None, base_url: str) -> list[str]:
    if not key:
        print(f"\n{label}: no API key configured")
        return []
    try:
        models = await AsyncOpenAI(api_key=key, base_url=base_url).models.list()
    except Exception as exc:  # noqa: BLE001
        print(f"\n{label}: FAIL listing models -- {type(exc).__name__}: {str(exc)[:200]}")
        return []
    ids = sorted(m.id for m in models.data)
    print(f"\n{label}: {len(ids)} models (key len {len(key)})")
    for i in ids:
        print(f"    {i}")
    return ids


async def smoke(label: str, key: str, base_url: str, model: str) -> bool:
    try:
        r = await asyncio.wait_for(
            AsyncOpenAI(api_key=key, base_url=base_url).chat.completions.create(
                model=model, max_tokens=10, temperature=0,
                messages=[{"role": "user", "content": "Reply with exactly: OK"}],
            ), timeout=120.0)
        print(f"  {label}/{model}: {r.choices[0].message.content!r} "
              f"(prompt={r.usage.prompt_tokens} completion={r.usage.completion_tokens})")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"  {label}/{model}: FAIL {type(exc).__name__}: {str(exc)[:160]}")
        return False


SMALL_HINTS = ("8b", "9b", "7b", "4b", "3b", "1b", "mini", "small", "instant", "flash", "lite")


async def main() -> int:
    providers = [
        ("groq", settings.groq_api_key, settings.groq_base_url),
        ("general_compute", settings.general_compute_api_key, settings.general_compute_base_url),
        ("cerebras", settings.cerebras_api_key, settings.cerebras_base_url),
    ]
    found = {}
    for label, key, url in providers:
        found[label] = (await probe(label, key, url), key, url)

    print("\n=== smoke test: small-model candidates ===")
    for label, (ids, key, url) in found.items():
        if not ids:
            continue
        small = [m for m in ids
                 if any(t in m.lower() for t in SMALL_HINTS)
                 and not any(x in m.lower() for x in ("whisper", "tts", "guard", "embed"))]
        print(f"\n{label} small candidates: {small or '(none)'}")
        for m in small[:4]:
            await smoke(label, key, url, m)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
