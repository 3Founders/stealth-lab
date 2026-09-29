"""Offline: the rebuilt SkillMD pipeline -- exact-commit provenance, outcome mapping, near duplicates, credit."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.ingest.common import neardup
from app.ingest.common.github import RepoGone, git_blob_id
from app.ingest.common.ledger import FAILED, REJECTED, WRITTEN
from app.ingest.skills import pipeline as sk

SHA_HEAD = "a" * 40
SHA_OLD = "b" * 40


def _row(path="skills/x/SKILL.md", ref="main", repo="o/r"):
    return sk.Row(0, "h" * 64, repo, path, f"https://github.com/{repo}/blob/{ref}/{path}", 5, "registry")


class FakeGH:
    def __init__(self, head_blobs=None, at=None, history=None, gone=False):
        self.head_blobs, self.at, self.history, self.gone = head_blobs or {}, at or {}, history, gone
        self.calls = []

    async def files_at(self, owner, name, ref, paths):
        self.calls.append(("files_at", ref))
        if self.gone:
            raise RepoGone("gone")
        if ref == "HEAD":
            return SHA_HEAD, {p: self.head_blobs.get(p) for p in paths}
        return ref, {p: self.at.get((ref, p)) for p in paths}

    async def commit_with_blob(self, owner, name, path, blob):
        self.calls.append(("history", path))
        if self.gone:
            raise RepoGone("gone")
        return self.history

    async def license_at(self, owner, name, commit):
        return "MIT"


def _prov(gh, row, text):
    async def go():
        res = await sk.resolve_repo(gh, row.repo, [row])
        return await sk.provenance(gh, res, row, git_blob_id(text))
    return asyncio.run(go())


def test_blob_id_is_gits_own():
    assert git_blob_id("") == "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"      # git hash-object /dev/null


def test_text_at_the_head_is_pinned_to_the_head_commit():
    row = _row()
    gh = FakeGH(head_blobs={row.path: git_blob_id("skill text")})
    assert _prov(gh, row, "skill text") == (SHA_HEAD, "default_branch_head")
    assert ("history", row.path) not in gh.calls


def test_a_commit_in_the_url_is_checked_exactly():
    row = _row(ref=SHA_OLD)
    gh = FakeGH(head_blobs={row.path: "changed"}, at={(SHA_OLD, row.path): git_blob_id("old text")})
    assert _prov(gh, row, "old text") == (SHA_OLD, "url_commit")


def test_changed_files_are_found_in_their_history_or_rejected():
    row = _row()
    assert _prov(FakeGH(head_blobs={row.path: "changed"}, history=SHA_OLD), row, "t") == (SHA_OLD, "path_history")
    assert _prov(FakeGH(head_blobs={row.path: "changed"}, history=None), row, "t") == (None, "content_not_at_any_commit")
    assert _prov(FakeGH(gone=True), row, "t") == (None, "source_gone")


def test_url_ref_parsing():
    assert _row(ref="main").ref() == "main"
    assert _row(ref=SHA_OLD).ref() == SHA_OLD


def _outcome(**kw):
    base = dict(status="rejected", reason="", quarantined=False, injection_screened=False, admission_decision="admit")
    return SimpleNamespace(**{**base, **kw})


@pytest.mark.parametrize("kw,expected", [
    (dict(status="captured"), (WRITTEN, "written")),
    (dict(status="captured", quarantined=True, admission_decision="review"), (WRITTEN, "quarantined_for_review")),
    (dict(status="unchanged"), (WRITTEN, "recovered_from_earlier_attempt")),
    (dict(reason="extraction failed: gemma: timeout"), (FAILED, "extraction_failed")),
    (dict(injection_screened=True, reason="flagged"), (REJECTED, "screened_prompt_injection")),
    (dict(admission_decision="reject", reason="spam"), (REJECTED, "admission_rejected")),
    (dict(reason="extraction abstained -- nothing extractable in this document"), (REJECTED, "nothing_extractable")),
    (dict(reason="extraction produced zero procedures"), (REJECTED, "nothing_extractable")),
])
def test_compiler_outcomes_map_to_ledger_outcomes(kw, expected):
    assert sk.classify_outcome(_outcome(**kw)) == expected


def test_a_model_outage_is_never_a_permanent_rejection():
    status, _ = sk.classify_outcome(_outcome(reason="extraction failed: gemma-4-31B-it: HTTP 503"))
    assert status == FAILED


def test_credit_names_file_commit_license_and_dataset():
    c = sk.attribution(_row(), SHA_HEAD, "MIT", "code-review")
    assert c["license"] == "MIT" and c["commit"] == SHA_HEAD
    assert SHA_HEAD in c["source_uri"] and "SkillMD-138K" in c["notice"] and "o/r" in c["notice"]


def test_near_duplicates_are_close_and_different_texts_are_not():
    base = " ".join(f"step {i} run the linter then fix every warning in module {i % 7}" for i in range(60))
    near = base.replace("step 3 ", "Step three ", 1)
    other = " ".join(f"deploy service {i} with helm and wait for rollout {i % 5}" for i in range(60))
    a, b, c = neardup.signature(base), neardup.signature(near), neardup.signature(other)
    assert neardup.similarity(a, b) >= neardup.THRESHOLD
    assert neardup.similarity(a, c) < 0.2
    assert set(neardup.band_keys(a)) & set(neardup.band_keys(b))
