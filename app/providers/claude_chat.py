"""The chatbot on Claude.

Answers stream through `client.beta.messages.stream` with the server-side refusal fallback enabled
(`fallbacks: "default"`): if Claude's safety classifiers decline a question, the API re-runs it on
Anthropic's recommended fallback model inside the same stream instead of returning a refusal. A
refusal that survives the fallback still ends with `stop_reason: "refusal"`, and the partial text is
then discarded rather than kept as an answer.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from app.core.config import get_settings
from app.providers.base import ChatProvider, ChatProviderError, ChatRefusedError, ChatTurn, EvidenceChunk
from app.providers.chat_prompts import (
    CHAT_SYSTEM,
    REWRITE_SYSTEM,
    answer_prompt,
    clean_query,
    history_messages,
    rewrite_prompt,
)
from app.providers.claude import build_anthropic_client

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class ClaudeChatProvider(ChatProvider):
    def __init__(self, model: str | None = None, client: Any | None = None) -> None:
        settings = get_settings()
        self.model = model or settings.chat_model
        self._effort = settings.chat_effort
        self._max_tokens = settings.chat_max_tokens
        self._client = client if client is not None else build_anthropic_client()

    def rewrite_query(self, history: list[ChatTurn], question: str) -> str:
        try:
            response = self._client.beta.messages.create(
                model=self.model,
                max_tokens=2000,
                system=REWRITE_SYSTEM,
                messages=[{"role": "user", "content": rewrite_prompt(history, question)}],
                # Resolving a reference is simple work; low effort keeps the extra call quick.
                output_config={"effort": "low"},
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        except Exception as error:
            raise ChatProviderError(_describe(error)) from error
        if response.stop_reason == "refusal":
            return question
        text = "".join(block.text for block in response.content if block.type == "text")
        return clean_query(text, question)

    def stream_answer(self, history: list[ChatTurn], question: str, evidence: list[EvidenceChunk]) -> Iterator[str]:
        messages = history_messages(history) + [{"role": "user", "content": answer_prompt(question, evidence)}]
        try:
            with self._client.beta.messages.stream(
                model=self.model,
                max_tokens=self._max_tokens,
                system=CHAT_SYSTEM,
                messages=messages,
                output_config={"effort": self._effort},
                betas=[FALLBACK_BETA],
                fallbacks="default",
            ) as stream:
                for text in stream.text_stream:
                    yield text
                final = stream.get_final_message()
        except ChatProviderError:
            raise
        except Exception as error:
            raise ChatProviderError(_describe(error)) from error
        # Checked before trusting the text: a refusal can arrive after part of an answer streamed.
        if final.stop_reason == "refusal":
            raise ChatRefusedError("The model declined to answer this question")
        if final.stop_reason == "max_tokens":
            raise ChatProviderError("The answer was cut off at the token limit; raise CHAT_MAX_TOKENS")


def _describe(error: Exception) -> str:
    """A short message for the user that never includes request details or credentials."""
    try:
        import anthropic
    except ImportError:  # pragma: no cover - the client could not have been built without it
        return f"The chat model failed ({type(error).__name__})"
    if isinstance(error, anthropic.AuthenticationError):
        return "Claude rejected the API key; check ANTHROPIC_API_KEY"
    if isinstance(error, anthropic.RateLimitError):
        return "Claude is rate limiting requests; try again shortly"
    if isinstance(error, anthropic.APIConnectionError):
        return "Claude could not be reached; check the network connection"
    if isinstance(error, anthropic.APIStatusError):
        return f"Claude returned an error ({error.status_code})"
    return f"The chat model failed ({type(error).__name__})"
