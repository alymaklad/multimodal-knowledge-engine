from pydantic import BaseModel


class IndexResult(BaseModel):
    # None when a single source was reindexed: its vectors serve every collection containing it.
    collection_id: int | None = None
    indexed_sources: int
    indexed_chunks: int
    model: str


class DuplicatePair(BaseModel):
    source_id: int
    title: str
    duplicate_source_id: int
    duplicate_title: str
    similarity: float
