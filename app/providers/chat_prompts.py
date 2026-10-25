"""Prompts shared by every chat model, so Claude and a local model are held to the same rules."""

from __future__ import annotations

import re

from app.providers.base import ChatTurn, EvidenceChunk

CHAT_SYSTEM = (
    "You are a research assistant for one person's collection of sources: documents, web pages, "
    "transcripts, images, and recordings. Answer only from the numbered passages supplied with each "
    "question. Never use outside knowledge, and never state something the passages do not support.\n\n"
    "Cite every claim by the number of the passage it comes from, in square brackets right after the "
    "claim, like this: [2]. Cite several passages as [1][3]. Use only numbers that appear in the "
    "passages.\n\n"
    "If the passages do not answer the question, say plainly that the collection does not cover it, "
    "and do not cite anything. Earlier turns of the conversation are there to show you what the user "
    "means; they are not a source of facts. Answer in the language of the question, concisely."
)

REWRITE_SYSTEM = (
    "You turn a follow-up question into a standalone search query for a document search engine. "
    "Use the conversation only to resolve what the question refers to. Return only the query, on one "
    "line, with no explanation."
)

_MARKERS = re.compile(r"\s*\[\d+(?:\s*,\s*\d+)*\]")


def strip_markers(text: str) -> str:
    """Citation markers refer to the passages of their own turn, so they are removed from history."""
    return _MARKERS.sub("", text).strip()


def history_messages(history: list[ChatTurn]) -> list[dict[str, str]]:
    return [
        {"role": turn.role, "content": strip_markers(turn.content) if turn.role == "assistant" else turn.content}
        for turn in history
        if turn.content.strip()
    ]


def evidence_block(evidence: list[EvidenceChunk]) -> str:
    passages = []
    for number, chunk in enumerate(evidence, start=1):
        where = f", {chunk.citation}" if chunk.citation else ""
        passages.append(f"[{number}] {chunk.title}{where}\n{chunk.text}")
    return "\n\n".join(passages)


def answer_prompt(question: str, evidence: list[EvidenceChunk]) -> str:
    return f"PASSAGES:\n\n{evidence_block(evidence)}\n\nQUESTION:\n{question}"


def rewrite_prompt(history: list[ChatTurn], question: str) -> str:
    lines = [f"{turn.role.upper()}: {strip_markers(turn.content)}" for turn in history if turn.content.strip()]
    return "CONVERSATION:\n" + "\n".join(lines) + f"\n\nFOLLOW-UP QUESTION:\n{question}\n\nSTANDALONE QUERY:"


def clean_query(raw: str, fallback: str) -> str:
    """The first line of a model's rewrite, or the original question if the model gave nothing usable."""
    line = next((line.strip().strip('"') for line in raw.splitlines() if line.strip()), "")
    return line[:500] if line else fallback
