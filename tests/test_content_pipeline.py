from fastapi.testclient import TestClient

from app.db.database import SessionLocal
from app.main import app
from app.models.embedding import ContentEmbedding
from app.models.job import JobStatus, ProcessingJob
from app.models.segment import SourceSegment
from app.models.source import Source, SourceStatus
from app.models.stage_metric import StageMetric, StageStatus
from app.processors.base import ProcessorContext
from app.providers.base import OCRObservation
from app.workers.pipeline import CONTENT_EXTRACTION, process_source_content, start_source_job
from tests.test_processors import make_pdf

client = TestClient(app)


class _NoOCR:
    def extract(self, frame_paths):
        return []


def _context() -> ProcessorContext:
    return ProcessorContext(ocr=_NoOCR, speech=lambda: None, image_describer=lambda: None)


def _run(source_id: int) -> ProcessingJob:
    with SessionLocal() as db:
        job_id = db.query(ProcessingJob).filter(ProcessingJob.source_id == source_id).one().id
        start_source_job(db, job_id)
        job = process_source_content(db, job_id, context=_context())
        db.expunge(job)
        return job


def _upload(collection_id: int, filename: str, data: bytes) -> int:
    response = client.post(
        "/api/sources/upload", data={"collection_id": str(collection_id)}, files={"media": (filename, data, "")}
    )
    assert response.status_code == 202, response.text
    return response.json()["source_id"]


def test_a_markdown_upload_is_segmented_indexed_and_answerable() -> None:
    collection_id = client.post("/api/collections", json={"name": "Docs"}).json()["id"]
    source_id = _upload(
        collection_id,
        "guide.md",
        b"# Retrieval\n\nReciprocal rank fusion merges lexical and dense rankings.\n\n# Serving\n\nUse a GPU.",
    )
    job = _run(source_id)
    assert job.status is JobStatus.completed

    with SessionLocal() as db:
        source = db.get(Source, source_id)
        assert source.status is SourceStatus.completed
        segments = db.query(SourceSegment).filter(SourceSegment.source_id == source_id).order_by(SourceSegment.sequence).all()
        assert [segment.locator.get("section") for segment in segments] == ["Retrieval", "Serving"]
        keys = {row.chunk_key for row in db.query(ContentEmbedding).filter(ContentEmbedding.source_id == source_id)}
        assert {"segment:1", "segment:2", "summary"} <= keys
        metric = db.query(StageMetric).filter(StageMetric.source_id == source_id).one()
        assert metric.stage == CONTENT_EXTRACTION and metric.status is StageStatus.succeeded
        assert metric.items_processed == 2

    hits = client.post("/api/search", json={"collection_id": collection_id, "query": "rank fusion", "mode": "hybrid"}).json()
    assert hits and hits[0]["source_id"] == source_id
    answer = client.post(f"/api/collections/{collection_id}/ask", json={"question": "How does rank fusion work?"}).json()
    assert answer["grounded"] is True
    assert {item["source_id"] for item in answer["sources"]} == {source_id}


def test_a_pdf_upload_keeps_page_numbers() -> None:
    collection_id = client.post("/api/collections", json={"name": "Papers"}).json()["id"]
    source_id = _upload(collection_id, "paper.pdf", make_pdf(["Abstract on retrieval.", "Results on page two."]))
    assert _run(source_id).status is JobStatus.completed
    with SessionLocal() as db:
        source = db.get(Source, source_id)
        assert [segment.locator for segment in source.segments] == [{"page": 1}, {"page": 2}]
        assert source.details["page_count"] == 2
        assert source.provenance.models["processor_models"] == ["pdfium"]


def test_pasted_text_is_processed_too() -> None:
    created = client.post("/api/sources/text", json={"title": "Idea", "text": "Cache the embeddings per source."})
    assert _run(created.json()["source_id"]).status is JobStatus.completed


def test_an_unprocessable_source_fails_with_its_reason() -> None:
    collection_id = client.post("/api/collections", json={"name": "Images"}).json()["id"]
    source_id = _upload(collection_id, "blank.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    job = _run(source_id)
    assert job.status is JobStatus.failed
    assert "No text or description could be extracted" in job.error_message
    with SessionLocal() as db:
        assert db.get(Source, source_id).status is SourceStatus.failed
        metric = db.query(StageMetric).filter(StageMetric.source_id == source_id).one()
        assert metric.status is StageStatus.failed


def test_reprocessing_replaces_the_segments() -> None:
    collection_id = client.post("/api/collections", json={"name": "Reprocess"}).json()["id"]
    source_id = _upload(collection_id, "notes.txt", b"First version of the notes.")
    _run(source_id)
    assert client.post(f"/api/sources/{source_id}/reprocess").status_code == 202
    with SessionLocal() as db:
        assert db.query(SourceSegment).filter(SourceSegment.source_id == source_id).count() == 0
    assert _run(source_id).status is JobStatus.completed
    with SessionLocal() as db:
        assert db.query(SourceSegment).filter(SourceSegment.source_id == source_id).count() == 1


def test_an_image_with_ocr_text_becomes_searchable() -> None:
    class _OCR:
        def extract(self, frame_paths):
            return [OCRObservation(frame_path=str(frame_paths[0]), text="Quarterly revenue by region")]

    collection_id = client.post("/api/collections", json={"name": "Charts"}).json()["id"]
    source_id = _upload(collection_id, "chart.png", b"\x89PNG\r\n\x1a\n" + b"\x01" * 32)
    with SessionLocal() as db:
        job_id = db.query(ProcessingJob).filter(ProcessingJob.source_id == source_id).one().id
        start_source_job(db, job_id)
        context = ProcessorContext(ocr=_OCR, speech=lambda: None, image_describer=lambda: None)
        assert process_source_content(db, job_id, context=context).status is JobStatus.completed
    hits = client.post("/api/search", json={"collection_id": collection_id, "query": "revenue"}).json()
    assert [hit["source_id"] for hit in hits] == [source_id]
