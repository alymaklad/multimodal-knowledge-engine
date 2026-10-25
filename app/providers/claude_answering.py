"""Claude-backed grounded answering.

The contract this implements is stricter than "answer the question": every claim must trace back to
a source that was actually retrieved. That is enforced three ways - the evidence is the only material
supplied, the prompt forbids outside knowledge, and the returned citations are intersected with the
evidence that was passed in, so a hallucinated source id cannot reach the caller.
"""

from __future__ import annotations

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
            "description": "The answer, written only from the supplied sources.",
        },
        "grounded": {
            "type": "boolean",
            "description": "False when the evidence does not answer the question.",
        },
        "cited_source_ids": {
            "type": "array",
            "items": {"type": "integer"},
            "description": "Ids of the sources the answer actually used.",
        },
    },
    "required": ["answer", "grounded", "cited_source_ids"],
    "additionalProperties": False,
}

ANSWER_SYSTEM = (
    "You answer questions about a person's own collection of sources: documents, web pages, images, "
    "audio, and video. The evidence supplied in the message is the only thing you know about them. "
    "Never use outside knowledge, never describe a source that is not in the evidence, and never state "
    "a fact the evidence does not support. If the evidence does not answer the question, set grounded "
    "to false and say plainly that nothing in the collection covers it. Cite the source ids you used."
)


class ClaudeAnswerProvider(AnswerProvider):
    """Composes over retrieved evidence instead of quoting one sentence per source."""

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
        available = {chunk.source_id for chunk in evidence}
        cited = [source_id for source_id in payload.get("cited_source_ids", []) if source_id in available]
        if not cited:
            return GroundedAnswer(answer=NO_EVIDENCE_ANSWER, grounded=False, model=self._model)
        return GroundedAnswer(
            answer=payload["answer"], grounded=True, model=self._model, cited_source_ids=cited
        )


def _answer_prompt(question: str, evidence: list[EvidenceChunk]) -> str:
    blocks = [
        f"[Source {chunk.source_id}] {chunk.title}"
        + (f" ({chunk.origin_url})" if chunk.origin_url else "")
        + f" evidence type: {chunk.content_type or 'mixed'}\n{chunk.text}"
        for chunk in evidence
    ]
    return (
        "QUESTION:\n" + question + "\n\n"
        "EVIDENCE FROM THE COLLECTION'S SOURCES:\n" + "\n\n".join(blocks) + "\n\n"
        "Answer the question using only the evidence above, and list the source ids you used."
    )
