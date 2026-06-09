"""Cloudflare R2 object-storage client abstraction (S3-compatible).

`StorageClient` is a small Protocol so handlers depend on the interface
rather than on boto3 / R2 specifically. `R2StorageClient` is the
production wiring; `FakeStorageClient` is used by tests to record uploads
and assert on them without a network call. This mirrors the PandaDoc /
GHL client seams in `bullet_api.pandadoc.client` / `bullet_api.ghl.client`.

R2 speaks the S3 API, so we use boto3's S3 client pointed at the R2
endpoint. Two deliberate choices:

- **One client per process, reused.** `get_s3_client()` is an
  `lru_cache` singleton: constructing a boto3 client loads service models
  from disk and resolves credentials (~tens of ms) and each client owns a
  urllib3 connection pool, so building one per call would be wasteful. The
  API runs as a single uvicorn process with the Inngest functions served
  in-process, so one cached client is shared across every invocation. If
  the deployment ever runs multiple worker processes, each gets its own
  singleton (one client per process) - exactly right; you cannot share a
  client across processes anyway. boto3 *clients* are thread-safe, so the
  singleton is safe to share across the `asyncio.to_thread` worker threads
  the production client uses.
- **boto3 is synchronous**, so `R2StorageClient.put_object` runs the
  blocking call via `asyncio.to_thread` to keep the event loop free.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Protocol

import boto3

from bullet_api.config import get_settings

# R2's region is always "auto" (Cloudflare does not use AWS regions).
R2_REGION = "auto"


@lru_cache(maxsize=1)
def get_s3_client() -> Any:
    """Return the process-wide singleton boto3 S3 client pointed at R2.

    Created once on first use and reused thereafter (the "one client per
    process" rule). `lru_cache` is thread-safe, so a race on the first call
    cannot construct two clients. Raises `RuntimeError` when the R2
    credentials / endpoint are not configured, so a mis-configured
    deployment fails loudly rather than building a broken client.
    """
    settings = get_settings()
    endpoint = settings.r2_endpoint_url
    if not (endpoint and settings.r2_access_key_id and settings.r2_secret_access_key):
        raise RuntimeError(
            "R2 storage is not configured (R2_ACCOUNT_ID / R2_ACCESS_KEY_ID / "
            "R2_SECRET_ACCESS_KEY empty). Set them on the Render env group."
        )
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        region_name=R2_REGION,
    )


class StorageClient(Protocol):
    async def put_object(self, key: str, body: bytes, content_type: str) -> str:
        """Upload `body` to `key` and return the object's canonical URL.

        Idempotent on the key: re-uploading the same key overwrites the
        object (so a retried fan-out does not create a duplicate).
        """
        ...


class R2StorageClient:
    """Production storage client - puts objects into the R2 bucket via boto3.

    Holds the cached singleton S3 client (not a per-instance client) and
    runs the blocking `put_object` in a worker thread so the event loop is
    not stalled.
    """

    def __init__(self, bucket: str, endpoint_url: str) -> None:
        self._bucket = bucket
        self._endpoint_url = endpoint_url.rstrip("/")

    async def put_object(self, key: str, body: bytes, content_type: str) -> str:
        if not self._bucket:
            raise RuntimeError(
                "R2_BUCKET_NAME is empty; cannot store object. Set it on the Render env group."
            )
        client = get_s3_client()
        await asyncio.to_thread(
            client.put_object,
            Bucket=self._bucket,
            Key=key,
            Body=body,
            ContentType=content_type,
        )
        return f"{self._endpoint_url}/{self._bucket}/{key}"


@dataclass
class FakeStorageClient:
    """Test double. Records every upload on `puts` and returns a fake URL.

    `error` (when set) is raised instead of recording, so tests can
    exercise the upload-failure path without boto3.
    """

    puts: list[tuple[str, bytes, str]] = field(default_factory=list)
    error: Exception | None = None
    base_url: str = "https://fake-r2.local/bucket"

    async def put_object(self, key: str, body: bytes, content_type: str) -> str:
        if self.error is not None:
            raise self.error
        self.puts.append((key, body, content_type))
        return f"{self.base_url}/{key}"


def get_storage_client() -> StorageClient:
    """Worker / FastAPI factory. Tests substitute a FakeStorageClient."""
    settings = get_settings()
    return R2StorageClient(
        bucket=settings.r2_bucket_name,
        endpoint_url=settings.r2_endpoint_url,
    )
