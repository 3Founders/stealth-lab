"""
Object storage for large RAW artifacts (documents, traces, logs).

The relational databases (control DB and shards) keep only a small LOCATOR + sha256 + size; the bytes live in
object storage. This keeps `ingestion_jobs.payload` and every shard small, makes duplicate content free (keys are
content-addressed), and lets any worker on any provider fetch the same bytes.

Configuration (environment; no credentials in the repo):
    OBJECT_STORAGE_URL              s3://bucket/optional/prefix   (AWS S3, Cloudflare R2, Backblaze B2, MinIO,
                                                                  OCI/GCS S3-interop -- all one code path)
                                    file:///abs/path              (a directory; a shared disk / dev)
                                    memory://                     (TESTS ONLY -- refused when STEALTHLAB_ENV=PRODUCTION)
    OBJECT_STORAGE_ENDPOINT_URL     custom S3 endpoint (R2/B2/MinIO/OCI); standard AWS_* variables carry credentials
    OBJECT_STORAGE_ACCESS_KEY_ID / OBJECT_STORAGE_SECRET_ACCESS_KEY / OBJECT_STORAGE_REGION
                                    the store's own credentials, preferred over AWS_*: on an AWS machine the AWS_*
                                    names belong to the machine (Google's workload identity federation reads them),
                                    not to an R2 bucket
    OBJECT_STORAGE_PUBLIC_BASE      the public side of the store: an R2 custom domain, a Cloudflare-fronted B2
                                    domain, or a bucket's own public host. Empty = nothing is public, which is the
                                    safe default. Composed into URLs at read time, never stored.
    RAW_PAYLOAD_INLINE_MAX_BYTES    strings larger than this are offloaded from job payloads (default 65536)
    RAW_PAYLOAD_HARD_MAX_BYTES      without an object store, a payload above this is REFUSED (default 1048576)

Provider choice and the numbers behind it: `docs/blinkit/storage_choice.md`. Nothing here names a provider; R2 vs
B2 differs only in endpoint, region and public base.

Failure semantics (worker): store unreachable / throttled -> `ObjectStoreUnavailable` (retryable);
object missing / hash mismatch -> `ObjectStoreCorrupt` (permanent: retrying cannot recover lost data).
The local `.kell/*.md` cache never touches this module.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, Optional
from urllib.parse import urlsplit

import asyncpg

INLINE_MAX_DEFAULT = 64 * 1024
HARD_MAX_DEFAULT = 1024 * 1024
BLOB_KEY = "$blob"


class ObjectStoreUnavailable(Exception):
    """Transient: store down / throttled / timed out. Retry with backoff."""


class ObjectStoreCorrupt(Exception):
    """Permanent: the object is missing or its bytes do not match the recorded sha256."""


class PayloadTooLarge(ValueError):
    """No object store is configured and the payload exceeds the inline hard limit (fail closed)."""


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _key(sha: str) -> str:
    return f"{sha[:2]}/{sha[2:4]}/{sha}"


def get_public_store() -> Optional[ObjectStore]:
    """The store behind the PUBLIC base URL, or None when nothing is public.

    Two buckets, not one. A Way's paid artefact must never be fetchable without a licence, while a thumbnail has to
    be fetchable by anyone or a listing is a grid of grey boxes (`docs/procedure_outputs.md` §3.2). Those are
    opposite requirements, so they are opposite buckets:

        OBJECT_STORAGE_URL           the private bucket: artefacts, presigned only
        OBJECT_STORAGE_PUBLIC_URL    the public bucket: thumbnails, served from OBJECT_STORAGE_PUBLIC_BASE

    Unset = nothing is public, which is the safe default. There is deliberately no fallback to the private bucket:
    that would silently make every paid artefact public.
    """
    url = (os.environ.get("OBJECT_STORAGE_PUBLIC_URL") or "").strip()
    if not url:
        return None
    global _PUBLIC_STORE
    cache = _PUBLIC_STORE
    if cache is not None and cache[0] == url:
        return cache[1]
    u = urlsplit(url)
    if u.scheme != "s3":
        raise RuntimeError(f"unsupported OBJECT_STORAGE_PUBLIC_URL scheme {u.scheme!r} (only s3:// is public)")
    store = S3Store(u.netloc, u.path, endpoint_url=os.environ.get("OBJECT_STORAGE_ENDPOINT_URL"))
    _PUBLIC_STORE = (url, store)
    return store


def public_url_for(key: str) -> Optional[str]:
    """`key` under the configured public base, or None when no public bucket is configured."""
    base = (os.environ.get("OBJECT_STORAGE_PUBLIC_BASE") or "").strip().rstrip("/")
    if not base:
        return None
    return f"{base}/{key.strip('/')}"


class ObjectStore:
    backend = "base"

    async def put(self, data: bytes, *, content_type: str = "application/octet-stream") -> tuple[str, str]:
        """Store bytes; returns (sha256, locator). Idempotent: identical bytes -> identical locator."""
        raise NotImplementedError

    async def get(self, locator: str) -> bytes:
        raise NotImplementedError

    async def presign_get(self, locator: str, *, ttl_seconds: int = 300, filename: Optional[str] = None) -> Optional[str]:
        """A short-lived URL that serves this object, or None when this backend cannot sign one.

        Exists so a paid output is not proxied through the API: the licence check happens once, and the
        capability it issues is a URL that expires (`docs/procedure_outputs.md` §4). `filename` asks the store to
        send it as an attachment under that name, which is what keeps a `.html` artefact from rendering on our origin.

        The default returns None -- "proxy it" -- rather than raising, so a caller written against this interface
        works on every backend and simply gets the slower path where signing is impossible (a local directory).
        """
        return None

    def public_url(self, prefix: str = "", key: str = "") -> Optional[str]:
        """The URL the public side of this store is served from, or None when no public base is configured.

        Composed from configuration at read time and never stored, so a provider swap or a new CDN domain rewrites
        no rows (`docs/procedure_outputs.md` §3.1). Providers differ only in the values of that configuration.
        """
        base = (os.environ.get("OBJECT_STORAGE_PUBLIC_BASE") or "").strip().rstrip("/")
        if not base:
            return None
        parts = [p.strip("/") for p in (prefix, key) if p and p.strip("/")]
        return base + ("/" + "/".join(parts) if parts else "")


class MemoryStore(ObjectStore):
    backend = "memory"

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.fail_next: Optional[Exception] = None

    async def put(self, data, *, content_type="application/octet-stream"):
        if self.fail_next:
            e, self.fail_next = self.fail_next, None
            raise e
        sha = sha256_hex(data)
        self.objects[f"memory://{_key(sha)}"] = data
        return sha, f"memory://{_key(sha)}"

    async def get(self, locator):
        if self.fail_next:
            e, self.fail_next = self.fail_next, None
            raise e
        try:
            return self.objects[locator]
        except KeyError as exc:
            raise ObjectStoreCorrupt(f"object {locator} not found") from exc


class LocalFileStore(ObjectStore):
    backend = "file"

    def __init__(self, root: str):
        self.root = root

    def _path(self, sha: str) -> str:
        return os.path.join(self.root, *_key(sha).split("/"))

    async def put(self, data, *, content_type="application/octet-stream"):
        import asyncio

        sha = sha256_hex(data)
        path = self._path(sha)

        def write() -> None:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if not os.path.exists(path):
                tmp = f"{path}.{os.getpid()}.tmp"
                with open(tmp, "wb") as fh:
                    fh.write(data)
                os.replace(tmp, path)              # atomic: readers never see a partial object

        try:
            await asyncio.to_thread(write)
        except OSError as exc:
            raise ObjectStoreUnavailable(f"file store write failed: {exc}") from exc
        return sha, "file://" + path.replace("\\", "/")

    async def get(self, locator):
        import asyncio

        path = locator[len("file://"):]
        if os.name == "nt" and path.startswith("/") and len(path) > 2 and path[2] == ":":
            path = path[1:]
        try:
            return await asyncio.to_thread(lambda: open(path, "rb").read())
        except FileNotFoundError as exc:
            raise ObjectStoreCorrupt(f"object {locator} not found") from exc
        except OSError as exc:
            raise ObjectStoreUnavailable(f"file store read failed: {exc}") from exc


class S3Store(ObjectStore):
    """S3-compatible (AWS, R2, MinIO, OCI/GCS interop). boto3 is imported lazily (requirements-objectstore.txt)."""

    backend = "s3"

    def __init__(self, bucket: str, prefix: str = "", *, endpoint_url: Optional[str] = None, client: Any = None):
        self.bucket, self.prefix = bucket, prefix.strip("/")
        if client is None:
            try:
                import boto3
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError("OBJECT_STORAGE_URL is s3:// but boto3 is not installed (pip install -r requirements-objectstore.txt)") from exc
            own = {k: os.environ.get(v) for k, v in (("aws_access_key_id", "OBJECT_STORAGE_ACCESS_KEY_ID"),
                                                       ("aws_secret_access_key", "OBJECT_STORAGE_SECRET_ACCESS_KEY"),
                                                       ("region_name", "OBJECT_STORAGE_REGION"))}
            client = boto3.client("s3", endpoint_url=endpoint_url, **{k: v for k, v in own.items() if v})
        self.client = client

    def _k(self, sha: str) -> str:
        return "/".join(p for p in (self.prefix, _key(sha)) if p)

    def _classify(self, exc: Exception) -> Exception:
        code = getattr(exc, "response", {}).get("Error", {}).get("Code", "") if hasattr(exc, "response") else ""
        if code in ("NoSuchKey", "404", "NotFound"):
            return ObjectStoreCorrupt(f"object not found ({code})")
        return ObjectStoreUnavailable(f"{type(exc).__name__}: {exc}")

    async def put(self, data, *, content_type="application/octet-stream"):
        import asyncio

        sha = sha256_hex(data)
        key = self._k(sha)

        def run() -> None:
            try:
                self.client.head_object(Bucket=self.bucket, Key=key)       # content-addressed: already there
                return
            except Exception as exc:  # noqa: BLE001
                if not isinstance(self._classify(exc), ObjectStoreCorrupt):   # anything but "not found" is an outage
                    raise
            self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

        try:
            await asyncio.to_thread(run)
        except Exception as exc:  # noqa: BLE001
            raise self._classify(exc) from exc
        return sha, f"s3://{self.bucket}/{key}"

    async def get(self, locator):
        import asyncio

        key = locator.split(f"s3://{self.bucket}/", 1)[1]
        try:
            resp = await asyncio.to_thread(lambda: self.client.get_object(Bucket=self.bucket, Key=key))
            return await asyncio.to_thread(lambda: resp["Body"].read())
        except Exception as exc:  # noqa: BLE001
            raise self._classify(exc) from exc

    async def presign_get(self, locator, *, ttl_seconds=300, filename=None):
        """A signed GET URL, built by boto3 so it works unchanged on R2, B2, MinIO and AWS.

        `ResponseContentDisposition` is part of the signature, so the store itself sends the file as an attachment:
        the API never sees the bytes and a `.html` artefact cannot render on a presigned host or on our origin.
        """
        import asyncio

        prefix = f"s3://{self.bucket}/"
        if not locator.startswith(prefix):
            raise ObjectStoreCorrupt(f"{locator} is not in bucket {self.bucket}")
        key = locator[len(prefix):]
        params = {"Bucket": self.bucket, "Key": key}
        if filename:
            # Untrusted input, and it becomes a header value: strip quotes, backslashes AND line breaks, because
            # `\r\n` inside a header is response splitting. Same conservative character set as `safe_name`.
            safe = re.sub(r'["\\\r\n\t]', "", str(filename))[:200] or "download"
            params["ResponseContentDisposition"] = f'attachment; filename="{safe}"'
        try:
            return await asyncio.to_thread(
                lambda: self.client.generate_presigned_url("get_object", Params=params,
                                                           ExpiresIn=int(ttl_seconds)))
        except Exception as exc:  # noqa: BLE001 -- an unsigned fallback (proxy) beats a failed download
            raise ObjectStoreUnavailable(f"could not presign {key}: {exc}") from exc


_STORE: Optional[ObjectStore] = None
_STORE_LOADED = False
_PUBLIC_STORE: Optional[tuple[str, ObjectStore]] = None   # (url it was built from, the store)


def get_store() -> Optional[ObjectStore]:
    """The configured store, or None (inline mode). memory:// is refused in PRODUCTION: production must never
    silently fall back to in-memory storage."""
    global _STORE, _STORE_LOADED
    if _STORE_LOADED:
        return _STORE
    url = os.environ.get("OBJECT_STORAGE_URL")
    if url:
        u = urlsplit(url)
        if u.scheme == "memory":
            from app.config import settings

            if settings.is_production:
                raise RuntimeError("OBJECT_STORAGE_URL=memory:// is not allowed when STEALTHLAB_ENV=PRODUCTION")
            _STORE = MemoryStore()
        elif u.scheme == "file":
            _STORE = LocalFileStore(url[len("file://"):] if not (os.name == "nt" and url.startswith("file:///")) else url[len("file:///"):])
        elif u.scheme == "s3":
            _STORE = S3Store(u.netloc, u.path, endpoint_url=os.environ.get("OBJECT_STORAGE_ENDPOINT_URL"))
        else:
            raise RuntimeError(f"unsupported OBJECT_STORAGE_URL scheme {u.scheme!r}")
    _STORE_LOADED = True
    return _STORE


def set_store(store: Optional[ObjectStore]) -> None:
    """Test/deployment hook."""
    global _STORE, _STORE_LOADED
    _STORE, _STORE_LOADED = store, True


def _limits() -> tuple[int, int]:
    return (int(os.environ.get("RAW_PAYLOAD_INLINE_MAX_BYTES", INLINE_MAX_DEFAULT)),
            int(os.environ.get("RAW_PAYLOAD_HARD_MAX_BYTES", HARD_MAX_DEFAULT)))


async def store_blob(pool: asyncpg.Pool, store: ObjectStore, data: bytes, *, content_type: str = "text/plain") -> dict:
    sha, locator = await store.put(data, content_type=content_type)
    await pool.execute(
        "INSERT INTO raw_objects (sha256, locator, backend, size_bytes, content_type) VALUES ($1, $2, $3, $4, $5) ON CONFLICT (sha256) DO NOTHING",
        sha, locator, store.backend, len(data), content_type)
    return {"sha256": sha, "locator": locator, "size": len(data), "content_type": content_type}


async def offload_payload(pool: asyncpg.Pool, payload: dict, *, store: Optional[ObjectStore] = None) -> dict:
    """Replace every top-level string above the inline limit with a blob reference; the DB row stays small.
    With no store configured, a payload above the hard limit is refused (never silently stored inline)."""
    inline_max, hard_max = _limits()
    store = store if store is not None else get_store()
    out = dict(payload)
    total = len(json.dumps(payload, default=str))
    for k, v in payload.items():
        if isinstance(v, str) and len(v.encode()) > inline_max:
            if store is None:
                if total > hard_max:
                    raise PayloadTooLarge(f"payload field {k!r} is {len(v)} chars and no OBJECT_STORAGE_URL is configured "
                                          f"(hard inline limit {hard_max} bytes)")
                continue
            out[k] = {BLOB_KEY: await store_blob(pool, store, v.encode())}
    return out


async def hydrate_payload(payload: dict, *, store: Optional[ObjectStore] = None) -> dict:
    """Inverse of offload_payload: fetch and verify every blob reference (sha256 checked)."""
    out = dict(payload)
    for k, v in payload.items():
        if isinstance(v, dict) and BLOB_KEY in v:
            ref = v[BLOB_KEY]
            s = store if store is not None else get_store()
            if s is None:
                raise ObjectStoreUnavailable("payload references object storage but OBJECT_STORAGE_URL is not configured on this worker")
            data = await s.get(ref["locator"])
            if sha256_hex(data) != ref["sha256"]:
                raise ObjectStoreCorrupt(f"sha256 mismatch for {ref['locator']}")
            out[k] = data.decode()
    return out


async def authorized_hydrate(payload: dict, ctx, obj, *, store: Optional[ObjectStore] = None) -> dict:
    """hydrate_payload behind the authorization policy. Blobs are content-addressed
    and DEDUPLICATED across owners, so a storage key is never an access grant and
    carries no ACL of its own: the ACL is the referencing job/object. The caller
    must be able to read that object (authorization.can_read) before any blob
    reference in its payload is dereferenced."""
    from app.services.authorization import Action, authorize

    authorize(ctx, Action.READ, obj)
    return await hydrate_payload(payload, store=store)
