"""Knowledge-graph extraction and source summaries on a language model (Claude or Ollama).

Both providers return the same structured output, constrained by a JSON schema, so the graph
service does not care which one produced it. Every entity and relationship comes from one chunk, so
the chunk stays the citation for anything the graph links.
"""

from __future__ import annotations

import json
from typing import Any

from app.core.config import get_settings
from app.providers.base import (
    ExtractedEntity,
    ExtractedRelation,
    GraphExtraction,
    GraphExtractor,
    SourceSummary,
    Summarizer,
)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
ENTITY_KINDS = ["person", "organization", "method", "system", "dataset", "metric", "concept", "place", "other"]

GRAPH_SYSTEM = (
    "You build a knowledge graph from one passage. List the specific named things it mentions: people, "
    "organizations, methods, systems, datasets, metrics, and technical concepts. Skip generic nouns. "
    "List the relationships the passage states between them as short verb phrases, such as "
    "'introduced', 'evaluated', 'is written in'. Use names exactly as the passage writes them, and "
    "never add a relationship the passage does not state."
)
GRAPH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "kind": {"type": "string", "enum": ENTITY_KINDS}},
                "required": ["name", "kind"],
                "additionalProperties": False,
            },
        },
        "relations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string"},
                    "label": {"type": "string"},
                    "object": {"type": "string"},
                },
                "required": ["subject", "label", "object"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["entities", "relations"],
    "additionalProperties": False,
}

SUMMARY_SYSTEM = (
    "You write a short overview of one source for someone deciding whether to read it: what it is "
    "about and its main points, in two to four plain sentences. Use only what the passages say."
)
SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
    "additionalProperties": False,
}
# Passages beyond this are left out of the summary prompt; the opening of a source frames it best.
SUMMARY_MAX_CHARACTERS = 24_000


def summary_prompt(title: str, passages: list[str]) -> str:
    text, used = [], 0
    for passage in passages:
        if used + len(passage) > SUMMARY_MAX_CHARACTERS:
            break
        text.append(passage)
        used += len(passage)
    return f"SOURCE: {title}\n\nPASSAGES:\n" + "\n\n".join(text)


def parse_extraction(payload: dict, model: str) -> GraphExtraction:
    entities = [
        ExtractedEntity(name=item["name"].strip(), kind=item.get("kind", "other"))
        for item in payload.get("entities", [])
        if item.get("name", "").strip()
    ]
    relations = [
        ExtractedRelation(subject=item["subject"].strip(), label=item["label"].strip(), object=item["object"].strip())
        for item in payload.get("relations", [])
        if item.get("subject", "").strip() and item.get("object", "").strip()
    ]
    return GraphExtraction(entities=entities, relations=relations, model=model)


class _ClaudeStructured:
    def __init__(self, model: str | None = None, client: Any | None = None) -> None:
        from app.providers.claude import build_anthropic_client

        self.model = model or get_settings().chat_model
        self._client = client if client is not None else build_anthropic_client()

    def _call(self, system: str, prompt: str, schema: dict) -> dict:
        response = self._client.beta.messages.create(
            model=self.model,
            max_tokens=4000,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            # Extraction is routine work, run once per chunk, so low effort keeps indexing affordable.
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            return {}
        text = "".join(block.text for block in response.content if block.type == "text")
        try:
            return json.loads(text)
        except ValueError:
            return {}


class ClaudeGraphExtractor(_ClaudeStructured, GraphExtractor):
    def extract(self, text: str) -> GraphExtraction:
        return parse_extraction(self._call(GRAPH_SYSTEM, text, GRAPH_SCHEMA), self.model)


class ClaudeSummarizer(_ClaudeStructured, Summarizer):
    def summarize(self, title: str, passages: list[str]) -> SourceSummary:
        payload = self._call(SUMMARY_SYSTEM, summary_prompt(title, passages), SUMMARY_SCHEMA)
        return SourceSummary(text=payload.get("summary", "").strip(), model=self.model)


class _OllamaStructured:
    def __init__(self, client: Any | None = None) -> None:
        from app.providers.ollama import OllamaClient

        self._ollama = client or OllamaClient()
        self.model = f"ollama:{self._ollama.model}"

    def _call(self, system: str, prompt: str, schema: dict) -> dict:
        content = self._ollama.chat(
            [{"role": "system", "content": system}, {"role": "user", "content": prompt}], response_format=schema
        )
        try:
            return json.loads(content)
        except ValueError:
            return {}


class OllamaGraphExtractor(_OllamaStructured, GraphExtractor):
    def extract(self, text: str) -> GraphExtraction:
        return parse_extraction(self._call(GRAPH_SYSTEM, text, GRAPH_SCHEMA), self.model)


class OllamaSummarizer(_OllamaStructured, Summarizer):
    def summarize(self, title: str, passages: list[str]) -> SourceSummary:
        payload = self._call(SUMMARY_SYSTEM, summary_prompt(title, passages), SUMMARY_SCHEMA)
        return SourceSummary(text=payload.get("summary", "").strip(), model=self.model)
