import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.db.database import SessionLocal
from app.main import app
from app.models.analysis import ExtractedContent, Transcript, VisualAnalysis
from app.models.collection import Collection
from app.models.embedding import ContentEmbedding
from app.models.job import JobStatus, ProcessingJob
from app.models.media_asset import MediaAsset, MediaAssetKind
from app.models.provenance import ProcessingProvenance
from app.models.source import Source, SourceKind, SourceStatus
from app.providers.answering import NO_EVIDENCE_ANSWER
from app.providers.base import EvidenceChunk, ExtractionResult, OCRObservation, TranscriptionResult, VisionResult
from app.providers.claude_answering import ClaudeAnswerProvider
from app.providers.factory import get_answer_provider
from app.workers.pipeline import analyze_source

client = TestClient(app)


class FakeClaudeClient:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.requests: list[dict] = []

    @property
    def messages(self):
        return self

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(self._payload))])


def _chunk(source_id: int) -> EvidenceChunk:
    return EvidenceChunk(
        source_id=source_id,
        title=f"Source E{source_id}",
        origin_url=f"https://example.com/E{source_id}",
        content_type="transcript",
        text="Hybrid search fuses BM25 with dense retrieval.",
        score=0.8,
    )


def test_claude_answering_sends_every_chunk_and_returns_citations() -> None:
    fake = FakeClaudeClient(
        {"answer": "Two saved sources explain hybrid search.", "grounded": True, "cited_source_ids": [3, 7]}
    )
    result = ClaudeAnswerProvider(client=fake).answer("Which videos explain hybrid search?", [_chunk(3), _chunk(7)])

    prompt = fake.requests[0]["messages"][0]["content"]
    assert "[Source 3]" in prompt and "[Source 7]" in prompt
    assert "only the evidence above" in prompt
    assert "Never use outside knowledge" in fake.requests[0]["system"]
    assert result.grounded is True
    assert result.cited_source_ids == [3, 7]


def test_claude_answering_drops_a_citation_that_was_never_retrieved() -> None:
    """A source id the retriever did not supply must not reach the caller as a source."""
    fake = FakeClaudeClient({"answer": "Source 99 covers it.", "grounded": True, "cited_source_ids": [99]})
    result = ClaudeAnswerProvider(client=fake).answer("What about QLoRA?", [_chunk(3)])
    assert result.grounded is False
    assert result.answer == NO_EVIDENCE_ANSWER


def test_claude_answering_keeps_only_the_retrieved_subset_of_citations() -> None:
    fake = FakeClaudeClient({"answer": "Source 3 covers it.", "grounded": True, "cited_source_ids": [3, 99]})
    result = ClaudeAnswerProvider(client=fake).answer("What about hybrid search?", [_chunk(3)])
    assert result.grounded is True
    assert result.cited_source_ids == [3]


def test_claude_answering_honours_its_own_ungrounded_verdict() -> None:
    fake = FakeClaudeClient({"answer": "Nothing saved covers QLoRA.", "grounded": False, "cited_source_ids": []})
    result = ClaudeAnswerProvider(client=fake).answer("What about QLoRA?", [_chunk(3)])
    assert result.grounded is False
    assert result.answer == "Nothing saved covers QLoRA."


def test_claude_answering_skips_the_request_when_nothing_was_retrieved() -> None:
    fake = FakeClaudeClient({})
    result = ClaudeAnswerProvider(client=fake).answer("What did I save about RAG?", [])
    assert fake.requests == []
    assert result.grounded is False


def test_answer_provider_factory_defaults_to_extractive() -> None:
    assert type(get_answer_provider()).__name__ == "ExtractiveAnswerProvider"


def test_answer_provider_factory_rejects_an_unknown_name(monkeypatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("ANSWER_PROVIDER", "nonexistent")
    get_settings.cache_clear()
    with pytest.raises(ValueError, match="Unsupported answer provider"):
        get_answer_provider()
    get_settings.cache_clear()


class _Speech:
    def transcribe(self, _path) -> TranscriptionResult:
        return TranscriptionResult(text="Explains RAG.", language="en", model="fake-whisper-small")


class _OCR:
    def extract(self, frame_paths) -> list[OCRObservation]:
        return [OCRObservation(frame_path=str(frame_paths[0]), text="RAG pipeline", confidence=0.9)]


class _Vision:
    def analyze_video(self, _frames, _transcript, _ocr) -> VisionResult:
        return VisionResult(description="A presenter explains RAG.", model="fake-vlm-v2", confidence=0.9)


class _Extraction:
    def extract(self, _vision, _transcript, _ocr) -> list[ExtractionResult]:
        return [
            ExtractionResult(
                content_type="ai_knowledge",
                data={"topic": "RAG", "evidence": {"topic": "ocr"}},
                text_representation="topic: RAG",
                confidence=0.95,
            )
        ]


def test_analysis_records_the_model_set_that_produced_the_knowledge() -> None:
    with SessionLocal() as db:
        collection = Collection(name="Provenance")
        db.add(collection)
        db.flush()
        source = Source(
            user_id=collection.user_id,
            kind=SourceKind.video,
            title="upload://provenance.mp4",
            collections=[collection],
            media_path="data/media/provenance.mp4",
            status=SourceStatus.processing,
            processing_version="v7",
        )
        db.add(source)
        db.flush()
        db.add_all(
            [
                MediaAsset(source_id=source.id, kind=MediaAssetKind.audio, path="data/media/audio.wav"),
                MediaAsset(
                    source_id=source.id,
                    kind=MediaAssetKind.frame,
                    path="data/media/frame_00001.jpg",
                    sequence_number=1,
                    timestamp_seconds=0,
                ),
            ]
        )
        job = ProcessingJob(source_id=source.id, status=JobStatus.queued, current_stage="ocr_and_transcription")
        db.add(job)
        db.commit()

        analyze_source(
            db,
            job.id,
            speech_provider=_Speech(),
            ocr_provider=_OCR(),
            vision_provider=_Vision(),
            extraction_provider=_Extraction(),
        )
        record = db.query(ProcessingProvenance).filter(ProcessingProvenance.source_id == source.id).one()
        assert record.processing_version == "v7"
        assert record.models["transcription_model"] == "fake-whisper-small"
        assert record.models["vision_model"] == "fake-vlm-v2"
        assert record.models["embedding_model"].startswith("local-hashing-v1")
        assert record.models["extraction_prompt_version"] == "v1"
        source_id = source.id

    analysis = client.get(f"/api/sources/{source_id}/analysis")
    assert analysis.status_code == 200
    assert analysis.json()["provenance"]["models"]["vision_model"] == "fake-vlm-v2"


def test_reprocessing_clears_the_provenance_of_the_discarded_run() -> None:
    collection_id = client.post("/api/collections", json={"name": "Provenance reset"}).json()["id"]
    created = client.post(
        "/api/sources/url",
        json={"collection_id": collection_id, "url": "https://example.com/source/PRV001/"},
    )
    source_id = created.json()["source_id"]
    with SessionLocal() as db:
        db.add(
            ProcessingProvenance(source_id=source_id, processing_version="v1", models={"vision_model": "stale"})
        )
        db.add(VisualAnalysis(source_id=source_id, description="stale", analysis_json={}, model="stale"))
        db.commit()

    assert client.post(f"/api/sources/{source_id}/reprocess").status_code == 202
    with SessionLocal() as db:
        assert db.query(ProcessingProvenance).filter(ProcessingProvenance.source_id == source_id).count() == 0
        assert db.query(ContentEmbedding).filter(ContentEmbedding.source_id == source_id).count() == 0
