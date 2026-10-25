"""Reranking, the stage that follows fusion in architecture section 43.

Fusion decides which candidates are plausible; reranking decides their final order by scoring each
candidate against the query directly. A cross-encoder sees the query and the passage together,
which is why it can order results that bi-encoder similarity ranks nearly the same.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from functools import lru_cache

from app.core.config import get_settings
from app.core.gpu import half_precision_model_kwargs


class RerankProvider(ABC):
    @property
    @abstractmethod
    def model(self) -> str: ...

    @abstractmethod
    def score(self, query: str, passages: list[str]) -> list[float]:
        """A relevance score per passage, higher being more relevant."""


class IdentityReranker(RerankProvider):
    """Keeps the fusion order. The default, so retrieval never silently depends on model weights."""

    @property
    def model(self) -> str:
        return "identity"

    def score(self, query: str, passages: list[str]) -> list[float]:
        # Descending scores preserve the incoming order under a stable sort.
        return [float(len(passages) - index) for index in range(len(passages))]


@lru_cache(maxsize=2)
def _load_cross_encoder(model_name: str, fp16: bool):
    """Cached per process; a reranker is constructed on every hybrid search.

    `fp16` is part of the cache key so a changed setting cannot be served a model in the old dtype.
    """
    try:
        from sentence_transformers import CrossEncoder
    except ImportError as error:  # pragma: no cover - depends on optional install
        raise RuntimeError(
            "Install sentence-transformers to enable reranking, or set RERANKER=none"
        ) from error
    model_kwargs = half_precision_model_kwargs(fp16)
    if model_kwargs:
        return CrossEncoder(model_name, model_kwargs=model_kwargs)
    return CrossEncoder(model_name)


class CrossEncoderReranker(RerankProvider):
    """A sentence-transformers cross-encoder, e.g. BAAI/bge-reranker-base."""

    def __init__(self, model_name: str | None = None) -> None:
        settings = get_settings()
        self._model_name = model_name or settings.reranker_model
        self._model = _load_cross_encoder(self._model_name, settings.retrieval_fp16)

    @property
    def model(self) -> str:
        return self._model_name

    def score(self, query: str, passages: list[str]) -> list[float]:  # pragma: no cover - needs weights
        if not passages:
            return []
        return [float(value) for value in self._model.predict([(query, passage) for passage in passages])]


def get_rerank_provider() -> RerankProvider:
    name = get_settings().reranker.lower()
    if name in {"none", "identity", ""}:
        return IdentityReranker()
    if name in {"cross_encoder", "cross-encoder", "bge"}:
        return CrossEncoderReranker()
    raise ValueError(f"Unsupported reranker: {name}")
