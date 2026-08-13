"""
Real SWE-bench Pro problem-statement text through the backend's untrusted-
input scanner -- this exact input shape (real GitHub bug-report prose, not
hand-picked synthetic examples) has never been run through untrusted.py
before.

Skips (does not fail) when the SWE-bench Pro dataset parquet isn't present
in the local HF cache -- unlike the rest of this suite, reading it needs
the dataset already downloaded to this machine, which a fresh checkout
cannot guarantee. Everything else in backend/tests/ stays DB/network/LLM-
free; this one file trades that guarantee for real corpus text, and says
so explicitly via skip rather than failing when the data isn't there.
"""
from __future__ import annotations

import glob
import os
import sys

import pytest

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "..", "..", "experiments", "swebench_pro")
)

from app.services.untrusted import scan_for_injection  # noqa: E402

_PARQUET_GLOB = os.path.expanduser(
    "~/.cache/huggingface/hub/datasets--ScaleAI--SWE-bench_Pro/snapshots/*/data/*.parquet")


def _dataset_available() -> bool:
    return bool(glob.glob(_PARQUET_GLOB))


pytestmark = pytest.mark.skipif(
    not _dataset_available(),
    reason="SWE-bench Pro parquet not present in the local HF cache")


def _load_problem_statements(limit: int = 200) -> list[str]:
    from graph_ingest import load_dataset, normalize_statement
    df = load_dataset()
    if limit:
        df = df.head(limit)
    return [normalize_statement(t) for t in df["problem_statement"]]


class TestRealIssueTextThroughTheScanner:
    def test_scanner_runs_without_crashing_on_every_row(self):
        texts = _load_problem_statements()
        assert texts, "expected at least one problem statement"
        for text in texts:
            flags = scan_for_injection(text)
            assert isinstance(flags, list)

    def test_false_positive_rate_is_bounded(self):
        """
        Real bug-report prose legitimately uses words like 'ignore',
        'override', 'admin' -- SWE-bench Pro issues are GitHub bug reports,
        not injection attempts, so a high flag rate here would mean a
        pattern is too broad for real usage, not that this corpus is
        adversarial. Threshold is loose (10%) since this is a regression
        guard against a pattern becoming pathologically broad, not a
        precision benchmark.
        """
        texts = _load_problem_statements()
        flagged = sum(1 for t in texts if scan_for_injection(t))
        rate = flagged / len(texts)
        assert rate < 0.10, (
            f"{flagged}/{len(texts)} ({rate:.1%}) real SWE-bench Pro issues "
            f"flagged as suspicious -- investigate which pattern is over-firing")
