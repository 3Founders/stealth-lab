"""Hugging Face datasets, read at a pinned commit.

A source is identified by (repo, full 40-character revision, file). A branch name or a short hash is refused:
the revision is what makes "re-check exactly what was ingested" possible (plan, provenance rule), and a moving
reference would silently change the data under a resumed run.

The file is downloaded once into the Hugging Face cache and read with pyarrow, one record batch at a time, only
the columns asked for. No `datasets` dependency.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional, Sequence

_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


class SourceUnavailable(RuntimeError):
    """The pinned file could not be fetched or opened."""


@dataclass(frozen=True)
class PinnedFile:
    repo: str          # e.g. "nebius/SWE-rebench-openhands-trajectories"
    revision: str      # full commit sha
    filename: str      # e.g. "trajectories.parquet"

    def __post_init__(self) -> None:
        if not _FULL_SHA.match(self.revision):
            raise ValueError(f"{self.repo}: revision must be a full 40-character commit sha, got {self.revision!r}")

    @property
    def source_id(self) -> str:
        return f"hf:{self.repo}"

    def uri(self, row_ref: str) -> str:
        return f"hf://datasets/{self.repo}@{self.revision}/{self.filename}#{row_ref}"

    def local_path(self) -> Path:
        """Download (or reuse the cached copy of) the pinned file. Blocking: call through run_blocking."""
        import os

        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        from huggingface_hub import hf_hub_download

        try:
            return Path(hf_hub_download(self.repo, self.filename, repo_type="dataset", revision=self.revision))
        except Exception as exc:  # noqa: BLE001 -- network, auth or a vanished revision: the run cannot start
            raise SourceUnavailable(f"{self.repo}@{self.revision[:12]}/{self.filename}: {exc}") from exc


def iter_rows(path: Path, columns: Sequence[str], *, batch_size: int = 256,
              row_groups: Optional[Sequence[int]] = None) -> Iterator[dict[str, Any]]:
    """Rows as dicts, `columns` only, in file order. Blocking."""
    import pyarrow.parquet as pq

    handle = pq.ParquetFile(str(path))
    missing = [c for c in columns if c not in handle.schema_arrow.names]
    if missing:
        raise SourceUnavailable(f"{path.name}: expected columns missing from the pinned file: {missing}")
    for batch in handle.iter_batches(batch_size=batch_size, columns=list(columns), row_groups=row_groups):
        yield from batch.to_pylist()


def read_columns(path: Path, columns: Sequence[str]) -> list[dict[str, Any]]:
    """Small columns for the whole file at once (selection passes). Blocking."""
    import pyarrow.parquet as pq

    handle = pq.ParquetFile(str(path))
    missing = [c for c in columns if c not in handle.schema_arrow.names]
    if missing:
        raise SourceUnavailable(f"{path.name}: expected columns missing from the pinned file: {missing}")
    return handle.read(columns=list(columns)).to_pylist()
