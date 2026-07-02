"""Unit tests for the S1-30 OpenAI embeddings seam (no network).

The HttpOpenAIEmbeddingClient tests inject a fake AsyncOpenAI so no real key or
call is made; they assert the request shape (batch input, model), that an empty
input short-circuits without a call, that the response is re-ordered by `index`,
and that an empty key fails loud. The FakeEmbeddingClient tests lock its
test-double contract.
"""

from __future__ import annotations

import types

import pytest

from bullet_api.worker.embedding_client import (
    EMBEDDING_DIM,
    EmbeddingConfigError,
    FakeEmbeddingClient,
    HttpOpenAIEmbeddingClient,
)


def _fake_openai(captured: dict, *, reverse: bool = False):
    """A stand-in AsyncOpenAI whose embeddings.create records its kwargs and
    returns one vector per input. `reverse` returns the data list in reversed
    index order to prove the client sorts by `index`."""

    class _Embeddings:
        async def create(self, *, model, input):
            captured["model"] = model
            captured["input"] = list(input)
            captured["calls"] = captured.get("calls", 0) + 1
            data = [
                types.SimpleNamespace(index=i, embedding=[float(i)] * 4) for i in range(len(input))
            ]
            return types.SimpleNamespace(data=list(reversed(data)) if reverse else data)

    return types.SimpleNamespace(embeddings=_Embeddings())


async def test_embed_batches_and_passes_model() -> None:
    captured: dict = {}
    client = HttpOpenAIEmbeddingClient(
        api_key="sk-x", model="text-embedding-3-small", client=_fake_openai(captured)
    )
    out = await client.embed(["alpha", "beta"])

    assert captured["model"] == "text-embedding-3-small"
    assert captured["input"] == ["alpha", "beta"]  # one batch request
    assert captured["calls"] == 1
    assert out == [[0.0, 0.0, 0.0, 0.0], [1.0, 1.0, 1.0, 1.0]]


async def test_embed_reorders_by_index() -> None:
    captured: dict = {}
    client = HttpOpenAIEmbeddingClient(
        api_key="sk-x", model="m", client=_fake_openai(captured, reverse=True)
    )
    out = await client.embed(["a", "b", "c"])
    # Even though the fake returned data reversed, output is index-ordered.
    assert out == [[0.0] * 4, [1.0] * 4, [2.0] * 4]


async def test_embed_empty_short_circuits_without_call() -> None:
    captured: dict = {}
    client = HttpOpenAIEmbeddingClient(api_key="sk-x", model="m", client=_fake_openai(captured))
    out = await client.embed([])
    assert out == []
    assert "calls" not in captured  # no network call for an empty batch


async def test_partial_response_raises() -> None:
    """A partial response (fewer vectors than inputs) must raise, not silently
    drop or IndexError downstream."""

    class _Messages:
        async def create(self, *, model, input):
            # Return one fewer embedding than requested.
            data = [
                types.SimpleNamespace(index=i, embedding=[0.0] * 4) for i in range(len(input) - 1)
            ]
            return types.SimpleNamespace(data=data)

    client = HttpOpenAIEmbeddingClient(
        api_key="sk-x", model="m", client=types.SimpleNamespace(embeddings=_Messages())
    )
    with pytest.raises(RuntimeError, match="2 vectors for 3 inputs"):
        await client.embed(["a", "b", "c"])


async def test_empty_key_raises_config_error() -> None:
    client = HttpOpenAIEmbeddingClient(api_key="", model="m")
    with pytest.raises(EmbeddingConfigError, match="OPENAI_API_KEY is empty"):
        await client.embed(["x"])


async def test_empty_key_but_empty_input_does_not_raise() -> None:
    # No inputs -> no call -> the empty-key guard is never reached.
    client = HttpOpenAIEmbeddingClient(api_key="", model="m")
    assert await client.embed([]) == []


async def test_fake_returns_canned_vectors_and_counts() -> None:
    fake = FakeEmbeddingClient()
    out = await fake.embed(["a", "b"])
    assert len(out) == 2
    assert all(len(v) == EMBEDDING_DIM for v in out)
    assert fake.calls == 1
    assert fake.embedded == ["a", "b"]
    # Empty batch: no call, no recorded texts.
    assert await fake.embed([]) == []
    assert fake.calls == 1


async def test_fake_raises_seeded_error() -> None:
    fake = FakeEmbeddingClient(error=EmbeddingConfigError("boom"))
    with pytest.raises(EmbeddingConfigError):
        await fake.embed(["a"])
