import json
import logging
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.logging import JsonFormatter, request_id_var
from app.db.database import SessionLocal
from app.main import app
from app.models.analysis import ExtractedContent, OCRResult, Transcript, VisualAnalysis
from app.models.collection import Collection
from app.models.job import JobStatus, ProcessingJob
from app.models.media_asset import MediaAsset, MediaAssetKind
from app.models.reel import Reel, ReelStatus
from app.models.stage_metric import StageMetric, StageStatus
from app.providers.base import ExtractionResult, OCRObservation, TranscriptionResult, VisionResult
from app.services.analysis import run_analysis
from app.workers.pipeline import analyze_reel

client = TestClient(app)


class GoodSpeech:
    def transcribe(self, _path) -> TranscriptionResult:
        return TranscriptionResult(text="Spoken narration.", language="en", model="fake-whisper")


class BrokenSpeech:
    def transcribe(self, _path) -> TranscriptionResult:
        raise RuntimeError("whisper weights are missing")


class GoodOCR:
    def extract(self, frame_paths) -> list[OCRObservation]:
        return [OCRObservation(frame_path=str(frame_paths[0]), text="3 sets of 10", confidence=0.9)]


class BrokenOCR:
    def extract(self, _frame_paths) -> list[OCRObservation]:
        raise RuntimeError("tesseract binary not found")


class GoodVision:
    def analyze_video(self, _frames, _transcript, _ocr) -> VisionResult:
        return VisionResult(description="A person performs a split squat.", model="fake-vlm", confidence=0.9)


class BrokenVision:
    def analyze_video(self, _frames, _transcript, _ocr) -> VisionResult:
        raise RuntimeError("vision endpoint returned 503")


class FallbackVision:
    def analyze_video(self, frames, _transcript, _ocr) -> VisionResult:
        return VisionResult(description=f"{len(frames)} frames available.", model="local-evidence-v1")


class GoodExtraction:
    def extract(self, _vision, _transcript, _ocr) -> list[ExtractionResult]:
        return [
            ExtractionResult(
                content_type="workout_exercise",
                data={"exercise": "Split Squat", "evidence": {"exercise": "ocr"}},
                text_representation="Split Squat",
                confidence=0.95,
            )
        ]


class BrokenExtraction:
    def extract(self, _vision, _transcript, _ocr) -> list[ExtractionResult]:
        raise RuntimeError("extraction model refused")


def _seed(db, name: str, *, with_audio: bool = True) -> tuple[Reel, ProcessingJob]:
    collection = Collection(name=name)
    db.add(collection)
    db.flush()
    reel = Reel(
        collection_id=collection.id,
        instagram_url=f"upload://{name}.mp4",
        media_path=f"data/media/{name}.mp4",
        status=ReelStatus.processing,
        processing_version="v1",
    )
    db.add(reel)
    db.flush()
    if with_audio:
        db.add(MediaAsset(reel_id=reel.id, kind=MediaAssetKind.audio, path="data/media/audio.wav"))
    db.add(
        MediaAsset(
            reel_id=reel.id,
            kind=MediaAssetKind.frame,
            path="data/media/frame_00001.jpg",
            sequence_number=1,
            timestamp_seconds=0,
        )
    )
    job = ProcessingJob(reel_id=reel.id, status=JobStatus.queued, current_stage="ocr_and_transcription")
    db.add(job)
    db.commit()
    return reel, job


def _stage(outcome, name: str):
    return next(stage for stage in outcome.stages if stage.stage == name)


def test_failed_transcription_does_not_discard_the_ocr_and_vision_result() -> None:
    """Architecture section 50: Whisper failing continues with VLM plus OCR."""
    with SessionLocal() as db:
        reel, _ = _seed(db, "degrade-speech")
        outcome = run_analysis(
            db,
            reel_id=reel.id,
            speech_provider=BrokenSpeech(),
            ocr_provider=GoodOCR(),
            vision_provider=GoodVision(),
            extraction_provider=GoodExtraction(),
        )
        db.commit()

        assert _stage(outcome, "transcription").status is StageStatus.failed
        assert "whisper weights" in _stage(outcome, "transcription").error_message
        assert _stage(outcome, "ocr").status is StageStatus.succeeded
        assert outcome.ocr_text == "3 sets of 10"
        assert outcome.extractions[0].data["exercise"] == "Split Squat"
        assert db.query(OCRResult).filter(OCRResult.reel_id == reel.id).count() == 1
        assert db.query(VisualAnalysis).filter(VisualAnalysis.reel_id == reel.id).count() == 1


def test_failed_ocr_does_not_discard_the_transcript() -> None:
    """Architecture section 50: OCR failing continues with VLM plus transcript."""
    with SessionLocal() as db:
        reel, _ = _seed(db, "degrade-ocr")
        outcome = run_analysis(
            db,
            reel_id=reel.id,
            speech_provider=GoodSpeech(),
            ocr_provider=BrokenOCR(),
            vision_provider=GoodVision(),
            extraction_provider=GoodExtraction(),
        )
        db.commit()

        assert _stage(outcome, "ocr").status is StageStatus.failed
        assert outcome.transcript_text == "Spoken narration."
        assert db.query(Transcript).filter(Transcript.reel_id == reel.id).count() == 1
        assert db.query(ExtractedContent).filter(ExtractedContent.reel_id == reel.id).count() == 1


def test_vision_failure_falls_back_before_giving_up() -> None:
    with SessionLocal() as db:
        reel, _ = _seed(db, "degrade-vision-fallback")
        outcome = run_analysis(
            db,
            reel_id=reel.id,
            speech_provider=GoodSpeech(),
            ocr_provider=GoodOCR(),
            vision_provider=BrokenVision(),
            extraction_provider=GoodExtraction(),
            fallback_vision_provider=FallbackVision(),
        )
        db.commit()

        stage = _stage(outcome, "vision")
        assert stage.status is StageStatus.degraded
        assert stage.model == "local-evidence-v1"
        assert "503" in stage.error_message
        assert outcome.degraded is False  # degraded is not failed: the stage produced a result
        stored = db.query(VisualAnalysis).filter(VisualAnalysis.reel_id == reel.id).one()
        assert stored.model == "local-evidence-v1"


def test_vision_failure_without_a_fallback_still_runs_extraction() -> None:
    with SessionLocal() as db:
        reel, _ = _seed(db, "degrade-vision-hard")
        outcome = run_analysis(
            db,
            reel_id=reel.id,
            speech_provider=GoodSpeech(),
            ocr_provider=GoodOCR(),
            vision_provider=BrokenVision(),
            extraction_provider=GoodExtraction(),
        )
        db.commit()

        assert _stage(outcome, "vision").status is StageStatus.failed
        assert outcome.degraded is True
        assert outcome.failed_stages == ["vision"]
        assert db.query(VisualAnalysis).filter(VisualAnalysis.reel_id == reel.id).count() == 0
        assert outcome.extractions  # extraction still ran on OCR and transcript evidence


def test_failed_extraction_preserves_the_previous_knowledge() -> None:
    with SessionLocal() as db:
        reel, _ = _seed(db, "degrade-extraction")
        run_analysis(
            db,
            reel_id=reel.id,
            speech_provider=GoodSpeech(),
            ocr_provider=GoodOCR(),
            vision_provider=GoodVision(),
            extraction_provider=GoodExtraction(),
        )
        db.commit()

        outcome = run_analysis(
            db,
            reel_id=reel.id,
            speech_provider=GoodSpeech(),
            ocr_provider=GoodOCR(),
            vision_provider=GoodVision(),
            extraction_provider=BrokenExtraction(),
        )
        db.commit()
        assert _stage(outcome, "extraction").status is StageStatus.failed
        # A failed run must not erase knowledge a good run produced.
        assert db.query(ExtractedContent).filter(ExtractedContent.reel_id == reel.id).count() == 1


def test_a_silent_reel_marks_transcription_skipped_not_failed() -> None:
    with SessionLocal() as db:
        reel, _ = _seed(db, "silent-skip", with_audio=False)
        outcome = run_analysis(
            db,
            reel_id=reel.id,
            speech_provider=GoodSpeech(),
            ocr_provider=GoodOCR(),
            vision_provider=GoodVision(),
            extraction_provider=GoodExtraction(),
        )
        db.commit()
        assert _stage(outcome, "transcription").status is StageStatus.skipped
        assert outcome.degraded is False


def test_worker_records_stage_metrics_and_routes_a_degraded_run_to_review() -> None:
    with SessionLocal() as db:
        reel, job = _seed(db, "metrics-degraded")
        updated = analyze_reel(
            db,
            job.id,
            speech_provider=GoodSpeech(),
            ocr_provider=GoodOCR(),
            vision_provider=BrokenVision(),
            extraction_provider=GoodExtraction(),
        )
        # Extraction confidence is 0.95, which would normally auto-accept; the failed stage wins.
        assert updated.status == JobStatus.needs_review
        metrics = list(db.query(StageMetric).filter(StageMetric.reel_id == reel.id))
        assert {metric.stage for metric in metrics} == {"transcription", "ocr", "vision", "extraction"}
        vision_metric = next(metric for metric in metrics if metric.stage == "vision")
        assert vision_metric.status is StageStatus.failed
        assert vision_metric.duration_ms >= 0
        ocr_metric = next(metric for metric in metrics if metric.stage == "ocr")
        assert ocr_metric.items_processed == 1


def test_pipeline_metrics_endpoint_aggregates_latency_and_failure_rate() -> None:
    # A dedicated identity, because the endpoint aggregates every Reel the user owns and other
    # tests in this module analyse Reels under the default one.
    owner = {"X-User-Id": "metrics-owner"}
    collection_id = client.post("/api/collections", json={"name": "Metrics"}, headers=owner).json()["id"]
    with SessionLocal() as db:
        reel = Reel(
            collection_id=collection_id,
            instagram_url="upload://metrics-endpoint.mp4",
            status=ReelStatus.completed,
            processing_version="v1",
        )
        db.add(reel)
        db.flush()
        db.add_all(
            [
                StageMetric(reel_id=reel.id, stage="ocr", status=StageStatus.succeeded, duration_ms=10.0, items_processed=4),
                StageMetric(reel_id=reel.id, stage="ocr", status=StageStatus.failed, duration_ms=30.0),
                StageMetric(reel_id=reel.id, stage="transcription", status=StageStatus.skipped, duration_ms=0.0),
                StageMetric(reel_id=reel.id, stage="vision", status=StageStatus.succeeded, duration_ms=50.0, model="fake-vlm"),
            ]
        )
        db.commit()

    response = client.get("/api/system/metrics/pipeline", headers=owner)
    assert response.status_code == 200
    body = response.json()
    stages = {stage["stage"]: stage for stage in body["stages"]}
    assert stages["ocr"]["runs"] == 2
    assert stages["ocr"]["failure_rate"] == 0.5
    assert stages["ocr"]["average_duration_ms"] == 20.0
    assert stages["ocr"]["total_items_processed"] == 4
    # A skipped stage never ran, so it must not inflate the failure rate.
    assert stages["transcription"]["failure_rate"] == 0.0
    assert stages["vision"]["models"] == ["fake-vlm"]
    assert body["degraded_reels"] == 1
    assert body["stages"][0]["stage"] == "transcription"


def test_pipeline_metrics_are_owner_scoped() -> None:
    response = client.get("/api/system/metrics/pipeline", headers={"X-User-Id": "nobody-here"})
    assert response.status_code == 200
    assert response.json() == {"analyzed_reels": 0, "degraded_reels": 0, "stages": []}


def test_json_formatter_emits_request_id_and_extra_fields() -> None:
    token = request_id_var.set("req-abc")
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "Request completed", None, None)
    record.status_code = 200
    payload = json.loads(JsonFormatter().format(record))
    request_id_var.reset(token)

    assert payload["message"] == "Request completed"
    assert payload["level"] == "INFO"
    assert payload["request_id"] == "req-abc"
    assert payload["status_code"] == 200


def test_responses_echo_a_supplied_request_id() -> None:
    response = client.get("/health", headers={"X-Request-Id": "trace-me"})
    assert response.headers["X-Request-Id"] == "trace-me"
