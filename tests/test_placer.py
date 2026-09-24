"""Загруженные треки встают в начало плейлиста, когда Яндекс их обработает."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace as NS

import pytest

from bot import placer as placer_module
from bot.placer import TopPlacer
from bot.ym import YandexMusic


def short(track_id, album_id=None, source=None):
    track = NS(id=track_id, track_source=source) if source else None
    return NS(id=track_id, album_id=album_id, track=track,
              track_id=f"{track_id}:{album_id}" if album_id else str(track_id))


def apply_diff(tracks: list, ops: list[dict]) -> list:
    """Как Яндекс применяет diff плейлиста: операции по очереди."""
    tracks = list(tracks)
    for op in ops:
        if op["op"] == "insert":
            new = [short(t["id"], t.get("albumId")) for t in op["tracks"]]
            tracks[op["at"]:op["at"]] = new
        else:
            del tracks[op["from"]:op["to"]]
    return tracks


class FakeClient:
    def __init__(self, owner: FakePlaylistYM) -> None:
        self.owner = owner

    async def users_playlists_change(self, kind, diff, revision=1):
        self.owner.changes.append(json.loads(diff))
        if self.owner.reject_changes:
            raise RuntimeError("revision mismatch")
        self.owner.tracks = apply_diff(self.owner.tracks, json.loads(diff))
        self.owner.revision += 1


class FakePlaylistYM(YandexMusic):
    """Плейлист, в который Яндекс «дописывает» загруженные треки через `delay` проверок."""

    def __init__(self, tracks) -> None:
        super().__init__("t")
        self.uid = 42
        self.tracks = list(tracks)
        self.revision = 1
        self.processing: list[tuple[int, object]] = []  # (осталось проверок, трек)
        self.changes: list[list[dict]] = []
        self.reject_changes = False
        self._client = FakeClient(self)

    def upload(self, track, delay: int = 1) -> None:
        self.processing.append((delay, track))

    async def get_playlist(self, kind, owner=None):
        still = []
        for left, track in self.processing:
            if left <= 0:
                self.tracks.append(track)
            else:
                still.append((left - 1, track))
        self.processing = still
        return NS(kind=kind, revision=self.revision, tracks=list(self.tracks))


def ids(ym) -> list[str]:
    return [str(t.id) for t in ym.tracks]


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(placer_module, "FIRST_CHECK", 0)
    monkeypatch.setattr(placer_module, "POLL_INTERVAL", 0.01)


# ---------- перестановка ----------

async def test_move_to_top_inserts_first_then_deletes():
    ym = FakePlaylistYM([short("1", "10"), short("2", "20"), short("ugc-a"), short("3", "30"), short("ugc-b")])
    assert await ym.move_to_top(1003, ["ugc-a", "ugc-b"])
    assert ids(ym) == ["ugc-a", "ugc-b", "1", "2", "3"]
    [ops] = ym.changes
    assert ops[0] == {"op": "insert", "at": 0, "tracks": [{"id": "ugc-a"}, {"id": "ugc-b"}]}, "у загрузок нет albumId"
    assert [op["op"] for op in ops[1:]] == ["delete", "delete"], "удаление после вставки: трек не пропадёт"

    assert await ym.move_to_top(1003, ["1"])
    assert ym.changes[-1][0]["tracks"] == [{"id": "1", "albumId": "10"}]
    assert ids(ym) == ["1", "ugc-a", "ugc-b", "2", "3"]


async def test_move_to_top_skips_needless_changes():
    ym = FakePlaylistYM([short("a"), short("b"), short("c")])
    assert await ym.move_to_top(1003, ["a", "b"]) and not ym.changes, "уже наверху — не трогаем"
    assert not await ym.move_to_top(1003, ["zzz"])


# ---------- ожидание обработки ----------

async def test_upload_goes_to_top_when_yandex_shows_it():
    ym = FakePlaylistYM([short("1", "10"), short("2", "20")])
    placer = TopPlacer()
    known = await placer.before_upload(ym, 1003)
    ym.upload(short("ugc-new"), delay=2)
    done = placer.after_upload(ym, 1003, known, "ugc-new")
    assert await asyncio.wait_for(done, 2) is True
    assert ids(ym) == ["ugc-new", "1", "2"]


async def test_batch_keeps_upload_order_and_newer_batch_goes_above(monkeypatch):
    ym = FakePlaylistYM([short("old", "1")])
    placer = TopPlacer()
    known = await placer.before_upload(ym, 1003)
    ym.upload(short("b"), delay=0)  # Яндекс обработал второй файл раньше первого
    ym.upload(short("a"), delay=3)
    first = placer.after_upload(ym, 1003, known, "a")
    second = placer.after_upload(ym, 1003, await placer.before_upload(ym, 1003), "b")
    assert await asyncio.wait_for(asyncio.gather(first, second), 2) == [True, True]
    assert ids(ym) == ["a", "b", "old"], "внутри пачки — в порядке загрузки"

    monkeypatch.setattr(placer_module, "GROUP_GAP", 0)
    await asyncio.sleep(0.01)
    known = await placer.before_upload(ym, 1003)
    ym.upload(short("c"), delay=0)
    assert await asyncio.wait_for(placer.after_upload(ym, 1003, known, "c"), 2)
    assert ids(ym) == ["c", "a", "b", "old"], "новая загрузка — над прежними"


async def test_track_is_found_without_matching_id():
    ym = FakePlaylistYM([short("1", "10"), short("mine-before")])
    placer = TopPlacer()
    known = await placer.before_upload(ym, 1003)
    ym.upload(short("5", "50"), delay=0)  # кто-то добавил обычный трек из каталога — его не трогаем
    ym.upload(short("x9", source="OWN"), delay=1)
    assert await asyncio.wait_for(placer.after_upload(ym, 1003, known, "другой-формат-id"), 2)
    assert ids(ym) == ["x9", "1", "mine-before", "5"]


async def test_gives_up_quietly(monkeypatch):
    monkeypatch.setattr(placer_module, "GIVE_UP_AFTER", 0.05)
    ym = FakePlaylistYM([short("1", "10")])
    placer = TopPlacer()
    done = placer.after_upload(ym, 1003, await placer.before_upload(ym, 1003), "never")
    assert await asyncio.wait_for(done, 2) is False
    assert ids(ym) == ["1"] and not ym.changes


async def test_rejected_move_is_retried_then_reported(monkeypatch):
    monkeypatch.setattr(placer_module, "MAX_FAILURES", 2)
    ym = FakePlaylistYM([short("1", "10")])
    ym.reject_changes = True
    placer = TopPlacer()
    known = await placer.before_upload(ym, 1003)
    ym.upload(short("u"), delay=0)
    assert await asyncio.wait_for(placer.after_upload(ym, 1003, known, "u"), 2) is False
    assert len(ym.changes) == 2 and ids(ym) == ["1", "u"], "трек остался в конце, но на месте"
