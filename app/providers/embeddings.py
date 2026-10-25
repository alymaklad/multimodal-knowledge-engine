from __future__ import annotations

import hashlib
import math
import re
from abc import ABC, abstractmethod
from functools import lru_cache

from app.core.config import get_settings
from app.core.gpu import half_precision_model_kwargs

_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")


class EmbeddingProvider(ABC):
    """Contract shared by the local baseline and a real dense model such as BGE-M3."""

    @property
    @abstractmethod
    def model(self) -> str: ...

    @property
    @abstractmethod
    def dimensions(self) -> int: ...

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]: ...

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]


class HashingEmbeddingProvider(EmbeddingProvider):
    """Dependency-free hashed bag-of-features vectoriser.

    This is a lexical signal projected into a fixed vector space, not a semantic model. It exists
    so that vector indexing, fusion, and retrieval are executable and testable without downloading
    model weights. Configure `EMBEDDING_PROVIDER=sentence_transformer` for genuine semantics.
    """

    def __init__(self, dimensions: int | None = None) -> None:
        self._dimensions = dimensions or get_settings().embedding_dimensions

    @property
    def model(self) -> str:
        return f"local-hashing-v1-{self._dimensions}"

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_single(text) for text in texts]

    def _embed_single(self, text: str) -> list[float]:
        vector = [0.0] * self._dimensions
        for feature, weight in _features(text):
            index = int.from_bytes(hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest(), "big")
            vector[index % self._dimensions] += weight
        return _l2_normalize(vector)


@lru_cache(maxsize=2)
def _load_sentence_transformer(model_name: str, fp16: bool):
    """Cached per process: this provider is constructed on every search and every indexed Reel,
    and loading the weights each time would dominate the work.

    `fp16` is part of the cache key so a changed setting cannot be served a model in the old dtype.
    """
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as error:  # pragma: no cover - depends on optional install
        raise RuntimeError(
            "Install sentence-transformers to enable dense embeddings, or set EMBEDDING_PROVIDER=hashing"
        ) from error
    model_kwargs = half_precision_model_kwargs(fp16)
    if model_kwargs:
        return SentenceTransformer(model_name, model_kwargs=model_kwargs)
    return SentenceTransformer(model_name)


class SentenceTransformerEmbeddingProvider(EmbeddingProvider):
    """BGE-M3 (or any sentence-transformers model) behind the same contract."""

    def __init__(self, model_name: str | None = None) -> None:
        settings = get_settings()
        self._model_name = model_name or settings.embedding_model
        self._model = _load_sentence_transformer(self._model_name, settings.retrieval_fp16)

    @property
    def model(self) -> str:
        return self._model_name

    @property
    def dimensions(self) -> int:
        # sentence-transformers 6.x renamed this; support both so the provider works on either.
        getter = getattr(self._model, "get_embedding_dimension", None) or self._model.get_sentence_embedding_dimension
        return int(getter())

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = self._model.encode(texts, normalize_embeddings=True)
        return [[float(value) for value in vector] for vector in vectors]


def get_embedding_provider() -> EmbeddingProvider:
    name = get_settings().embedding_provider.lower()
    if name in {"sentence_transformer", "sentence-transformers", "bge", "bge-m3"}:
        return SentenceTransformerEmbeddingProvider()
    if name == "hashing":
        return HashingEmbeddingProvider()
    raise ValueError(f"Unsupported embedding provider: {name}")


def _features(text: str) -> list[tuple[str, float]]:
    """Word unigrams plus character 4-grams, so near-miss spellings still overlap."""
    tokens = _TOKEN_PATTERN.findall(text.lower())
    features: list[tuple[str, float]] = [(f"w:{token}", 1.0) for token in tokens]
    for token in tokens:
        padded = f"^{token}$"
        for start in range(max(len(padded) - 3, 1)):
            features.append((f"c:{padded[start:start + 4]}", 0.35))
    return features


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


def _l2_normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        return vector
    return [value / norm for value in vector]
