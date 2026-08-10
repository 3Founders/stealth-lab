"""
Confirm every model provider the experiments depend on actually answers,
before a long run discovers it does not.

Checks, in order of how badly each would silently corrupt a result:
  1. General Compute panel + judge   -- decomposition (Exp 1B) and debate (Exp 3)
  2. Panel heterogeneity             -- enforce_independence rejects a judge
                                        sharing a family with the panel, and it
                                        raises at construction, mid-run
  3. Ollama local models             -- SLM arm (Exp 4)
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.config import settings


async def check_general_compute() -> bool:
    from app.debate.panel import default_judge, default_panel

    ok = True
    try:
        panel = default_panel()
        judge = default_judge()
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL: could not construct panel/judge: {exc}")
        return False

    print(f"  base_url: {settings.general_compute_base_url}")
    print(f"  panel: {[getattr(a, 'model', '?') for a in panel]}")
    print(f"  judge: {getattr(judge, 'model', '?')}")

    try:
        from app.eval.layer1 import enforce_independence
        enforce_independence(judge, panel)
        print("  heterogeneity: OK (judge family independent of panel)")
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL heterogeneity: {exc}")
        ok = False

    for agent in list(panel) + [judge]:
        name = getattr(agent, "model", "?")
        try:
            reply = await asyncio.wait_for(
                agent.respond("Reply with exactly: OK", "Say OK."), timeout=90.0
            )
            snippet = (reply or "").strip().replace("\n", " ")[:60]
            print(f"  {name}: responded {snippet!r}")
        except Exception as exc:  # noqa: BLE001
            print(f"  {name}: FAIL {type(exc).__name__}: {str(exc)[:200]}")
            ok = False
    return ok


async def check_ollama() -> bool:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key="not-needed", base_url=settings.local_base_url)
    ok = True
    for model in ("llama3.1:8b", "qwen2.5-coder:7b"):
        try:
            r = await asyncio.wait_for(
                client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": "Reply with exactly: OK"}],
                    max_tokens=10,
                ),
                timeout=180.0,
            )
            print(f"  {model}: {r.choices[0].message.content!r} "
                  f"(prompt={r.usage.prompt_tokens} completion={r.usage.completion_tokens})")
        except Exception as exc:  # noqa: BLE001
            print(f"  {model}: FAIL {type(exc).__name__}: {str(exc)[:200]}")
            ok = False
    return ok


async def main() -> int:
    print("=== General Compute (decomposition + debate) ===")
    gc_ok = await check_general_compute()
    print("\n=== Ollama (SLM arm) ===")
    ol_ok = await check_ollama()

    print("\n=== verdict ===")
    print(f"  General Compute: {'OK' if gc_ok else 'UNAVAILABLE'}")
    print(f"  Ollama:          {'OK' if ol_ok else 'UNAVAILABLE'}")
    return 0 if (gc_ok and ol_ok) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
