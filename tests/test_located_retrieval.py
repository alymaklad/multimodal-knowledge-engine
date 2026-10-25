from itertools import count

from fastapi.testclient import TestClient

from app.db.database import SessionLocal
from app.main import app
from app.models.job import ProcessingJob
from app.processors.base import ProcessorContext
from app.workers.pipeline import process_source_content, start_source_job
from tests.test_processors import make_pdf

client = TestClient(app)
# Uploads are deduplicated per library, so each test works in a library of its own.
_owners = count(1)


def _context() -> ProcessorContext:
    return ProcessorContext(ocr=lambda: None, speech=lambda: None, image_describer=lambda: None)


def _add(owner: dict, collection_id: int, filename: str, data: bytes) -> int:
    response = client.post(
        "/api/sources/upload",
        data={"collection_id": str(collection_id)},
        files={"media": (filename, data, "")},
        headers=owner,
    )
    assert response.status_code == 202, response.text
    source_id = response.json()["source_id"]
    with SessionLocal() as db:
        job_id = db.query(ProcessingJob).filter(ProcessingJob.source_id == source_id).one().id
        start_source_job(db, job_id)
        process_source_content(db, job_id, context=_context())
    return source_id


def _owner() -> dict:
    return {"X-User-Id": f"located-{next(_owners)}"}


def _library(owner: dict) -> tuple[int, int, int]:
    collection_id = client.post("/api/collections", json={"name": "Located"}, headers=owner).json()["id"]
    paper = _add(
        owner,
        collection_id,
        "paper.pdf",
        make_pdf(
            [
                "Introduction to the survey of search methods.",
                "Reciprocal rank fusion combines lexical and dense rankings by rank position.",
            ]
        ),
    )
    notes = _add(owner, collection_id, "notes.md", b"# Serving\n\nQuantised models fit on a small GPU.\n\n# Costs\n\nBatch requests.")
    return collection_id, paper, notes


def test_search_results_say_where_in_the_source_each_passage_is() -> None:
    owner = _owner()
    collection_id, paper, _ = _library(owner)
    for mode in ("lexical", "semantic", "hybrid"):
        results = client.post(
            "/api/search", json={"collection_id": collection_id, "query": "reciprocal rank fusion", "mode": mode}, headers=owner
        ).json()
        assert results[0]["source_id"] == paper, mode
        passage = results[0]["passages"][0]
        assert passage["locator"]["page"] in {1, 2}
        assert passage["citation"].startswith("p")


def test_answers_cite_the_page_they_quote() -> None:
    owner = _owner()
    collection_id, paper, _ = _library(owner)
    answer = client.post(f"/api/collections/{collection_id}/ask", json={"question": "What does rank fusion combine?"}, headers=owner).json()
    assert answer["grounded"] is True
    cited = [item for item in answer["sources"] if item["source_id"] == paper]
    assert cited and cited[0]["citation"] in {"p. 2", "pp. 1–2"}
    assert cited[0]["chunk_key"].startswith("text:")
    assert "paper, p" in answer["answer"]


def test_a_question_can_be_limited_to_some_sources() -> None:
    owner = _owner()
    collection_id, paper, notes = _library(owner)
    scoped = client.post(
        f"/api/collections/{collection_id}/ask", json={"question": "rank fusion GPU", "source_ids": [notes]}, headers=owner
    ).json()
    assert {item["source_id"] for item in scoped["sources"]} == {notes}

    results = client.post(
        "/api/search",
        json={"collection_id": collection_id, "query": "rank fusion", "mode": "hybrid", "source_ids": [notes]},
        headers=owner,
    ).json()
    assert paper not in {item["source_id"] for item in results}


def test_sources_outside_the_collection_are_refused() -> None:
    owner = _owner()
    collection_id, _, _ = _library(owner)
    other_collection = client.post("/api/collections", json={"name": "Elsewhere"}, headers=owner).json()["id"]
    outsider = _add(owner, other_collection, "outside.txt", b"Text that belongs to another collection.")
    ask = client.post(f"/api/collections/{collection_id}/ask", json={"question": "anything", "source_ids": [outsider]}, headers=owner)
    assert ask.status_code == 404
    assert ask.json()["detail"]["source_ids"] == [outsider]
    search = client.post("/api/search", json={"collection_id": collection_id, "query": "text", "source_ids": [outsider]}, headers=owner)
    assert search.status_code == 404


def test_lexical_ranking_prefers_the_passage_dense_in_the_query_terms() -> None:
    owner = _owner()
    collection_id = client.post("/api/collections", json={"name": "BM25"}, headers=owner).json()["id"]
    focused = _add(owner, collection_id, "focused.txt", b"Quantisation. Quantisation trades accuracy for memory: quantisation.")
    passing = _add(
        owner,
        collection_id,
        "passing.txt",
        b"A long survey of many topics: caching, batching, routing, sharding, and once, quantisation, among others.",
    )
    results = client.post("/api/search", json={"collection_id": collection_id, "query": "quantisation"}, headers=owner).json()
    assert [item["source_id"] for item in results] == [focused, passing]
    minimum = client.post(
        "/api/search", json={"collection_id": collection_id, "query": "quantisation", "minimum_score": 2}, headers=owner
    ).json()
    assert [item["source_id"] for item in minimum] == [focused]


def test_the_summary_chunk_is_not_returned_as_evidence() -> None:
    owner = _owner()
    collection_id, _, _ = _library(owner)
    answer = client.post(f"/api/collections/{collection_id}/ask", json={"question": "survey search methods"}, headers=owner).json()
    assert all(item["chunk_key"] != "summary" for item in answer["sources"])
