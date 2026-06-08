"""Object-storage integration (Cloudflare R2, S3-compatible).

`client` holds the storage abstraction (Protocol + production boto3-backed
client + test double), mirroring `bullet_api.pandadoc` / `bullet_api.ghl`.
S1-25b (`store_signed_pdf`) is the first consumer; S1-27/S1-28 (transcripts)
and the research scraper reuse the same seam.
"""

from __future__ import annotations

from bullet_api.storage.client import (
    FakeStorageClient,
    R2StorageClient,
    StorageClient,
    get_s3_client,
    get_storage_client,
)

__all__ = [
    "FakeStorageClient",
    "R2StorageClient",
    "StorageClient",
    "get_s3_client",
    "get_storage_client",
]
