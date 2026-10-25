import io
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.db.database import SessionLocal
from app.main import app
from app.models.job import JobStatus, ProcessingJob
from app.models.stored_object import StoredObject, StoredObjectKind
from app.workers.pipeline import AWAITING_PROCESSOR, start_source_job

client = TestClient(app)


def _docx(with_body: bool = True) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        if with_body:
            archive.writestr("word/document.xml", "<w:document/>")
    return buffer.getvalue()


# Minimal leading bytes for each accepted type. A unique tail keeps the content hashes distinct.
SAMPLES = {
    "report.pdf": (b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n", "document", "application/pdf"),
    "notes.txt": ("Plain notes about retrieval.\n".encode(), "document", "text/plain"),
    "readme.md": (b"# Heading\n\nSome *markdown*.\n", "document", "text/markdown"),
    "letter.docx": (_docx(), "document", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    "chart.png": (b"\x89PNG\r\n\x1a\n" + b"\x00" * 16, "image", "image/png"),
    "photo.jpg": (b"\xff\xd8\xff\xe0" + b"\x00" * 16, "image", "image/jpeg"),
    "sticker.webp": (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "image", "image/webp"),
    "talk.mp3": (b"ID3\x04\x00\x00\x00\x00\x00\x00", "audio", "audio/mpeg"),
    "clip.wav": (b"RIFF\x00\x00\x00\x00WAVEfmt ", "audio", "audio/wav"),
    "memo.m4a": (b"\x00\x00\x00\x20ftypM4A ", "audio", "audio/mp4"),
    "voice.ogg": (b"OggS\x00\x02" + b"\x00" * 10, "audio", "audio/ogg"),
    "song.flac": (b"fLaC\x00\x00\x00\x22", "audio", "audio/flac"),
    "demo.mp4": (b"\x00\x00\x00\x18ftypmp42", "video", "video/mp4"),
    "screen.mov": (b"\x00\x00\x00\x14ftypqt  ", "video", "video/quicktime"),
    "stream.webm": (b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81", "video", "video/webm"),
}


def _upload(filename: str, data: bytes, declared: str = "application/octet-stream", headers: dict | None = None):
    return client.post("/api/sources/upload", files={"media": (filename, data, declared)}, headers=headers or {})


def _media_files() -> set[str]:
    root = Path(get_settings().media_root)
    return {str(path) for path in root.glob("*") if path.is_file()} if root.exists() else set()


@pytest.mark.parametrize("filename", sorted(SAMPLES))
def test_every_supported_type_is_accepted_with_its_verified_kind(filename: str) -> None:
    data, kind, mime_type = SAMPLES[filename]
    owner = {"X-User-Id": f"types-{filename}"}
    response = _upload(filename, data, headers=owner)
    assert response.status_code == 202, response.text
    source = client.get(f"/api/sources/{response.json()['source_id']}", headers=owner).json()
    assert source["kind"] == kind
    assert source["mime_type"] == mime_type
    assert source["title"] == Path(filename).stem


def test_the_declared_type_is_ignored_in_favour_of_the_content() -> None:
    data, _, _ = SAMPLES["chart.png"]
    response = _upload("declared.png", data + b"declared", declared="application/pdf")
    assert response.status_code == 202
    assert client.get(f"/api/sources/{response.json()['source_id']}").json()["mime_type"] == "image/png"


def test_a_file_without_an_extension_uses_its_declared_type() -> None:
    response = _upload("scan", b"%PDF-1.4\nno-extension", declared="application/pdf")
    assert response.status_code == 202
    assert client.get(f"/api/sources/{response.json()['source_id']}").json()["kind"] == "document"


@pytest.mark.parametrize(
    ("filename", "data", "message"),
    [
        ("renamed.pdf", b"\x89PNG\r\n\x1a\n" + b"\x00" * 8, "does not match its .pdf extension"),
        ("binary.txt", b"text then \x00 binary", "contains binary data"),
        ("latin1.txt", "café".encode("latin-1"), "not UTF-8 text"),
        ("empty-zip.docx", _docx(with_body=False), "no document body"),
        ("broken.docx", b"PK\x03\x04 not really a zip", "damaged"),
        ("program.exe", b"MZ\x90\x00", "Unsupported file type .exe"),
    ],
)
def test_mislabelled_or_unsupported_files_are_refused_and_not_kept(filename: str, data: bytes, message: str) -> None:
    before = _media_files()
    response = _upload(filename, data)
    assert response.status_code == 415
    assert message in response.json()["detail"]
    assert _media_files() == before


def test_an_empty_file_is_refused() -> None:
    response = _upload("nothing.txt", b"")
    assert response.status_code == 422


def test_pasted_text_becomes_a_text_source() -> None:
    collection_id = client.post("/api/collections", json={"name": "Notes"}).json()["id"]
    response = client.post(
        "/api/sources/text",
        json={"title": "Meeting notes", "text": "We agreed on hybrid retrieval.", "collection_id": collection_id},
    )
    assert response.status_code == 202
    source = client.get(f"/api/sources/{response.json()['source_id']}").json()
    assert source["kind"] == "text"
    assert source["title"] == "Meeting notes"
    assert Path(source["media_path"]).read_text(encoding="utf-8") == "We agreed on hybrid retrieval."
    assert response.json()["source_id"] in {
        item["id"] for item in client.get("/api/sources", params={"collection_id": collection_id}).json()
    }

    again = client.post("/api/sources/text", json={"title": "Same again", "text": "We agreed on hybrid retrieval."})
    assert again.status_code == 409
    assert client.post("/api/sources/text", json={"title": "Blank", "text": "   "}).status_code == 422


def test_pasted_text_is_bounded(monkeypatch) -> None:
    monkeypatch.setenv("MAX_TEXT_CHARACTERS", "10")
    get_settings.cache_clear()
    try:
        response = client.post("/api/sources/text", json={"title": "Long", "text": "x" * 11})
        assert response.status_code == 413
    finally:
        get_settings.cache_clear()


def test_a_non_video_upload_is_stored_and_waits_for_its_processor() -> None:
    response = _upload("waiting.pdf", b"%PDF-1.7\nwaiting for a processor")
    source_id = response.json()["source_id"]
    with SessionLocal() as db:
        job = db.query(ProcessingJob).filter(ProcessingJob.source_id == source_id).one()
        job = start_source_job(db, job.id)
        assert job.status is JobStatus.queued
        assert job.current_stage == AWAITING_PROCESSOR
        stored = db.query(StoredObject).filter(StoredObject.source_id == source_id).one()
        assert stored.kind is StoredObjectKind.original
        assert stored.content_type == "application/pdf"
    assert client.get(f"/api/sources/{source_id}").json()["status"] == "pending"


def test_capabilities_list_the_accepted_upload_types() -> None:
    capabilities = client.get("/api/capabilities").json()
    assert ".pdf" in capabilities["upload_types"]["document"]
    assert ".mp3" in capabilities["upload_types"]["audio"]
    assert "pasted_text" in capabilities["ingestion"]
