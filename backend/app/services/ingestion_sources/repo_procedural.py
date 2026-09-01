r"""
Repository-procedural source adapters -- historical bootstrap beyond
SKILL.md (confirmed gap: repo bootstrap covered SKILL.md only).

WHY THIS FILE EXISTS AND WHAT IT DELIBERATELY DOES NOT DO. skill_md.py's
adapters already established the seam this package's __init__ docstring
names: `discover()` enumerates what exists, `fetch()` pulls bytes and
stamps a content hash, and everything downstream of that
(`SourceArtifact` -> `parse_skill_md` -> `compile_skill_artifact`) is
source-type-agnostic and lives in app/services/skill_ingestion.py,
UNTOUCHED by this file. Every adapter below produces the exact same
`SourceArtifact` shape (source_type / uri / content / content_hash /
repository / path / commit) skill_md.py's own adapters produce, with
`.content` being real Markdown text `parse_skill_md()` can already parse
(frontmatter + numbered/bulleted steps + prose fallback) -- there is no
new parsing entry point on the compiler side, and none is needed.

THREE ADAPTERS, THREE DIFFERENT "IS THIS REALLY A CANDIDATE" GATES:

  * AgentsMdSource (source_type="agents_md") -- gated on FILENAME ONLY,
    the same discipline skill_md.py itself uses for `SKILL.md` /
    `*.skill.md`: a file literally named `AGENTS.md` or `CLAUDE.md`
    (case-insensitive) IS an agent-instruction document by construction;
    there is no separate content heuristic to second-guess that. Its raw
    content is handed to parse_skill_md() unmodified -- exactly what
    LocalDirSkillSource.fetch() does for a real SKILL.md.

  * CIWorkflowSource (source_type="ci_workflow") -- gated on being a real
    parseable `.github/workflows/*.yml`/`*.yaml` document that has at
    least one job with at least one step. One candidate is produced PER
    JOB (a workflow file with three jobs yields three candidate
    procedures, not one). `fetch()` does NOT hand the YAML through
    verbatim (the task is explicit: NOT full YAML dumps) -- it walks the
    job's `steps:` list and synthesizes a small SKILL.md-shaped document
    (frontmatter name/description + one numbered step per CI step, each
    step summarized from `name:`/`uses:`/the first line of `run:`, never
    the full script body).

  * RunbookSource (source_type="runbook") -- the narrow heuristic the
    task calls out by name. A markdown file is a runbook candidate iff
    EITHER:
      (1) FILENAME/PATH gate -- its basename is `RUNBOOK.md`
          (case-insensitive), OR its repo-relative path has a
          `docs/runbooks/` segment (case-insensitive); these paths are
          self-declaring, same as (a) above, OR
      (2) CONTENT gate -- for any OTHER `.md` file: it must contain BOTH
          a real fenced code block (a complete ```...``` pair -- a
          command actually shown, not just mentioned) AND a numbered
          imperative step list (>=1 line matching `^\s*\d+[.)]\s+`).
          Either alone is not enough: a numbered list with no commands
          reads as an outline, not a runbook; a stray code fence with no
          numbered steps reads as a snippet in prose, not a procedure.
          BOTH together is the actual "is this really procedural" gate --
          it is exactly what rejects a plain prose README (no fences, no
          numbered steps) while accepting a real runbook (numbered steps,
          each with a command).
    Content passes through to parse_skill_md() unmodified, same as (a):
    a real runbook's numbered command steps are already in the shape
    parse_skill_md() natively understands.

None of the three gates lives in skill_ingestion.py. All three live here,
in `discover()`, so a file that doesn't pass its adapter's gate is never
even offered to the compiler -- "no candidate" is decided before
`fetch()`, not by relying on parse_skill_md() to reject it after the
fact (parse_skill_md() only rejects a genuinely EMPTY document; it would
happily turn a prose paragraph into a one-step procedure, which is
exactly the outcome the runbook content gate exists to prevent).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterator

from app.services.ingestion_sources.base import (
    SourceArtifact,
    SourceRef,
    compute_content_hash,
)

# ---------------------------------------------------------------------------
# (a) AGENTS.md / CLAUDE.md-style agent instruction files
# ---------------------------------------------------------------------------

_AGENTS_FILENAME_RE = re.compile(r"^(AGENTS|CLAUDE)\.md$", re.IGNORECASE)


class LocalDirAgentsMdSource:
    """Every `AGENTS.md` / `CLAUDE.md` under a directory tree.

    Filename-gated only (see module docstring) -- these are self-declaring
    agent-instruction documents, the same way `SKILL.md` is self-declaring
    for skill_md.py. Raw content passes through to the shared compiler
    unmodified.
    """

    source_type = "agents_md"

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    def discover(self) -> Iterator[SourceRef]:
        for p in sorted(self._root.rglob("*")):
            if not p.is_file() or not _AGENTS_FILENAME_RE.match(p.name):
                continue
            yield SourceRef(
                uri=p.resolve().as_uri(),
                repository=self._root.name or str(self._root),
                path=str(p.relative_to(self._root).as_posix()),
                commit=None,
            )

    def fetch(self, ref: SourceRef) -> SourceArtifact:
        path = self._root / ref.path
        content = path.read_text(encoding="utf-8")
        return SourceArtifact(
            source_type=self.source_type,
            uri=ref.uri,
            content=content,
            content_hash=compute_content_hash(content),
            repository=ref.repository,
            path=ref.path,
            commit=None,
        )

    def fingerprint(self, artifact: SourceArtifact) -> str:
        return artifact.content_hash


# ---------------------------------------------------------------------------
# (b) .github/workflows/*.yml -- CI job step sequences
# ---------------------------------------------------------------------------

_WORKFLOW_FILE_RE = re.compile(r"\.ya?ml$", re.IGNORECASE)
_RUN_FIRST_LINE_RE = re.compile(r"^\s*([^\n]+)")


def _load_yaml(content: str) -> Any:
    import yaml  # lazy import, same discipline app/onboarding/seed.py uses

    return yaml.safe_load(content)


def _workflow_jobs(doc: Any) -> list[tuple[str, dict]]:
    """`{job_id: job_dict}` pairs with a real, non-empty `steps:` list.
    Anything else about the document (triggers, env, permissions,
    concurrency, ...) is deliberately not inspected -- only step
    sequences become candidate procedures."""
    if not isinstance(doc, dict):
        return []
    jobs = doc.get("jobs")
    if not isinstance(jobs, dict):
        return []
    out: list[tuple[str, dict]] = []
    for job_id, job in jobs.items():
        if isinstance(job, dict) and isinstance(job.get("steps"), list) and job["steps"]:
            out.append((str(job_id), job))
    return out


def _summarize_step(step: Any, index: int) -> str:
    """One short human-readable line per CI step -- never the full `run:`
    script body (the task is explicit: candidates are extracted step
    SEQUENCES, not YAML dumps)."""
    if not isinstance(step, dict):
        return f"Step {index + 1}"
    if step.get("name"):
        return str(step["name"]).strip()
    if step.get("uses"):
        return f"Run action {step['uses']}".strip()
    run = step.get("run")
    if run:
        first_line = _RUN_FIRST_LINE_RE.match(str(run).strip())
        summary = first_line.group(1).strip() if first_line else str(run).strip()
        return f"Run: {summary[:100]}"
    return f"Step {index + 1}"


def _synthesize_job_document(*, workflow_name: str, workflow_path: str, job_id: str, job: dict) -> str:
    job_name = str(job.get("name") or job_id)
    lines = [
        "---",
        f"name: ci-{job_id}",
        f'description: CI job "{job_name}" from workflow {workflow_path}',
        "---",
        "",
    ]
    for i, step in enumerate(job["steps"]):
        lines.append(f"{i + 1}. {_summarize_step(step, i)}")
    return "\n".join(lines) + "\n"


class LocalDirCIWorkflowSource:
    """Every job with a real step list in every `.github/workflows/*.yml`
    (or `.yaml`) under a directory tree. One candidate PER JOB."""

    source_type = "ci_workflow"

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    def _workflow_files(self) -> Iterator[Path]:
        workflows_dir = self._root / ".github" / "workflows"
        if not workflows_dir.is_dir():
            return
        for p in sorted(workflows_dir.rglob("*")):
            if p.is_file() and _WORKFLOW_FILE_RE.search(p.name):
                yield p

    def discover(self) -> Iterator[SourceRef]:
        for p in self._workflow_files():
            try:
                doc = _load_yaml(p.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001 -- an unparseable workflow file
                # yields no candidates rather than aborting the whole walk.
                continue
            rel = str(p.relative_to(self._root).as_posix())
            for job_id, _job in _workflow_jobs(doc):
                yield SourceRef(
                    uri=p.resolve().as_uri() + f"#{job_id}",
                    repository=self._root.name or str(self._root),
                    path=f"{rel}#{job_id}",
                    commit=None,
                )

    def fetch(self, ref: SourceRef) -> SourceArtifact:
        file_path, _, job_id = ref.path.rpartition("#")
        abs_path = self._root / file_path
        doc = _load_yaml(abs_path.read_text(encoding="utf-8"))
        jobs = dict(_workflow_jobs(doc))
        job = jobs[job_id]
        workflow_name = str(doc.get("name") or file_path) if isinstance(doc, dict) else file_path
        content = _synthesize_job_document(
            workflow_name=workflow_name, workflow_path=file_path, job_id=job_id, job=job,
        )
        return SourceArtifact(
            source_type=self.source_type,
            uri=ref.uri,
            content=content,
            content_hash=compute_content_hash(content),
            repository=ref.repository,
            path=ref.path,
            commit=None,
        )

    def fingerprint(self, artifact: SourceArtifact) -> str:
        return artifact.content_hash


# ---------------------------------------------------------------------------
# (c) runbook-shaped docs
# ---------------------------------------------------------------------------

_MD_FILE_RE = re.compile(r"\.md$", re.IGNORECASE)
_RUNBOOK_NUMBERED_STEP_RE = re.compile(r"^\s*\d+[.)]\s+\S", re.MULTILINE)
_FENCED_CODE_BLOCK_RE = re.compile(r"```[^\n]*\n.*?```", re.DOTALL)


def _is_runbook_named_path(rel_posix_path: str) -> bool:
    lower = rel_posix_path.lower()
    basename = lower.rsplit("/", 1)[-1]
    if basename == "runbook.md":
        return True
    return "docs/runbooks/" in lower


def _passes_runbook_content_gate(content: str) -> bool:
    """The narrow "is this really procedural" heuristic (module
    docstring): a numbered step list AND a real fenced code block, both
    present. Neither alone is enough -- see the docstring for why."""
    return bool(_RUNBOOK_NUMBERED_STEP_RE.search(content)) and bool(
        _FENCED_CODE_BLOCK_RE.search(content)
    )


class LocalDirRunbookSource:
    """Runbook-shaped markdown under a directory tree: self-declaring by
    path (`RUNBOOK.md`, anything under `docs/runbooks/`), or -- for any
    other `.md` file -- gated on actually containing a numbered step list
    with real command blocks (see module docstring). A plain prose doc
    (a README with no commands and no numbered steps) yields nothing."""

    source_type = "runbook"

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    def discover(self) -> Iterator[SourceRef]:
        for p in sorted(self._root.rglob("*")):
            if not p.is_file() or not _MD_FILE_RE.search(p.name):
                continue
            rel = str(p.relative_to(self._root).as_posix())
            if not _is_runbook_named_path(rel):
                try:
                    content = p.read_text(encoding="utf-8")
                except Exception:  # noqa: BLE001 -- unreadable file, no candidate
                    continue
                if not _passes_runbook_content_gate(content):
                    continue
            yield SourceRef(
                uri=p.resolve().as_uri(),
                repository=self._root.name or str(self._root),
                path=rel,
                commit=None,
            )

    def fetch(self, ref: SourceRef) -> SourceArtifact:
        path = self._root / ref.path
        content = path.read_text(encoding="utf-8")
        return SourceArtifact(
            source_type=self.source_type,
            uri=ref.uri,
            content=content,
            content_hash=compute_content_hash(content),
            repository=ref.repository,
            path=ref.path,
            commit=None,
        )

    def fingerprint(self, artifact: SourceArtifact) -> str:
        return artifact.content_hash


# Dispatch table entries for this file's three adapters, same convention
# skill_md.py's own SOURCE_ADAPTERS uses (keys name a source TYPE + how it
# is reached). Kept separate from skill_md.SOURCE_ADAPTERS rather than
# merged into it -- merging would mean editing skill_md.py, which this
# work item leaves untouched; a caller that wants one combined dispatch
# table composes `{**skill_md.SOURCE_ADAPTERS, **repo_procedural.SOURCE_ADAPTERS}`
# at the call site.
SOURCE_ADAPTERS: dict[str, type] = {
    "agents_md_dir": LocalDirAgentsMdSource,
    "ci_workflow_dir": LocalDirCIWorkflowSource,
    "runbook_dir": LocalDirRunbookSource,
}
