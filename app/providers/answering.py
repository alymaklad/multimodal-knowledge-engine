from __future__ import annotations

import re

from app.providers.base import AnswerProvider, EvidenceChunk, GroundedAnswer

_SENTENCE_PATTERN = re.compile(r"(?<=[.!?])\s+|\n+")
_STOPWORDS = {
    "a", "about", "all", "and", "any", "are", "did", "do", "does", "explain", "explains", "find",
    "for", "from", "how", "i", "in", "is", "it", "me", "my", "of", "on", "or", "saved", "show",
    "some", "that", "the", "to", "videos", "what", "which", "who", "with",
}

NO_EVIDENCE_ANSWER = (
    "No saved Reel in this collection contains evidence for that question. "
    "Ingest and analyse more Reels, or rephrase the question using terms that appear in them."
)


class ExtractiveAnswerProvider(AnswerProvider):
    """Answers by quoting retrieved evidence, so nothing outside the collection can be asserted.

    An LLM provider implementing the same contract can replace this; the guarantee to preserve is
    that every sentence in the answer traces back to a cited Reel.
    """

    model = "local-extractive-v1"

    def answer(self, question: str, evidence: list[EvidenceChunk]) -> GroundedAnswer:
        if not evidence:
            return GroundedAnswer(answer=NO_EVIDENCE_ANSWER, grounded=False, model=self.model)
        terms = _query_terms(question)
        lines: list[str] = []
        cited: list[int] = []
        for chunk in evidence:
            sentence = _best_sentence(chunk.text, terms)
            if not sentence:
                continue
            if chunk.reel_id not in cited:
                cited.append(chunk.reel_id)
            lines.append(f"- Reel {chunk.reel_id} ({chunk.instagram_url}): {sentence}")
            if len(lines) >= 5:
                break
        if not lines:
            return GroundedAnswer(answer=NO_EVIDENCE_ANSWER, grounded=False, model=self.model)
        body = "\n".join(lines)
        return GroundedAnswer(
            answer=f"Based only on the saved Reels in this collection:\n{body}",
            grounded=True,
            model=self.model,
            cited_reel_ids=cited,
        )


def _query_terms(question: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]+", question.lower()) if word not in _STOPWORDS and len(word) > 2}


def _best_sentence(text: str, terms: set[str]) -> str:
    sentences = [sentence.strip() for sentence in _SENTENCE_PATTERN.split(text) if sentence.strip()]
    if not sentences:
        return ""
    scored = [
        (sum(1 for term in terms if term in sentence.lower()), -index, sentence)
        for index, sentence in enumerate(sentences)
    ]
    overlap, _, sentence = max(scored)
    if overlap == 0:
        sentence = sentences[0]
    return sentence[:400]
