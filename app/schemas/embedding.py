from pydantic import BaseModel


class IndexResult(BaseModel):
    collection_id: int
    indexed_reels: int
    indexed_chunks: int
    model: str


class DuplicatePair(BaseModel):
    reel_id: int
    instagram_url: str
    duplicate_reel_id: int
    duplicate_instagram_url: str
    similarity: float
