from fastapi.testclient import TestClient

from app.db.database import SessionLocal
from app.main import app
from app.models.analysis import ExtractedContent, Transcript

client = TestClient(app)


def _seed(collection_id: int, url: str, author: str, topic: str, text: str) -> int:
    source_id = client.post(
        "/api/sources/url", json={"collection_id": collection_id, "url": f"{url}{collection_id}"}
    ).json()["source_id"]
    client.patch(f"/api/sources/{source_id}", json={"author": author})
    with SessionLocal() as db:
        db.add(Transcript(source_id=source_id, text=text, language="en", segments=[], model="test"))
        db.add(
            ExtractedContent(
                source_id=source_id,
                content_type="key_points",
                data={"topic": topic, "entities": []},
                text_representation=f"topic: {topic}. {text}",
                confidence=0.9,
            )
        )
        db.commit()
    return source_id


def _collection() -> tuple[int, int, int]:
    collection_id = client.post("/api/collections", json={"name": "Filtering"}).json()["id"]
    rag = _seed(
        collection_id,
        "https://example.com/source/FIL001/",
        "@alice",
        "RAG",
        "Retrieval augmented generation with hybrid search.",
    )
    tuning = _seed(
        collection_id,
        "https://example.com/source/FIL002/",
        "@bob",
        "fine-tuning",
        "LoRA fine-tuning explained with hybrid search comparisons.",
    )
    client.post(f"/api/collections/{collection_id}/index")
    return collection_id, rag, tuning


def test_results_carry_their_author_and_topics() -> None:
    collection_id, rag, _ = _collection()
    results = client.post(
        "/api/search", json={"collection_id": collection_id, "query": "hybrid search"}
    ).json()
    match = next(item for item in results if item["source_id"] == rag)
    assert match["author"] == "@alice"
    assert match["topics"] == ["RAG"]


def test_author_filter_narrows_every_mode() -> None:
    collection_id, rag, _ = _collection()
    for mode in ("lexical", "semantic", "hybrid"):
        results = client.post(
            "/api/search",
            json={"collection_id": collection_id, "query": "hybrid search", "mode": mode, "author": "@alice"},
        ).json()
        assert [item["source_id"] for item in results] == [rag], mode


def test_author_filter_ignores_a_leading_at_sign() -> None:
    collection_id, rag, _ = _collection()
    results = client.post(
        "/api/search", json={"collection_id": collection_id, "query": "hybrid search", "author": "alice"}
    ).json()
    assert [item["source_id"] for item in results] == [rag]


def test_topic_filter_selects_by_extracted_knowledge() -> None:
    collection_id, _, tuning = _collection()
    results = client.post(
        "/api/search",
        json={"collection_id": collection_id, "query": "hybrid search", "topic": "fine-tuning"},
    ).json()
    assert [item["source_id"] for item in results] == [tuning]


def test_filters_combine() -> None:
    collection_id, _, _ = _collection()
    results = client.post(
        "/api/search",
        json={
            "collection_id": collection_id,
            "query": "hybrid search",
            "author": "@alice",
            "topic": "fine-tuning",
        },
    ).json()
    assert results == []


def test_an_unmatched_author_returns_nothing_rather_than_everything() -> None:
    collection_id, _, _ = _collection()
    for mode in ("lexical", "semantic", "hybrid"):
        results = client.post(
            "/api/search",
            json={"collection_id": collection_id, "query": "hybrid search", "mode": mode, "author": "@nobody"},
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
            "author": "@alice",
            "topic": "RAG",
        },
    )
    assert saved.status_code == 201
    assert saved.json()["author"] == "@alice"
    assert saved.json()["topic"] == "RAG"

    listed = next(item for item in client.get("/api/saved-searches").json() if item["id"] == saved.json()["id"])
    assert listed["author"] == "@alice"

    results = client.post(f"/api/saved-searches/{saved.json()['id']}/run").json()
    assert [item["source_id"] for item in results] == [rag]
