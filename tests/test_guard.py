"""Бережное обращение с Яндексом (bot/net.py, Guard): общий темп, люди раньше фона, «остывание» при отказах."""

import asyncio

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from yandex_music import ClientAsync

from bot import net
from bot.config import ConfigError, load_config
from bot.ym import YandexBusyError, YandexRequest, close_api_session


class Clock:
    """Поддельные часы: sleep не ждёт, а переводит стрелки."""

    def __init__(self) -> None:
        self.t = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.t

    async def sleep(self, delay: float) -> None:
        self.slept.append(delay)
        self.t += delay
        await asyncio.sleep(0)


def make(rate: float = 3, burst: int = 10) -> tuple[net.Guard, Clock]:
    clock = Clock()
    guard = net.Guard(rate, burst)
    guard.clock, guard.sleep = clock, clock.sleep
    guard.reset()
    trips: list[tuple[str, int, bool]] = []
    guard.on_trip = lambda *args: trips.append(args)
    guard.trips = trips
    return guard, clock


async def test_burst_then_steady_rate():
    guard, clock = make(rate=2, burst=4)
    for _ in range(4):
        await guard.acquire()
    assert clock.t == 1000, "пачка до burst — сразу"
    for _ in range(4):
        await guard.acquire()
    assert clock.t == pytest.approx(1002), "дальше — по темпу: 4 запроса при 2 в секунду — 2 секунды"
    assert guard.totals["requests"] == 8 and guard.totals["waited"] == 4


async def test_person_gets_busy_instead_of_waiting_forever():
    guard, clock = make(rate=0.01, burst=1)
    await guard.acquire()
    with pytest.raises(net.YandexBusy, match="попробуйте через минуту"):
        await guard.acquire()
    assert clock.t == 1000 and guard.totals["busy"] == 1, "ждать 100 секунд человеку незачем — сразу отказ"


async def test_background_is_spaced_and_leaves_half_for_people():
    guard, clock = make(rate=1, burst=10)
    with net.background():
        for _ in range(3):
            await guard.acquire()
    assert clock.t == pytest.approx(1000 + 2 * net.BACKGROUND_GAP), "фон — не чаще раза в BACKGROUND_GAP"

    clock.t += net.BACKGROUND_GAP
    for _ in range(8):  # люди выбрали почти весь запас: осталось 2 из 10
        await guard.acquire()
    start = clock.t
    with net.background():
        await guard.acquire()
    assert clock.t - start == pytest.approx(3), "фон дождался, пока запас снова половина (2 → 5 при 1 в секунду)"
    assert guard._tokens == pytest.approx(4) and guard.totals["background"] == 4


async def test_throttled_answer_cools_down_everything():
    guard, clock = make()
    guard.report(ok=True, throttled=True, retry_after=120)
    assert guard.cooling and guard.cooling_until == pytest.approx(1120) and guard.level == 1
    assert guard.trips == [("Яндекс ответил «слишком много запросов» (429)", 120, True)], "429 — сразу владельцу"
    guard.report(ok=True, throttled=True, retry_after=30)
    assert guard.level == 1 and len(guard.trips) == 1, "остальные ответы той же пачки не удваивают срок"

    with net.background():
        await guard.acquire()
    assert clock.t >= 1120, "фон ждал, пока Яндекс остынет"

    guard.report(ok=True, throttled=True)
    assert guard.cooling_until == pytest.approx(clock.t + 120), "второй раз подряд — вдвое дольше (60 → 120)"
    assert guard.level == 2


async def test_people_go_slower_while_cooling():
    guard, clock = make(rate=3, burst=10)
    guard.report(ok=True, throttled=True)
    for _ in range(10):
        await guard.acquire()
    start = clock.t
    for _ in range(3):
        await guard.acquire()
    assert clock.t - start == pytest.approx(3 / (3 * net.COOL_FACTOR)), "людям — меньше трети обычного темпа"


async def test_timeouts_trip_and_first_blip_is_not_reported_to_owners():
    guard, clock = make()
    for ok in [True] * 6 + [False] * 4:
        guard.report(ok=ok)
    assert guard.cooling and guard.cooling_until == pytest.approx(1060)
    assert guard.trips == [(guard.trips[0][0], 60, False)] and "4 из 10" in guard.trips[0][0], \
        "одна короткая заминка — только в лог и админ-панель"

    clock.t = 1061
    for ok in [True] * 6 + [False] * 4:
        guard.report(ok=ok)
    assert guard.trips[-1][1:] == (120, True), "повторилось сразу после остывания — серьёзно, пишем владельцам"

    clock.t = guard.cooling_until + net.CALM_RELEASE + 1
    guard.report(ok=True)
    assert guard.level == 1, "полчаса спокойно — следующее остывание снова короче"


async def test_few_failures_do_not_trip():
    guard, _ = make()
    for ok in [True] * 20 + [False] * 3:
        guard.report(ok=ok)
    assert not guard.cooling and guard.trips == []


async def test_cooling_is_capped():
    guard, clock = make()
    guard.level = 5
    guard.report(ok=True, throttled=True, retry_after=99999)
    assert guard.cooling_until - clock.t == net.MAX_COOLING


def test_stats_for_admin_panel():
    guard, _ = make(rate=2.5)
    guard.report(ok=True, throttled=True, retry_after=90)
    st = guard.stats()
    assert st["rate"] == 2.5 and st["cooling"] > 0 and st["throttled"] == 1 and st["trips"] == 1
    assert st["last_trip"]["reason"].endswith("(429)") and {"per_min", "last_hour", "busy", "waited"} <= st.keys()


def test_configure():
    guard = net.Guard()
    guard.configure(10)
    assert guard.rate == 10 and guard.burst == 30
    guard.configure(None)
    assert guard.rate == 10


# ---------- все запросы сессий Яндекса идут через guard ----------

@pytest.fixture
async def yandex():
    state = {"status": 200, "hits": 0}

    async def status(request):
        state["hits"] += 1
        if state["status"] != 200:
            return web.Response(status=state["status"], headers={"Retry-After": "90"})
        account = {"uid": 7, "login": "me", "now": "2026-09-26T00:00:00+00:00", "serviceAvailable": True}
        plus = {"hasPlus": True, "isTutorialCompleted": True}
        return web.json_response({"result": {"account": account, "plus": plus}})

    app = web.Application()
    app.router.add_get("/account/status", status)
    server = TestServer(app)
    await server.start_server()
    state["url"] = str(server.make_url("")).rstrip("/")
    yield state
    await close_api_session()
    await server.close()


async def test_every_yandex_request_is_counted_and_429_cools(yandex, yandex_guard):
    client = await ClientAsync("tok", base_url=yandex["url"], request=YandexRequest()).init()
    async with net.yandex_session() as s, s.get(f"{yandex['url']}/account/status") as r:
        assert r.status == 200
    assert yandex_guard.totals["requests"] == 2, "и API, и прямые запросы (скачивание, плеер) — в одном счёте"

    yandex["status"] = 429
    with pytest.raises(Exception):  # noqa: B017 — библиотека выдаёт на 429 свою ошибку, нам важен guard
        await client.account_status()
    assert yandex_guard.cooling and yandex_guard.stats()["cooling"] >= 89, "Retry-After Яндекса соблюдён"


async def test_network_failures_are_reported(yandex_guard, monkeypatch):
    import socket

    monkeypatch.setattr(net, "RETRY_PAUSE", 0.01)
    with socket.socket() as sock:  # свободный порт — соединение отвергнут
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    async with net.yandex_session() as s:
        with pytest.raises(Exception):  # noqa: B017
            await s.get(f"http://127.0.0.1:{port}/")
    assert yandex_guard.totals["failed"] == 1 and yandex_guard.totals["requests"] == 1


async def test_library_request_becomes_network_error_when_busy(yandex, yandex_guard):
    yandex_guard.rate, yandex_guard.burst = 0.01, 1
    yandex_guard.reset()
    client = await ClientAsync("tok", base_url=yandex["url"], request=YandexRequest()).init()
    with pytest.raises(YandexBusyError):
        await client.account_status()
    assert client.me.account.login == "me" and yandex["hits"] == 1, "лишний запрос до Яндекса не ушёл"


async def test_non_yandex_sessions_are_not_throttled(yandex, yandex_guard):
    async with net.session() as s, s.get(f"{yandex['url']}/account/status") as r:
        assert r.status == 200
    assert yandex_guard.totals["requests"] == 0, "Telegram и прочее — мимо guard"


@pytest.mark.parametrize(("value", "expected"), [("", 3.0), ("1.5", 1.5), ("0,5", 0.5)])
def test_rps_config(monkeypatch, value, expected):
    monkeypatch.setattr("bot.config.load_dotenv", lambda: None)
    monkeypatch.setenv("BOT_TOKEN", "1:x")
    monkeypatch.setenv("YANDEX_RPS", value)
    assert load_config().yandex_rps == expected


@pytest.mark.parametrize("value", ["быстро", "0", "100"])
def test_rps_config_rejects_nonsense(monkeypatch, value):
    monkeypatch.setattr("bot.config.load_dotenv", lambda: None)
    monkeypatch.setenv("BOT_TOKEN", "1:x")
    monkeypatch.setenv("YANDEX_RPS", value)
    with pytest.raises(ConfigError, match="YANDEX_RPS"):
        load_config()


async def test_background_jobs_yield_to_people():
    """Ynison, статистика и перестановка треков — фон, даже если их запустил запрос человека."""
    from bot.listening import Listening
    from bot.live import LiveTracker
    from bot.placer import TopPlacer

    seen = []

    async def record(*args):
        seen.append(net._background.get())

    live = LiveTracker.__new__(LiveTracker)
    live._keep_connected = record
    await live._keep(None)
    placer = TopPlacer()
    placer._watch = record
    await placer._run((1, 1), None)
    listening = Listening.__new__(Listening)
    listening.sync = record
    await listening._sync_in_background(1)
    assert seen == [True, True, True] and not net._background.get()
