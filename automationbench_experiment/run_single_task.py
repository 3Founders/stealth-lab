"""Small utility: run ONE named AutomationBench task with a given model,
through the official unmodified env/rubric, optionally with a system-prompt
suffix injected. Reused by the ablation scripts."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "AutomationBench"))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

from dotenv import load_dotenv

load_dotenv(r"C:\Users\chait\Prog\3Found\Stealth\StealthLab\backend\.env")

import automationbench.scripts.eval as ab_eval
from automationbench.domains import get_combined_dataset

RESULTS_DIR = Path(__file__).parent / "results"
STEALTH_DIR = Path(__file__).parent / ".stealth" / "procedures"
STEALTH_DIR.mkdir(parents=True, exist_ok=True)


def _find_task_index(ds, task_name: str) -> int:
    short_name = task_name.split(".", 1)[-1]
    for i, row in enumerate(ds):
        info = row["info"] if isinstance(row["info"], dict) else json.loads(row["info"])
        candidate = info.get("task_name") or ""
        if candidate in (task_name, short_name):
            return i
    raise ValueError(f"task {task_name!r} not found")


def _one_task_dataset(domain: str, task_name: str, system_prompt_suffix: str | None = None):
    from datasets import Dataset

    ds = get_combined_dataset([domain])
    idx = _find_task_index(ds, task_name)
    row = dict(ds[idx])
    if system_prompt_suffix:
        prompt = row["prompt"]
        prompt = json.loads(prompt) if isinstance(prompt, str) else prompt
        prompt = [dict(m) for m in prompt]
        assert prompt[0]["role"] == "system"
        prompt[0]["content"] = prompt[0]["content"] + "\n\n" + system_prompt_suffix
        row["prompt"] = prompt
    return Dataset.from_dict({k: [row[k]] for k in ds.column_names})


async def run_one(*, model: str, api: str, task_name: str, domain: str,
                   export_name: str, system_prompt_suffix: str | None = None,
                   base_url: str | None = None, api_key_var: str = "GEMINI_API_KEY",
                   max_turns: int = 50) -> dict:
    ds = _one_task_dataset(domain, task_name, system_prompt_suffix)
    export_path = RESULTS_DIR / export_name

    orig = ab_eval.get_combined_dataset
    ab_eval.get_combined_dataset = lambda domains: ds
    try:
        await ab_eval.run_evaluation(
            model=model, domains=[domain], base_url=base_url, api_key_var=api_key_var,
            num_examples=-1, export_json=str(export_path), api=api, toolset="api",
            max_concurrent=1, max_turns=max_turns,
        )
    finally:
        ab_eval.get_combined_dataset = orig

    data = json.loads(export_path.read_text(encoding="utf-8"))
    t = data["tasks"][0]
    return {
        "task": t["name"], "passed": t["passed"], "score": t["score"],
        "input_tokens": t["input_tokens"], "output_tokens": t["output_tokens"],
        "num_tool_calls": t["num_tool_calls"], "steps": t["steps"], "messages": t["messages"],
    }
