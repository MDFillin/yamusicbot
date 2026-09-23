"""Разбор ссылок на music.yandex.*"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

_HOST = r"(?:https?://)?music\.yandex\.(?:ru|com|by|kz|uz)"

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("track", re.compile(_HOST + r"/album/(?P<album>\d+)/track/(?P<track>\d+)")),
    ("track", re.compile(_HOST + r"/track/(?P<track>\d+)")),
    ("album", re.compile(_HOST + r"/album/(?P<album>\d+)")),
    ("playlist", re.compile(_HOST + r"/users/(?P<owner>[^/?#\s]+)/playlists/(?P<kind>\d+)")),
    ("playlist_uuid", re.compile(_HOST + r"/playlists/(?P<uuid>[\w.\-]+)")),
    ("artist", re.compile(_HOST + r"/artist/(?P<artist>\d+)")),
]

LinkKind = Literal["track", "album", "playlist", "playlist_uuid", "artist"]


@dataclass(frozen=True)
class YMLink:
    kind: LinkKind
    track: str | None = None
    album: str | None = None
    owner: str | None = None
    playlist_kind: str | None = None
    uuid: str | None = None
    artist: str | None = None


def parse_link(text: str) -> YMLink | None:
    for kind, pattern in _PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        g = m.groupdict()
        return YMLink(
            kind=kind,  # type: ignore[arg-type]
            track=g.get("track"),
            album=g.get("album"),
            owner=g.get("owner"),
            playlist_kind=g.get("kind"),
            uuid=g.get("uuid"),
            artist=g.get("artist"),
        )
    return None
