from fastapi.testclient import TestClient

from app.db.database import SessionLocal
from app.main import app
from app.models.analysis import ExtractedContent, Transcript
from app.providers.answering import NO_EVIDENCE_ANSWER, ExtractiveAnswerProvider
from app.providers.base import EvidenceChunk

client = TestClient(app)


def test_extractive_answer_refuses_without_evidence() -> None:
    result = ExtractiveAnswerProvider().answer("What did I save about RAG?", [])
    assert result.grounded is False
    assert result.answer == NO_EVIDENCE_ANSWER


def test_extractive_answer_only_quotes_supplied_evidence() -> None:
    evidence = [
        EvidenceChunk(
            reel_id=7,
            instagram_url="https://www.instagram.com/reel/RAG001/",
            content_type="transcript",
            text="Retrieval augmented generation grounds answers in retrieved documents. Unrelated aside.",
            score=0.9,
        )
    ]
    result = ExtractiveAnswerProvider().answer("How does retrieval augmented generation work?", evidence)
    assert result.grounded is True
    assert result.cited_reel_ids == [7]
    assert "https://www.instagram.com/reel/RAG001/" in result.answer
    assert "Retrieval augmented generation grounds answers" in result.answer


def test_ask_endpoint_returns_grounded_answer_with_sources() -> None:
    collection_id = client.post("/api/collections", json={"name": "RAG"}).json()["id"]
    created = client.post(
        "/api/reels",
        json={"collection_id": collection_id, "instagram_url": "https://www.instagram.com/reel/RAG010/"},
    )
    reel_id = created.json()["reel_id"]
    with SessionLocal() as db:
        db.add(
            Transcript(
                reel_id=reel_id,
                text="Hybrid search fuses BM25 keyword matching with dense vector retrieval before reranking.",
                language="en",
                segments=[],
                model="test",
            )
        )
        db.add(
            ExtractedContent(
                reel_id=reel_id,
                content_type="ai_knowledge",
                data={"topic": "hybrid search"},
                text_representation="topic: hybrid search. technologies: BM25",
                confidence=0.8,
            )
        )
        db.commit()
    client.post(f"/api/collections/{collection_id}/index")

    answered = client.post(
        f"/api/collections/{collection_id}/ask", json={"question": "Which videos explain hybrid search?"}
    )
    assert answered.status_code == 200
    body = answered.json()
    assert body["grounded"] is True
    assert body["sources"]
    assert {source["reel_id"] for source in body["sources"]} == {reel_id}
    assert "instagram.com/reel/RAG010" in body["answer"]


def test_ask_endpoint_reports_no_evidence_for_an_empty_collection() -> None:
    collection_id = client.post("/api/collections", json={"name": "Empty"}).json()["id"]
    answered = client.post(f"/api/collections/{collection_id}/ask", json={"question": "What did I save about QLoRA?"})
    assert answered.status_code == 200
    assert answered.json()["grounded"] is False
    assert answered.json()["sources"] == []
