import json
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest

from app.providers.base import ChatProviderError, ChatRefusedError, ChatTurn, EvidenceChunk
from app.providers.chat_prompts import strip_markers
from app.providers.claude_chat import FALLBACK_BETA, ClaudeChatProvider
from app.providers.factory import get_chat_provider
from app.providers.ollama import OllamaAnswerProvider, OllamaChatProvider, OllamaClient

EVIDENCE = [
    EvidenceChunk(
        source_id=4, title="paper", origin_url=None, content_type="text",
        text="Fusion sums reciprocal ranks.", score=0.9, chunk_key="text:1", citation="p. 2",
    )
]
HISTORY = [ChatTurn("user", "What is fusion?"), ChatTurn("assistant", "It merges rankings [1].")]


class FakeClaude:
    def __init__(self, parts=("Fusion ", "sums ranks [1]."), stop_reason="end_turn", rewrite="fusion summing ranks"):
        self.parts, self.stop_reason, self.rewrite = list(parts), stop_reason, rewrite
        self.stream_calls: list[dict] = []
        self.create_calls: list[dict] = []
        self.beta = SimpleNamespace(messages=self)

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=self.rewrite + "\n")])

    @contextmanager
    def stream(self, **kwargs):
        self.stream_calls.append(kwargs)
        yield SimpleNamespace(
            text_stream=iter(self.parts),
            get_final_message=lambda: SimpleNamespace(stop_reason=self.stop_reason),
        )


def test_claude_streams_with_the_refusal_fallback_and_explicit_effort() -> None:
    fake = FakeClaude()
    provider = ClaudeChatProvider(client=fake)
    assert "".join(provider.stream_answer(HISTORY, "Why summing?", EVIDENCE)) == "Fusion sums ranks [1]."

    request = fake.stream_calls[0]
    assert request["model"] == "claude-opus-5-5"
    assert request["fallbacks"] == "default" and request["betas"] == [FALLBACK_BETA]
    assert request["output_config"] == {"effort": "medium"}
    assert "thinking" not in request
    # History comes first, with old citation markers removed; the passages travel with the question.
    assert request["messages"][1] == {"role": "assistant", "content": "It merges rankings."}
    assert "[1] paper, p. 2\nFusion sums reciprocal ranks." in request["messages"][-1]["content"]
    assert "Cite every claim" in request["system"]


def test_claude_rewrites_follow_ups_at_low_effort() -> None:
    fake = FakeClaude()
    assert ClaudeChatProvider(client=fake).rewrite_query(HISTORY, "Why summing?") == "fusion summing ranks"
    assert fake.create_calls[0]["output_config"] == {"effort": "low"}
    assert "FOLLOW-UP QUESTION:\nWhy summing?" in fake.create_calls[0]["messages"][0]["content"]


@pytest.mark.parametrize(
    ("stop_reason", "error", "message"),
    [("refusal", ChatRefusedError, "declined"), ("max_tokens", ChatProviderError, "CHAT_MAX_TOKENS")],
)
def test_claude_stop_reasons_are_checked_before_the_answer_is_trusted(stop_reason, error, message) -> None:
    provider = ClaudeChatProvider(client=FakeClaude(stop_reason=stop_reason))
    with pytest.raises(error, match=message):
        list(provider.stream_answer([], "q", EVIDENCE))


def _ollama(handler) -> OllamaClient:
    return OllamaClient(client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_ollama_streams_ndjson_with_a_large_enough_context() -> None:
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        lines = [
            {"message": {"role": "assistant", "content": "Fusion "}, "done": False},
            {"message": {"role": "assistant", "content": "sums ranks [1]."}, "done": False},
            {"message": {"role": "assistant", "content": ""}, "done": True, "done_reason": "stop"},
        ]
        return httpx.Response(200, content="\n".join(json.dumps(line) for line in lines))

    provider = OllamaChatProvider(client=_ollama(handler))
    assert "".join(provider.stream_answer(HISTORY, "Why?", EVIDENCE)) == "Fusion sums ranks [1]."
    body = requests[0]
    assert body["stream"] is True and body["options"]["num_ctx"] == 8192
    assert body["messages"][0]["role"] == "system"
    assert provider.model == "ollama:qwen2.5:7b"


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(404, json={"error": 'model "qwen2.5:7b" not found, try pulling it first'}), "ollama pull qwen2.5:7b"),
        (httpx.Response(500, json={"error": "out of memory"}), "out of memory"),
    ],
)
def test_ollama_errors_say_what_to_do(response, message) -> None:
    provider = OllamaChatProvider(client=_ollama(lambda request: response))
    with pytest.raises(ChatProviderError, match=message):
        list(provider.stream_answer([], "q", EVIDENCE))


def test_an_unreachable_ollama_is_reported_plainly() -> None:
    def handler(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(ChatProviderError, match="ollama serve"):
        list(OllamaChatProvider(client=_ollama(handler)).stream_answer([], "q", EVIDENCE))


def test_ollama_answers_ask_with_a_constrained_schema() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        answer = {"answer": "Fusion sums ranks.", "grounded": True, "cited_source_ids": [4, 99]}
        return httpx.Response(200, json={"message": {"role": "assistant", "content": json.dumps(answer)}, "done": True})

    result = OllamaAnswerProvider(client=_ollama(handler)).answer("What is fusion?", EVIDENCE)
    assert result.grounded is True and result.cited_source_ids == [4]
    assert seen[0]["format"]["required"] == ["answer", "grounded", "cited_source_ids"]
    assert seen[0]["stream"] is False


def test_the_chat_provider_is_chosen_by_configuration(monkeypatch) -> None:
    from app.core.config import get_settings

    assert type(get_chat_provider()).__name__ == "ExtractiveChatProvider"
    monkeypatch.setenv("CHAT_PROVIDER", "ollama")
    get_settings.cache_clear()
    try:
        assert type(get_chat_provider()).__name__ == "OllamaChatProvider"
    finally:
        get_settings.cache_clear()


def test_markers_are_removed_from_history() -> None:
    assert strip_markers("Fusion [1] works [2, 3].") == "Fusion works."
