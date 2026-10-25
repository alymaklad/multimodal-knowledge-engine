"""Claude-backed grounded answering and report narration.

The contract these implement is stricter than "answer the question": every claim must trace back to
a Reel that was actually retrieved. That is enforced three ways - the evidence is the only material
supplied, the prompt forbids outside knowledge, and the returned citations are intersected with the
evidence that was passed in, so a hallucinated Reel id cannot reach the caller.
"""

from __future__ import annotations

import json
from typing import Any

from app.core.config import get_settings
from app.providers.base import AnswerProvider, EvidenceChunk, GroundedAnswer
from app.providers.answering import NO_EVIDENCE_ANSWER
from app.providers.claude import build_anthropic_client, structured_payload

ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "answer": {
            "type": "string",
            "description": "The answer, written only from the supplied Reels.",
        },
        "grounded": {
            "type": "boolean",
            "description": "False when the evidence does not answer the question.",
        },
        "cited_reel_ids": {
            "type": "array",
            "items": {"type": "integer"},
            "description": "Ids of the Reels the answer actually used.",
        },
    },
    "required": ["answer", "grounded", "cited_reel_ids"],
    "additionalProperties": False,
}

REPORT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "What this collection is about, in a short paragraph."},
        "themes": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Learning themes visible in the aggregated counts.",
        },
        "observations": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Notable patterns, gaps, or concentrations.",
        },
    },
    "required": ["summary", "themes", "observations"],
    "additionalProperties": False,
}

ANSWER_SYSTEM = (
    "You answer questions about a person's own saved videos. The evidence supplied in the message is "
    "the only thing you know about them. Never use outside knowledge, never describe a video that is "
    "not in the evidence, and never state a fact the evidence does not support. If the evidence does "
    "not answer the question, set grounded to false and say plainly that nothing saved covers it. "
    "Cite the Reel ids you used."
)

REPORT_SYSTEM = (
    "You summarise a precomputed statistical profile of someone's saved video collection. You receive "
    "counts and aggregates, not the videos themselves. Describe only what the numbers show. Do not "
    "invent topics, creators, or technologies that are absent from the aggregates."
)


class ClaudeAnswerProvider(AnswerProvider):
    """Composes over retrieved evidence instead of quoting one sentence per Reel."""

    def __init__(self, model: str | None = None, client: Any | None = None) -> None:
        settings = get_settings()
        self._model = model or settings.anthropic_model
        self._max_tokens = settings.anthropic_max_tokens
        self._client = client if client is not None else build_anthropic_client()

    def answer(self, question: str, evidence: list[EvidenceChunk]) -> GroundedAnswer:
        if not evidence:
            # Not worth a request: with nothing retrieved there is nothing that could be grounded.
            return GroundedAnswer(answer=NO_EVIDENCE_ANSWER, grounded=False, model=self._model)

        response = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=ANSWER_SYSTEM,
            messages=[{"role": "user", "content": _answer_prompt(question, evidence)}],
            output_config={"format": {"type": "json_schema", "schema": ANSWER_SCHEMA}},
        )
        payload = structured_payload(response)
        if not payload.get("grounded", False):
            return GroundedAnswer(answer=payload.get("answer") or NO_EVIDENCE_ANSWER, grounded=False, model=self._model)

        # A citation the retriever never supplied is dropped rather than trusted.
        available = {chunk.reel_id for chunk in evidence}
        cited = [reel_id for reel_id in payload.get("cited_reel_ids", []) if reel_id in available]
        if not cited:
            return GroundedAnswer(answer=NO_EVIDENCE_ANSWER, grounded=False, model=self._model)
        return GroundedAnswer(
            answer=payload["answer"], grounded=True, model=self._model, cited_reel_ids=cited
        )


class ClaudeReportNarrator:
    """Turns precomputed aggregates into prose (architecture section 45).

    The model never sees the Reels themselves - only counts already computed in the database - which
    keeps report cost flat as a collection grows.
    """

    def __init__(self, model: str | None = None, client: Any | None = None) -> None:
        settings = get_settings()
        self._model = model or settings.anthropic_model
        self._max_tokens = settings.anthropic_max_tokens
        self._client = client if client is not None else build_anthropic_client()

    def narrate(self, aggregates: dict[str, Any]) -> dict[str, Any]:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=REPORT_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Aggregated profile of this collection:\n"
                        + json.dumps(aggregates, ensure_ascii=False, indent=2, default=str)
                        + "\n\nSummarise what this collection covers, name the learning themes the "
                        "counts support, and note any concentrations or gaps. Describe only what "
                        "these numbers show."
                    ),
                }
            ],
            output_config={"format": {"type": "json_schema", "schema": REPORT_SCHEMA}},
        )
        payload = structured_payload(response)
        payload["model"] = self._model
        return payload


def _answer_prompt(question: str, evidence: list[EvidenceChunk]) -> str:
    blocks = [
        f"[Reel {chunk.reel_id}] ({chunk.instagram_url}) evidence type: {chunk.content_type or 'mixed'}\n{chunk.text}"
        for chunk in evidence
    ]
    return (
        "QUESTION:\n" + question + "\n\n"
        "EVIDENCE FROM THE SAVED REELS:\n" + "\n\n".join(blocks) + "\n\n"
        "Answer the question using only the evidence above, and list the Reel ids you used."
    )
