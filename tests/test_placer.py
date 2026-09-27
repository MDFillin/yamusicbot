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
    assert await asyncio.wait_for(done, 2) is None, "None — Яндекс так и не добавил трек"
    assert ids(ym) == ["1"] and not ym.changes


# ---------- Яндекс принял файл, но трек так и не появился ----------

class ResendYM(FakePlaylistYM):
    """Первая загрузка «теряется» (как бывало с первой после перезапуска бота), повторная — проходит."""

    def __init__(self, tracks, second: object | None) -> None:
        super().__init__(tracks)
        self.second = second
        self.resent: list[tuple[int, str, bytes]] = []

    async def upload_track(self, kind, name, data):
        from bot.ym import UploadResult

        self.resent.append((kind, name, data))
        if self.second is not None:
            self.upload(self.second, delay=1)
        return UploadResult("ugc-2", "CREATED")


async def test_lost_upload_is_sent_again(monkeypatch):
    monkeypatch.setattr(placer_module, "RESEND_AFTER", 0.05)
    ym = ResendYM([short("1", "10")], second=short("ugc-2"))
    placer = TopPlacer()
    known = await placer.before_upload(ym, 1003)
    done = placer.after_upload(ym, 1003, known, "ugc-1", resend=("Кино - Кукушка.mp3", b"mp3"))
    assert placer._held == 3
    assert await asyncio.wait_for(done, 2) is True
    assert ym.resent == [(1003, "Кино - Кукушка.mp3", b"mp3")], "отправлен ещё раз — один раз"
    assert ids(ym) == ["ugc-2", "1"] and placer._held == 0, "трек наверху, файл из памяти убран"


async def test_upload_that_shows_up_is_not_sent_again(monkeypatch):
    monkeypatch.setattr(placer_module, "RESEND_AFTER", 0.05)
    ym = ResendYM([short("1", "10")], second=short("dup"))
    placer = TopPlacer()
    known = await placer.before_upload(ym, 1003)
    ym.upload(short("ugc-1"), delay=0)
    assert await asyncio.wait_for(placer.after_upload(ym, 1003, known, "ugc-1", resend=("a.mp3", b"x")), 2)
    await asyncio.sleep(0.1)
    assert ym.resent == [] and placer._held == 0 and ids(ym) == ["ugc-1", "1"]


async def test_resent_once_then_reported_as_lost(monkeypatch):
    monkeypatch.setattr(placer_module, "RESEND_AFTER", 0.02)
    monkeypatch.setattr(placer_module, "GIVE_UP_AFTER", 0.2)
    ym = ResendYM([short("1", "10")], second=None)
    placer = TopPlacer()
    done = placer.after_upload(ym, 1003, await placer.before_upload(ym, 1003), "ugc-1", resend=("a.mp3", b"x"))
    assert await asyncio.wait_for(done, 3) is None
    assert len(ym.resent) == 1 and placer._held == 0


async def test_big_files_are_not_kept_for_resend(monkeypatch):
    monkeypatch.setattr(placer_module, "RESEND_AFTER", 0.02)
    monkeypatch.setattr(placer_module, "GIVE_UP_AFTER", 0.1)
    monkeypatch.setattr(placer_module, "MAX_HELD_BYTES", 10)
    ym = ResendYM([short("1", "10")], second=short("ugc-2"))
    placer = TopPlacer()
    done = placer.after_upload(ym, 1003, await placer.before_upload(ym, 1003), "ugc-1", resend=("a.mp3", b"x" * 11))
    assert placer._held == 0
    assert await asyncio.wait_for(done, 3) is None and ym.resent == [], "памяти жалко — не держим и не шлём"


def test_bigger_files_get_more_time():
    assert placer_module._resend_after(0) == placer_module.RESEND_AFTER
    assert placer_module._resend_after(20 * 1024 * 1024) == placer_module.RESEND_AFTER + 120
    assert placer_module._resend_after(10**10) == placer_module.RESEND_MAX_WAIT


async def test_rejected_move_is_retried_then_reported(monkeypatch):
    monkeypatch.setattr(placer_module, "MAX_FAILURES", 2)
    ym = FakePlaylistYM([short("1", "10")])
    ym.reject_changes = True
    placer = TopPlacer()
    known = await placer.before_upload(ym, 1003)
    ym.upload(short("u"), delay=0)
    # 10 с: сбой пишется в журнал, а он при первом вызове подгружает aiogram — одиночный прогон ждёт это дольше
    assert await asyncio.wait_for(placer.after_upload(ym, 1003, known, "u"), 10) is False
    assert len(ym.changes) == 2 and ids(ym) == ["1", "u"], "трек остался в конце, но на месте"
