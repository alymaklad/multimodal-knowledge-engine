from pydantic import BaseModel, Field


class CollectionReport(BaseModel):
    collection_id: int
    total_reels: int
    analyzed_reels: int
    top_topics: list[tuple[str, int]]
    top_technologies: list[tuple[str, int]]
    top_creators: list[tuple[str, int]]
    top_content_types: list[tuple[str, int]]
    average_extraction_confidence: float | None


class NarratedCollectionReport(BaseModel):
    """The same aggregates, plus prose written from those aggregates alone."""

    report: CollectionReport
    summary: str
    themes: list[str]
    observations: list[str]
    model: str
    narrated: bool = Field(
        default=True,
        description="False when no narration provider is configured; the aggregates are still returned.",
    )
