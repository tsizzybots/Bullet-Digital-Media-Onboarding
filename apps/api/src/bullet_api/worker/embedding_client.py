"""OpenAI embeddings seam (S1-30).

`EmbeddingClient` is a small Protocol so the worker depends on the interface,
not on the OpenAI SDK. `HttpOpenAIEmbeddingClient` is the production wiring
(async SDK, batch embed, one pooled client per process); `FakeEmbeddingClient`
returns canned vectors (or a seeded error) for tests. Mirrors the Anthropic
summariser seam in `worker/summary_client.py`.

Provider decision (approved 02/07/2026): OpenAI `text-embedding-3-small`, whose
native dimension is 1536 - an exact match for the `client_knowledge.embedding
vector(1536)` column. Anthropic has no embeddings API, so this is a distinct
vendor from the S1-29 summariser. No live key exists yet; the worker runs
against `FakeEmbeddingClient` and the live key is a pre-prod follow-up.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Protocol

from openai import AsyncOpenAI

from bullet_api.config import get_settings

# text-embedding-3-small's native dimension; matches the vector(1536) column.
EMBEDDING_DIM = 1536


class EmbeddingConfigError(RuntimeError):
    """A deployment-config failure (e.g. an empty OPENAI_API_KEY) that will not
    self-heal on retry. A SUBCLASS of RuntimeError so fail-loud callers still
    match, but a DISTINCT type the worker can catch precisely - so an unrelated,
    genuinely transient RuntimeError is NOT mis-classified as non-retriable."""


class EmbeddingClient(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed each input string, returning one vector per input IN ORDER.

        Called with only the NON-EMPTY value_texts (empty fields get a NULL
        embedding upstream), so an empty `texts` list returns `[]` without a
        network call.

        Raises:
            EmbeddingConfigError: the API key is empty (non-retriable).
            openai.APIStatusError / openai.APIConnectionError / APITimeoutError:
                propagate for the worker to classify retriable-vs-not.
        """
        ...


class HttpOpenAIEmbeddingClient:
    """Production embedder - OpenAI async SDK, batch embeddings, pooled client.

    Accepts an optional pre-built `client` so tests can inject a fake
    AsyncOpenAI; production passes None and the client is built from the api key
    once and cached on the instance. Fails loudly (EmbeddingConfigError) on an
    empty key rather than silently producing nothing.
    """

    def __init__(self, api_key: str, model: str, client: Any | None = None) -> None:
        self._api_key = api_key
        self._model = model
        self._client = client

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        client = self._client
        if client is None:
            if not self._api_key:
                raise EmbeddingConfigError(
                    "OPENAI_API_KEY is empty; cannot embed. Set it on the Render env group."
                )
            # Build once and cache on the instance; a per-call AsyncOpenAI opens
            # (and never closes) an httpx pool. Paired with the cached
            # get_embedding_client() factory, that is one client per process.
            client = AsyncOpenAI(api_key=self._api_key)
            self._client = client

        # One batch request for all non-empty texts. The API echoes an `index`
        # per embedding; sort by it so the returned order matches the input even
        # if the service ever reorders.
        response = await client.embeddings.create(model=self._model, input=texts)
        ordered = sorted(response.data, key=lambda d: d.index)
        # Guarantee one vector per input: a partial response would otherwise leave
        # the caller mapping vectors[pos] out of range (IndexError -> unclassified
        # retry). Raising here surfaces it as a retriable transport-class error.
        if len(ordered) != len(texts):
            raise RuntimeError(
                f"embeddings API returned {len(ordered)} vectors for {len(texts)} inputs"
            )
        return [d.embedding for d in ordered]


class FakeEmbeddingClient:
    """Test double. Returns a canned 1536-vector per input (or raises a seeded
    error); records the texts it was asked to embed and counts calls so tests
    can assert the embedder was (not) invoked and with what."""

    def __init__(
        self,
        vector: list[float] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.vector = vector if vector is not None else [0.1] * EMBEDDING_DIM
        self.error = error
        self.calls = 0
        self.embedded: list[str] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        self.calls += 1
        if self.error is not None:
            raise self.error
        self.embedded.extend(texts)
        return [list(self.vector) for _ in texts]


@lru_cache(maxsize=1)
def get_embedding_client() -> EmbeddingClient:
    """Worker factory, process-cached so the client (and its pooled AsyncOpenAI)
    is reused across runs. Tests inject a FakeEmbeddingClient into the core, so
    this cache is only exercised on the production path."""
    settings = get_settings()
    return HttpOpenAIEmbeddingClient(
        api_key=settings.openai_api_key, model=settings.openai_embedding_model
    )


__all__ = [
    "EMBEDDING_DIM",
    "EmbeddingClient",
    "EmbeddingConfigError",
    "FakeEmbeddingClient",
    "HttpOpenAIEmbeddingClient",
    "get_embedding_client",
]
