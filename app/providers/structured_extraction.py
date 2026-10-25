"""Extraction that reads the vision model's structured fields instead of re-parsing its prose.

A vision-language model already returns `exercise`, `sets`, `reps`, `equipment` and the rest as
typed fields. The rule-based extractor then threw those away and ran regexes over the description
text, which produced records like `exercise: "performed by an animated character"` — a sentence
fragment lifted out of a caption. Reading the fields directly removes a lossy round-trip through
English.

**Evidence tags are re-derived, not trusted.** Observed with Qwen2.5-VL-3B: it labelled every field
`visual`, including reps that came from on-screen text and a technique note quoted verbatim from the
transcript. Since the OCR and transcript text are both available here, each value is checked against
them and attributed to where it can actually be found. The model's own claim is only used when
neither corroborates it — and then it is downgraded to `inferred`, because a value nothing
corroborates is exactly the one a reviewer should question.
"""

from __future__ import annotations

import logging
from typing import Any

from app.providers.base import ExtractionProvider, ExtractionResult, VisionResult

logger = logging.getLogger(__name__)

WORKOUT_FIELDS = (
    "exercise",
    "sets",
    "reps",
    "equipment",
    "target_muscles",
    "technique_notes",
    "tempo",
    "duration_seconds",
    "rest_seconds",
    "warnings",
)
AI_FIELDS = ("topic", "technologies")

# Short values match too easily by accident ("3" appears in almost any transcript), so corroboration
# is only claimed for values long enough for the match to mean something.
MIN_CORROBORATION_LENGTH = 4


class VisionFieldExtractionProvider(ExtractionProvider):
    """Turn the vision model's typed fields into structured records."""

    def __init__(self, fallback: ExtractionProvider | None = None) -> None:
        # Local/evidence vision providers return only a description; those still need the regexes.
        self._fallback = fallback

    def extract(self, vision: VisionResult, transcript: str, ocr_text: str) -> list[ExtractionResult]:
        analysis = vision.analysis or {}
        claimed = analysis.get("evidence") if isinstance(analysis.get("evidence"), dict) else {}

        records: list[ExtractionResult] = []
        workout = _subset(analysis, WORKOUT_FIELDS)
        if workout.get("exercise"):
            records.append(
                _record("workout_exercise", workout, claimed, transcript, ocr_text, vision, _workout_text)
            )

        knowledge = _subset(analysis, AI_FIELDS)
        if knowledge.get("topic") or knowledge.get("technologies"):
            records.append(
                _record("ai_knowledge", knowledge, claimed, transcript, ocr_text, vision, _knowledge_text)
            )

        if records:
            return records

        # Nothing structured came back — fall through rather than returning an empty result, so a
        # local vision provider still yields whatever the deterministic patterns can find.
        if self._fallback is not None:
            logger.debug("No structured vision fields; using the fallback extractor")
            return self._fallback.extract(vision, transcript, ocr_text)
        return []


def _record(
    content_type: str,
    fields: dict[str, Any],
    claimed: dict[str, Any],
    transcript: str,
    ocr_text: str,
    vision: VisionResult,
    render: Any,
) -> ExtractionResult:
    data = dict(fields)
    data["evidence"] = {
        field: _attribute(value, claimed.get(field), transcript, ocr_text)
        for field, value in fields.items()
    }
    return ExtractionResult(
        content_type=content_type,
        data=data,
        text_representation=render(fields),
        # The model's own confidence, passed through unchanged. It is a claim, not a measurement:
        # this model reports high confidence readily, which is why the evidence tags above are
        # derived independently and the review queue exists.
        confidence=vision.confidence,
    )


def _attribute(value: Any, claimed: Any, transcript: str, ocr_text: str) -> str:
    """Where can this value actually be found?"""
    ocr_normalised = _normalise(ocr_text)
    transcript_normalised = _normalise(transcript)
    for candidate in _searchable(value):
        if candidate in ocr_normalised:
            return "ocr"
        if candidate in transcript_normalised:
            return "transcript"
    if claimed in {"visual", "metadata"}:
        return str(claimed)
    return "inferred"


def _normalise(text: str) -> str:
    """Lowercase alphanumerics only.

    OCR and the vision model disagree about spacing and punctuation constantly - PP-OCR read a
    caption as `BULGARIANSPLITSQUAT` while the model wrote `Bulgarian Split Squat`. Those are the
    same evidence, and a plain substring test would call the second one unattributable and label a
    genuinely on-screen value `visual`. Whitespace is an OCR artefact, not a difference in meaning.
    """
    return "".join(character for character in text.lower() if character.isalnum())


def _searchable(value: Any) -> list[str]:
    items = value if isinstance(value, list) else [value]
    out = []
    for item in items:
        text = _normalise(str(item))
        if len(text) >= MIN_CORROBORATION_LENGTH:
            out.append(text)
    return out


def _subset(analysis: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {
        field: analysis[field]
        for field in fields
        if analysis.get(field) not in (None, "", [], {})
    }


def _workout_text(fields: dict[str, Any]) -> str:
    """Readable sentences, because this string is what gets embedded and shown as evidence."""
    parts = [f"{fields['exercise']}."]
    if fields.get("sets"):
        parts.append(f"{fields['sets']} sets.")
    if fields.get("reps"):
        parts.append(f"{fields['reps']} repetitions.")
    if fields.get("target_muscles"):
        parts.append("Targets " + _join(fields["target_muscles"]) + ".")
    if fields.get("equipment"):
        parts.append("Uses " + _join(fields["equipment"]) + ".")
    if fields.get("tempo"):
        parts.append(f"Tempo: {fields['tempo']}.")
    if fields.get("technique_notes"):
        parts.append("Technique: " + "; ".join(str(note) for note in fields["technique_notes"]) + ".")
    if fields.get("warnings"):
        parts.append("Warnings: " + "; ".join(str(item) for item in fields["warnings"]) + ".")
    return " ".join(parts)


def _knowledge_text(fields: dict[str, Any]) -> str:
    parts = []
    if fields.get("topic"):
        parts.append(f"Topic: {fields['topic']}.")
    if fields.get("technologies"):
        parts.append("Technologies: " + _join(fields["technologies"]) + ".")
    return " ".join(parts)


def _join(values: Any) -> str:
    items = [str(value) for value in (values if isinstance(values, list) else [values])]
    if len(items) > 1:
        return ", ".join(items[:-1]) + " and " + items[-1]
    return items[0] if items else ""
