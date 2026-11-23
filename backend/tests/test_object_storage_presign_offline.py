"""Storage stays provider-agnostic: presigning, public URLs, and where a switching provider touches nothing.

`docs/blinkit/storage_choice.md` §5 makes the claim this file pins. Choosing R2 or B2 is a block of environment
variables, and that is only true while:

* every backend answers `presign_get` / `public_url` (a backend that cannot sign says so with None, it does not
  raise), and
* a URL is composed from configuration at read time rather than stored with the object, and
* the licence check runs before a signed capability is issued.

No network here: `S3Store` is given a stub client, because the thing under test is our URL composition, not boto3.
"""
from __future__ import annotations

import pytest

from app.services.object_storage import LocalFileStore, MemoryStore, S3Store, set_store


class StubClient:
    """Records what it was asked to sign, and returns a URL that shows which params made the signature."""

    def __init__(self):
        self.calls: list[tuple[str, dict, int]] = []
        self.fail: Exception | None = None

    def generate_presigned_url(self, op, Params=None, ExpiresIn=None):
        if self.fail:
            raise self.fail
        self.calls.append((op, dict(Params or {}), ExpiresIn))
        params = "&".join(f"{k}={v}" for k, v in sorted((Params or {}).items()))
        return f"https://store.example/{params}?expires={ExpiresIn}"


def _s3(**kw) -> tuple[S3Store, StubClient]:
    client = StubClient()
    store = S3Store("kell-artifacts", "outputs", endpoint_url="https://r2.example", client=client, **kw)
    return store, client


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("OBJECT_STORAGE_PUBLIC_BASE", raising=False)
    set_store(None)
    yield
    set_store(None)


# ------------------------------------------------------------------ presigning

@pytest.mark.asyncio
async def test_a_s3_object_presigns_as_an_attachment_under_its_real_name():
    store, client = _s3()

    url = await store.presign_get("s3://kell-artifacts/outputs/ab/cd/abc", ttl_seconds=120, filename="report.md")

    assert "expires=120" in url
    op, params, ttl = client.calls[0]
    assert op == "get_object"
    assert params["Bucket"] == "kell-artifacts"
    assert params["Key"] == "outputs/ab/cd/abc"          # the prefix is part of the key, not of the bucket
    assert params["ResponseContentDisposition"] == 'attachment; filename="report.md"'


@pytest.mark.asyncio
async def test_a_quoted_filename_cannot_break_out_of_the_disposition_header():
    store, client = _s3()

    await store.presign_get("s3://kell-artifacts/x", filename='a"b;x=\r\nInjected: 1')

    disposition = client.calls[0][1]["ResponseContentDisposition"]
    assert disposition.count('"') == 2                   # exactly the wrapper quotes survive
    assert "\r" not in disposition and "\n" not in disposition


@pytest.mark.asyncio
async def test_an_object_from_another_bucket_is_refused_rather_than_signed():
    store, _ = _s3()

    with pytest.raises(Exception):
        await store.presign_get("s3://someone-else/outputs/ab/cd/abc")


@pytest.mark.asyncio
async def test_a_backend_that_cannot_sign_says_so_instead_of_raising():
    """`file://` in development has no signer. The caller must be able to fall back to proxying."""
    assert await LocalFileStore("C:/tmp").presign_get("file:///C:/tmp/x") is None
    assert await MemoryStore().presign_get("memory://ab/cd/abc") is None


@pytest.mark.asyncio
async def test_a_failing_signer_is_reported_as_an_outage_not_a_corrupt_object():
    """Unavailable (retry/proxy) and Corrupt (permanent) mean different things to a caller and must not blur."""
    from app.services.object_storage import ObjectStoreCorrupt, ObjectStoreUnavailable

    store, client = _s3()
    client.fail = RuntimeError("clock skew")

    with pytest.raises(ObjectStoreUnavailable):
        await store.presign_get("s3://kell-artifacts/x")
    assert not issubclass(ObjectStoreUnavailable, ObjectStoreCorrupt)


# ------------------------------------------------------------------ public URLs

def test_a_public_url_is_composed_from_config_and_has_no_double_slashes(monkeypatch):
    monkeypatch.setenv("OBJECT_STORAGE_PUBLIC_BASE", "https://files.kell.example/")
    store, _ = _s3()

    assert store.public_url(key="thumbs/ab.webp") == "https://files.kell.example/thumbs/ab.webp"
    assert store.public_url("thumbs") == "https://files.kell.example/thumbs"


def test_no_public_base_means_nothing_is_public():
    """The safe default: an unset base must not fall back to the bucket's own endpoint, which would expose it."""
    store, _ = _s3()

    assert store.public_url(key="thumbs/ab.webp") is None


def test_switching_providers_changes_only_config(monkeypatch):
    r2 = S3Store("kell-artifacts", "outputs", endpoint_url="https://acct.r2.cloudflarestorage.com", client=StubClient())
    b2 = S3Store("kell-artifacts", "outputs", endpoint_url="https://s3.us-west-004.backblazeb2.com", client=StubClient())

    monkeypatch.setenv("OBJECT_STORAGE_PUBLIC_BASE", "https://files.kell.example")
    assert r2.public_url(key="thumbs/x.webp") == b2.public_url(key="thumbs/x.webp")


# ------------------------------------------------------------------ thumbnails

def test_a_thumbnail_key_is_content_addressed_and_public(monkeypatch):
    from app.services import output_thumbnails as ot

    monkeypatch.setenv("OBJECT_STORAGE_PUBLIC_BASE", "https://files.kell.example")
    set_store(_s3()[0])

    assert ot.thumbnail_key("deadbeef") == "thumbs/deadbeef.webp"
    assert ot.thumbnail_url("deadbeef") == "https://files.kell.example/thumbs/deadbeef.webp"


def test_a_thumbnail_url_is_none_when_nothing_is_public():
    from app.services import output_thumbnails as ot

    set_store(_s3()[0])

    assert ot.thumbnail_url("deadbeef") is None


# ------------------------------------------------------------------ two buckets: public thumbnails, private artefacts

def test_the_public_bucket_is_a_separate_store_from_the_private_one(monkeypatch):
    """Thumbnails and paid artefacts have opposite access rules, so they are opposite buckets."""
    from app.services import object_storage as obs

    monkeypatch.setattr(obs, "_PUBLIC_STORE", None)
    monkeypatch.setenv("OBJECT_STORAGE_PUBLIC_URL", "s3://kell-thumbs")
    monkeypatch.setenv("OBJECT_STORAGE_ENDPOINT_URL", "https://acct.r2.cloudflarestorage.com")
    monkeypatch.setattr(obs, "S3Store", lambda bucket, prefix, **kw: _s3()[0])

    public = obs.get_public_store()
    assert public is not None


def test_no_public_bucket_configured_means_nothing_is_public(monkeypatch):
    """The safe default, and no silent fallback to the PRIVATE bucket: that would expose every paid artefact."""
    from app.services import object_storage as obs

    monkeypatch.setattr(obs, "_PUBLIC_STORE", None)
    monkeypatch.delenv("OBJECT_STORAGE_PUBLIC_URL", raising=False)
    monkeypatch.delenv("OBJECT_STORAGE_PUBLIC_BASE", raising=False)

    assert obs.get_public_store() is None
    assert obs.public_url_for("thumbs/x.webp") is None


def test_a_public_url_is_composed_from_the_public_base(monkeypatch):
    from app.services import object_storage as obs

    monkeypatch.setenv("OBJECT_STORAGE_PUBLIC_BASE", "https://files.kell.org.in/")

    assert obs.public_url_for("thumbs/x.webp") == "https://files.kell.org.in/thumbs/x.webp"


def test_a_public_url_needs_a_base_even_with_a_public_bucket(monkeypatch):
    """A bucket with no domain in front of it has no public URL; the caller draws a tile, not a broken image."""
    from app.services import object_storage as obs

    monkeypatch.setenv("OBJECT_STORAGE_PUBLIC_URL", "s3://kell-thumbs")
    monkeypatch.delenv("OBJECT_STORAGE_PUBLIC_BASE", raising=False)

    assert obs.public_url_for("thumbs/x.webp") is None


# ------------------------------------------------------------------ the download path

class _Row(dict):
    def __getitem__(self, k):
        return super().__getitem__(k)


class _Pool:
    """Just enough pool for `_run_artifacts`: one run row, one submission row.

    `outputs` is the persisted shape: a LIST of step entries, each with its own `files` -- not a dict.
    """

    def __init__(self, files: list[dict]):
        self._outputs = [{"order": 1, "title": "Write it", "output": "done", "files": files}]

    async def fetchrow(self, sql, *args):
        if "FROM way_runs" in sql:
            return {"submission_id": "11111111-2222-3333-4444-555555555555", "owner_id": "u-1",
                    "outputs": self._outputs}
        return None

    async def fetchval(self, sql, *args):
        return None


class _Request:
    headers = {"origin": "https://kell.example"}
    url = "https://api.kell.example/v1/goals/runs/run-1/artifacts/report.md"


@pytest.mark.asyncio
async def test_a_paid_artifact_download_redirects_to_a_signed_url_rather_than_proxying(monkeypatch):
    """The licence check happens first; the bytes never pass through the API (`procedure_outputs.md` §4)."""
    from app.submissions import api as sub_api

    async def licensed(*a, **k):
        return True

    monkeypatch.setattr("app.services.procedure_billing.may_download", licensed)
    monkeypatch.setattr("app.services.procedure_billing._uuid", lambda x: x == "u-1")
    store, client = _s3()
    set_store(store)
    files = [{"name": "report.md", "media_type": "text/markdown", "object_key": "s3://kell-artifacts/outputs/ab/x"}]

    resp = await sub_api.download_artifact(_Pool(files), "u-1", "run-1", "report.md", _Request())

    assert resp.status_code == 302
    assert client.calls[0][1]["ResponseContentDisposition"].endswith('filename="report.md"')


@pytest.mark.asyncio
async def test_an_unlicensed_download_is_refused_before_anything_is_signed(monkeypatch):
    from app.submissions import api as sub_api

    async def not_licensed(*a, **k):
        return False

    monkeypatch.setattr("app.services.procedure_billing.may_download", not_licensed)
    monkeypatch.setattr("app.services.procedure_billing._uuid", lambda x: x == "u-1")
    store, client = _s3()
    set_store(store)
    files = [{"name": "report.md", "media_type": "text/markdown", "object_key": "s3://kell-artifacts/outputs/ab/x"}]

    resp = await sub_api.download_artifact(_Pool(files), "u-1", "run-1", "report.md", _Request())

    assert resp.status_code == 402
    assert client.calls == []                            # no capability was ever minted


@pytest.mark.asyncio
async def test_a_backend_that_cannot_sign_still_serves_the_file(monkeypatch):
    from app.submissions import api as sub_api

    async def licensed(*a, **k):
        return True

    monkeypatch.setattr("app.services.procedure_billing.may_download", licensed)
    monkeypatch.setattr("app.services.procedure_billing._uuid", lambda x: x == "u-1")
    store = MemoryStore()
    set_store(store)
    _sha, locator = await store.put(b"# body", content_type="text/markdown")
    files = [{"name": "report.md", "media_type": "text/markdown", "object_key": locator}]

    resp = await sub_api.download_artifact(_Pool(files), "u-1", "run-1", "report.md", _Request())

    assert resp.status_code == 200 and resp.body == b"# body"
