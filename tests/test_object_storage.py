from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db.database import SessionLocal
from app.main import app
from app.models.collection import Collection
from app.models.media_asset import MediaAsset, MediaAssetKind
from app.models.source import Source, SourceKind, SourceStatus
from app.models.stored_object import StoredObject
from app.services.object_store import (
    LocalObjectStore,
    content_type_for,
    get_object_store,
    object_key,
    source_prefix,
)
from app.services.publishing import publish_source_media

client = TestClient(app)


def test_keys_follow_the_documented_layout() -> None:
    assert source_prefix("local-user", 12) == "users/local-user/sources/12"
    assert object_key("local-user", 12, "video.mp4") == "users/local-user/sources/12/video.mp4"
    assert object_key("local-user", 12, "frames/frame_00001.jpg") == (
        "users/local-user/sources/12/frames/frame_00001.jpg"
    )
    assert content_type_for(Path("a/video.mp4")) == "video/mp4"
    assert content_type_for(Path("a/audio.wav")) == "audio/wav"
    assert content_type_for(Path("a/mystery.bin")) == "application/octet-stream"


def test_local_store_round_trips_a_file(tmp_path) -> None:
    media_file = tmp_path / "source.mp4"
    media_file.write_bytes(b"video-bytes")
    store = LocalObjectStore(tmp_path / "objects")

    info = store.put(media_file,"users/u/sources/1/video.mp4")
    assert info.size_bytes == len(b"video-bytes")
    assert info.content_type == "video/mp4"
    assert info.backend == "local"
    with store.open(info.key) as handle:
        assert handle.read() == b"video-bytes"

    assert store.delete_prefix("users/u/sources/1") == 1
    with pytest.raises(FileNotFoundError):
        store.open(info.key)


def test_local_store_refuses_a_key_that_escapes_its_root(tmp_path) -> None:
    store = LocalObjectStore(tmp_path / "objects")
    with pytest.raises(ValueError, match="escapes the store root"):
        store.open("../../etc/passwd")


def test_local_store_issues_no_signed_url(tmp_path) -> None:
    assert LocalObjectStore(tmp_path).signed_url("users/u/sources/1/video.mp4") is None


def test_deleting_a_missing_prefix_is_not_an_error(tmp_path) -> None:
    assert LocalObjectStore(tmp_path / "objects").delete_prefix("users/u/sources/404") == 0


def test_object_store_factory_rejects_an_unknown_backend(monkeypatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("OBJECT_STORE", "nonexistent")
    get_settings.cache_clear()
    with pytest.raises(ValueError, match="Unsupported object store"):
        get_object_store()
    get_settings.cache_clear()


def _seed_source_with_files(db, tmp_path: Path, name: str) -> Source:
    video = tmp_path / f"{name}.mp4"
    video.write_bytes(b"video")
    audio = tmp_path / f"{name}.wav"
    audio.write_bytes(b"audio")
    frames = tmp_path / "frames"
    frames.mkdir(exist_ok=True)
    frame_paths = []
    for index in (1, 2):
        frame = frames / f"frame_{index:05d}.jpg"
        frame.write_bytes(b"frame")
        frame_paths.append(frame)

    collection = Collection(name=name, user_id="local-user")
    db.add(collection)
    db.flush()
    source = Source(
        user_id=collection.user_id,
        kind=SourceKind.video,
        title=f"upload://{name}.mp4",
        collections=[collection],
        media_path=str(video),
        status=SourceStatus.processing,
        processing_version="v1",
    )
    db.add(source)
    db.flush()
    db.add(MediaAsset(source_id=source.id, kind=MediaAssetKind.audio, path=str(audio)))
    for index, frame in enumerate(frame_paths, start=1):
        db.add(
            MediaAsset(
                source_id=source.id, kind=MediaAssetKind.frame, path=str(frame), sequence_number=index
            )
        )
    db.commit()
    db.refresh(source)
    return source


def test_publishing_stores_video_audio_and_every_frame(tmp_path) -> None:
    store = LocalObjectStore(tmp_path / "objects")
    with SessionLocal() as db:
        source = _seed_source_with_files(db, tmp_path, "publish-all")
        result = publish_source_media(db, source, store=store)
        db.commit()

        assert result.published == 4
        assert result.skipped == 0
        assert result.backend == "local"
        stored = list(db.query(StoredObject).filter(StoredObject.source_id == source.id))
        assert sorted(item.kind.value for item in stored) == ["audio", "frame", "frame", "video"]
        keys = {item.storage_key for item in stored}
        assert f"users/local-user/sources/{source.id}/video.mp4" in keys
        assert f"users/local-user/sources/{source.id}/audio.wav" in keys
        assert f"users/local-user/sources/{source.id}/frames/frame_00001.jpg" in keys
        assert (tmp_path / "objects" / f"users/local-user/sources/{source.id}/video.mp4").is_file()


def test_publishing_skips_working_files_that_no_longer_exist(tmp_path) -> None:
    store = LocalObjectStore(tmp_path / "objects")
    with SessionLocal() as db:
        source = _seed_source_with_files(db, tmp_path, "publish-partial")
        Path(source.media_path).unlink()
        result = publish_source_media(db, source, store=store)
        db.commit()
        assert result.published == 3
        assert result.skipped == 1


def test_republishing_replaces_the_previous_object_records(tmp_path) -> None:
    store = LocalObjectStore(tmp_path / "objects")
    with SessionLocal() as db:
        source = _seed_source_with_files(db, tmp_path, "publish-twice")
        publish_source_media(db, source, store=store)
        db.commit()
        publish_source_media(db, source, store=store)
        db.commit()
        assert db.query(StoredObject).filter(StoredObject.source_id == source.id).count() == 4


def test_media_endpoints_list_download_and_enforce_ownership(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OBJECT_STORE_ROOT", str(tmp_path / "objects"))
    from app.core.config import get_settings

    get_settings.cache_clear()
    with SessionLocal() as db:
        source = _seed_source_with_files(db, tmp_path, "media-api")
        publish_source_media(db, source)
        db.commit()
        source_id = source.id

    listing = client.get(f"/api/sources/{source_id}/media")
    assert listing.status_code == 200
    items = listing.json()
    assert len(items) == 4
    # The local backend cannot sign, so objects are served through this API's ownership check.
    assert all(item["signed"] is False for item in items)
    video = next(item for item in items if item["kind"] == "video")
    assert video["url"] == f"/api/sources/{source_id}/media/{video['id']}"
    assert video["content_type"] == "video/mp4"

    download = client.get(video["url"])
    assert download.status_code == 200
    assert download.content == b"video"

    other = {"X-User-Id": "someone-else"}
    assert client.get(f"/api/sources/{source_id}/media", headers=other).status_code == 404
    assert client.get(video["url"], headers=other).status_code == 404
    assert client.post(f"/api/sources/{source_id}/publish", headers=other).status_code == 404

    republished = client.post(f"/api/sources/{source_id}/publish")
    assert republished.status_code == 200
    assert republished.json()["published"] == 4
    get_settings.cache_clear()


def test_reprocessing_discards_published_object_records(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OBJECT_STORE_ROOT", str(tmp_path / "objects"))
    from app.core.config import get_settings

    get_settings.cache_clear()
    with SessionLocal() as db:
        source = _seed_source_with_files(db, tmp_path, "publish-reprocess")
        publish_source_media(db, source)
        db.commit()
        source_id = source.id

    assert client.post(f"/api/sources/{source_id}/reprocess").status_code == 202
    with SessionLocal() as db:
        assert db.query(StoredObject).filter(StoredObject.source_id == source_id).count() == 0
    get_settings.cache_clear()
