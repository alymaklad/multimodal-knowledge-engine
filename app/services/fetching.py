"""Fetching the raw content behind a link: a web page's bytes, or a YouTube video's transcript.

This runs in the worker, never in the request. The output is a file on disk plus a little metadata;
turning it into searchable evidence is a processor's job, the same as for an uploaded file.

Network policy for web pages, because a link is attacker-controlled input that makes the server
open a connection:

- only http and https, and every redirect hop is checked again rather than followed blindly;
- every address the host resolves to must be publicly routable, unless FETCH_ALLOW_PRIVATE_HOSTS is
  set, so a link cannot reach the machine's own services or its local network;
- responses are size-capped while streaming and limited to content types a processor can read.

The resolution check and the connection are separate lookups, so a DNS server answering differently
the second time could still steer a request inward. Closing that fully needs connecting to the
checked address itself; for a personal install the check above is the proportionate defence.
"""

from __future__ import annotations

import json
import socket
from collections.abc import Callable
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from app.core.config import get_settings
from app.services.links import is_public_address

Resolver = Callable[[str, int], list[str]]

# Content types a processor can turn into text, mapped to the suffix the saved file gets.
FETCHABLE_TYPES = {
    "text/html": ".html",
    "application/xhtml+xml": ".html",
    "text/plain": ".txt",
    "text/markdown": ".md",
    "application/pdf": ".pdf",
}
TITLE_SCAN_BYTES = 512 * 1024


class FetchError(RuntimeError):
    """A link could not be fetched. The message is safe to show the user."""


@dataclass(frozen=True)
class FetchedContent:
    path: str
    mime_type: str
    title: str | None = None
    author: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


def resolve_host(host: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as error:
        raise FetchError(f"The host {host} could not be resolved") from error
    return sorted({info[4][0] for info in infos})


class WebFetcher:
    def __init__(self, client: httpx.Client | None = None, resolver: Resolver | None = None) -> None:
        settings = get_settings()
        self._client = client or httpx.Client(timeout=settings.fetch_timeout_seconds)
        self._resolver = resolver or resolve_host

    def fetch(self, url: str, directory: Path) -> FetchedContent:
        settings = get_settings()
        current = url
        for _ in range(settings.fetch_max_redirects + 1):
            self._check_destination(current)
            try:
                with self._client.stream(
                    "GET",
                    current,
                    headers={"User-Agent": settings.fetch_user_agent, "Accept": ", ".join(FETCHABLE_TYPES)},
                    follow_redirects=False,
                ) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise FetchError("The page redirected without saying where to")
                        current = urljoin(current, location)
                        continue
                    return self._save(response, current, url, directory)
            except httpx.TimeoutException as error:
                raise FetchError("The page took too long to respond") from error
            except httpx.HTTPError as error:
                raise FetchError(f"The page could not be fetched ({type(error).__name__})") from error
        raise FetchError(f"The page redirected more than {settings.fetch_max_redirects} times")

    def _check_destination(self, url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"}:
            raise FetchError("The link redirected to something other than a web page")
        if get_settings().fetch_allow_private_hosts:
            return
        host = parts.hostname or ""
        addresses = self._resolver(host, parts.port or (443 if parts.scheme == "https" else 80))
        if not addresses:
            raise FetchError(f"The host {host} could not be resolved")
        if not all(is_public_address(address) for address in addresses):
            raise FetchError("The link points to a private or local network address")

    def _save(self, response: httpx.Response, final_url: str, requested_url: str, directory: Path) -> FetchedContent:
        if response.status_code >= 400:
            raise FetchError(f"The page returned HTTP {response.status_code}")
        header = response.headers.get("content-type", "")
        mime_type = header.split(";")[0].strip().lower()
        suffix = FETCHABLE_TYPES.get(mime_type)
        if suffix is None:
            raise FetchError(f"Pages of type {mime_type or 'unknown'} cannot be added")

        limit = get_settings().max_fetch_bytes
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"page{suffix}"
        size = 0
        head = bytearray()
        try:
            with target.open("wb") as destination:
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > limit:
                        raise FetchError(f"The page is larger than the {limit} byte limit")
                    if len(head) < TITLE_SCAN_BYTES:
                        head.extend(chunk[: TITLE_SCAN_BYTES - len(head)])
                    destination.write(chunk)
        except Exception:
            target.unlink(missing_ok=True)
            raise
        if size == 0:
            target.unlink(missing_ok=True)
            raise FetchError("The page was empty")

        charset = response.charset_encoding
        details: dict[str, Any] = {"fetched_url": final_url, "content_bytes": size}
        if charset:
            details["charset"] = charset
        if final_url != requested_url:
            details["redirected_from"] = requested_url
        title = _html_title(bytes(head), charset) if suffix == ".html" else None
        return FetchedContent(path=str(target), mime_type=mime_type, title=title, details=details)


class _TitleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: str | None = None
        self.og_title: str | None = None
        self._in_title = False
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title" and self.title is None:
            self._in_title = True
        elif tag == "meta":
            values = dict(attrs)
            if values.get("property") == "og:title" and values.get("content"):
                self.og_title = values["content"]

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "title" and self._in_title:
            self._in_title = False
            self.title = " ".join("".join(self._parts).split()) or None


def _html_title(head: bytes, charset: str | None) -> str | None:
    parser = _TitleParser()
    try:
        parser.feed(head.decode(charset or "utf-8", errors="replace"))
    except (LookupError, AssertionError):
        return None
    title = parser.title or parser.og_title
    return title[:500] if title else None


class YouTubeFetcher:
    """Fetches a video's transcript and title. Captions are the content; nothing is downloaded.

    `youtube-transcript-api` reads the same captions the YouTube player shows. It is not an official
    API, so it can break when YouTube changes, and YouTube may refuse requests from cloud addresses.
    """

    def __init__(self, transcript_api: Any | None = None, client: httpx.Client | None = None) -> None:
        self._api = transcript_api
        self._client = client or httpx.Client(timeout=get_settings().fetch_timeout_seconds)

    def fetch(self, video_id: str, url: str, directory: Path) -> FetchedContent:
        api = self._api or _load_transcript_api()
        try:
            transcript = _choose_transcript(list(api.list(video_id)), _preferred_languages())
            snippets = transcript.fetch()
        except FetchError:
            raise
        except Exception as error:
            raise FetchError(_youtube_reason(error)) from error

        segments = [
            {"text": snippet.text, "start": float(snippet.start), "duration": float(snippet.duration)}
            for snippet in snippets
            if snippet.text and snippet.text.strip()
        ]
        if not segments:
            raise FetchError("The video's transcript is empty")
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / "transcript.json"
        target.write_text(
            json.dumps(
                {
                    "video_id": video_id,
                    "language": transcript.language_code,
                    "is_generated": bool(transcript.is_generated),
                    "segments": segments,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        title, author = self._oembed(url)
        return FetchedContent(
            path=str(target),
            mime_type="application/json",
            title=title,
            author=author,
            details={
                "transcript_language": transcript.language_code,
                "transcript_generated": bool(transcript.is_generated),
                "transcript_segments": len(segments),
            },
        )

    def _oembed(self, url: str) -> tuple[str | None, str | None]:
        """Title and channel name. Best effort: a transcript without a title is still a source."""
        try:
            response = self._client.get(
                "https://www.youtube.com/oembed",
                params={"url": url, "format": "json"},
                headers={"User-Agent": get_settings().fetch_user_agent},
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            return None, None
        return payload.get("title"), payload.get("author_name")


def youtube_transcripts_available() -> bool:
    try:
        import youtube_transcript_api  # noqa: F401
    except ImportError:
        return False
    return True


def _load_transcript_api() -> Any:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError as error:
        raise FetchError("Install youtube-transcript-api to add YouTube sources") from error
    return YouTubeTranscriptApi()


def _preferred_languages() -> list[str]:
    return [code.strip() for code in get_settings().youtube_transcript_languages.split(",") if code.strip()]


def _choose_transcript(transcripts: list[Any], languages: list[str]) -> Any:
    """Prefer human-written captions in a preferred language, then generated ones, then anything."""
    if not transcripts:
        raise FetchError("This video has no transcript")

    def rank(transcript: Any) -> tuple[int, int]:
        language = transcript.language_code.split("-")[0]
        preference = languages.index(language) if language in languages else len(languages)
        return (preference, 1 if transcript.is_generated else 0)

    return min(transcripts, key=rank)


_YOUTUBE_REASONS = {
    "TranscriptsDisabled": "Captions are turned off for this video, so it has no transcript to add",
    "NoTranscriptFound": "This video has no transcript",
    "VideoUnavailable": "The video is unavailable",
    "VideoUnplayable": "The video cannot be played",
    "AgeRestricted": "The video is age-restricted",
    "InvalidVideoId": "The link does not point to a valid video",
    "RequestBlocked": "YouTube refused the transcript request from this server",
    "IpBlocked": "YouTube refused the transcript request from this server",
}


def _youtube_reason(error: Exception) -> str:
    return _YOUTUBE_REASONS.get(type(error).__name__, f"The transcript could not be fetched ({type(error).__name__})")
