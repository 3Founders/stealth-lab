"""R2 / S3-compatible object-storage bootstrap. Thin adapter over boto3 (already in requirements-objectstore.txt).

    create bucket if missing -> keep it private -> optional lifecycle -> put/get/delete round trip -> verify prod config

Never makes anything public. A bucket-scoped R2 token (Object Read & Write) cannot CREATE buckets: in that case this
prints the exact `wrangler` command and stops; it does not try to escalate.
"""
from __future__ import annotations

import hashlib
import os
import time
from typing import Any
from urllib.parse import urlparse

from .core import OpsError, load_env


def parse_url(url: str) -> tuple[str, str]:
    u = urlparse(url)
    if u.scheme != "s3" or not u.netloc:
        raise OpsError(f"OBJECT_STORAGE_URL must look like s3://bucket/prefix (got {url!r})")
    return u.netloc, u.path.strip("/")


def client(env: dict[str, str]) -> Any:
    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        raise OpsError("boto3 is not installed: pip install -r backend/requirements-objectstore.txt") from None
    for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        if not env.get(k):
            raise OpsError(f"{k} is not set")
    return boto3.client(
        "s3", endpoint_url=env.get("OBJECT_STORAGE_ENDPOINT_URL") or None,
        aws_access_key_id=env["AWS_ACCESS_KEY_ID"], aws_secret_access_key=env["AWS_SECRET_ACCESS_KEY"],
        region_name=env.get("AWS_DEFAULT_REGION") or "auto", config=Config(retries={"max_attempts": 3}, signature_version="s3v4"))


def _code(exc: Exception) -> str:
    return str(getattr(exc, "response", {}).get("Error", {}).get("Code", type(exc).__name__))


def ensure_bucket(s3: Any, bucket: str, log=print) -> bool:
    """True if created now. Existing bucket: left as is."""
    try:
        s3.head_bucket(Bucket=bucket)
        log(f"bucket {bucket}: exists")
        return False
    except Exception as exc:  # noqa: BLE001
        if _code(exc) not in ("404", "NoSuchBucket", "NotFound"):
            if _code(exc) in ("403", "AccessDenied"):
                # a token scoped to an existing bucket sees 403 on head for others; treat as "cannot verify"
                raise OpsError(f"bucket {bucket}: access denied ({_code(exc)}). Check the token is scoped to this bucket.") from None
            raise OpsError(f"bucket {bucket}: {_code(exc)}") from None
    try:
        s3.create_bucket(Bucket=bucket)
    except Exception as exc:  # noqa: BLE001
        raise OpsError(f"cannot create bucket {bucket!r} with this credential ({_code(exc)}). Create it once with an admin credential:\n"
                       f"    wrangler r2 bucket create {bucket}\n  then re-run this command.") from None
    log(f"bucket {bucket}: created (private by default)")
    return True


def make_private(s3: Any, bucket: str, log=print) -> None:
    """S3 needs an explicit public-access block; R2 has no such API and buckets are private unless a public domain is attached."""
    try:
        s3.put_public_access_block(Bucket=bucket, PublicAccessBlockConfiguration={
            "BlockPublicAcls": True, "IgnorePublicAcls": True, "BlockPublicPolicy": True, "RestrictPublicBuckets": True})
        log("public access block: enabled")
    except Exception as exc:  # noqa: BLE001
        log(f"public access block: not supported here ({_code(exc)}); verify in the provider that no public domain/policy is attached")


def lifecycle(s3: Any, bucket: str, prefix: str, days: int, log=print) -> None:
    """Optional: abort stale multipart uploads (never expires payloads: they are content-addressed evidence)."""
    try:
        s3.put_bucket_lifecycle_configuration(Bucket=bucket, LifecycleConfiguration={"Rules": [{
            "ID": "stealth-abort-stale-multipart", "Status": "Enabled", "Filter": {"Prefix": prefix},
            "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": days}}]})
        log(f"lifecycle: abort incomplete multipart uploads after {days}d")
    except Exception as exc:  # noqa: BLE001
        log(f"lifecycle: skipped ({_code(exc)})")


def roundtrip(s3: Any, bucket: str, prefix: str) -> float:
    key = "/".join(p for p in (prefix, "_ops_selftest", f"{int(time.time())}-{os.getpid()}.bin") if p)
    body = os.urandom(2048)
    t0 = time.perf_counter()
    try:
        s3.put_object(Bucket=bucket, Key=key, Body=body)
        got = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        if hashlib.sha256(got).digest() != hashlib.sha256(body).digest():
            raise OpsError("object storage returned different bytes than were written")
    finally:
        try:
            s3.delete_object(Bucket=bucket, Key=key)    # only our own self-test object
        except Exception:  # noqa: BLE001
            pass
    return (time.perf_counter() - t0) * 1000


def verify_config(env: dict[str, str]) -> list[str]:
    url = env.get("OBJECT_STORAGE_URL", "")
    problems = []
    if not url:
        problems.append("OBJECT_STORAGE_URL is not set")
    elif url.startswith("memory://"):
        problems.append("OBJECT_STORAGE_URL=memory:// is refused in PRODUCTION")
    elif url.startswith("file://"):
        problems.append("file:// storage only works on a single machine (not for Cloud Run / Actions)")
    return problems


def setup(*, lifecycle_days: int = 7, log=print) -> str:
    env = load_env()
    bad = verify_config(env)
    if bad:
        raise OpsError("; ".join(bad))
    bucket, prefix = parse_url(env["OBJECT_STORAGE_URL"])
    s3 = client(env)
    ensure_bucket(s3, bucket, log)
    make_private(s3, bucket, log)
    if lifecycle_days:
        lifecycle(s3, bucket, prefix, lifecycle_days, log)
    ms = roundtrip(s3, bucket, prefix)
    return f"s3://{bucket}/{prefix}: put/get/delete OK ({ms:.0f} ms)"


def health(env: dict[str, str] | None = None) -> str:
    env = env or load_env()
    bad = verify_config(env)
    if bad:
        raise OpsError("; ".join(bad))
    bucket, prefix = parse_url(env["OBJECT_STORAGE_URL"])
    return f"put/get/delete OK ({roundtrip(client(env), bucket, prefix):.0f} ms)"
