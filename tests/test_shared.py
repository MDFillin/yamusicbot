"""Общие плейлисты (bot/shared.py): у каждого свой плейлист в своём аккаунте, бот держит их одинаковыми."""

from __future__ import annotations

import json
from types import SimpleNamespace as NS

import pytest

from bot.shared import MASS_DELETE, SharedError, SharedPlaylists
from bot.storage import Storage
from bot.ym import YandexMusic

ME, FEDYA, THIRD = 1, 2, 3


def short(track_id, album_id="a"):
    """Как отдаёт Яндекс в плейлисте: albumId нет, альбом — только в полном треке (у своих загрузок альбома нет)."""
    return NS(id=track_id, album_id=None, track=NS(albums=[NS(id=album_id)] if album_id else []))


class Client:
    """Один аккаунт Яндекса: плейлисты по kind; diff применяется как у Яндекса, с проверкой ревизии."""

    def __init__(self) -> None:
        self.playlists: dict[int, NS] = {}
        self.fail_change = False

    def make(self, kind, ids):
        self.playlists[kind] = NS(kind=kind, title="Общий", revision=1,
                                  tracks=[short(t, None if t.startswith("ugc") else "a") for t in ids])

    async def users_playlists(self, kind, owner):
        return self.playlists.get(int(kind))

    async def users_playlists_change(self, kind, diff, revision=1):
        playlist = self.playlists[int(kind)]
        if self.fail_change or revision != playlist.revision:
            raise RuntimeError("wrong-revision")
        for op in json.loads(diff):
            if op["op"] == "delete":
                del playlist.tracks[op["from"]:op["to"]]
            else:
                playlist.tracks[op["at"]:op["at"]] = [short(t["id"], t.get("albumId")) for t in op["tracks"]]
        playlist.revision += 1
        return playlist


class Account(YandexMusic):
    def __init__(self, uid):
        super().__init__("t")
        self.uid = uid
        self._client = Client()

    def ids(self, kind=10):
        return [str(s.id) for s in self._client.playlists[kind].tracks]

    def set(self, ids, kind=10):  # человек правит свой плейлист в приложении Яндекса
        self._client.make(kind, ids) if kind not in self._client.playlists else None
        playlist = self._client.playlists[kind]
        playlist.tracks = [short(t, None if t.startswith("ugc") else "a") for t in ids]
        playlist.revision += 1


@pytest.fixture
def world(tmp_path):
    store = Storage(tmp_path / "bot.db")
    accounts = {uid: Account(uid) for uid in (ME, FEDYA, THIRD)}
    offline: set[int] = set()

    async def get(user_id):
        if user_id in offline:
            from bot.ym import YandexNotReady
            raise YandexNotReady("нет связи", network=True)
        return accounts[user_id]

    sent = []
    bot = NS(send_message=lambda uid, text: _record(sent, uid, text))
    shared = SharedPlaylists(store, NS(get=get), bot)
    yield NS(store=store, acc=accounts, shared=shared, offline=offline, sent=sent)
    store.close()


async def _record(sent, uid, text):
    sent.append((uid, text))


async def test_two_people_share_one_playlist(world):
    me, fedya = world.acc[ME], world.acc[FEDYA]
    me._client.make(10, ["1", "2", "ugc-mine"])
    fedya._client.make(10, [])
    sid = world.shared.create(ME, "Общий", 10)
    code = world.store.get_shared(sid)["code"]
    world.shared.join(code, FEDYA, 10)

    await world.shared.sync(sid)
    assert me.ids() == ["1", "2", "ugc-mine"] and fedya.ids() == ["1", "2"], "свои загрузки — только у владельца"

    fedya.set(["1", "2", "3"])  # Федя добавил трек в приложении
    me.set(["1", "2", "ugc-mine", "4"])  # и я тоже
    report = await world.shared.sync(sid)
    assert report.added == 2 and report.members == 2
    assert me.ids() == ["1", "2", "ugc-mine", "4", "3"] and fedya.ids() == ["1", "2", "3", "4"]

    fedya.set(["2", "3", "4"])  # Федя удалил «1»
    await world.shared.sync(sid)
    assert me.ids() == ["2", "ugc-mine", "4", "3"], "удалённое одним — удаляется у всех"
    assert [t for t, _, _ in world.store.shared_tracks(sid)] == ["2", "ugc-mine", "4", "3"]

    revisions = me._client.playlists[10].revision, fedya._client.playlists[10].revision
    again = await world.shared.sync(sid)
    assert (again.added, again.removed) == (0, 0)
    assert (me._client.playlists[10].revision, fedya._client.playlists[10].revision) == revisions, "лишних правок нет"


async def test_new_member_brings_own_tracks(world):
    me, fedya = world.acc[ME], world.acc[FEDYA]
    me._client.make(10, ["1", "2"])
    fedya._client.make(20, ["2", "9"])  # Федя присоединился своим плейлистом
    sid = world.shared.create(ME, "Общий", 10)
    await world.shared.sync(sid)
    world.shared.join(world.store.get_shared(sid)["code"], FEDYA, 20)
    await world.shared.sync(sid)
    assert me.ids() == ["1", "2", "9"] and fedya.ids(20) == ["2", "9", "1"], "ничего не теряется, без повторов"


async def test_unreachable_member_is_not_a_deletion(world):
    me, fedya = world.acc[ME], world.acc[FEDYA]
    me._client.make(10, ["1", "2"])
    fedya._client.make(10, [])
    sid = world.shared.create(ME, "Общий", 10)
    world.shared.join(world.store.get_shared(sid)["code"], FEDYA, 10)
    await world.shared.sync(sid)

    world.offline.add(FEDYA)
    me.set(["1", "2", "3"])
    report = await world.shared.sync(sid)
    assert FEDYA in report.errors and me.ids() == ["1", "2", "3"]
    world.offline.clear()
    fedya.set(["1", "2", "7"])  # пока был недоступен, добавил «7»
    await world.shared.sync(sid)
    assert fedya.ids() == ["1", "2", "7", "3"] and me.ids() == ["1", "2", "3", "7"], "догнал без потерь"
    assert world.store.shared_members(sid)[1]["error"] is None


async def test_mass_deletion_is_not_spread_and_restored(world):
    me, fedya = world.acc[ME], world.acc[FEDYA]
    ids = [str(i) for i in range(40)]
    me._client.make(10, ids)
    fedya._client.make(10, [])
    sid = world.shared.create(ME, "Общий", 10)
    world.shared.join(world.store.get_shared(sid)["code"], FEDYA, 10)
    await world.shared.sync(sid)
    fedya.set([])  # случайно очистил
    report = await world.shared.sync(sid)
    assert report.restored == {FEDYA: 40} and me.ids() == ids and fedya.ids() == ids
    assert world.sent and world.sent[0][0] == FEDYA and "разом удалено 40" in world.sent[0][1]
    assert MASS_DELETE[0] < 40


async def test_concurrent_edit_is_retried_next_time(world):
    me, fedya = world.acc[ME], world.acc[FEDYA]
    me._client.make(10, ["1"])
    fedya._client.make(10, [])
    sid = world.shared.create(ME, "Общий", 10)
    world.shared.join(world.store.get_shared(sid)["code"], FEDYA, 10)
    fedya._client.fail_change = True  # Федя как раз правит плейлист — ревизия не совпала
    report = await world.shared.sync(sid)
    assert FEDYA in report.errors and fedya.ids() == []
    fedya._client.fail_change = False
    await world.shared.sync(sid)
    assert fedya.ids() == ["1"]


async def test_membership_rules(world, monkeypatch):
    world.acc[ME]._client.make(10, [])
    sid = world.shared.create(ME, "Общий", 10)
    code = world.store.get_shared(sid)["code"]
    with pytest.raises(SharedError, match="уже в этом"):
        world.shared.join(code, ME, 10)
    with pytest.raises(SharedError, match="недействительно"):
        world.shared.join("nope", FEDYA, 10)
    monkeypatch.setattr("bot.shared.MAX_MEMBERS", 2)
    world.shared.join(code, FEDYA, 10)
    with pytest.raises(SharedError, match="больше нельзя"):
        world.shared.join(code, THIRD, 10)

    world.shared.leave(sid, ME)  # создатель вышел — общий остаётся у Феди
    assert world.store.get_shared(sid)["owner"] == FEDYA and world.shared.describe(sid, ME) is None
    world.shared.leave(sid, FEDYA)
    assert world.store.get_shared(sid) is None and world.store.shared_for_user(FEDYA) == []


async def test_deleted_playlist_is_reported(world):
    world.acc[ME]._client.make(10, ["1"])
    sid = world.shared.create(ME, "Общий", 10)
    world.acc[FEDYA]._client.make(10, [])
    world.shared.join(world.store.get_shared(sid)["code"], FEDYA, 10)
    del world.acc[FEDYA]._client.playlists[10]
    report = await world.shared.sync(sid)
    assert "удалён" in report.errors[FEDYA] and world.acc[ME].ids() == ["1"]
    info = world.shared.describe(sid, FEDYA)
    assert info["error"] and info["members"][0]["id"] == ME and info["tracks"] == 1


async def test_failed_removal_is_finished_later(world):
    me, fedya = world.acc[ME], world.acc[FEDYA]
    me._client.make(10, ["1", "2"])
    fedya._client.make(10, [])
    sid = world.shared.create(ME, "Общий", 10)
    world.shared.join(world.store.get_shared(sid)["code"], FEDYA, 10)
    await world.shared.sync(sid)
    me.set(["2"])  # я удалил «1», а у Феди как раз не получилось изменить плейлист
    fedya._client.fail_change = True
    await world.shared.sync(sid)
    assert fedya.ids() == ["1", "2"]
    fedya._client.fail_change = False
    await world.shared.sync(sid)
    assert fedya.ids() == ["2"] and me.ids() == ["2"], "удаление доделано, а не потеряно"


async def test_friends_catalog_track_reaches_me(world):
    """Ровно случай из жизни: Федя добавил трек из каталога — он должен появиться у меня (раньше бот принимал его
    за загруженный файл, потому что Яндекс не присылает albumId в плейлисте)."""
    me, fedya = world.acc[ME], world.acc[FEDYA]
    me._client.make(10, [])
    fedya._client.make(10, [])
    sid = world.shared.create(ME, "Общий", 10)
    world.shared.join(world.store.get_shared(sid)["code"], FEDYA, 10)
    await world.shared.sync(sid)
    fedya.set(["555"])
    await world.shared.sync(sid)
    assert me.ids() == ["555"]
    assert me._client.playlists[10].tracks[0].track.albums[0].id == "a", "вставлен с альбомом"


async def test_old_albumless_entries_are_repaired(world):
    """Общий список, записанный прошлой версией без альбома, чинится по текущим плейлистам."""
    me, fedya = world.acc[ME], world.acc[FEDYA]
    me._client.make(10, [])
    fedya._client.make(10, ["555"])
    sid = world.shared.create(ME, "Общий", 10)
    world.shared.join(world.store.get_shared(sid)["code"], FEDYA, 10)
    world.store.set_shared_tracks(sid, [("555", None, FEDYA)])
    world.store.set_shared_seen(sid, ME, set())
    world.store.set_shared_seen(sid, FEDYA, {"555"})
    await world.shared.sync(sid)
    assert me.ids() == ["555"] and world.store.shared_tracks(sid) == [("555", "a", FEDYA)]
