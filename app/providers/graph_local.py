"""Knowledge-graph extraction and summaries without a language model.

Entities are the names a passage uses: acronyms and identifiers (BM25, DPR, monoBERT, nDCG) and
capitalised names (Qdrant, Karpukhin, Microsoft Research). A capitalised word that only ever starts a
sentence is ignored, since that is grammar rather than a name. Two entities named in the same
sentence, or in consecutive sentences, are linked, which is weaker than a stated relationship but cheap, deterministic, and enough
to connect sources that talk about the same things.

Summaries are extractive: the source's own sentences that mention its most central entities, in their
original order. Nothing is paraphrased, so a summary can never claim what the source does not say.
"""

from __future__ import annotations

import re
from collections import Counter
from itertools import combinations

from app.providers.base import (
    ExtractedEntity,
    ExtractedRelation,
    GraphExtraction,
    GraphExtractor,
    SourceSummary,
    Summarizer,
)

_PARAGRAPH = re.compile(r"\n\s*\n")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_LIST_MARKER = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
# Identifiers with an internal capital or digit: BM25, DPR, HNSW, monoBERT, nDCG, GPT-4.
_IDENTIFIER = re.compile(r"\b(?:[A-Z]{2,}[A-Za-z0-9]*|[a-z]+[A-Z]{2,}[A-Za-z0-9]*|[A-Z][a-z]*[A-Z0-9][A-Za-z0-9]*)(?:-[A-Za-z0-9]+)?\b")
_NAME = re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b")
_COMMON = {
    "The", "A", "An", "This", "That", "These", "Those", "It", "Its", "In", "On", "For", "Of", "And", "But",
    "Or", "If", "When", "Each", "Every", "Because", "Once", "Plain", "For", "Most", "Some", "Values",
    "Systems", "Documents", "Chunks", "Collections", "Public", "Adding", "Combined", "Indexing",
    "What", "Why", "How", "Which", "Where", "Who", "They", "We", "Our", "There", "Then", "Both", "Many",
    "Such", "After",
}
MAX_PAIRS_PER_SENTENCE = 6


def split_sentences(text: str) -> list[str]:
    """Sentences of prose, with hard-wrapped lines rejoined and headings left out.

    A heading is a label, not a statement, so it neither names entities in context nor belongs in a
    summary.
    """
    sentences: list[str] = []
    for paragraph in _PARAGRAPH.split(text):
        lines = [_LIST_MARKER.sub("", line).strip() for line in paragraph.splitlines()]
        lines = [line for line in lines if line and not line.startswith("#")]
        if lines:
            sentences += [sentence for sentence in _SENTENCE_END.split(" ".join(lines)) if sentence]
    return sentences


def normalize_entity(name: str) -> str:
    return " ".join(re.sub(r"'s\b", "", name).lower().split())


class RuleGraphExtractor(GraphExtractor):
    model = "rule-graph-v1"

    def extract(self, text: str) -> GraphExtraction:
        sentences = split_sentences(text)
        mid_sentence_names = {
            match.group(0)
            for sentence in sentences
            for match in _NAME.finditer(sentence)
            if match.start() > 0
        }
        # A word that opens several sentences, or also titles a heading, and is never written in
        # lowercase is a name: "## Qdrant" followed by "Qdrant is written in Rust."
        lowercase = set(re.findall(r"\b[a-z]+\b", text))
        headings = {line.lstrip("#").strip() for line in text.splitlines() if line.lstrip().startswith("#")}
        openers = Counter(match.group(0) for sentence in sentences if (match := _NAME.match(sentence)))
        mid_sentence_names |= {
            name for name, count in openers.items()
            if (count >= 2 or name in headings)
            and " " not in name and name.lower() not in lowercase and name not in _COMMON
        }
        entities: dict[str, ExtractedEntity] = {}
        relations: list[ExtractedRelation] = []
        previous: dict[str, str] = {}
        for sentence in sentences:
            found: dict[str, str] = {}
            for match in _IDENTIFIER.finditer(sentence):
                found.setdefault(normalize_entity(match.group(0)), match.group(0))
            for match in _NAME.finditer(sentence):
                name = match.group(0)
                words = name.split()
                if match.start() == 0 and name not in mid_sentence_names:
                    # A sentence-initial word is usually grammar; keep the rest of a multi-word name.
                    if len(words) == 1:
                        continue
                    name = " ".join(words[1:]) if words[0] in _COMMON else name
                if name in _COMMON or len(name) < 3:
                    continue
                found.setdefault(normalize_entity(name), name)
            for key, name in found.items():
                kind = "identifier" if _IDENTIFIER.fullmatch(name) else "name"
                entities.setdefault(key, ExtractedEntity(name=name, kind=kind))
            # A sentence often continues the previous one's subject ("Qdrant is ... It indexes with
            # HNSW"), so entities are also linked to those of the sentence before.
            pairs = list(combinations(sorted(found.values()), 2))
            pairs += [
                (left, right)
                for left in sorted(previous.values())
                for right in sorted(found.values())
                if normalize_entity(left) not in found
            ]
            relations += [
                ExtractedRelation(subject=left, label="mentioned with", object=right)
                for left, right in pairs[:MAX_PAIRS_PER_SENTENCE]
            ]
            previous = found
        return GraphExtraction(entities=list(entities.values()), relations=relations, model=self.model)


class ExtractiveSummarizer(Summarizer):
    model = "extractive-summary-v1"

    def __init__(self, sentences: int = 3, max_characters: int = 700) -> None:
        self._sentences = sentences
        self._max = max_characters
        self._extractor = RuleGraphExtractor()

    def summarize(self, title: str, passages: list[str]) -> SourceSummary:
        sentences = [
            sentence
            for passage in passages
            for sentence in split_sentences(passage)
            if len(sentence.split()) >= 6
        ]
        if not sentences:
            return SourceSummary(text="", model=self.model)
        weights = Counter(
            normalize_entity(entity.name)
            for passage in passages
            for entity in self._extractor.extract(passage).entities
        )

        def score(item: tuple[int, str]) -> tuple[float, int]:
            index, sentence = item
            named = {normalize_entity(entity.name) for entity in self._extractor.extract(sentence).entities}
            # Central entities count most; earlier sentences break ties, since openings tend to frame.
            return (sum(weights[name] for name in named) + 1.0 / (index + 1), -index)

        ranked = sorted(enumerate(sentences), key=score, reverse=True)
        chosen: list[tuple[int, str]] = []
        length = 0
        for index, sentence in ranked:
            if len(chosen) >= self._sentences or length + len(sentence) > self._max:
                break
            if sentence not in {text for _, text in chosen}:
                chosen.append((index, sentence))
                length += len(sentence) + 1
        return SourceSummary(text=" ".join(sentence for _, sentence in sorted(chosen)), model=self.model)
