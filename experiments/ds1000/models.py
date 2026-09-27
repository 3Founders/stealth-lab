"""Prompts and model calls -- IDENTICAL for every arm; only the notes block differs.

Open models: General Compute (OpenAI-compatible; key from backend/.env via app.config,
never printed or written). Temperature 0, one sample, max_tokens 4096.
Sonnet: a fresh Claude Code subagent per batch (no conversation context) reads a batch
file and writes its answers; tokens are ESTIMATED (chars / 4) and flagged.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx

SYSTEM = (
    "You are an expert Python data scientist. You are given a problem together with its setup code. "
    "Write ONLY the code that goes at the solution point (where the problem says BEGIN SOLUTION / put "
    "solution in this variable). It runs right after the setup code, so the variables the setup defines "
    "already exist: do not recreate or hard-code the example data. Store the answer in the variable the "
    "problem asks for (usually `result`). If the problem asks you to complete a function, write only the "
    "body of that function. Reply with exactly one ```python code block and nothing else.")
NOTES_INTRO = ("Notes from similar past work are below. They may or may not apply to this problem; "
               "use them only if they help.")
OPEN_MODELS = ("gemma-4-31B-it", "gpt-oss-120b", "deepseek-v3.2")
SONNET = "claude-sonnet-5"
PRICES = json.loads((Path(__file__).resolve().parents[1] / "bigcodebench" / "prices.json").read_text(encoding="utf-8"))


def build_prompt(problem: str, notes: Optional[str] = None) -> str:
    if not notes:
        return f"PROBLEM:\n{problem}"
    return f"{NOTES_INTRO}\n<notes>\n{notes}\n</notes>\n\nPROBLEM:\n{problem}"


def cost_usd(model: str, tokens_in: int, tokens_out: int) -> float:
    p = PRICES[model]
    return (tokens_in * p["input"] + tokens_out * p["output"]) / 1e6


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


@dataclass
class Reply:
    text: str
    tokens_in: int
    tokens_out: int
    estimated: bool
    latency_ms: int
    error: Optional[str] = None


class GeneralCompute:
    def __init__(self) -> None:
        from app.config import settings

        self.base = str(settings.general_compute_base_url).rstrip("/")
        keys = [k.strip() for k in (settings.general_compute_api_keys or "").split(",") if k.strip()]
        self.keys = keys or [str(settings.general_compute_api_key or "")]
        if not self.keys[0]:
            raise RuntimeError("GENERAL_COMPUTE_API_KEY is not configured")
        self.client = httpx.Client(timeout=240)
        self._turn = 0

    def complete(self, model: str, prompt: str, *, system: str = SYSTEM, max_tokens: int = 4096) -> Reply:
        body = {"model": model, "temperature": 0, "max_tokens": max_tokens,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
        last = None
        for attempt in range(8):
            key = self.keys[self._turn % len(self.keys)]
            self._turn += 1
            started = time.time()
            try:
                r = self.client.post(f"{self.base}/chat/completions", json=body,
                                     headers={"Authorization": f"Bearer {key}"})
            except httpx.HTTPError as exc:
                last = f"network: {type(exc).__name__}"
                time.sleep(min(60, 2 ** attempt))
                continue
            if r.status_code in (429, 500, 502, 503, 504):
                last = f"HTTP {r.status_code}"
                time.sleep(min(60, 2 ** attempt))
                continue
            if r.status_code >= 400:
                return Reply("", 0, 0, False, int((time.time() - started) * 1000), f"HTTP {r.status_code}: {r.text[:200]}")
            data = r.json()
            text = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
            usage = data.get("usage") or {}
            tin, tout = usage.get("prompt_tokens"), usage.get("completion_tokens")
            return Reply(text, int(tin or estimate_tokens(system + prompt)), int(tout or estimate_tokens(text)),
                         tin is None or tout is None, int((time.time() - started) * 1000))
        return Reply("", 0, 0, False, 0, f"gave up after retries: {last}")
