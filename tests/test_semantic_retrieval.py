from fastapi.testclient import TestClient

from app.db.database import SessionLocal
from app.main import app
from app.models.analysis import ExtractedContent, Transcript
from app.models.embedding import ContentEmbedding
from app.models.reel import Reel
from app.providers.embeddings import HashingEmbeddingProvider, cosine_similarity

client = TestClient(app)


def _seed_reel(collection_id: int, url: str, transcript: str, extracted: str) -> int:
    created = client.post("/api/reels", json={"collection_id": collection_id, "instagram_url": url})
    assert created.status_code == 202
    reel_id = created.json()["reel_id"]
    with SessionLocal() as db:
        db.add(Transcript(reel_id=reel_id, text=transcript, language="en", segments=[], model="test"))
        db.add(
            ExtractedContent(
                reel_id=reel_id,
                content_type="ai_knowledge",
                data={"topic": "retrieval"},
                text_representation=extracted,
                confidence=0.8,
            )
        )
        db.commit()
    return reel_id


def test_hashing_embeddings_are_normalized_and_deterministic() -> None:
    provider = HashingEmbeddingProvider(dimensions=64)
    first, second = provider.embed(["hybrid search combines BM25 and dense retrieval", "unrelated cooking content"])
    assert len(first) == 64
    assert abs(sum(value * value for value in first) - 1.0) < 1e-9
    assert first == provider.embed_one("hybrid search combines BM25 and dense retrieval")
    assert cosine_similarity(first, second) < cosine_similarity(first, first)


def test_semantic_and_hybrid_search_return_indexed_reels() -> None:
    collection_id = client.post("/api/collections", json={"name": "Semantic AI"}).json()["id"]
    target = _seed_reel(
        collection_id,
        "https://www.instagram.com/reel/SEM001/",
        "This clip explains hybrid search combining BM25 with dense vectors in Qdrant.",
        "topic: hybrid search. technologies: BM25, Qdrant",
    )
    _seed_reel(
        collection_id,
        "https://www.instagram.com/reel/SEM002/",
        "A short recipe for overnight oats with banana.",
        "topic: breakfast",
    )

    indexed = client.post(f"/api/collections/{collection_id}/index")
    assert indexed.status_code == 200
    assert indexed.json()["indexed_reels"] == 2
    assert indexed.json()["indexed_chunks"] > 2

    semantic = client.post(
        "/api/search", json={"collection_id": collection_id, "query": "hybrid search", "mode": "semantic"}
    )
    assert semantic.status_code == 200
    assert semantic.json()[0]["reel_id"] == target
    assert semantic.json()[0]["match_type"] == "semantic"

    hybrid = client.post(
        "/api/search", json={"collection_id": collection_id, "query": "hybrid search", "mode": "hybrid"}
    )
    assert hybrid.status_code == 200
    assert hybrid.json()[0]["reel_id"] == target


def test_reprocessing_discards_stale_vectors() -> None:
    collection_id = client.post("/api/collections", json={"name": "Reindex"}).json()["id"]
    reel_id = _seed_reel(
        collection_id, "https://www.instagram.com/reel/SEM003/", "Explains LoRA fine-tuning.", "topic: LoRA"
    )
    assert client.post(f"/api/reels/{reel_id}/index").json()["indexed_chunks"] > 0

    assert client.post(f"/api/reels/{reel_id}/reprocess").status_code == 202
    with SessionLocal() as db:
        remaining = db.query(ContentEmbedding).filter(ContentEmbedding.reel_id == reel_id).count()
    assert remaining == 0


def test_near_duplicate_detection_pairs_identical_content() -> None:
    collection_id = client.post("/api/collections", json={"name": "Duplicates"}).json()["id"]
    text = "Barbell back squat. 4 sets of 8 repetitions targeting quadriceps and glutes."
    first = _seed_reel(collection_id, "https://www.instagram.com/reel/DUP001/", text, text)
    second = _seed_reel(collection_id, "https://www.instagram.com/reel/DUP002/", text, text)
    client.post(f"/api/collections/{collection_id}/index")

    duplicates = client.get(f"/api/collections/{collection_id}/duplicates")
    assert duplicates.status_code == 200
    pairs = {(item["reel_id"], item["duplicate_reel_id"]) for item in duplicates.json()}
    assert (min(first, second), max(first, second)) in pairs
    assert all(item["similarity"] >= 0.92 for item in duplicates.json())


def test_ownership_is_enforced_for_index_and_ask() -> None:
    collection_id = client.post("/api/collections", json={"name": "Private"}).json()["id"]
    other = {"X-User-Id": "someone-else"}
    assert client.post(f"/api/collections/{collection_id}/index", headers=other).status_code == 404
    assert client.post(f"/api/collections/{collection_id}/ask", json={"question": "what?"}, headers=other).status_code == 404
    assert client.get(f"/api/collections/{collection_id}/duplicates", headers=other).status_code == 404


def test_reel_deletion_removes_its_vectors() -> None:
    collection_id = client.post("/api/collections", json={"name": "Cascade"}).json()["id"]
    reel_id = _seed_reel(
        collection_id, "https://www.instagram.com/reel/SEM004/", "Explains KV cache.", "topic: KV cache"
    )
    client.post(f"/api/reels/{reel_id}/index")
    assert client.delete(f"/api/reels/{reel_id}").status_code == 204
    with SessionLocal() as db:
        assert db.query(ContentEmbedding).filter(ContentEmbedding.reel_id == reel_id).count() == 0
        assert db.get(Reel, reel_id) is None
