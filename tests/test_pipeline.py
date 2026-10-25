from pathlib import Path

from sqlalchemy import select

from app.db.database import SessionLocal
from app.models.collection import Collection
from app.models.job import JobStatus, ProcessingJob
from app.models.job_event import JobEvent
from app.models.media_asset import MediaAsset, MediaAssetKind
from app.models.source import Source, SourceKind, SourceStatus
from app.services.video import PreparedMedia, VideoMetadata
from app.workers.pipeline import prepare_source_media


class FakeVideoProcessor:
    def prepare(self, _media_path: str, output_directory: str | Path) -> PreparedMedia:
        output = Path(output_directory)
        return PreparedMedia(
            audio_path=str(output / "audio.wav"),
            frame_paths=[str(output / "frames" / "frame_00001.jpg"), str(output / "frames" / "frame_00002.jpg")],
            metadata=VideoMetadata(duration_seconds=8.0, has_audio=True, width=1080, height=1920),
        )


def test_prepare_job_persists_artifacts_and_advances_stage() -> None:
    with SessionLocal() as db:
        collection = Collection(name="Pipeline test")
        db.add(collection)
        db.flush()
        source = Source(
            user_id=collection.user_id,
            kind=SourceKind.video,
            title="upload://pipeline-test.mp4",
            collections=[collection],
            media_path="data/media/pipeline-test.mp4",
            status=SourceStatus.pending,
            processing_version="v1",
        )
        db.add(source)
        db.flush()
        job = ProcessingJob(source_id=source.id)
        db.add(job)
        db.commit()

        updated = prepare_source_media(db, job.id, FakeVideoProcessor())
        assets = list(db.scalars(select(MediaAsset).where(MediaAsset.source_id == source.id)))

        assert updated.status == JobStatus.queued
        assert updated.current_stage == "ocr_and_transcription"
        assert updated.attempt_count == 1
        assert len(assets) == 3
        assert {asset.kind for asset in assets} == {MediaAssetKind.audio, MediaAssetKind.frame}
        events = list(db.scalars(select(JobEvent).where(JobEvent.job_id == job.id)))
        # Publication runs at the end of the stage; this fake produces no files on disk, so it
        # records the attempt while storing nothing.
        assert [event.event_type for event in events] == ["started", "completed", "published"]
        assert events[-1].stage == "media_publication"
