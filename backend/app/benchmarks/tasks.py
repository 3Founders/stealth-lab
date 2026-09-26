"""Source-neutral benchmark tasks and the deterministic fit / held-out split."""
from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence


def stable_hash(*parts: Any) -> str:
    return hashlib.sha256("\x1f".join(map(str, parts)).encode("utf-8")).hexdigest()


@dataclass
class BenchmarkTask:
    source: str                         # "bigcodebench"
    source_version: str                 # "v0.1.4"
    external_id: str                    # "BigCodeBench/0"
    goal_name: str                      # the Goal's canonical name (what someone wants done)
    goal_description: str               # the task statement given to a model
    domains: list[str]                  # parent Goal names, most specific first
    test_code: str                      # the full test suite (gold)
    test_names: list[str]               # every test in test_code
    visible_tests: list[str]            # the runtime-check subset
    entry_point: str
    libs: list[str] = field(default_factory=list)
    prompt_prefix: str = ""             # code the model completes / must keep (e.g. imports + signature)
    reference_sha256: Optional[str] = None
    split: Optional[str] = None         # "fit" | "heldout" (assign_splits)
    excluded_reason: Optional[str] = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def hidden_tests(self) -> list[str]:
        return [t for t in self.test_names if t not in self.visible_tests]


def choose_visible(external_id: str, test_names: Sequence[str], fraction: float = 1 / 3) -> list[str]:
    """A stable ~1/3 of the tests (at least one, never all): order by hash, take the head."""
    ordered = sorted(test_names, key=lambda name: stable_hash(external_id, name))
    count = min(len(ordered) - 1, max(1, math.ceil(len(ordered) * fraction)))
    return sorted(ordered[:count])


def assign_splits(tasks: Sequence[BenchmarkTask], *, fit_fraction: float = 0.6, seed: str = "kel-v1") -> None:
    """Stratified by primary domain; within a domain order by hash(seed, id): the first
    round(fit_fraction * n) are 'fit', the rest 'heldout'. Same inputs, same split."""
    by_domain: dict[str, list[BenchmarkTask]] = defaultdict(list)
    for task in tasks:
        if task.excluded_reason is None:
            by_domain[task.domains[0]].append(task)
    for members in by_domain.values():
        members.sort(key=lambda t: stable_hash(seed, t.source, t.external_id))
        cut = round(fit_fraction * len(members))
        for i, task in enumerate(members):
            task.split = "fit" if i < cut else "heldout"
