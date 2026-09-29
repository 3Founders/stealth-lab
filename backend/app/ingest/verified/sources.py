"""The four verified-solution datasets: pinned revisions and how each one's columns map onto a task.

Verified on the pinned revisions (2026-09-29):
  SWE-rebench      27,878 rows (test split 21,336 used)  license_name (GitHub display name)   docker_image
  SWE-rebench-V2   32,079 rows, 8+ languages             license (SPDX, or "custom-check-github") image_name
  SWE-bench-extra   6,376 rows                            license (lowercase SPDX)              no image
  SWE-Gym           2,438 rows, 11 repositories           no license column                     no image
A task whose license cannot be read from the row is decided by GitHub's license detection at its base commit.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from app.ingest.common.hf import PinnedFile


@dataclass(frozen=True)
class Source:
    key: str
    files: tuple[PinnedFile, ...]
    license_col: Optional[str]
    license_kind: str               # "github_name" | "spdx" | "none"
    image_col: Optional[str]
    language_col: Optional[str]
    hints_col: Optional[str]

    @property
    def source_id(self) -> str:
        f = self.files[0]
        return f"hf:{f.repo}@{f.revision}"


SOURCES: dict[str, Source] = {
    "swe-rebench": Source(
        "swe-rebench",
        tuple(PinnedFile("nebius/SWE-rebench", "89cdfbab4ab1bd8f5a658bb212d1b63624f4f881", f)
              for f in ("data/test-00000-of-00002.parquet", "data/test-00001-of-00002.parquet")),
        "license_name", "github_name", "docker_image", None, "hints_text"),
    "swe-rebench-v2": Source(
        "swe-rebench-v2",
        (PinnedFile("nebius/SWE-rebench-V2", "475dd5e8703bb5fb22dd3c60b5d038b019eba1e0",
                    "data/train-00000-of-00001.parquet"),),
        "license", "spdx", "image_name", "language", None),
    "swe-bench-extra": Source(
        "swe-bench-extra",
        (PinnedFile("nebius/SWE-bench-extra", "11dcbfb30e19552df2a2f8030bd764adc95c92a5",
                    "data/train-00000-of-00001.parquet"),),
        "license", "spdx", None, None, "hints_text"),
    "swe-gym": Source(
        "swe-gym",
        (PinnedFile("SWE-Gym/SWE-Gym", "bb94ed9e39bbeb96a7fcbfb533b80f25a7fd59cb",
                    "data/train-00000-of-00001.parquet"),),
        None, "none", None, None, "hints_text"),
}
ORDER = ("swe-rebench", "swe-rebench-v2", "swe-bench-extra", "swe-gym")   # a shared task is written by the first

# V2 marks rows whose license must be looked up on GitHub with this value
GITHUB_LOOKUP = {"custom-check-github", ""}


@dataclass(frozen=True)
class Task:
    source: str
    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    hints: str
    patch: str
    test_patch: str
    fail_to_pass: tuple[str, ...]
    pass_to_pass: tuple[str, ...]
    fail_to_fail: tuple[str, ...]
    pass_to_fail: tuple[str, ...]
    license_raw: Optional[str]
    docker_image: Optional[str]
    test_cmd: Optional[str]
    language: Optional[str]
    quality: dict

    @property
    def item_key(self) -> str:
        return f"{self.source}:{self.instance_id}"

    @property
    def dedup_key(self) -> str:
        return f"swe-solution:{self.instance_id}"


def _list(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        import json

        try:
            value = json.loads(value)
        except ValueError:
            return (value,)
    return tuple(str(v) for v in value)


def to_task(src: Source, row: dict) -> Task:
    install = row.get("install_config") or {}
    meta = row.get("meta") or {}
    quality = {k: meta.get(k) for k in ("llm_score", "llm_metadata", "failed_lite_validators", "is_lite")
               if meta.get(k) is not None}
    return Task(
        source=src.key, instance_id=str(row.get("instance_id") or ""), repo=str(row.get("repo") or ""),
        base_commit=str(row.get("base_commit") or ""), problem_statement=str(row.get("problem_statement") or ""),
        hints=str(row.get(src.hints_col) or "") if src.hints_col else "", patch=str(row.get("patch") or ""),
        test_patch=str(row.get("test_patch") or ""), fail_to_pass=_list(row.get("FAIL_TO_PASS")),
        pass_to_pass=_list(row.get("PASS_TO_PASS")), fail_to_fail=_list(row.get("FAIL_TO_FAIL")),
        pass_to_fail=_list(row.get("PASS_TO_FAIL")),
        license_raw=(str(row[src.license_col]) if src.license_col and row.get(src.license_col) is not None else None),
        docker_image=(str(row.get(src.image_col)) if src.image_col and row.get(src.image_col) else None),
        test_cmd=(install.get("test_cmd") if isinstance(install, dict) else None) or None,
        language=(str(row.get(src.language_col)) if src.language_col and row.get(src.language_col) else "python"
                  if src.key != "swe-rebench-v2" else None),
        quality=quality,
    )


COLUMNS = ("instance_id", "repo", "base_commit", "problem_statement", "patch", "test_patch", "FAIL_TO_PASS",
           "PASS_TO_PASS", "FAIL_TO_FAIL", "PASS_TO_FAIL", "install_config", "meta", "hints_text", "license_name",
           "license", "docker_image", "image_name", "language")


def columns_for(path) -> list[str]:
    import pyarrow.parquet as pq

    names = set(pq.ParquetFile(str(path)).schema_arrow.names)
    return [c for c in COLUMNS if c in names]
