"""Link normalisation and the network policy every fetched link must satisfy.

Normalising before the duplicate check is what makes "the same link" mean the same thing: a YouTube
video shared as `youtu.be/ID`, `/shorts/ID` or `watch?v=ID&t=30` is one source, not three.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit, urlunsplit

from app.core.config import get_settings
from app.models.source import SourceKind

YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
    "youtu.be",
}
_YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_YOUTUBE_PATH_PREFIXES = ("shorts", "embed", "live", "v")
_DEFAULT_PORTS = {"http": 80, "https": 443}
_LOCAL_NAMES = ("localhost", ".localhost", ".local", ".internal")

# Kinds whose content lives behind a link and is fetched by the worker rather than uploaded.
FETCHED_KINDS = frozenset({SourceKind.web, SourceKind.youtube})


class InvalidLinkError(ValueError):
    pass


@dataclass(frozen=True)
class NormalizedLink:
    url: str
    kind: SourceKind
    details: dict[str, Any] = field(default_factory=dict)


def normalize_link(raw: str) -> NormalizedLink:
    parts = urlsplit(raw.strip())
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS:
        raise InvalidLinkError("Only http and https links can be added")
    if parts.username or parts.password:
        raise InvalidLinkError("Links with embedded credentials are not accepted")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise InvalidLinkError("The link has no host")
    if not get_settings().fetch_allow_private_hosts and is_private_hostname(host):
        raise InvalidLinkError("Links to private or local network addresses are not accepted")

    if host in YOUTUBE_HOSTS:
        video_id = youtube_video_id(parts.path, parts.query, short_host=host == "youtu.be")
        if video_id is None:
            raise InvalidLinkError("Only links to a single YouTube video are supported")
        return NormalizedLink(
            url=f"https://www.youtube.com/watch?v={video_id}", kind=SourceKind.youtube, details={"video_id": video_id}
        )

    netloc = f"[{host}]" if ":" in host else host
    if parts.port and parts.port != _DEFAULT_PORTS[scheme]:
        netloc = f"{netloc}:{parts.port}"
    # The fragment never reaches the server, so it cannot distinguish two documents.
    return NormalizedLink(url=urlunsplit((scheme, netloc, parts.path.rstrip("/"), parts.query, "")), kind=SourceKind.web)


def youtube_video_id(path: str, query: str, *, short_host: bool = False) -> str | None:
    segments = [segment for segment in path.split("/") if segment]
    if short_host:
        candidate = segments[0] if segments else None
    elif segments[:1] == ["watch"]:
        candidate = (parse_qs(query).get("v") or [None])[0]
    elif len(segments) >= 2 and segments[0] in _YOUTUBE_PATH_PREFIXES:
        candidate = segments[1]
    else:
        candidate = None
    return candidate if candidate and _YOUTUBE_ID.match(candidate) else None


def is_private_hostname(host: str) -> bool:
    """True for names and literal addresses that obviously point inside the local network.

    Ordinary names pass here; the fetcher resolves them and checks every address before connecting.
    """
    if host == "localhost" or host.endswith(_LOCAL_NAMES):
        return True
    try:
        return not is_public_address(host)
    except ValueError:
        return False


def is_public_address(address: str) -> bool:
    """Whether an IP address is globally routable. Raises ValueError for anything that is not an IP."""
    # A resolved IPv6 link-local address can carry a zone suffix such as "%eth0".
    ip = ipaddress.ip_address(address.strip("[]").split("%")[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast
