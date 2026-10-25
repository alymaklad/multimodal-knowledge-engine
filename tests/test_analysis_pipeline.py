from pathlib import Path

from sqlalchemy import select

from app.db.database import SessionLocal
from app.models.analysis import ExtractedContent, OCRResult, Transcript, VisualAnalysis
from app.models.collection import Collection
from app.models.job import JobStatus, ProcessingJob
from app.models.media_asset import MediaAsset, MediaAssetKind
from app.models.source import Source, SourceKind, SourceStatus
from app.providers.base import ExtractionResult, OCRObservation, TranscriptionResult, VisionResult
from app.services.retrieval import search_collection
from app.workers.pipeline import analyze_source
from app.main import app
from fastapi.testclient import TestClient

client = TestClient(app)


class FakeSpeechProvider:
    def transcribe(self, _audio_path: Path) -> TranscriptionResult:
        return TranscriptionResult(text="This source explains hybrid retrieval.", language="en", model="fake-whisper")


class FakeOCRProvider:
    def extract(self, frame_paths: list[Path]) -> list[OCRObservation]:
        return [OCRObservation(frame_path=str(frame_paths[0]), text="BM25 + embeddings", confidence=0.98)]


class FakeVisionProvider:
    def analyze_video(self, _frames: list[Path], _transcript: str, _ocr: str) -> VisionResult:
        return VisionResult(description="A presenter discusses RAG search.", model="fake-vlm", confidence=0.9)


class FakeExtractionProvider:
    def extract(self, _vision: VisionResult, _transcript: str, _ocr: str) -> list[ExtractionResult]:
        return [
            ExtractionResult(
                content_type="key_points",
                data={"topic": "hybrid search", "entities": ["BM25", "embeddings"]},
                text_representation="Hybrid search combines BM25 and dense embeddings for RAG.",
                confidence=0.95,
            )
        ]


def test_analysis_worker_persists_multimodal_knowledge_and_searches_it() -> None:
    with SessionLocal() as db:
        collection = Collection(name="Analysis test")
        db.add(collection)
        db.flush()
        source = Source(
            user_id=collection.user_id,
            kind=SourceKind.video,
            title="upload://analysis-test.mp4",
            collections=[collection],
            media_path="data/media/analysis-test.mp4",
            status=SourceStatus.processing,
            processing_version="v1",
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

        updated = analyze_source(
            db,
            job.id,
            speech_provider=FakeSpeechProvider(),
            ocr_provider=FakeOCRProvider(),
            vision_provider=FakeVisionProvider(),
            extraction_provider=FakeExtractionProvider(),
        )

        assert updated.status == JobStatus.completed
        assert db.scalar(select(Transcript).where(Transcript.source_id == source.id)).model == "fake-whisper"
        assert db.scalar(select(OCRResult).where(OCRResult.source_id == source.id)).text == "BM25 + embeddings"
        assert db.scalar(select(VisualAnalysis).where(VisualAnalysis.source_id == source.id)).model == "fake-vlm"
        assert db.scalar(select(ExtractedContent).where(ExtractedContent.source_id == source.id)).data["topic"] == "hybrid search"

        results = search_collection(db, collection_id=collection.id, query="hybrid BM25", limit=10)
        assert len(results) == 1
        assert results[0].source_id == source.id

        response = client.get(f"/api/sources/{source.id}/analysis")
        assert response.status_code == 200
        payload = response.json()
        assert payload["transcript"]["text"] == "This source explains hybrid retrieval."
        assert payload["ocr_results"][0]["timestamp_seconds"] == 0
        assert payload["extracted_content"][0]["content_type"] == "key_points"


class MidConfidenceExtractionProvider:
    """Confidence between the review and auto-accept thresholds, so verification decides."""

    def __init__(self, topic: str) -> None:
        self._topic = topic

    def extract(self, _vision: VisionResult, _transcript: str, _ocr: str) -> list[ExtractionResult]:
        return [
            ExtractionResult(
                content_type="key_points",
                data={"topic": self._topic, "parameter": "k = 60"},
                text_representation=f"Topic: {self._topic}. k = 60.",
                confidence=0.7,
            )
        ]


class SilentOCRProvider:
    def extract(self, frame_paths: list[Path]) -> list[OCRObservation]:
        return [OCRObservation(frame_path=str(frame_paths[0]), text="Reciprocal Rank Fusion k = 60", confidence=0.95)]


def _seed_analyzable_source(db, name: str) -> tuple[Source, ProcessingJob]:
    collection = Collection(name=name)
    db.add(collection)
    db.flush()
    source = Source(
        user_id=collection.user_id,
        kind=SourceKind.video,
        title=f"upload://{name}.mp4",
        collections=[collection],
        media_path=f"data/media/{name}.mp4",
        status=SourceStatus.processing,
        processing_version="v1",
    )
    db.add(source)
    db.flush()
    db.add(
        MediaAsset(
            source_id=source.id,
            kind=MediaAssetKind.frame,
            path="data/media/frame_00001.jpg",
            sequence_number=1,
            timestamp_seconds=0,
        )
    )
    job = ProcessingJob(source_id=source.id, status=JobStatus.queued, current_stage="ocr_and_transcription")
    db.add(job)
    db.commit()
    return source, job


def _verification_event(db, job_id: int):
    from app.models.job_event import JobEvent

    return db.scalar(
        select(JobEvent).where(JobEvent.job_id == job_id, JobEvent.stage == "confidence_verification")
    )


def test_mid_confidence_knowledge_is_accepted_when_on_screen_text_corroborates_it() -> None:
    """A silent video whose facts are only on screen still completes (architecture section 23)."""
    with SessionLocal() as db:
        source, job = _seed_analyzable_source(db, "verified-silent")
        updated = analyze_source(
            db,
            job.id,
            speech_provider=None,
            ocr_provider=SilentOCRProvider(),
            vision_provider=FakeVisionProvider(),
            extraction_provider=MidConfidenceExtractionProvider("Reciprocal Rank Fusion"),
        )
        assert updated.status == JobStatus.completed
        assert db.get(Source, source.id).status == SourceStatus.completed
        assert _verification_event(db, job.id).event_type == "verified"


def test_mid_confidence_knowledge_without_corroboration_is_routed_to_review() -> None:
    with SessionLocal() as db:
        source, job = _seed_analyzable_source(db, "unverified-silent")
        updated = analyze_source(
            db,
            job.id,
            speech_provider=None,
            ocr_provider=SilentOCRProvider(),
            vision_provider=FakeVisionProvider(),
            extraction_provider=MidConfidenceExtractionProvider("Vector Quantisation"),
        )
        assert updated.status == JobStatus.needs_review
        assert db.get(Source, source.id).status == SourceStatus.needs_review
        event = _verification_event(db, job.id)
        assert event.event_type == "needs_review"
        assert "key_points.topic" in event.message
