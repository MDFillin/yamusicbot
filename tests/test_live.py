"""Точный подсчёт прослушиваний: разбор кадров Ynison, прослушивания по кадрам, сведение с историей,
подключение к Ynison (на поддельном сервере) и управление соединениями."""

import asyncio
import json
import re
import urllib.parse
from datetime import date, datetime
from types import SimpleNamespace as NS

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from bot import live as live_module
from bot import ynison
from bot.listening import Listening, period_at
from bot.live import LiveTracker, PlayTracker, msk_day, needed_ms
from bot.storage import Storage
from bot.ynison import Snapshot, YnisonAuthError, YnisonProtocolError, parse_state, queue_context

_versions = iter(range(1, 10**9))


def frame(track="1", progress=0, duration=180_000, paused=False, status_ts=1_000_000, frame_ts=None,
          entity=("RADIO", "user:onyourwave"), camel=False, kind="TRACK", version=None, index=1, full=False):
    """Кадр состояния, как его присылает Ynison: protobuf-JSON — поля со значением по умолчанию (false, 0, "")
    не передаются (full=True — передать всё), имена в snake_case или camelCase, 64-битные числа — строками.

    Каждый кадр — новый статус (устройство что-то поменяло), если не задать version явно."""
    playables = [{"playable_id": "999", "playable_type": "TRACK"}]
    playables.insert(index, {"playable_id": track, "album_id_optional": "10", "playable_type": kind, "title": "t"})
    data = {
        "player_state": {
            "status": {"progress_ms": str(progress), "duration_ms": str(duration), "paused": paused,
                       "playback_speed": 1, "version": {"device_id": "phone",
                                                        "version": str(version or next(_versions)),
                                                        "timestamp_ms": str(status_ts)}},
            "player_queue": {"entity_id": entity[1], "entity_type": entity[0], "current_playable_index": index,
                             "playable_list": playables, "options": {"repeat_mode": "NONE"}},
        },
        "devices": [], "timestamp_ms": str(frame_ts if frame_ts is not None else status_ts), "rid": "r",
    }
    data = data if full else _proto3(data)
    return _camel(data) if camel else data


def _proto3(obj):
    """Убрать поля со значениями по умолчанию — так их отдаёт сервер."""
    if isinstance(obj, dict):
        return {k: _proto3(v) for k, v in obj.items() if v not in (False, 0, "0", "", [], None)
                or isinstance(v, dict)}
    if isinstance(obj, list):
        return [_proto3(x) for x in obj]
    return obj


def _camel(obj):
    if isinstance(obj, dict):
        return {re.sub(r"_(\w)", lambda m: m.group(1).upper(), k): _camel(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_camel(x) for x in obj]
    return obj


# ---------- разбор кадров ----------

@pytest.mark.parametrize("camel", [False, True])
def test_parse_state(camel):
    snap = parse_state(frame(track="123:10", progress=5_000, camel=camel, version=7))
    assert snap == Snapshot(track_id="123", album_id="10", context="wave:user:onyourwave", context_type="wave",
                            progress_ms=5_000, duration_ms=180_000, paused=False, speed=1.0, status_ts=1_000_000,
                            frame_ts=1_000_000, status_key="phone:7:1000000")


def test_default_fields_are_omitted_by_the_server():
    """Регрессия: играющий трек приходит без "paused" — это «играет», а не «пауза»; без индекса — первый трек."""
    raw = frame(track="42", progress=0, index=0)
    status = raw["player_state"]["status"]
    assert "paused" not in status and "progress_ms" not in status
    assert "current_playable_index" not in raw["player_state"]["player_queue"]
    snap = parse_state(raw)
    assert snap.track_id == "42" and not snap.paused and snap.progress_ms == 0
    assert parse_state(frame(paused=True)).paused
    assert parse_state({"player_state": {}}).track_id is None, "пустое состояние (ничего ещё не играло)"
    assert parse_state(frame(full=True, version=5)) == parse_state(frame(version=5)), "полный кадр — то же самое"


def test_full_track_is_counted_from_real_frames(store):
    """Как на сайте: трек включили (без "paused" и "progress_ms"), дослушали, включился следующий."""
    t, c = PlayTracker(store, 1), Clock()
    t.on_snapshot(parse_state(frame(track="1", index=0, status_ts=0, frame_ts=0)), now=c(), wall=WALL)
    t.tick(c(30))
    assert t.cur.playing and t.cur.listened_ms == 30_000
    t.on_snapshot(parse_state(frame(track="2", index=1)), now=c(150), wall=WALL + 180)
    assert plays(store) == [("1", 180_000, "live")]


def test_marks_from_the_paused_bug_are_forgotten_once(tmp_path):
    s = Storage(tmp_path / "bot.db")
    s.mark_seen(1, "1", "2026-09-25")  # «видел», но не засчитал — след ошибки
    s.add_play(1, 1, "2026-09-25", "2", "none", 100_000, "live")  # настоящее прослушивание остаётся
    s._db.execute("DELETE FROM meta WHERE key = 'live_seen_fixed'")
    s.close()
    s = Storage(tmp_path / "bot.db")
    assert s._all("SELECT track_id FROM live_seen") == [("2",)]
    s.mark_seen(1, "3", "2026-09-25")
    s.close()
    s = Storage(tmp_path / "bot.db")
    assert len(s._all("SELECT track_id FROM live_seen")) == 2, "только один раз"
    s.close()


def test_parse_state_edge_cases():
    assert parse_state({"devices": [], "rid": "x"}) is None, "служебный кадр без плеера"
    assert parse_state(frame(kind="VIDEO_CLIP")).track_id is None, "клип — не трек"
    assert parse_state(frame(kind=1)).track_id == "1", "перечисление числом"
    empty = frame()
    empty["player_state"]["player_queue"]["current_playable_index"] = -1
    assert parse_state(empty).track_id is None
    with pytest.raises(YnisonAuthError):
        parse_state({"error": {"http_code": 401, "message": "Unauthorized"}})
    with pytest.raises(YnisonProtocolError):
        parse_state({"error": {"message": "bad request"}})
    with pytest.raises(YnisonProtocolError):
        parse_state({"player_state": {"status": "?"}})
    with pytest.raises(YnisonProtocolError):
        parse_state([1, 2])


def test_queue_context_matches_history_keys():
    assert queue_context({"entity_type": "PLAYLIST", "entity_id": "42:1003"}) == ("playlist:42:1003", "playlist")
    assert queue_context({"entityType": 3, "entityId": "20"}) == ("album:20", "album")
    assert queue_context({"entity_type": "ARTIST", "entity_id": "7"}) == ("artist:7", "artist")
    assert queue_context({"entity_type": "RADIO", "entity_id": "user:onyourwave"})[0] == "wave:user:onyourwave"
    assert queue_context({"entity_type": "VARIOUS", "entity_id": ""}) == ("none", "other")


def test_position_accounts_for_time_since_status():
    snap = parse_state(frame(progress=10_000, status_ts=1_000_000, frame_ts=1_030_000))
    assert snap.position() == 40_000, "играет: за 30 секунд с отметки трек ушёл вперёд"
    assert parse_state(frame(progress=10_000, paused=True, frame_ts=1_030_000)).position() == 10_000
    stale = parse_state(frame(progress=10_000, status_ts=1_000_000, frame_ts=9_000_000))
    assert stale.position() == 180_000 and stale.stale(), "устройство давно пропало, не сказав «пауза»"
    assert not parse_state(frame(progress=10_000, status_ts=1_000_000, frame_ts=1_100_000)).stale()


def test_needed_ms():
    assert needed_ms(180_000) == 90_000
    assert needed_ms(600_000) == 240_000, "длинный трек — не больше 4 минут"
    assert needed_ms(20_000) == 16_000 and needed_ms(0) == 30_000


# ---------- прослушивания по кадрам ----------

@pytest.fixture
def store(tmp_path):
    s = Storage(tmp_path / "bot.db")
    yield s
    s.close()


WALL = datetime(2026, 9, 23, 12, 0).timestamp()  # по Москве 23-е в любом случае


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self, seconds=0.0):
        self.t += seconds
        return self.t


def feed(tracker, clock, seconds=0.0, **kw):
    now = clock(seconds)
    tracker.on_snapshot(parse_state(frame(**kw)), now=now, wall=WALL + now)


def plays(store, user_id=1):
    return [(p["track_id"], p["ms"], p["source"]) for p in reversed(store.recent_plays(user_id, 100))]


def test_every_repeat_is_counted(store):
    t, c = PlayTracker(store, 1), Clock()
    feed(t, c, track="1", progress=0)
    t.tick(c(100))
    assert plays(store) == [("1", 100_000, "live")], "засчитан, как только дослушан до половины"
    feed(t, c, 80, track="1", progress=0)  # повтор одного трека: позиция снова в начале
    feed(t, c, 180, track="1", progress=0)
    feed(t, c, 180, track="2", progress=0)
    t.finish(c(10))
    assert plays(store) == [("1", 180_000, "live")] * 3, "три прослушивания подряд, у каждого — реальное время"


def test_skip_seek_and_pause(store):
    t, c = PlayTracker(store, 1), Clock()
    feed(t, c, track="1")
    feed(t, c, 20, track="2")  # «1» пропустили через 20 секунд
    feed(t, c, 5, track="2", progress=0)  # перемотка в начало через 5 секунд — не повтор
    feed(t, c, 60, track="2", progress=60_000, paused=True)
    t.tick(c(600))  # на паузе время не идёт
    feed(t, c, 0, track="2", progress=60_000)
    t.tick(c(30))
    assert plays(store) == [("2", 95_000, "live")]
    assert store.recent_plays(1) and {r[0] for r in store._all("SELECT track_id FROM live_seen")} == {"1", "2"}, \
        "пропущенный трек запомнен, чтобы история Яндекса не выдала его за прослушанный"
    assert t.new_tracks == {"1", "2"} and t.new_contexts == {"wave:user:onyourwave": "wave"}


def test_vanished_device_does_not_inflate_time(store):
    t, c = PlayTracker(store, 1), Clock()
    feed(t, c, track="1", progress=120_000)  # подключились посреди трека
    t.tick(c(3600))  # кадров больше нет: устройство пропало, не сказав «пауза»
    t.finish(c(1))
    assert plays(store) == [], "дослушали только 60 секунд из 90 нужных"
    feed(t, c, track="3", progress=0, paused=False, duration=200_000)
    t.tick(c(3600))
    t.finish(c(1))
    assert plays(store) == [("3", 200_000, "live")], "не больше длины трека"


def test_restart_mid_track_does_not_count_twice(store):
    t, c = PlayTracker(store, 1), Clock()
    feed(t, c, track="1", progress=0, duration=600_000)
    t.tick(c(250))  # длинный трек засчитан на 4-й минуте
    assert len(store.recent_plays(1)) == 1
    t2 = PlayTracker(store, 1)  # бот перезапустили, трек играет дальше
    feed(t2, c, 10, track="1", progress=260_000, duration=600_000)
    t2.tick(c(340))
    t2.finish(c(1))
    # Одно прослушивание: 250 с до перезапуска + 340 с после (10 секунд, пока бот стоял, не видели).
    assert plays(store) == [("1", 590_000, "live")]


def test_device_clock_skew_does_not_break_counting(store):
    """Часы телефона отстают на 10 минут: считать от них позицию нельзя — трек «кончился бы» сразу."""
    t, c = PlayTracker(store, 1), Clock()
    feed(t, c, track="9", progress=0, status_ts=1_000_000, frame_ts=1_000_000)  # первый кадр: что-то играло
    skew = 600_000
    feed(t, c, 5, track="1", progress=0, status_ts=1_005_000 - skew, frame_ts=1_005_000)
    feed(t, c, 20, track="1", progress=20_000, status_ts=1_025_000 - skew, frame_ts=1_025_000,
         version=424242)  # перемотка/пауза на телефоне — новый статус
    feed(t, c, 1, track="1", progress=20_000, status_ts=1_025_000 - skew, frame_ts=1_026_000,
         version=424242)  # другое изменение (громкость): статус тот же — позицию не трогаем
    t.tick(c(80))
    assert plays(store) == [("1", 101_000, "live")]


def test_stale_first_frame_is_not_playing(store):
    t, c = PlayTracker(store, 1), Clock()
    feed(t, c, track="1", progress=10_000, status_ts=1_000_000, frame_ts=90_000_000)  # «играет» с позавчера
    t.tick(c(3600))
    t.finish(c(1))
    assert plays(store) == [] and {r[0] for r in store._all("SELECT track_id FROM live_seen")} == {"1"}


def test_msk_day():
    assert msk_day(1790000000) == "2026-09-21"  # 14:13 UTC — 17:13 по Москве
    assert msk_day(1790024400) == "2026-09-22", "21:00 UTC — уже полночь по Москве"


# ---------- сведение с историей Яндекса ----------

def test_live_plays_merge_with_history(store):
    for tid, title, dur in (("1", "Кукушка", 180_000), ("2", "Группа крови", 240_000), ("3", "Хочешь?", 200_000)):
        store.save_track_meta(tid, title, "10", "Альбом", "rusrock", dur, None, [("7", "Кино")])
    day = "2026-09-23"
    for _ in range(3):
        store.add_play(1, 1, day, "1", "wave:user:onyourwave", 170_000, "live")
    store.mark_seen(1, "3", day)  # «3» бот видел вживую, но его пропустили
    store.add_listens(1, [(day, "1", "wave:user:onyourwave"), (day, "2", "album:10"), (day, "3", "album:10"),
                          ("2026-09-24", "1", "none")])  # соседний день в истории — тот же вечер по другому поясу
    st = store.listening_stats(1, "2026-09-21", "2026-09-27")
    assert st["plays"] == 4 and st["estimated"] == 1, "3 повтора вживую + «2», которую бот не видел"
    assert st["top_tracks"][0] == {"id": "1", "plays": 3}
    assert st["minutes"] == (3 * 170_000 + 240_000) // 60000
    assert st["by_day"] == {day: 4}


def test_stats_counts_repeats_in_report(store):
    listening = Listening(store, accounts=None, admin=NS(), bot=None, config=NS(webapp_url=None))
    store.save_track_meta("1", "Кукушка", "10", "Альбом", "rusrock", 180_000, None, [("7", "Кино")])
    store.set_setting(1, "stats", "1")
    for _ in range(10):
        store.add_play(1, 1, "2026-09-23", "1", "none", 180_000, "live")
    text = listening.report_text(1, period_at("day", date(2026, 9, 23)))
    assert "<b>10</b> прослушиваний · 30 мин" in text and "Кукушка — Кино · 10 раз" in text
    assert "≈" not in text, "всё вживую — время точное"


# ---------- подключение к Ynison ----------

@pytest.fixture
async def fake_ynison(monkeypatch):
    """Поддельный Ynison: редиректор и сервис состояния на локальном сервере."""
    seen = NS(redirect=None, state=None, first=None)
    frames: list = [frame(track="5", progress=0)]

    def device(request):
        protocols = [p.strip() for p in request.headers.get("Sec-WebSocket-Protocol", "").split(",")]
        return protocols[:2], json.loads(urllib.parse.unquote(protocols[2]))

    async def redirector(request):
        if request.headers.get("Authorization") != "OAuth good":
            raise web.HTTPUnauthorized()
        seen.redirect = (request.headers.get("Origin"), *device(request))
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_str(json.dumps({"host": f"127.0.0.1:{server.port}", "redirect_ticket": "TICKET",
                                      "session_id": "55", "keep_alive_params": {"keep_alive_time_seconds": 30}}))
        await ws.close()
        return ws

    async def state(request):
        seen.state = device(request)
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        seen.first = json.loads((await ws.receive()).data)
        for f in frames:
            await ws.send_str(json.dumps(f))
        await ws.close()
        return ws

    app = web.Application()
    app.router.add_get(ynison.REDIRECT_PATH, redirector)
    app.router.add_get(ynison.STATE_PATH, state)
    server = TestServer(app)
    await server.start_server()
    monkeypatch.setattr(ynison, "YNISON_URL", f"ws://127.0.0.1:{server.port}")
    monkeypatch.setattr(ynison, "STATE_SCHEME", "ws")
    monkeypatch.setattr(ynison, "_HOST_RE", re.compile(r"^127\.0\.0\.1:\d+$"))
    yield NS(seen=seen, frames=frames, server=server)
    await server.close()


async def test_listen_handshake_and_frames(fake_ynison):
    got = []

    async def on_frame(f, raw):
        got.append(parse_state(f).track_id)

    async with aiohttp.ClientSession() as http:
        await ynison.listen(http, "good", "dev1", on_frame)
    seen = fake_ynison.seen
    origin, head, info = seen.redirect
    assert origin == "https://music.yandex.ru" and head == ["Bearer", "v2"]
    assert info["Ynison-Device-Id"] == "dev1" and json.loads(info["Ynison-Device-Info"])["type"] == "1"
    _, state_info = seen.state
    assert state_info["Ynison-Redirect-Ticket"] == "TICKET" and state_info["Ynison-Session-Id"] == "55"
    full = seen.first["updateFullState"]
    assert full["isCurrentlyActive"] is False, "бот не становится играющим устройством"
    assert full["device"]["capabilities"] == {"canBePlayer": False, "canBeRemoteController": False,
                                              "volumeGranularity": 0}
    assert full["playerState"]["playerQueue"]["version"]["timestampMs"] == "0", "не перебивает очередь пользователя"
    assert got == ["5"]


async def test_listen_rejects_bad_token_and_foreign_host(fake_ynison, monkeypatch):
    async def on_frame(f, raw):
        pass

    async with aiohttp.ClientSession() as http:
        with pytest.raises(YnisonAuthError):
            await ynison.listen(http, "bad", "dev1", on_frame)
        monkeypatch.setattr(ynison, "_HOST_RE", re.compile(r"^never$"))
        with pytest.raises(YnisonProtocolError, match="чужой хост"):
            await ynison.listen(http, "good", "dev1", on_frame)


# ---------- управление соединениями ----------

class FakeHealth:
    def __init__(self):
        self.ok, self.failures = 0, []

    async def succeeded(self):
        self.ok += 1

    def json(self):
        return {"broken": False}

    async def failed(self, user_id, details):
        self.failures.append((user_id, details))


@pytest.fixture
def tracker(store, monkeypatch):
    monkeypatch.setattr(live_module, "RETRY_MIN", 0.01)
    monkeypatch.setattr(live_module, "RETRY_MAX", 0.05)
    admin = NS(settings=NS(live_tracking=True), check=lambda uid: None, is_admin=lambda uid: uid == 1,
               live_health=FakeHealth())
    filled = []

    async def fill_meta(ym, ids):
        filled.append(("meta", list(ids)))

    async def fill_contexts(ym, contexts):
        filled.append(("ctx", dict(contexts)))

    async def get(user_id):
        return NS(uid=user_id)

    listening = NS(fill_meta=fill_meta, fill_contexts=fill_contexts, enabled=lambda uid: True)
    lt = LiveTracker(store, NS(get=get), admin, listening)
    lt.filled = filled
    for uid in (1, 2):
        store.set_account(uid, f"tok{uid}", "me")
        store.set_setting(uid, "stats", "1")
    return lt


async def settle():
    for _ in range(20):
        await asyncio.sleep(0)


async def test_tracker_connects_counts_and_reports(tracker, monkeypatch):
    calls = []

    async def fake_listen(http, token, device_id, on_frame):
        calls.append((token, device_id))
        if token == "tok1":
            await on_frame(frame(track="1", progress=100_000), "{raw}")
            await asyncio.sleep(3600)  # соединение держится
        raise YnisonProtocolError("новый формат")

    monkeypatch.setattr(ynison, "listen", fake_listen)
    await tracker.tick()
    await settle()
    assert {c[0] for c in calls} >= {"tok1", "tok2"}
    assert tracker.conns[1].connected and tracker.conns[1].raw == "{raw}", "сырой кадр — только у админа"
    assert tracker.admin.live_health.ok == 1 and tracker.admin.live_health.failures[0] == (2, "новый формат")
    device = tracker.store.get_setting(1, "ynison_device")
    assert device and all(d == device for t, d in calls if t == "tok1"), "постоянный id устройства"

    await tracker.tick()  # трек играет дальше — засчитать и подтянуть данные трека
    assert ("meta", ["1"]) in tracker.filled and ("ctx", {"wave:user:onyourwave": "wave"}) in tracker.filled
    tracker.conns[1].tracker.cur.listened_ms = 90_000
    await tracker.tick()
    assert [p["track_id"] for p in tracker.store.recent_plays(1)] == ["1"]
    status = tracker.user_status(1)
    assert status["connected"] and status["now"]["counted"] and status["now"]["track_id"] == "1"

    tracker.store.set_setting(1, "stats", None)  # выключил статистику — соединение закрывается
    await tracker.tick()
    assert 1 not in tracker.conns
    await tracker.close()


async def test_tracker_auth_error_waits_and_token_change_reconnects(tracker, monkeypatch):
    calls = []

    async def fake_listen(http, token, device_id, on_frame):
        calls.append(token)
        if token == "tok1":
            raise YnisonAuthError("HTTP 401")
        await asyncio.sleep(3600)

    monkeypatch.setattr(ynison, "listen", fake_listen)
    await tracker.tick()
    await settle()
    await tracker.tick()
    await settle()
    assert calls.count("tok1") == 1, "отозванный вход не долбим"
    assert 1 not in tracker.conns and tracker.user_status(1)["cooldown"]
    first = tracker.conns[2]
    tracker.store.set_account(2, "tok2-new", "me")  # сменил аккаунт
    await tracker.tick()
    await settle()
    assert tracker.conns[2] is not first and calls[-1] == "tok2-new"
    tracker.admin.settings.live_tracking = False  # владелец выключил точный подсчёт
    await tracker.tick()
    assert tracker.conns == {}
    await tracker.close()
