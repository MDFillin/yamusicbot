"""Плохой маршрут до Яндекса: соединение устанавливается с нескольких попыток, рабочее переиспользуется."""

import asyncio

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from yandex_music import ClientAsync
from yandex_music.exceptions import TimedOutError, UnauthorizedError

from bot import net
from bot import ym as ym_module
from bot.ym import YandexRequest, api_session, close_api_session


@pytest.fixture
def flaky(monkeypatch):
    """Первые `fail` попыток соединиться теряются (как пакеты на сломанном пути), дальше — как обычно."""
    original = aiohttp.TCPConnector._create_connection
    state = {"fail": 0, "calls": 0, "error": asyncio.TimeoutError}

    async def create(self, req, traces, timeout):
        state["calls"] += 1
        if state["fail"] > 0:
            state["fail"] -= 1
            raise state["error"]()
        return await original(self, req, traces, timeout)

    monkeypatch.setattr(aiohttp.TCPConnector, "_create_connection", create)
    monkeypatch.setattr(net, "CONNECT_TIMEOUT", 1)
    return state


@pytest.fixture
async def yandex():
    """Поддельный API Яндекс Музыки: запоминает, с какого соединения пришёл каждый запрос."""
    seen = {"peers": [], "auth": []}

    async def status(request):
        seen["peers"].append(request.transport.get_extra_info("peername"))
        seen["auth"].append(request.headers.get("Authorization"))
        if request.headers.get("Authorization") != "OAuth good":
            return web.json_response({"error": {"name": "session-expired", "message": "Unauthorized"}}, status=401)
        account = {"uid": 7, "login": "me", "now": "2026-09-26T00:00:00+00:00", "serviceAvailable": True}
        plus = {"hasPlus": True, "isTutorialCompleted": True}
        return web.json_response({"result": {"account": account, "plus": plus}})

    async def slow(request):
        await asyncio.sleep(2)
        return web.json_response({"result": {}})

    app = web.Application()
    app.router.add_get("/account/status", status)
    app.router.add_get("/slow", slow)
    server = TestServer(app)
    await server.start_server()
    yield server, seen
    await close_api_session()
    await server.close()


def client(server, token="good"):
    return ClientAsync(token, base_url=str(server.make_url("")).rstrip("/"), request=YandexRequest())


async def test_connection_is_retried_until_a_working_path(flaky, yandex):
    server, _ = yandex
    flaky["fail"] = 4  # 4 из 5 попыток теряются — как у хостера с частично сломанным маршрутом
    c = await client(server).init()
    assert c.me.account.login == "me"
    assert flaky["calls"] == 5, "пятая попытка соединиться прошла"


async def test_working_connection_is_reused(flaky, yandex):
    server, seen = yandex
    c = await client(server).init()
    for _ in range(3):
        await c.account_status()
    assert len(set(seen["peers"])) == 1, "одно соединение на все запросы (keep-alive)"
    assert flaky["calls"] == 1 and seen["auth"] == ["OAuth good"] * 4


async def test_gives_up_after_all_attempts(flaky, yandex):
    server, _ = yandex
    flaky["fail"] = 100
    with pytest.raises(TimedOutError):
        await client(server).init()
    assert flaky["calls"] == net.CONNECT_ATTEMPTS
    flaky["fail"], flaky["error"], flaky["calls"] = 100, lambda: aiohttp.ClientConnectorError(
        aiohttp.client_reqrep.ConnectionKey("h", 443, True, True, None, None, None), OSError(101, "unreachable")), 0
    with pytest.raises(Exception, match="unreachable"):
        await client(server).init()
    assert flaky["calls"] == net.CONNECT_ATTEMPTS


async def test_slow_answer_times_out_and_errors_still_mapped(yandex, monkeypatch):
    server, _ = yandex
    monkeypatch.setattr(ym_module, "API_TOTAL_TIMEOUT", 1)
    monkeypatch.setattr(ym_module, "API_READ_TIMEOUT", 0.5)
    c = client(server)
    with pytest.raises(TimedOutError):
        await c._request.get(f"{c.base_url}/slow")
    with pytest.raises(UnauthorizedError):
        await client(server, token="bad").init()


async def test_session_per_event_loop():
    first = api_session()
    assert api_session() is first
    await close_api_session()
    assert first.closed and api_session() is not first
    await close_api_session()
