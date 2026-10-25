import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.db.database import SessionLocal
from app.main import app
from app.models.job import JobStatus, ProcessingJob
from app.models.job_event import JobEvent
from app.models.source import Source, SourceStatus
from app.services.fetching import FetchError, WebFetcher, YouTubeFetcher
from app.workers.pipeline import AWAITING_PROCESSOR, start_source_job

client = TestClient(app)

PUBLIC = "93.184.216.34"


def _public(_host: str, _port: int) -> list[str]:
    return [PUBLIC]


def _web(handler, resolver=_public) -> WebFetcher:
    return WebFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)), resolver=resolver)


def _page(html: str, content_type: str = "text/html; charset=utf-8") -> httpx.Response:
    return httpx.Response(200, headers={"content-type": content_type}, content=html.encode("utf-8"))


def test_a_page_is_saved_with_its_title(tmp_path: Path) -> None:
    fetcher = _web(lambda request: _page("<html><head><title> Hybrid   search\nguide </title></head><body>x</body></html>"))
    content = fetcher.fetch("https://example.com/guide", tmp_path)
    assert content.mime_type == "text/html"
    assert content.title == "Hybrid search guide"
    assert content.details["charset"] == "utf-8"
    assert Path(content.path).read_text(encoding="utf-8").startswith("<html>")


def test_the_open_graph_title_is_a_fallback(tmp_path: Path) -> None:
    fetcher = _web(lambda request: _page('<meta property="og:title" content="From OG"><p>no title tag</p>'))
    assert fetcher.fetch("https://example.com/og", tmp_path).title == "From OG"


def test_a_pdf_link_is_kept_as_a_pdf(tmp_path: Path) -> None:
    fetcher = _web(lambda request: httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF-1.7"))
    content = fetcher.fetch("https://example.com/paper.pdf", tmp_path)
    assert content.path.endswith("page.pdf")
    assert content.title is None


def test_redirects_are_followed_and_recorded(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "/new"})
        return _page("<title>Moved</title>")

    content = _web(handler).fetch("https://example.com/old", tmp_path)
    assert content.details["fetched_url"] == "https://example.com/new"
    assert content.details["redirected_from"] == "https://example.com/old"


def test_a_redirect_into_the_private_network_is_refused(tmp_path: Path) -> None:
    def resolver(host: str, _port: int) -> list[str]:
        return ["10.0.0.5"] if host == "intranet.example" else [PUBLIC]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://intranet.example/admin"})

    with pytest.raises(FetchError, match="private or local network"):
        _web(handler, resolver).fetch("https://example.com/", tmp_path)


def test_a_host_resolving_to_any_private_address_is_refused(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _page("<title>never</title>")

    with pytest.raises(FetchError, match="private or local network"):
        _web(handler, lambda _h, _p: [PUBLIC, "127.0.0.1"]).fetch("https://rebind.example/", tmp_path)
    # The check happens before any connection is made.
    assert requests == []


def test_endless_redirects_stop(tmp_path: Path) -> None:
    handler = lambda request: httpx.Response(302, headers={"location": request.url.path + "x"})  # noqa: E731
    with pytest.raises(FetchError, match="redirected more than"):
        _web(handler).fetch("https://example.com/a", tmp_path)


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(404), "returned HTTP 404"),
        (httpx.Response(200, headers={"content-type": "image/png"}, content=b"png"), "type image/png cannot be added"),
        (httpx.Response(200, headers={"content-type": "text/html"}, content=b""), "was empty"),
    ],
)
def test_unusable_responses_are_reported(tmp_path: Path, response: httpx.Response, message: str) -> None:
    with pytest.raises(FetchError, match=message):
        _web(lambda request: response).fetch("https://example.com/", tmp_path)


def test_an_oversized_page_is_refused_and_removed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MAX_FETCH_BYTES", "100")
    get_settings.cache_clear()
    try:
        with pytest.raises(FetchError, match="larger than the 100 byte limit"):
            _web(lambda request: _page("x" * 500)).fetch("https://example.com/big", tmp_path)
    finally:
        get_settings.cache_clear()
    assert not list(tmp_path.glob("page.*"))


def _transcript(language: str, generated: bool, lines: list[str]):
    snippets = [SimpleNamespace(text=text, start=float(index), duration=1.0) for index, text in enumerate(lines)]
    return SimpleNamespace(language_code=language, is_generated=generated, fetch=lambda: snippets)


class FakeTranscriptApi:
    def __init__(self, transcripts=None, error: Exception | None = None) -> None:
        self._transcripts = transcripts or []
        self._error = error

    def list(self, _video_id: str):
        if self._error:
            raise self._error
        return iter(self._transcripts)


def _oembed(payload: dict | None):
    def handler(request: httpx.Request) -> httpx.Response:
        if payload is None:
            return httpx.Response(404)
        return httpx.Response(200, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_youtube_prefers_written_captions_in_a_preferred_language(tmp_path: Path) -> None:
    api = FakeTranscriptApi(
        [
            _transcript("de", False, ["Hallo"]),
            _transcript("en", True, ["auto english"]),
            _transcript("en-GB", False, ["written english", "  ", "second line"]),
        ]
    )
    fetcher = YouTubeFetcher(transcript_api=api, client=_oembed({"title": "A talk", "author_name": "Channel"}))
    content = fetcher.fetch("dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ", tmp_path)

    saved = json.loads(Path(content.path).read_text(encoding="utf-8"))
    assert saved["language"] == "en-GB" and saved["is_generated"] is False
    # Blank caption lines are dropped; timestamps are kept for citations.
    assert [segment["text"] for segment in saved["segments"]] == ["written english", "second line"]
    assert saved["segments"][1]["start"] == 2.0
    assert (content.title, content.author) == ("A talk", "Channel")


def test_youtube_falls_back_to_any_language_and_survives_a_missing_title(tmp_path: Path) -> None:
    fetcher = YouTubeFetcher(transcript_api=FakeTranscriptApi([_transcript("fr", True, ["Bonjour"])]), client=_oembed(None))
    content = fetcher.fetch("dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ", tmp_path)
    assert content.details["transcript_language"] == "fr"
    assert content.title is None


def test_youtube_errors_become_readable_reasons(tmp_path: Path) -> None:
    TranscriptsDisabled = type("TranscriptsDisabled", (Exception,), {})
    fetcher = YouTubeFetcher(transcript_api=FakeTranscriptApi(error=TranscriptsDisabled("raw")), client=_oembed(None))
    with pytest.raises(FetchError, match="Captions are turned off"):
        fetcher.fetch("dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ", tmp_path)
    with pytest.raises(FetchError, match="no transcript"):
        YouTubeFetcher(transcript_api=FakeTranscriptApi([]), client=_oembed(None)).fetch(
            "dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ", tmp_path
        )


def _queued_job(url: str, title: str | None = None) -> tuple[int, int]:
    payload = {"url": url} if title is None else {"url": url, "title": title}
    source_id = client.post("/api/sources/url", json=payload).json()["source_id"]
    with SessionLocal() as db:
        job_id = db.query(ProcessingJob).filter(ProcessingJob.source_id == source_id).one().id
    return source_id, job_id


def test_the_worker_fetches_a_link_and_holds_it_for_its_processor() -> None:
    source_id, job_id = _queued_job("https://example.com/worker-fetch")
    fetcher = _web(lambda request: _page("<title>Fetched title</title>"))
    with SessionLocal() as db:
        job = start_source_job(db, job_id, web_fetcher=fetcher)
        assert job.status is JobStatus.queued and job.current_stage == AWAITING_PROCESSOR
        source = db.get(Source, source_id)
        assert source.status is SourceStatus.pending
        assert source.title == "Fetched title"
        assert source.mime_type == "text/html"
        assert Path(source.media_path).is_file()
        events = [event.stage for event in db.query(JobEvent).filter(JobEvent.job_id == job_id).order_by(JobEvent.id)]
        assert events[-3:] == ["fetching", "fetching", AWAITING_PROCESSOR]

    # The page snapshot is served only as a sandboxed download, never rendered on this origin.
    listing = client.get(f"/api/sources/{source_id}/media").json()
    original = next(item for item in listing if item["kind"] == "original")
    served = client.get(original["url"])
    assert served.headers["content-disposition"] == "attachment"
    assert served.headers["content-security-policy"] == "sandbox"
    assert served.headers["x-content-type-options"] == "nosniff"


def test_a_title_the_user_typed_is_kept() -> None:
    source_id, job_id = _queued_job("https://example.com/user-title", title="My own name")
    with SessionLocal() as db:
        start_source_job(db, job_id, web_fetcher=_web(lambda request: _page("<title>Page title</title>")))
        assert db.get(Source, source_id).title == "My own name"


def test_a_failed_fetch_fails_the_job_with_the_reason() -> None:
    source_id, job_id = _queued_job("https://example.com/missing")
    with SessionLocal() as db:
        job = start_source_job(db, job_id, web_fetcher=_web(lambda request: httpx.Response(404)))
        assert job.status is JobStatus.failed
        assert job.error_message == "The page returned HTTP 404"
        assert db.get(Source, source_id).status is SourceStatus.failed


def test_the_worker_fetches_youtube_transcripts() -> None:
    source_id, job_id = _queued_job("https://youtu.be/wOrKeRtEsT1")
    fetcher = YouTubeFetcher(
        transcript_api=FakeTranscriptApi([_transcript("en", False, ["Welcome"])]),
        client=_oembed({"title": "Worker video", "author_name": "Someone"}),
    )
    with SessionLocal() as db:
        job = start_source_job(db, job_id, youtube_fetcher=fetcher)
        assert job.current_stage == AWAITING_PROCESSOR
        source = db.get(Source, source_id)
        assert (source.title, source.author) == ("Worker video", "Someone")
        assert source.details["video_id"] == "wOrKeRtEsT1"
        assert source.details["transcript_segments"] == 1


def test_the_local_runner_resumes_links_that_were_never_fetched() -> None:
    from app.services import local_runner

    _, job_id = _queued_job("https://example.com/resume-me")
    submitted: list[int] = []
    original = local_runner.submit
    local_runner.submit = lambda queued_id: submitted.append(queued_id) or "local-test"
    try:
        local_runner.resume_unfinished()
    finally:
        local_runner.submit = original
    assert job_id in submitted
