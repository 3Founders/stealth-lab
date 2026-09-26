"""Model calls for the demo.

Open models: General Compute's OpenAI-compatible endpoint (base URL and key from
backend/.env via app.config -- the key is never printed or written anywhere).
Sonnet: not called from here. A fresh Sonnet subagent (spawned by the orchestrating
Claude Code session, with no conversation context) answers each prompt; its replies
are recorded with record_external.py. Subagents do not report token usage, so Sonnet
tokens are ESTIMATED from text length (chars / 4) and flagged as estimated.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx

SYSTEM = ("You are an expert Python programmer. Solve the task. Reply with exactly ONE ```python code block "
          "containing the complete, self-contained solution: every import it needs and the function it asks for. "
          "No explanation outside the code block.")
PRICES = json.loads((Path(__file__).resolve().parent / "prices.json").read_text(encoding="utf-8"))
SCAFFOLD_API = "direct-prompt"
SCAFFOLD_SUBAGENT = "claude-code-subagent"


@dataclass
class Reply:
    text: str
    tokens_in: int
    tokens_out: int
    estimated: bool
    latency_ms: int
    error: Optional[str] = None


def build_prompt(statement: str, knowledge: Optional[str] = None) -> str:
    if not knowledge:
        return statement
    return (f"Kel found how this kind of task is done (retrieved knowledge -- use it if it applies):\n"
            f"{knowledge}\n\n--- TASK ---\n{statement}")


def cost_usd(model: str, tokens_in: int, tokens_out: int) -> float:
    price = PRICES[model]
    return (tokens_in * price["input"] + tokens_out * price["output"]) / 1e6


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class GeneralCompute:
    def __init__(self) -> None:
        from app.config import settings

        self.base = str(settings.general_compute_base_url).rstrip("/")
        keys = [k.strip() for k in (settings.general_compute_api_keys or "").split(",") if k.strip()]
        self.keys = keys or [str(settings.general_compute_api_key or "")]
        if not self.keys[0]:
            raise RuntimeError("GENERAL_COMPUTE_API_KEY is not configured")
        self.client = httpx.Client(timeout=180)
        self._turn = 0

    def complete(self, model: str, prompt: str, *, max_tokens: int = 4096) -> Reply:
        body = {"model": model, "temperature": 0, "max_tokens": max_tokens,
                "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]}
        last = None
        for attempt in range(6):
            key = self.keys[self._turn % len(self.keys)]
            self._turn += 1
            started = time.time()
            try:
                r = self.client.post(f"{self.base}/chat/completions", json=body,
                                     headers={"Authorization": f"Bearer {key}"})
            except httpx.HTTPError as exc:
                last = f"network: {type(exc).__name__}"
                time.sleep(min(30, 2 ** attempt))
                continue
            if r.status_code in (429, 500, 502, 503, 504):
                last = f"HTTP {r.status_code}"
                time.sleep(min(30, 2 ** attempt))
                continue
            if r.status_code >= 400:
                return Reply("", 0, 0, False, int((time.time() - started) * 1000), f"HTTP {r.status_code}: {r.text[:200]}")
            data = r.json()
            text = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
            usage = data.get("usage") or {}
            tin, tout = usage.get("prompt_tokens"), usage.get("completion_tokens")
            return Reply(text, int(tin or estimate_tokens(SYSTEM + prompt)), int(tout or estimate_tokens(text)),
                         tin is None or tout is None, int((time.time() - started) * 1000))
        return Reply("", 0, 0, False, 0, f"gave up after retries: {last}")
