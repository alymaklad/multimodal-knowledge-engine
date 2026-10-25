from pathlib import Path

import pytest

from app.db.database import SessionLocal
from app.main import app
from app.models.analysis import ExtractedContent, Transcript
from app.providers.base import VisionResult
from app.providers.reranking import IdentityReranker, RerankProvider, get_rerank_provider
from app.providers.routing import RoutingVisionProvider, classify_complexity
from app.schemas.search import SearchResult
from app.services.retrieval import hybrid_search, rerank_results
from fastapi.testclient import TestClient

client = TestClient(app)


class ReverseReranker(RerankProvider):
    """Scores the last candidate highest, so a reordering is unambiguous in a test."""

    @property
    def model(self) -> str:
        return "reverse"

    def score(self, query: str, passages: list[str]) -> list[float]:
        return [float(index) for index in range(len(passages))]


class BrokenReranker(RerankProvider):
    @property
    def model(self) -> str:
        return "broken"

    def score(self, query: str, passages: list[str]) -> list[float]:
        return [1.0]


def _results(count: int) -> list[SearchResult]:
    return [
        SearchResult(
            reel_id=index,
            instagram_url=f"https://www.instagram.com/reel/R{index}/",
            score=1.0 / index,
            snippets=[f"passage {index}"],
        )
        for index in range(1, count + 1)
    ]


def test_identity_reranker_preserves_the_incoming_order() -> None:
    results = _results(5)
    reranked = rerank_results("query", results, limit=5, provider=IdentityReranker())
    assert [item.reel_id for item in reranked] == [1, 2, 3, 4, 5]


def test_a_reranker_can_promote_a_candidate_below_the_cut() -> None:
    """The whole point of reranking: something fusion ranked 5th can end up first."""
    results = _results(5)
    reranked = rerank_results("query", results, limit=2, provider=ReverseReranker())
    assert [item.reel_id for item in reranked] == [5, 4]


def test_a_mismatched_score_vector_is_ignored_rather_than_trusted() -> None:
    results = _results(4)
    reranked = rerank_results("query", results, limit=2, provider=BrokenReranker())
    assert [item.reel_id for item in reranked] == [1, 2]


def test_reranking_an_empty_result_set_is_safe() -> None:
    assert rerank_results("query", [], limit=10, provider=ReverseReranker()) == []


def test_rerank_provider_factory_defaults_to_identity() -> None:
    assert isinstance(get_rerank_provider(), IdentityReranker)


def test_rerank_provider_factory_rejects_an_unknown_name(monkeypatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("RERANKER", "nonexistent")
    get_settings.cache_clear()
    with pytest.raises(ValueError, match="Unsupported reranker"):
        get_rerank_provider()
    get_settings.cache_clear()


def test_hybrid_search_applies_the_supplied_reranker() -> None:
    collection_id = client.post("/api/collections", json={"name": "Rerank"}).json()["id"]
    ids = []
    for suffix, text in (("RR1", "hybrid search explained"), ("RR2", "hybrid search compared")):
        reel_id = client.post(
            "/api/reels",
            json={"collection_id": collection_id, "instagram_url": f"https://www.instagram.com/reel/{suffix}/"},
        ).json()["reel_id"]
        with SessionLocal() as db:
            db.add(Transcript(reel_id=reel_id, text=text, language="en", segments=[], model="test"))
            db.add(
                ExtractedContent(
                    reel_id=reel_id,
                    content_type="ai_knowledge",
                    data={"topic": "hybrid search"},
                    text_representation=text,
                    confidence=0.9,
                )
            )
            db.commit()
        ids.append(reel_id)
    client.post(f"/api/collections/{collection_id}/index")

    with SessionLocal() as db:
        default_order = [
            item.reel_id
            for item in hybrid_search(db, collection_id=collection_id, query="hybrid search", limit=2)
        ]
        reversed_order = [
            item.reel_id
            for item in hybrid_search(
                db, collection_id=collection_id, query="hybrid search", limit=2, reranker=ReverseReranker()
            )
        ]
    assert sorted(default_order) == sorted(ids)
    assert reversed_order == list(reversed(default_order))


class SmallVision:
    def __init__(self, confidence: float) -> None:
        self._confidence = confidence
        self.calls = 0

    def analyze_video(self, _frames, _transcript, _ocr) -> VisionResult:
        self.calls += 1
        return VisionResult(description="small model", model="small", confidence=self._confidence)


class StrongVision:
    def __init__(self) -> None:
        self.calls = 0

    def analyze_video(self, _frames, _transcript, _ocr) -> VisionResult:
        self.calls += 1
        return VisionResult(description="strong model", model="strong", confidence=0.95)


def test_a_short_clip_with_text_evidence_routes_to_the_small_model() -> None:
    decision = classify_complexity(frame_count=3, has_transcript=True, has_ocr_text=True)
    assert decision.tier == "small"


def test_many_frames_route_to_the_strong_model() -> None:
    decision = classify_complexity(frame_count=40, has_transcript=True, has_ocr_text=True)
    assert decision.tier == "strong"
    assert "movement" in decision.reason


def test_a_reel_with_no_text_evidence_routes_to_the_strong_model() -> None:
    """With no speech and no on-screen text, vision is the only channel left."""
    decision = classify_complexity(frame_count=2, has_transcript=False, has_ocr_text=False)
    assert decision.tier == "strong"


def test_routing_uses_the_small_model_when_it_is_confident() -> None:
    small, strong = SmallVision(0.95), StrongVision()
    provider = RoutingVisionProvider(small_provider=small, strong_provider=strong)
    result = provider.analyze_video([Path("f1.jpg")], "narration", "on-screen text")

    assert small.calls == 1
    assert strong.calls == 0
    assert provider.escalated is False
    assert result.analysis["routing"]["tier"] == "small"
    assert result.analysis["routing"]["escalated"] is False


def test_routing_escalates_once_when_the_small_model_is_uncertain() -> None:
    small, strong = SmallVision(0.30), StrongVision()
    provider = RoutingVisionProvider(small_provider=small, strong_provider=strong)
    result = provider.analyze_video([Path("f1.jpg")], "narration", "on-screen text")

    assert small.calls == 1
    assert strong.calls == 1
    assert provider.escalated is True
    assert result.model == "strong"
    assert result.analysis["routing"]["escalated"] is True
    assert "0.30" in result.analysis["routing"]["reason"]


def test_a_complex_reel_skips_the_small_model_entirely() -> None:
    small, strong = SmallVision(0.95), StrongVision()
    provider = RoutingVisionProvider(small_provider=small, strong_provider=strong)
    provider.analyze_video([Path(f"f{index}.jpg") for index in range(20)], "", "")

    assert small.calls == 0
    assert strong.calls == 1
