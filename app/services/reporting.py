from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.analysis import ExtractedContent
from app.models.reel import Reel
from app.core.config import get_settings
from app.schemas.reporting import CollectionReport, NarratedCollectionReport


def build_collection_report(db: Session, collection_id: int) -> CollectionReport:
    reels = list(db.scalars(select(Reel).where(Reel.collection_id == collection_id)))
    entries = list(
        db.scalars(
            select(ExtractedContent)
            .join(Reel)
            .where(Reel.collection_id == collection_id, ExtractedContent.content_type == "ai_knowledge")
        )
    )
    topics: Counter[str] = Counter()
    technologies: Counter[str] = Counter()
    creators: Counter[str] = Counter()
    content_types: Counter[str] = Counter()
    confidences: list[float] = []
    for reel in reels:
        if reel.creator_username:
            creators[reel.creator_username] += 1
    for entry in entries:
        content_types[entry.content_type] += 1
        if entry.confidence is not None:
            confidences.append(entry.confidence)
        topic = entry.data.get("topic")
        if isinstance(topic, str) and topic.strip():
            topics[topic.strip()] += 1
        for technology in entry.data.get("technologies", []):
            if isinstance(technology, str) and technology.strip():
                technologies[technology.strip()] += 1
    return CollectionReport(
        collection_id=collection_id,
        total_reels=len(reels),
        analyzed_reels=len({entry.reel_id for entry in entries}),
        top_topics=topics.most_common(10),
        top_technologies=technologies.most_common(10),
        top_creators=creators.most_common(10),
        top_content_types=content_types.most_common(10),
        average_extraction_confidence=sum(confidences) / len(confidences) if confidences else None,
    )


UNNARRATED_SUMMARY = (
    "No narration provider is configured, so this report contains aggregates only. "
    "Set ANSWER_PROVIDER=claude to have the aggregates summarised."
)


def build_narrated_report(db: Session, collection_id: int, *, narrator=None) -> NarratedCollectionReport:
    """Aggregate in the database first, then summarise only the aggregates.

    The model never sees the Reels themselves, so report cost stays flat as a collection grows
    (architecture section 45). Without a narrator configured the aggregates are still returned,
    clearly marked as un-narrated rather than padded with invented prose.
    """
    report = build_collection_report(db, collection_id)
    if narrator is None:
        narrator = _default_narrator()
    if narrator is None:
        return NarratedCollectionReport(
            report=report,
            summary=UNNARRATED_SUMMARY,
            themes=[],
            observations=[],
            model="none",
            narrated=False,
        )
    narration = narrator.narrate(report.model_dump(mode="json"))
    return NarratedCollectionReport(
        report=report,
        summary=narration["summary"],
        themes=narration.get("themes", []),
        observations=narration.get("observations", []),
        model=narration.get("model", "unknown"),
    )


def _default_narrator():
    if get_settings().answer_provider.lower() not in {"claude", "anthropic"}:
        return None
    from app.providers.claude_answering import ClaudeReportNarrator

    return ClaudeReportNarrator()
