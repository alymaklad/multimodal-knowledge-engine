from fastapi.testclient import TestClient

from app.db.database import SessionLocal
from app.main import app
from app.models.analysis import ExtractedContent, Transcript

client = TestClient(app)


def _seed(collection_id: int, url: str, creator: str, topic: str, text: str) -> int:
    reel_id = client.post(
        "/api/reels", json={"collection_id": collection_id, "instagram_url": url}
    ).json()["reel_id"]
    client.patch(f"/api/reels/{reel_id}", json={"creator_username": creator})
    with SessionLocal() as db:
        db.add(Transcript(reel_id=reel_id, text=text, language="en", segments=[], model="test"))
        db.add(
            ExtractedContent(
                reel_id=reel_id,
                content_type="ai_knowledge",
                data={"topic": topic, "technologies": []},
                text_representation=f"topic: {topic}. {text}",
                confidence=0.9,
            )
        )
        db.commit()
    return reel_id


def _collection() -> tuple[int, int, int]:
    collection_id = client.post("/api/collections", json={"name": "Filtering"}).json()["id"]
    rag = _seed(
        collection_id,
        "https://www.instagram.com/reel/FIL001/",
        "@alice",
        "RAG",
        "Retrieval augmented generation with hybrid search.",
    )
    tuning = _seed(
        collection_id,
        "https://www.instagram.com/reel/FIL002/",
        "@bob",
        "fine-tuning",
        "LoRA fine-tuning explained with hybrid search comparisons.",
    )
    client.post(f"/api/collections/{collection_id}/index")
    return collection_id, rag, tuning


def test_results_carry_their_creator_and_topics() -> None:
    collection_id, rag, _ = _collection()
    results = client.post(
        "/api/search", json={"collection_id": collection_id, "query": "hybrid search"}
    ).json()
    match = next(item for item in results if item["reel_id"] == rag)
    assert match["creator"] == "@alice"
    assert match["topics"] == ["RAG"]


def test_creator_filter_narrows_every_mode() -> None:
    collection_id, rag, _ = _collection()
    for mode in ("lexical", "semantic", "hybrid"):
        results = client.post(
            "/api/search",
            json={"collection_id": collection_id, "query": "hybrid search", "mode": mode, "creator": "@alice"},
        ).json()
        assert [item["reel_id"] for item in results] == [rag], mode


def test_creator_filter_ignores_a_leading_at_sign() -> None:
    collection_id, rag, _ = _collection()
    results = client.post(
        "/api/search", json={"collection_id": collection_id, "query": "hybrid search", "creator": "alice"}
    ).json()
    assert [item["reel_id"] for item in results] == [rag]


def test_topic_filter_selects_by_extracted_knowledge() -> None:
    collection_id, _, tuning = _collection()
    results = client.post(
        "/api/search",
        json={"collection_id": collection_id, "query": "hybrid search", "topic": "fine-tuning"},
    ).json()
    assert [item["reel_id"] for item in results] == [tuning]


def test_filters_combine() -> None:
    collection_id, _, _ = _collection()
    results = client.post(
        "/api/search",
        json={
            "collection_id": collection_id,
            "query": "hybrid search",
            "creator": "@alice",
            "topic": "fine-tuning",
        },
    ).json()
    assert results == []


def test_an_unmatched_creator_returns_nothing_rather_than_everything() -> None:
    collection_id, _, _ = _collection()
    for mode in ("lexical", "semantic", "hybrid"):
        results = client.post(
            "/api/search",
            json={"collection_id": collection_id, "query": "hybrid search", "mode": mode, "creator": "@nobody"},
        ).json()
        assert results == [], mode


def test_saved_searches_preserve_their_filters() -> None:
    collection_id, rag, _ = _collection()
    saved = client.post(
        "/api/saved-searches",
        json={
            "collection_id": collection_id,
            "name": "Alice on RAG",
            "query": "hybrid search",
            "mode": "hybrid",
            "creator": "@alice",
            "topic": "RAG",
        },
    )
    assert saved.status_code == 201
    assert saved.json()["creator"] == "@alice"
    assert saved.json()["topic"] == "RAG"

    listed = next(item for item in client.get("/api/saved-searches").json() if item["id"] == saved.json()["id"])
    assert listed["creator"] == "@alice"

    results = client.post(f"/api/saved-searches/{saved.json()['id']}/run").json()
    assert [item["reel_id"] for item in results] == [rag]
