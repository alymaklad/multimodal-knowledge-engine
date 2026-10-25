from __future__ import annotations

import logging

from abc import ABC, abstractmethod
from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.embedding import ContentEmbedding
from app.providers.embeddings import cosine_similarity

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VectorPoint:
    source_id: int
    chunk_key: str
    text: str
    vector: list[float]
    model: str
    content_type: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VectorMatch:
    source_id: int
    chunk_key: str
    content_type: str | None
    text: str
    score: float


class VectorStore(ABC):
    """Storage boundary so Qdrant can replace the relational baseline without touching callers."""

    @abstractmethod
    def replace_source(self, points: list[VectorPoint], *, source_id: int) -> None: ...

    @abstractmethod
    def delete_source(self, source_id: int) -> None: ...

    @abstractmethod
    def search(
        self, *, source_ids: Collection[int], vector: list[float], limit: int, content_type: str | None = None
    ) -> list[VectorMatch]:
        """Nearest chunks among the given sources; a collection is searched by passing its members."""


class DatabaseVectorStore(VectorStore):
    """Exact brute-force cosine search over persisted vectors.

    Exact rather than approximate, which is the right trade for a personal collection: recall is
    perfect and there is no second system to keep consistent. Swap in Qdrant once a collection
    outgrows scanning every stored chunk.
    """

    def __init__(self, db: Session) -> None:
        self._db = db

    def replace_source(self, points: list[VectorPoint], *, source_id: int) -> None:
        self._db.execute(delete(ContentEmbedding).where(ContentEmbedding.source_id == source_id))
        for point in points:
            self._db.add(
                ContentEmbedding(
                    source_id=point.source_id,
                    chunk_key=point.chunk_key,
                    content_type=point.content_type,
                    text=point.text,
                    vector=point.vector,
                    dimensions=len(point.vector),
                    model=point.model,
                )
            )

    def delete_source(self, source_id: int) -> None:
        self._db.execute(delete(ContentEmbedding).where(ContentEmbedding.source_id == source_id))

    def search(
        self, *, source_ids: Collection[int], vector: list[float], limit: int, content_type: str | None = None
    ) -> list[VectorMatch]:
        if not source_ids:
            return []
        statement = select(ContentEmbedding).where(ContentEmbedding.source_id.in_(list(source_ids)))
        if content_type is not None:
            statement = statement.where(ContentEmbedding.content_type == content_type)
        matches = []
        stale = 0
        for row in self._db.scalars(statement):
            stored = list(row.vector or [])
            # Changing EMBEDDING_PROVIDER changes the dimension. Old rows would otherwise score
            # zero forever and simply vanish from results, which looks like "no matches" rather
            # than "this collection needs reindexing".
            if len(stored) != len(vector):
                stale += 1
                continue
            matches.append(
                VectorMatch(
                    source_id=row.source_id,
                    chunk_key=row.chunk_key,
                    content_type=row.content_type,
                    text=row.text,
                    score=cosine_similarity(vector, stored),
                )
            )
        if stale:
            logger.warning(
                "Ignoring %s embedding rows whose dimension does not match the current provider; "
                "reindex with POST /api/collections/{id}/index",
                stale,
            )
        ranked = [match for match in matches if match.score > 0.0]
        return sorted(ranked, key=lambda match: (-match.score, match.source_id, match.chunk_key))[:limit]


class QdrantVectorStore(VectorStore):
    """Qdrant-backed store using source-scoped payload filters."""

    def __init__(self, dimensions: int, *, db: Session | None = None) -> None:
        try:
            from qdrant_client import QdrantClient
            from qdrant_client.http import models as qdrant_models
        except ImportError as error:  # pragma: no cover - depends on optional install
            raise RuntimeError(
                "Install qdrant-client to enable Qdrant retrieval, or set VECTOR_STORE=database"
            ) from error
        settings = get_settings()
        self._models = qdrant_models
        self._client = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key)
        self._collection = settings.qdrant_collection
        self._db = db
        self._ensure_collection(dimensions)

    def _ensure_collection(self, dimensions: int) -> None:  # pragma: no cover - requires a server
        if self._client.collection_exists(self._collection):
            return
        self._client.create_collection(
            collection_name=self._collection,
            vectors_config=self._models.VectorParams(size=dimensions, distance=self._models.Distance.COSINE),
        )

    def replace_source(self, points: list[VectorPoint], *, source_id: int) -> None:  # pragma: no cover
        self.delete_source(source_id)
        if self._db is not None:
            DatabaseVectorStore(self._db).replace_source(points, source_id=source_id)
        if not points:
            return
        self._client.upsert(
            collection_name=self._collection,
            points=[
                self._models.PointStruct(
                    id=_point_id(point.source_id, point.chunk_key),
                    vector=point.vector,
                    payload={
                        "source_id": point.source_id,
                        "chunk_key": point.chunk_key,
                        "content_type": point.content_type,
                        "text": point.text,
                        **point.payload,
                    },
                )
                for point in points
            ],
        )

    def delete_source(self, source_id: int) -> None:  # pragma: no cover - requires a server
        if self._db is not None:
            DatabaseVectorStore(self._db).delete_source(source_id)
        self._client.delete(
            collection_name=self._collection,
            points_selector=self._models.FilterSelector(
                filter=self._models.Filter(
                    must=[self._models.FieldCondition(key="source_id", match=self._models.MatchValue(value=source_id))]
                )
            ),
        )

    def search(  # pragma: no cover - requires a server
        self, *, source_ids: Collection[int], vector: list[float], limit: int, content_type: str | None = None
    ) -> list[VectorMatch]:
        if not source_ids:
            return []
        conditions = [
            self._models.FieldCondition(key="source_id", match=self._models.MatchAny(any=list(source_ids)))
        ]
        if content_type is not None:
            conditions.append(
                self._models.FieldCondition(key="content_type", match=self._models.MatchValue(value=content_type))
            )
        response = self._client.query_points(
            collection_name=self._collection,
            query=vector,
            query_filter=self._models.Filter(must=conditions),
            limit=limit,
            with_payload=True,
        )
        return [
            VectorMatch(
                source_id=int(point.payload["source_id"]),
                chunk_key=str(point.payload.get("chunk_key", "")),
                content_type=point.payload.get("content_type"),
                text=str(point.payload.get("text", "")),
                score=float(point.score),
            )
            for point in response.points
        ]


def get_vector_store(db: Session, *, dimensions: int) -> VectorStore:
    name = get_settings().vector_store.lower()
    if name == "qdrant":
        return QdrantVectorStore(dimensions, db=db)
    if name == "database":
        return DatabaseVectorStore(db)
    raise ValueError(f"Unsupported vector store: {name}")


def _point_id(source_id: int, chunk_key: str) -> str:
    from uuid import NAMESPACE_URL, uuid5

    return str(uuid5(NAMESPACE_URL, f"source/{source_id}/{chunk_key}"))
