"""
Tests for ArtifactRef (ticket 10's typed union: GitSha | BlobUri | DbId).
Pure Pydantic validation -- no database, no network.
"""
from app.services.state import ArtifactRef, GitSha, BlobUri, DbId
from pydantic import TypeAdapter


def test_git_sha_validates_and_discriminates():
    ref = GitSha(sha="abc123def456", repo="stealth-lab")
    assert ref.kind == "git_sha"
    assert ref.sha == "abc123def456"


def test_blob_uri_validates_and_discriminates():
    ref = BlobUri(uri="s3://bucket/key", content_hash="sha256:xyz")
    assert ref.kind == "blob_uri"


def test_db_id_validates_and_discriminates():
    ref = DbId(table="trace_events", id="some-uuid")
    assert ref.kind == "db_id"
    assert ref.table == "trace_events"


def test_union_type_adapter_round_trips_each_variant():
    """Real check that the union itself works, not just each member type
    in isolation -- confirms a caller can validate an arbitrary dict
    against ArtifactRef and get back the right concrete type."""
    adapter = TypeAdapter(ArtifactRef)

    git_ref = adapter.validate_python({"kind": "git_sha", "sha": "abc123"})
    assert isinstance(git_ref, GitSha)

    blob_ref = adapter.validate_python({"kind": "blob_uri", "uri": "s3://x/y"})
    assert isinstance(blob_ref, BlobUri)

    db_ref = adapter.validate_python({"kind": "db_id", "table": "claims", "id": "1"})
    assert isinstance(db_ref, DbId)


def test_missing_required_field_for_the_kind_is_rejected():
    """Real check, verified against the actual error before writing this
    comment: this fails because GitSha's `sha` is a required field with
    no default, not because the extra `uri` key is rejected -- Pydantic
    ignores unknown keys by default, it doesn't forbid them. The real
    guarantee this union provides is that each kind's own required shape
    is enforced, not that stray fields are caught."""
    adapter = TypeAdapter(ArtifactRef)
    import pytest
    with pytest.raises(Exception):
        adapter.validate_python({"kind": "git_sha", "uri": "s3://wrong/field/for/this/kind"})
