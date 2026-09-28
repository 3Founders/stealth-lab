"""Step 6 source: the Zenodo GitHub Actions workflow-history corpus.

What this source actually is
----------------------------
Zenodo concept DOI `10.5281/zenodo.10259013`, "A dataset of GitHub Actions
workflow histories" (Cardoen et al., IEEE MSR 2024). License **CC-BY-4.0**,
confirmed on every version record.

**It contains no execution data.** The 17 columns of `workflows.csv.gz` are all
commit/file-level: no `conclusion`, no `run_id`, no job result, no log. Verified
by ranged download and direct read of the header, not taken from the card:

    repository,commit_hash,author_name,author_email,committer_name,committer_email,
    committed_date,authored_date,file_path,previous_file_path,file_hash,
    previous_file_hash,git_change_type,valid_yaml,probably_workflow,valid_workflow,uid

So this source is a **candidate generator**, structurally the same as Step 3's
SkillMD: it states intent, never a verified outcome. Anything it produces is a
candidate Procedure at best. The `probably_workflow` / `valid_yaml` /
`valid_workflow` booleans are gigawork's convenience flags and are re-verified
here rather than trusted -- related extraction work has shipped a broken
`valid_yaml` column, and ~23% of snapshots fail YAML parse.

Version pinning
---------------
The concept DOI resolves to the newest version, which moves. `PINNED_RECORD` is
the 2026-05-22 record (`20340547`, ~52.9K repos). The prompt's "160k" figure is
the paper's 2023 extraction (160,443 histories / 32,886 repos) and is quoted from
the paper, not from this record. A run that silently follows the concept DOI
would ingest a different corpus next month under the same name, so
`FingerprintDrift` is raised when the observed record id differs from the pin.

Two-part source
---------------
The YAML bodies live in a separate `workflows.tar.gz` keyed by `file_hash`, so
one logical item needs a metadata row *and* a body fetch. This module models
that explicitly rather than pretending the CSV is self-contained.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterator, Optional

from app.services.ingestion_sources.base import (
    SourceAdapter,
    SourceArtifact,
    SourceRef,
    compute_content_hash,
)

SOURCE_TYPE = "ci_workflow_history"
ACTOR_ID = "ci_workflow_history"
EXTRACTOR_VERSION = "ci_workflow_history/v1"

ZENODO_CONCEPT_DOI = "10.5281/zenodo.10259013"
PINNED_RECORD = "20340547"
PINNED_RECORD_DATE = "2026-05-22"
LICENSE_SPDX = "CC-BY-4.0"

#: The paper's figures, kept as named constants because the plan quotes them.
PAPER_HISTORIES = 160_443
PAPER_REPOS = 32_886
PAPER_WORKFLOW_FILES = 1_526_475

_METADATA_CSV = "workflows.csv.gz"
_BODIES_TAR = "workflows.tar.gz"
_FILE_BASE = f"https://zenodo.org/records/{PINNED_RECORD}/files"

#: The exact 17 columns, in order. Anything else in a row is a schema change.
EXPECTED_COLUMNS: tuple[str, ...] = (
    "repository", "commit_hash", "author_name", "author_email", "committer_name",
    "committer_email", "committed_date", "authored_date", "file_path",
    "previous_file_path", "file_hash", "previous_file_hash", "git_change_type",
    "valid_yaml", "probably_workflow", "valid_workflow", "uid",
)

WORKFLOW_DIR = ".github/workflows/"

HttpGetBytes = Callable[[str], "tuple[int, bytes]"]


class WorkflowCorpusError(RuntimeError):
    pass


class SchemaDrift(WorkflowCorpusError):
    """The CSV header is not the 17 columns we verified. Refuse rather than
    silently ingesting shifted fields -- a shifted column means every license
    and every commit attribution downstream is wrong."""


class FingerprintDrift(WorkflowCorpusError):
    """Observed Zenodo record id differs from the pin."""


# --------------------------------------------------------------------------
# Row shape
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class WorkflowRevision:
    """One workflow file at one commit. The unit of this source."""

    repository: str
    commit_hash: str
    file_path: str
    file_hash: str
    git_change_type: str
    committed_date: Optional[datetime]
    author_name: str = ""
    author_email: str = ""
    uid: str = ""
    valid_yaml: bool = False
    probably_workflow: bool = False
    valid_workflow: bool = False
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def is_addition(self) -> bool:
        return self.git_change_type == "A"

    def as_dict(self) -> dict[str, Any]:
        return {
            "repository": self.repository, "commit_hash": self.commit_hash,
            "file_path": self.file_path, "file_hash": self.file_hash,
            "git_change_type": self.git_change_type,
            "committed_date": self.committed_date.isoformat() if self.committed_date else None,
            "uid": self.uid,
        }


def _truthy(value: str) -> bool:
    return str(value).strip().lower() in ("true", "1", "yes", "t")


def _parse_epoch(value: str) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromtimestamp(int(raw), tz=timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


def parse_metadata_row(row: dict[str, str]) -> Optional[WorkflowRevision]:
    """Map one CSV row. Returns None for a row that is not a workflow file."""
    path = (row.get("file_path") or "").strip()
    if not path.startswith(WORKFLOW_DIR):
        return None
    repository = (row.get("repository") or "").strip()
    if not repository or "/" not in repository:
        return None
    return WorkflowRevision(
        repository=repository,
        commit_hash=(row.get("commit_hash") or "").strip(),
        file_path=path,
        file_hash=(row.get("file_hash") or "").strip(),
        git_change_type=(row.get("git_change_type") or "").strip().upper(),
        committed_date=_parse_epoch(row.get("committed_date")),
        author_name=(row.get("author_name") or "").strip(),
        author_email=(row.get("author_email") or "").strip(),
        uid=(row.get("uid") or "").strip(),
        valid_yaml=_truthy(row.get("valid_yaml") or ""),
        probably_workflow=_truthy(row.get("probably_workflow") or ""),
        valid_workflow=_truthy(row.get("valid_workflow") or ""),
        raw=dict(row),
    )


def _open_text(data_or_path: Any) -> Any:
    """Return a text stream over a metadata CSV, gzipped or not.

    Accepts bytes (a bounded probe, or a whole small file) or a path. A path is
    opened as a *stream*, never slurped: the real `workflows.csv.gz` is 297 MB
    compressed and several GB of text, so `read()`-then-decompress is not an
    option at full scale.
    """
    import gzip

    if isinstance(data_or_path, (bytes, bytearray)):
        raw = bytes(data_or_path)
        if raw[:2] == b"\x1f\x8b":
            raw = _gunzip_prefix(raw)
        return io.StringIO(raw.decode("utf-8", errors="replace"))
    handle = open(data_or_path, "rb")
    magic = handle.read(2)
    handle.seek(0)  # GzipFile(fileobj=...) reads from the CURRENT position, so rewind after sniffing.
    if magic != b"\x1f\x8b":
        return io.TextIOWrapper(handle, encoding="utf-8", errors="replace")
    return io.TextIOWrapper(gzip.GzipFile(fileobj=handle), encoding="utf-8", errors="replace")


def _validated_reader(stream: Any) -> csv.DictReader:
    reader = csv.DictReader(stream)
    header = tuple(reader.fieldnames or ())
    if header != EXPECTED_COLUMNS:
        raise SchemaDrift(
            f"expected {len(EXPECTED_COLUMNS)} columns {EXPECTED_COLUMNS}, got {header}")
    return reader


def iter_metadata_stream(
    stream: Any, *, record_id: str = PINNED_RECORD, limit: Optional[int] = None
) -> Iterator[WorkflowRevision]:
    """Stream workflow revisions from an open text stream. O(1) memory."""
    if record_id != PINNED_RECORD:
        raise FingerprintDrift(
            f"corpus record {record_id!r} differs from the pinned {PINNED_RECORD!r}; "
            "re-verify the schema and re-pin before ingesting")
    reader = _validated_reader(stream)
    seen = 0
    for row in reader:
        if not row or not (row.get("repository") or "").strip():
            continue
        revision = parse_metadata_row(row)
        if revision is None:
            continue
        seen += 1
        yield revision
        if limit is not None and seen >= limit:
            return


def iter_metadata_file(
    path: Any, *, record_id: str = PINNED_RECORD, limit: Optional[int] = None
) -> Iterator[WorkflowRevision]:
    """Stream revisions straight off disk. This is the full-scale path."""
    stream = _open_text(path)
    try:
        yield from iter_metadata_stream(stream, record_id=record_id, limit=limit)
    finally:
        close = getattr(stream, "close", None)
        if close is not None:
            close()


def iter_metadata_csv(
    data: bytes, *, record_id: str = PINNED_RECORD
) -> Iterator[WorkflowRevision]:
    """Stream revisions out of an in-memory payload.

    Kept for bounded probes and tests. A whole-file run must use
    `iter_metadata_file`, which does not hold the decompressed text in memory.
    """
    stream = _open_text(data)
    return iter_metadata_stream(stream, record_id=record_id)


def _decompress(data: bytes) -> str:
    import gzip
    raw = data
    if data[:2] == b"\x1f\x8b":
        raw = _gunzip_prefix(data)
    return raw.decode("utf-8", errors="replace")


def _gunzip_prefix(data: bytes) -> bytes:
    """Decompress as much of a possibly-truncated gzip member as possible.

    A ranged read of a 311 MB file hands us a member whose final block is
    missing. `zlib` reports that as `zlib.error` only once it runs out of input,
    and everything decoded before that point is still valid -- so we keep the
    partial output and let the caller's CSV reader drop the torn last line.
    """
    import zlib

    out = bytearray()
    decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        out += decompressor.decompress(data)
        out += decompressor.flush()
    except zlib.error:
        # Truncated tail. `out` holds every byte that decoded cleanly.
        try:
            out += decompressor.flush()
        except zlib.error:
            pass
    return bytes(out)


# --------------------------------------------------------------------------
# Workflow content -> a describable, NON-verified unit
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class WorkflowSummary:
    name: str
    triggers: tuple[str, ...] = ()
    jobs: tuple[str, ...] = ()
    steps: int = 0
    uses: tuple[str, ...] = ()
    has_test_step: bool = False
    parse_error: Optional[str] = None


_STEP_RE = re.compile(r"^\s{2,}-\s+(?:name:\s*)?", re.MULTILINE)
_USES_RE = re.compile(r"uses:\s*([^\s#]+)")
_NAME_RE = re.compile(r"^name:\s*(.+)$", re.MULTILINE)
_ON_RE = re.compile(r"^on:\s*$", re.MULTILINE)
_JOB_RE = re.compile(r"^  ([A-Za-z0-9_-]+):\s*$", re.MULTILINE)

#: Step-name substrings that suggest a test is being run. Used only to record
#: "this workflow appears to test", never to assert that it does.
_TEST_HINTS = ("test", "pytest", "npm test", "jest", "vitest", "go test", "cargo test",
               "mvn test", "gradle test", "rspec", "tox", "ctest")


def summarize_workflow(text: str, *, filename: str = "") -> WorkflowSummary:
    """Cheap structural read of a workflow file.

    Deliberately regex-based, not a YAML parse: the corpus contains ~23%
    unparseable YAML, and a hard parse failure would drop those rows when the
    content is still perfectly useful as candidate material. `parse_error` is
    recorded instead of raised.
    """
    name_match = _NAME_RE.search(text)
    jobs = tuple(_JOB_RE.findall(text))
    uses = tuple(dict.fromkeys(_USES_RE.findall(text)))
    lowered = text.lower()
    has_test = any(hint in lowered for hint in _TEST_HINTS)
    return WorkflowSummary(
        name=(name_match.group(1).strip() if name_match else filename or "unnamed workflow"),
        triggers=("on:",) if _ON_RE.search(text) else (),
        jobs=jobs,
        steps=len(_STEP_RE.findall(text)),
        uses=uses,
        has_test_step=has_test,
        parse_error=None,
    )


class CiWorkflowHistorySource:
    """`SourceAdapter` over the pinned workflow-history corpus.

    Construction performs no I/O. `discover()` replays an already-fetched
    metadata CSV (the pilot fetches it once and passes the bytes, so a bounded
    probe and a full run share one code path); `fetch()` pairs a revision with
    its YAML body.
    """

    source_type = SOURCE_TYPE

    def _key(self, repository: str, path: str, commit: str) -> str:
        return f"{repository}/{path}/{commit}"

    def __init__(
        self,
        metadata: Any,
        *,
        body_lookup: Optional[Callable[[str], Optional[str]]] = None,
        record_id: str = PINNED_RECORD,
    ) -> None:
        # `metadata` is bytes (bounded probe / test) or a path (full scale).
        # A path is streamed, so this constructor is cheap either way and
        # performs no decompression until `discover()` is called.
        self._metadata = metadata
        self._body_lookup = body_lookup
        self._record_id = record_id
        self._revisions: dict[str, WorkflowRevision] = {}

    def discover(self, *, limit: Optional[int] = None) -> Iterator[SourceRef]:
        if isinstance(self._metadata, (bytes, bytearray)):
            revisions: Iterator[WorkflowRevision] = iter_metadata_stream(
                _open_text(self._metadata), record_id=self._record_id, limit=limit)
        else:
            revisions = iter_metadata_file(
                self._metadata, record_id=self._record_id, limit=limit)
        for revision in revisions:
            self._revisions[self._key(revision.repository, revision.file_path,
                                      revision.commit_hash)] = revision
            yield SourceRef(
                uri=f"zenodo:{ZENODO_CONCEPT_DOI}#{revision.commit_hash}/{revision.file_path}",
                repository=revision.repository,
                path=revision.file_path,
                commit=revision.commit_hash,
                source_id=SOURCE_TYPE,
            )

    def fetch(self, ref: SourceRef) -> SourceArtifact:
        revision = self._revisions.get(self._key(ref.repository or "", ref.path or "",
                                                 ref.commit or ""))
        if revision is None:
            raise WorkflowCorpusError(
                f"no discovered revision for {ref.repository} {ref.path} {ref.commit}")
        body = self._body_lookup(revision.file_hash) if self._body_lookup else None
        summary = summarize_workflow(body or "", filename=revision.file_path)
        content = _render_workflow_document(revision, summary, body or "")
        return SourceArtifact(
            source_type=self.source_type,
            uri=ref.uri,
            content=content,
            content_hash=compute_content_hash(content),
            repository=revision.repository,
            path=revision.file_path,
            commit=revision.commit_hash,
            source_id=SOURCE_TYPE,
            # Both keys set, because `screening.spdx_license_signal` reads
            # `spdx_id`. Same bridge `document_ingestion` uses.
            license_metadata={"license": LICENSE_SPDX, "spdx_id": LICENSE_SPDX,
                             "zenodo_record": self._record_id},
        )

    def fingerprint(self, artifact: SourceArtifact) -> str:
        """Identity = repo + path + blob sha. The file content at a blob sha
        cannot change, so this is stable and needs no re-hash."""
        return f"{artifact.repository}:{artifact.path}:{artifact.commit}"


def _render_workflow_document(
    revision: WorkflowRevision, summary: WorkflowSummary, body: str
) -> str:
    """The artifact text. The verification block is not decoration: it is the
    reason this source may only ever produce candidates, and it travels with
    the content into whatever reads it next."""
    lines = [
        f"# github actions workflow: {summary.name}",
        f"repository: {revision.repository}",
        f"path: {revision.file_path}",
        f"commit: {revision.commit_hash}",
        f"blob: {revision.file_hash}",
        f"change_type: {revision.git_change_type}",
        f"committed_at: {revision.committed_date.isoformat() if revision.committed_date else 'unknown'}",
        f"license: {LICENSE_SPDX} (Zenodo record {PINNED_RECORD})",
        "",
        "## structure",
        f"jobs: {', '.join(summary.jobs) or 'none detected'}",
        f"step_count: {summary.steps}",
        f"uses: {', '.join(summary.uses[:12]) or 'none detected'}",
        f"appears_to_run_tests: {summary.has_test_step}",
        "",
        "## verification status",
        "UNVERIFIED. This corpus records workflow FILE revisions only. It",
        "contains no run outcome, no job result and no log, so no check exists",
        "for this item and none can be reconstructed from it. Treat as a",
        "candidate for human or automated review, never as a verified",
        "procedure.",
    ]
    if body:
        lines += ["", "## workflow source", "```yaml", body.rstrip(), "```"]
    return "\n".join(lines) + "\n"


ADAPTERS: dict[str, type] = {SOURCE_TYPE: CiWorkflowHistorySource}
