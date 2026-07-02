"""Unit tests for the R2 storage seam (`bullet_api.storage.client`).

No DB, no live R2: the boto3 client is monkeypatched so we assert on the
request shape and the singleton behaviour, and `FakeStorageClient` is
exercised directly. The key invariant under test is the "ONE client per
process, reused" rule (`get_s3_client` is a cached singleton).
"""

from __future__ import annotations

import pytest
from botocore.exceptions import ClientError

from bullet_api.storage.client import (
    FakeStorageClient,
    ObjectNotFound,
    R2StorageClient,
    get_s3_client,
)


@pytest.fixture(autouse=True)
def _clear_singleton_cache():
    """Each test starts and ends with a clean `get_s3_client` cache so the
    monkeypatched settings/clients don't leak across tests."""
    get_s3_client.cache_clear()
    yield
    get_s3_client.cache_clear()


def test_get_s3_client_returns_same_cached_instance(monkeypatch) -> None:
    """The singleton rule: one client per process, reused. Two calls return
    the IDENTICAL object and boto3.client is invoked exactly once."""
    import bullet_api.storage.client as storage_mod

    calls: list[dict] = []

    class _SettingsStub:
        r2_endpoint_url = "https://acct.r2.cloudflarestorage.com"
        r2_access_key_id = "ak"
        r2_secret_access_key = "sk"

    monkeypatch.setattr(storage_mod, "get_settings", lambda: _SettingsStub())

    def _fake_boto_client(service, **kwargs):
        calls.append({"service": service, **kwargs})
        return object()

    monkeypatch.setattr(storage_mod.boto3, "client", _fake_boto_client)

    first = get_s3_client()
    second = get_s3_client()

    assert first is second  # same instance reused
    assert len(calls) == 1  # boto3.client constructed exactly once
    assert calls[0]["service"] == "s3"
    assert calls[0]["endpoint_url"] == "https://acct.r2.cloudflarestorage.com"
    assert calls[0]["region_name"] == "auto"


def test_get_s3_client_raises_when_unconfigured(monkeypatch) -> None:
    import bullet_api.storage.client as storage_mod

    class _EmptySettings:
        r2_endpoint_url = ""
        r2_access_key_id = ""
        r2_secret_access_key = ""

    monkeypatch.setattr(storage_mod, "get_settings", lambda: _EmptySettings())

    with pytest.raises(RuntimeError) as exc:
        get_s3_client()
    assert "R2 storage is not configured" in str(exc.value)


async def test_r2_storage_client_empty_bucket_raises() -> None:
    client = R2StorageClient(bucket="", endpoint_url="https://acct.r2.cloudflarestorage.com")
    with pytest.raises(RuntimeError) as exc:
        await client.put_object("k", b"x", "application/pdf")
    assert "R2_BUCKET_NAME" in str(exc.value)


async def test_r2_storage_client_puts_via_cached_client_and_returns_url(monkeypatch) -> None:
    """put_object calls the cached boto3 client's put_object with the right
    args and returns the canonical object URL."""
    import bullet_api.storage.client as storage_mod

    captured: dict = {}

    class _S3Stub:
        def put_object(self, **kwargs):
            captured.update(kwargs)
            return {"ETag": "x"}

    monkeypatch.setattr(storage_mod, "get_s3_client", lambda: _S3Stub())

    client = R2StorageClient(
        bucket="bullet-bucket", endpoint_url="https://acct.r2.cloudflarestorage.com"
    )
    url = await client.put_object("signed-agreements/c/d.pdf", b"%PDF-1.7", "application/pdf")

    assert captured["Bucket"] == "bullet-bucket"
    assert captured["Key"] == "signed-agreements/c/d.pdf"
    assert captured["Body"] == b"%PDF-1.7"
    assert captured["ContentType"] == "application/pdf"
    assert url == "https://acct.r2.cloudflarestorage.com/bullet-bucket/signed-agreements/c/d.pdf"


async def test_fake_storage_client_records_puts() -> None:
    fake = FakeStorageClient()
    url = await fake.put_object("k", b"bytes", "application/pdf")
    assert fake.puts == [("k", b"bytes", "application/pdf")]
    assert url == "https://fake-r2.local/bucket/k"


async def test_fake_storage_client_raises_when_error_set() -> None:
    fake = FakeStorageClient(error=RuntimeError("upload boom"))
    with pytest.raises(RuntimeError):
        await fake.put_object("k", b"x", "application/pdf")
    assert fake.puts == []  # nothing recorded on failure


# --------------------------------------------------------------------------- #
# get_object (S1-29 read path)
# --------------------------------------------------------------------------- #


async def test_r2_get_object_returns_bytes(monkeypatch) -> None:
    """get_object reads the streaming body via the cached client and returns bytes."""
    import bullet_api.storage.client as storage_mod

    captured: dict = {}

    class _Body:
        def read(self):
            return b"Rep: hi\nProspect: hello\n"

    class _S3Stub:
        def get_object(self, **kwargs):
            captured.update(kwargs)
            return {"Body": _Body()}

    monkeypatch.setattr(storage_mod, "get_s3_client", lambda: _S3Stub())

    client = R2StorageClient(
        bucket="bullet-bucket", endpoint_url="https://acct.r2.cloudflarestorage.com"
    )
    body = await client.get_object("sales-call-transcripts/x.txt")

    assert captured["Bucket"] == "bullet-bucket"
    assert captured["Key"] == "sales-call-transcripts/x.txt"
    assert body == b"Rep: hi\nProspect: hello\n"


async def test_r2_get_object_missing_key_raises_object_not_found(monkeypatch) -> None:
    """A NoSuchKey (404) ClientError becomes the typed ObjectNotFound."""
    import bullet_api.storage.client as storage_mod

    class _S3Stub:
        def get_object(self, **kwargs):
            raise ClientError({"Error": {"Code": "NoSuchKey", "Message": "missing"}}, "GetObject")

    monkeypatch.setattr(storage_mod, "get_s3_client", lambda: _S3Stub())
    client = R2StorageClient(bucket="b", endpoint_url="https://acct.r2.cloudflarestorage.com")
    with pytest.raises(ObjectNotFound):
        await client.get_object("gone.txt")


async def test_r2_get_object_transient_error_propagates(monkeypatch) -> None:
    """A 5xx ClientError is NOT swallowed - it propagates so the caller retries."""
    import bullet_api.storage.client as storage_mod

    class _S3Stub:
        def get_object(self, **kwargs):
            raise ClientError({"Error": {"Code": "InternalError", "Message": "boom"}}, "GetObject")

    monkeypatch.setattr(storage_mod, "get_s3_client", lambda: _S3Stub())
    client = R2StorageClient(bucket="b", endpoint_url="https://acct.r2.cloudflarestorage.com")
    with pytest.raises(ClientError):
        await client.get_object("k.txt")


async def test_r2_get_object_empty_bucket_raises() -> None:
    client = R2StorageClient(bucket="", endpoint_url="https://acct.r2.cloudflarestorage.com")
    with pytest.raises(RuntimeError) as exc:
        await client.get_object("k")
    assert "R2_BUCKET_NAME" in str(exc.value)


async def test_fake_storage_client_get_object() -> None:
    fake = FakeStorageClient(gets={"k.txt": b"hello"})
    assert await fake.get_object("k.txt") == b"hello"
    with pytest.raises(ObjectNotFound):
        await fake.get_object("missing.txt")


async def test_fake_storage_client_get_error() -> None:
    import httpx

    fake = FakeStorageClient(get_error=httpx.ReadTimeout("slow"))
    with pytest.raises(httpx.ReadTimeout):
        await fake.get_object("k.txt")
