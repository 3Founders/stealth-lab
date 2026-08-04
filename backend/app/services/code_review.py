"""
Code-sourced agent review (AGENT_STORE_PLAN.md, Section 3b, stage 5).

Two things happen here, and it's worth being precise that neither one,
alone or together, is equivalent to actual sandboxed execution testing.
That's Section 3b's whole point, restated in code: this review can move
an agent to `approved`, but `runnable` stays False regardless of outcome
until stage 6 (a real execution mechanism for code-sourced content)
exists. There is currently nowhere safe to run this content at all --
`runnable=True` here would be a guarantee this system cannot back up.

1. Independent multi-reviewer critique. Two reviewers from genuinely
   different model families (the same heterogeneity principle as the
   debate panel: a shared blind spot is worse than no review) each judge
   whether the agent's behavior matches its stated purpose and whether it
   requests capabilities beyond evident need. This is real, useful
   signal. It is not equivalent to static analysis or sandboxed testing,
   an LLM asked "does this look right" can be wrong the same way a human
   skimming code can be wrong.

2. Automated static scanning (bandit), only when source_detail.code is
   actually present -- true for external_marketplace when real source
   was ingested, essentially never true for user_submitted, which is
   deliberately scoped to a structured request, not raw code (Section 4).
   Bandit catches a real, specific class of issue (hardcoded credentials,
   shell injection, unsafe deserialization, and similar). It does not
   catch logic bugs, and it does not prove the absence of a vulnerability
   it wasn't built to look for.
"""
from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from typing import Optional
from uuid import UUID

import asyncpg

from app.debate.panel import PanelAgent, _extract_json
from app.services.agent_review_state_machine import AgentReviewStateMachine

log = logging.getLogger(__name__)

CODE_REVIEW_SYSTEM = """\
You are independently reviewing a submitted agent before it is listed \
in a public agent store. You are one of at least two reviewers judging \
this separately -- do not assume anything has already been checked.

Given the agent's name, description, and source detail, assess:
- Does what it actually does (per source_detail, if code is present) \
match what it claims to do (per name/description)? A mismatch here is \
the single most important thing to catch.
- Does it request or use capabilities (network access, file system \
access, credentials, subprocess execution) beyond what its stated \
purpose evidently needs?
- For a user-submitted request with no code (just a description of \
desired input/output), is the request itself well-specified and \
plausible, or vague in a way that could hide intent?

Respond with a single JSON object and nothing else:

{
  "sound": true | false,
  "matches_stated_purpose": true | false,
  "concerns": ["<specific concern>", ...],
  "notes": "<one or two sentences>"
}
"""


class WrongReviewPath(Exception):
    pass


def _run_bandit(code: str) -> tuple[list[dict], int]:
    """
    Runs real bandit static analysis against submitted Python source.

    Written to a temp file rather than passed via stdin -- bandit's CLI
    is file-path oriented, and this also means a submission that isn't
    even valid Python fails here with a clear parse error, not a
    confusing downstream one.

    Returns (findings, high_severity_count). A bandit invocation error
    itself (not a finding, the tool failing to run at all) is treated as
    a hard failure, not silently swallowed -- an unscannable submission
    must not be treated as equivalent to a clean scan.
    """
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(code)
        path = f.name

    try:
        result = subprocess.run(
            ["python3", "-m", "bandit", "-f", "json", path],
            capture_output=True, text=True, timeout=30,
        )
        # bandit exits 1 when it finds issues -- that's expected, not a
        # tool failure. Only a genuinely unparseable report is an error.
        try:
            report = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"bandit did not return a parseable report: {result.stderr[:500]}"
            ) from exc

        findings = report.get("results", [])
        high_severity = sum(1 for f_ in findings if f_.get("issue_severity") == "HIGH")
        return findings, high_severity
    finally:
        import os
        os.unlink(path)


class CodeSourcedReviewOrchestrator:
    def __init__(self, pool: asyncpg.Pool, reviewers: list[PanelAgent], on_call=None):
        if len(reviewers) < 2:
            raise ValueError(
                "code-sourced review requires at least 2 independent reviewers, "
                f"got {len(reviewers)}"
            )
        families = {r.family for r in reviewers}
        if len(families) < len(reviewers):
            raise ValueError(
                f"reviewers must be from genuinely distinct families, got {families!r} "
                f"for {len(reviewers)} reviewers -- a shared blind spot defeats the "
                "point of independent review"
            )
        self._pool = pool
        self._reviewers = reviewers
        self._on_call = on_call
        self._machine = AgentReviewStateMachine(pool)

    async def review_code_sourced(self, agent_id: UUID) -> dict:
        row = await self._pool.fetchrow(
            "SELECT name, description, source, source_detail FROM agents WHERE id = $1",
            agent_id,
        )
        if row is None:
            raise LookupError(f"no agent {agent_id}")
        if row["source"] not in ("user_submitted", "external_marketplace"):
            raise WrongReviewPath(
                f"agent {agent_id} has source={row['source']!r}; "
                "review_code_sourced only applies to user_submitted or "
                "external_marketplace. graph_derived agents use "
                "AgentReviewOrchestrator.review_graph_derived instead."
            )

        await self._machine.transition(agent_id, "under_review", actor="code_review")

        source_detail = row["source_detail"] or {}
        code = source_detail.get("code")
        prompt = (
            f"Name: {row['name']}\nDescription: {row['description']}\n"
            f"Source detail: {json.dumps(source_detail)[:4000]}"
        )

        opinions = []
        for reviewer in self._reviewers:
            try:
                raw = await reviewer.respond(CODE_REVIEW_SYSTEM, prompt)
                if self._on_call:
                    await self._on_call(reviewer, CODE_REVIEW_SYSTEM + prompt, raw)
                payload = _extract_json(raw)
                opinions.append({
                    "family": reviewer.family,
                    "sound": bool(payload.get("sound", False)),
                    "matches_stated_purpose": bool(payload.get("matches_stated_purpose", False)),
                    "concerns": [str(c)[:300] for c in (payload.get("concerns") or [])][:10],
                    "notes": str(payload.get("notes", ""))[:500],
                })
            except Exception as exc:  # noqa: BLE001
                # A reviewer failing must count against the submission,
                # not be silently skipped -- an incomplete review passing
                # by default would be worse than no review.
                log.error("reviewer %s failed on agent %s: %s", reviewer.family, agent_id, exc)
                opinions.append({
                    "family": reviewer.family, "sound": False,
                    "matches_stated_purpose": False,
                    "concerns": [f"reviewer unavailable: {exc}"], "notes": "",
                })

        scan_findings: list[dict] = []
        scan_high_severity = 0
        scan_error: Optional[str] = None
        if code:
            try:
                scan_findings, scan_high_severity = _run_bandit(code)
            except Exception as exc:  # noqa: BLE001
                log.error("bandit scan failed for agent %s: %s", agent_id, exc)
                scan_error = str(exc)

        all_sound = all(o["sound"] and o["matches_stated_purpose"] for o in opinions)
        # A scan that failed to run is treated as a failure, not skipped --
        # code with an unscannable submission is not equivalent to code
        # that scanned clean.
        scan_ok = (scan_high_severity == 0) and (code is None or scan_error is None)
        passed = all_sound and scan_ok

        notes_parts = [o["notes"] for o in opinions if o["notes"]]
        if scan_error:
            notes_parts.append(f"scan could not complete: {scan_error}")
        elif scan_high_severity:
            notes_parts.append(f"{scan_high_severity} high-severity finding(s)")

        await self._pool.execute(
            "INSERT INTO agent_code_reviews "
            "(agent_id, reviewer_opinions, scan_findings, scan_high_severity_count, "
            "passed, notes) VALUES ($1, $2, $3, $4, $5, $6)",
            agent_id, opinions, scan_findings, scan_high_severity,
            passed, "; ".join(notes_parts)[:1000],
        )

        if passed:
            await self._machine.transition(
                agent_id, "pending_human_approval", actor="code_review",
                reason="passed independent review" + (" and static scan" if code else ""),
            )
        else:
            reasons = []
            unsound = [o["family"] for o in opinions if not o["sound"]]
            if unsound:
                reasons.append(f"reviewers flagged concerns: {', '.join(unsound)}")
            if scan_high_severity:
                reasons.append(f"{scan_high_severity} high-severity scan finding(s)")
            if scan_error:
                reasons.append(f"scan failed: {scan_error}")
            await self._machine.transition(
                agent_id, "rejected", actor="code_review",
                reason="Code-sourced review rejected: " + "; ".join(reasons),
            )

        return {
            "agent_id": agent_id, "passed": passed, "opinions": opinions,
            "scan_findings": scan_findings, "scan_high_severity_count": scan_high_severity,
        }
