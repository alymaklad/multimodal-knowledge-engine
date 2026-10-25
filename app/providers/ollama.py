"""A local model through Ollama's HTTP API, for the chatbot and for one-shot answers.

Runs entirely on this machine: install Ollama, `ollama pull <model>`, and set CHAT_PROVIDER=ollama
(and ANSWER_PROVIDER=ollama for `/ask`). The context window is set explicitly per request because
Ollama's default of 2048 tokens would silently cut off the evidence passages.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx

from app.core.config import get_settings
from app.providers.answering import NO_EVIDENCE_ANSWER
from app.providers.base import (
    AnswerProvider,
    ChatProvider,
    ChatProviderError,
    ChatTurn,
    EvidenceChunk,
    GroundedAnswer,
)
from app.providers.chat_prompts import (
    CHAT_SYSTEM,
    REWRITE_SYSTEM,
    answer_prompt,
    clean_query,
    history_messages,
    rewrite_prompt,
)
from app.providers.claude_answering import ANSWER_SCHEMA, ANSWER_SYSTEM, grounded_answer_prompt


class OllamaClient:
    def __init__(self, client: httpx.Client | None = None) -> None:
        settings = get_settings()
        self.base_url = settings.ollama_url.rstrip("/")
        self.model = settings.ollama_model
        self._options = {"num_ctx": settings.ollama_num_ctx}
        self._client = client or httpx.Client(timeout=settings.ollama_timeout_seconds)

    def chat(self, messages: list[dict[str, str]], *, response_format: dict | None = None) -> str:
        payload: dict[str, Any] = {"model": self.model, "messages": messages, "stream": False, "options": self._options}
        if response_format is not None:
            payload["format"] = response_format
        try:
            response = self._client.post(f"{self.base_url}/api/chat", json=payload)
        except httpx.HTTPError as error:
            raise ChatProviderError(self._unreachable(error)) from error
        self._raise_for_status(response)
        return response.json().get("message", {}).get("content", "")

    def stream_chat(self, messages: list[dict[str, str]]) -> Iterator[str]:
        payload = {"model": self.model, "messages": messages, "stream": True, "options": self._options}
        try:
            with self._client.stream("POST", f"{self.base_url}/api/chat", json=payload) as response:
                if response.status_code >= 400:
                    response.read()
                    self._raise_for_status(response)
                # One JSON object per line; the last one has "done": true.
                for line in response.iter_lines():
                    if not line.strip():
                        continue
                    event = json.loads(line)
                    if event.get("error"):
                        raise ChatProviderError(f"Ollama failed: {event['error']}")
                    text = event.get("message", {}).get("content", "")
                    if text:
                        yield text
                    if event.get("done"):
                        if event.get("done_reason") == "length":
                            raise ChatProviderError("The answer was cut off at the context limit; raise OLLAMA_NUM_CTX")
                        return
        except httpx.HTTPError as error:
            raise ChatProviderError(self._unreachable(error)) from error

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        try:
            detail = response.json().get("error", "")
        except ValueError:
            detail = ""
        if response.status_code == 404 and "not found" in detail.lower():
            raise ChatProviderError(f"The Ollama model {self.model} is not installed; run `ollama pull {self.model}`")
        raise ChatProviderError(f"Ollama returned an error ({response.status_code}){': ' + detail if detail else ''}")

    def _unreachable(self, error: Exception) -> str:
        if isinstance(error, httpx.TimeoutException):
            return "The local model took too long to answer; raise OLLAMA_TIMEOUT_SECONDS or use a smaller model"
        return f"Ollama is not reachable at {self.base_url}; start it with `ollama serve`"


class OllamaChatProvider(ChatProvider):
    def __init__(self, client: OllamaClient | None = None) -> None:
        self._ollama = client or OllamaClient()
        self.model = f"ollama:{self._ollama.model}"

    def rewrite_query(self, history: list[ChatTurn], question: str) -> str:
        text = self._ollama.chat(
            [{"role": "system", "content": REWRITE_SYSTEM}, {"role": "user", "content": rewrite_prompt(history, question)}]
        )
        return clean_query(text, question)

    def stream_answer(self, history: list[ChatTurn], question: str, evidence: list[EvidenceChunk]) -> Iterator[str]:
        messages = (
            [{"role": "system", "content": CHAT_SYSTEM}]
            + history_messages(history)
            + [{"role": "user", "content": answer_prompt(question, evidence)}]
        )
        yield from self._ollama.stream_chat(messages)


class OllamaAnswerProvider(AnswerProvider):
    """One-shot grounded answers for `/ask`, constrained to the same schema the Claude provider uses."""

    def __init__(self, client: OllamaClient | None = None) -> None:
        self._ollama = client or OllamaClient()
        self.model = f"ollama:{self._ollama.model}"

    def answer(self, question: str, evidence: list[EvidenceChunk]) -> GroundedAnswer:
        if not evidence:
            return GroundedAnswer(answer=NO_EVIDENCE_ANSWER, grounded=False, model=self.model)
        content = self._ollama.chat(
            [{"role": "system", "content": ANSWER_SYSTEM}, {"role": "user", "content": grounded_answer_prompt(question, evidence)}],
            response_format=ANSWER_SCHEMA,
        )
        try:
            payload = json.loads(content)
        except ValueError:
            return GroundedAnswer(answer=NO_EVIDENCE_ANSWER, grounded=False, model=self.model)
        available = {chunk.source_id for chunk in evidence}
        cited = [source_id for source_id in payload.get("cited_source_ids", []) if source_id in available]
        if not payload.get("grounded", False) or not cited:
            return GroundedAnswer(answer=payload.get("answer") or NO_EVIDENCE_ANSWER, grounded=False, model=self.model)
        return GroundedAnswer(answer=payload["answer"], grounded=True, model=self.model, cited_source_ids=cited)
