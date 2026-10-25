"""Confidence-aware routing for extracted knowledge (architecture section 26).

Three tiers rather than a single threshold:

    confidence > AUTO_ACCEPT_CONFIDENCE      -> accepted
    AUTO_REVIEW_CONFIDENCE .. AUTO_ACCEPT    -> secondary verification, then accepted or review
    confidence < AUTO_REVIEW_CONFIDENCE      -> needs review

Of the verification strategies the architecture lists, this implements the cheapest honest one:
check the mid-confidence values against the literal OCR and transcript text already stored. A value
that is spelled out in on-screen text or speech is corroborated and promoted; one that rests on
inference alone is sent to a human. Running a second, stronger model is the natural upgrade and
fits the same interface.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.core.config import get_settings
from app.providers.base import ExtractionResult

CORROBORATING_EVIDENCE = {"ocr", "transcript"}


class ReviewDecision(str, Enum):
    accepted = "accepted"
    verified = "verified"
    needs_review = "needs_review"


@dataclass(frozen=True)
class VerificationOutcome:
    decision: ReviewDecision
    reason: str
    lowest_confidence: float | None

    @property
    def requires_review(self) -> bool:
        return self.decision is ReviewDecision.needs_review


def verify_extractions(
    results: list[ExtractionResult], *, transcript: str = "", ocr_text: str = ""
) -> VerificationOutcome:
    """Decide whether extracted knowledge can be trusted without a human."""
    settings = get_settings()
    if not results:
        return VerificationOutcome(ReviewDecision.needs_review, "No structured knowledge was extracted", None)

    confidences = [result.confidence for result in results if result.confidence is not None]
    if not confidences:
        return VerificationOutcome(
            ReviewDecision.needs_review, "Extracted knowledge carries no confidence value", None
        )

    lowest = min(confidences)
    if lowest >= settings.auto_accept_confidence:
        return VerificationOutcome(ReviewDecision.accepted, "Confidence above the auto-accept threshold", lowest)
    if lowest < settings.auto_review_confidence:
        return VerificationOutcome(
            ReviewDecision.needs_review, "Confidence below the review threshold", lowest
        )

    corpus = f"{transcript}\n{ocr_text}".lower()
    uncorroborated = [
        f"{result.content_type}.{field}"
        for result in results
        if result.confidence is not None and result.confidence < settings.auto_accept_confidence
        for field, value in _verifiable_fields(result)
        if not _is_corroborated(result, field, value, corpus)
    ]
    if uncorroborated:
        return VerificationOutcome(
            ReviewDecision.needs_review,
            "Secondary verification found no literal evidence for: " + ", ".join(sorted(uncorroborated)[:5]),
            lowest,
        )
    return VerificationOutcome(
        ReviewDecision.verified, "Secondary verification corroborated every field against stored evidence", lowest
    )


def _verifiable_fields(result: ExtractionResult) -> list[tuple[str, object]]:
    """Scalar fields worth checking; nested structures and the evidence map are not values."""
    return [
        (field, value)
        for field, value in result.data.items()
        if field != "evidence" and isinstance(value, (str, int, float)) and not isinstance(value, bool)
    ]


def _is_corroborated(result: ExtractionResult, field: str, value: object, corpus: str) -> bool:
    evidence = result.data.get("evidence")
    if isinstance(evidence, dict) and evidence.get(field) in CORROBORATING_EVIDENCE:
        return True
    text = str(value).strip().lower()
    return bool(text) and text in corpus
