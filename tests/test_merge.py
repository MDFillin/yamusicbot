"""Объединение и копирование плейлистов: bot/merge.py, /merge в чате и /api/merge."""

from __future__ import annotations

import json
from types import SimpleNamespace as NS

import pytest

from bot.merge import LIKES, MAX_TRACKS, Source, merge_into, source_from_link
from bot.sources import SourceNotFoundError
from bot.ym import YandexMusic

ME, FRIEND = 42, 77


def short(track_id, album_id=None, *, bare=False):
    """Как в плейлисте Яндекса: albumId нет, альбом — в полном треке; bare — трек пришёл совсем без данных."""
    track = None if bare else NS(albums=[NS(id=album_id)] if album_id else [])
    return NS(id=track_id, album_id=None, track=track, track_id=str(track_id))


class Client:
    """Поддельный Яндекс: плейлисты по (владелец, kind), лайки, альбомы; применяет diff как Яндекс."""

    def __init__(self) -> None:
        self.playlists: dict[tuple[int, int], NS] = {}
        self.likes: list[NS] = []
        self.albums: dict[str, NS] = {}
        self.changes: list[list[dict]] = []
        self.created: list[str] = []

    def add(self, owner, kind, title, tracks, uuid=None):
        self.playlists[(owner, kind)] = NS(kind=kind, title=title, tracks=list(tracks), revision=1, uuid=uuid,
                                           owner=NS(uid=owner, name="Федя" if owner != ME else "Я", login="x"),
                                           uid=owner, track_count=len(tracks))

    async def users_playlists(self, kind, owner):
        return self.playlists.get((int(owner), int(kind)))

    async def playlist(self, uuid):
        return next((p for p in self.playlists.values() if p.uuid == uuid), None)

    async def users_playlists_list(self):
        return [p for (owner, _), p in self.playlists.items() if owner == ME]

    async def users_likes_tracks(self):
        return NS(tracks=self.likes)

    async def tracks(self, ids):
        return [NS(id=i, albums=[NS(id="99")]) for i in ids]

    async def albums_with_tracks(self, album_id):
        return self.albums.get(str(album_id))

    async def users_playlists_create(self, title, visibility="private"):
        kind = 1000 + len(self.created)
        self.created.append(title)
        self.add(ME, kind, title, [])
        return self.playlists[(ME, kind)]

    async def users_playlists_change(self, kind, diff, revision=1):
        playlist = self.playlists[(ME, int(kind))]
        assert revision == playlist.revision, "ревизия должна быть свежей"
        ops = json.loads(diff)
        self.changes.append(ops)
        for op in ops:
            assert op["op"] == "insert" and op["at"] == len(playlist.tracks), "дописываем строго в конец"
            playlist.tracks[op["at"]:op["at"]] = [short(t["id"], t.get("albumId")) for t in op["tracks"]]
        playlist.revision += 1
        playlist.track_count = len(playlist.tracks)
        return playlist


@pytest.fixture
def ym():
    ym = YandexMusic("t")
    ym.uid = ME
    ym._client = client = Client()
    client.add(ME, 3, "Мне и Феде", [short("1", "10")])
    client.add(ME, 5, "Мои треки", [short("1", "10"), short("2", "20"), short("ugc-mine")])
    client.add(FRIEND, 7, "Федин плейлист", [short("2", "20"), short("3", "30"), short("ugc-feди")], uuid="abc-uuid")
    client.likes = [short("4", "40"), short("3", "30")]
    client.albums["50"] = NS(id=50, title="Группа крови", artists=[NS(name="Кино")],
                             volumes=[[NS(id="51"), NS(id="52")]])
    return ym


def ids(ym, kind=3):
    return [str(s.id) for s in ym._client.playlists[(ME, kind)].tracks]


async def test_merges_two_people_into_one_playlist_without_repeats(ym):
    sources = [Source("pl", f"{ME}.5", "Мои треки"), Source("pl", f"{FRIEND}.7", "Федин плейлист"), LIKES]
    r = await merge_into(ym, 3, sources)
    assert ids(ym) == ["1", "2", "ugc-mine", "3", "4"], "по порядку источников, без повторов"
    assert (r.added, r.already, r.skipped) == (4, 3, 1), "1 уже был; 2 и 3 повторялись; Федину загрузку нельзя"
    assert r.title == "Мне и Феде"

    again = await merge_into(ym, 3, sources)
    assert again.added == 0 and ids(ym) == ["1", "2", "ugc-mine", "3", "4"], "повторный запуск ничего не дублирует"


async def test_tracks_keep_album_ids_and_own_uploads_go_by_id(ym):
    await merge_into(ym, 3, [Source("pl", f"{ME}.5")])
    [ops] = ym._client.changes
    assert ops[0]["tracks"] == [{"id": "2", "albumId": "20"}, {"id": "ugc-mine"}]


async def test_copy_into_new_playlist_in_chunks(ym, monkeypatch):
    big = [short(str(i), "9") for i in range(250)]
    ym._client.add(FRIEND, 8, "Большой", big)
    new = await ym.create_playlist("Копия")
    r = await merge_into(ym, new.kind, [Source("pl", f"{FRIEND}.8")])
    assert r.added == 250 and len(ym._client.changes) == 3, "пачками по 100, каждая — со свежей ревизией"
    assert ids(ym, new.kind) == [str(i) for i in range(250)]


async def test_playlist_limit(ym, monkeypatch):
    monkeypatch.setattr("bot.merge.MAX_TRACKS", 3)
    r = await merge_into(ym, 3, [Source("pl", f"{ME}.5"), LIKES])
    assert r.added == 2 and r.over_limit == 2 and len(ids(ym)) == 3
    assert MAX_TRACKS == 10_000


async def test_album_and_links(ym):
    assert await source_from_link(ym, "https://music.yandex.ru/album/50") == Source("alb", "50", "Кино — Группа крови")
    friend = await source_from_link(ym, "смотри https://music.yandex.ru/playlists/abc-uuid")
    assert friend == Source("pl", f"{FRIEND}.7", "Федин плейлист (Федя)")
    assert await source_from_link(ym, f"https://music.yandex.ru/users/{FRIEND}/playlists/7") == friend
    r = await merge_into(ym, 3, [Source("alb", "50")])
    assert ids(ym) == ["1", "51", "52"] and ym._client.changes[0][0]["tracks"][0] == {"id": "51", "albumId": "50"}
    for bad in ("https://music.yandex.ru/album/404", "https://music.yandex.ru/playlists/closed", "просто текст",
                "https://music.yandex.ru/track/1"):
        with pytest.raises(SourceNotFoundError):
            await source_from_link(ym, bad)
    assert r.added == 2


async def test_merging_into_itself_or_missing_target(ym):
    r = await merge_into(ym, 3, [Source("pl", f"{ME}.3")])
    assert r.added == 0 and not ym._client.changes
    with pytest.raises(SourceNotFoundError):
        await merge_into(ym, 999, [LIKES])


async def test_friends_playlist_tracks_are_not_mistaken_for_uploads(ym):
    """Яндекс не присылает albumId в плейлисте — альбом берётся из трека, а трек без данных догружается."""
    ym._client.add(FRIEND, 9, "Без данных", [short("61", "60"), short("62", bare=True), short("ugc-x")])
    r = await merge_into(ym, 3, [Source("pl", f"{FRIEND}.9")])
    assert (r.added, r.skipped) == (2, 1) and ids(ym) == ["1", "61", "62"]
    assert ym._client.changes[-1][0]["tracks"] == [{"id": "61", "albumId": "60"}, {"id": "62", "albumId": "99"}]
