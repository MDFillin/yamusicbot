"""YANDEX_PROXY: весь трафик к Яндексу — через прокси (SOCKS5 или HTTP), имена хостов разрешает прокси."""

import asyncio
import os
import socket

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from yandex_music import ClientAsync
from yandex_music.exceptions import NetworkError

from bot import net
from bot.config import ConfigError, load_config
from bot.ym import YandexRequest, close_api_session


async def _pipe(reader, writer):
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        writer.close()


async def _bridge(reader, writer, host, port):
    up_reader, up_writer = await asyncio.open_connection("127.0.0.1" if host == "localhost" else host, port)
    await asyncio.gather(_pipe(reader, up_writer), _pipe(up_reader, writer))


@pytest.fixture
async def socks5():
    """Мини-SOCKS5 с логином и паролем: запоминает, куда просили соединиться."""
    seen = {"targets": [], "conns": 0}

    async def handle(reader, writer):
        seen["conns"] += 1
        _, n = await reader.readexactly(2)
        await reader.readexactly(n)
        writer.write(b"\x05\x02")  # логин и пароль
        await writer.drain()
        await reader.readexactly(1)
        user = await reader.readexactly((await reader.readexactly(1))[0])
        password = await reader.readexactly((await reader.readexactly(1))[0])
        ok = (user, password) == (b"bot", b"s3cret")
        writer.write(b"\x01" + (b"\x00" if ok else b"\x01"))
        await writer.drain()
        if not ok:
            writer.close()
            return
        _, _, _, atyp = await reader.readexactly(4)
        if atyp == 3:
            host = (await reader.readexactly((await reader.readexactly(1))[0])).decode()
        else:
            host = socket.inet_ntoa(await reader.readexactly(4))
        port = int.from_bytes(await reader.readexactly(2), "big")
        seen["targets"].append((host, port))
        writer.write(b"\x05\x00\x00\x01" + b"\x00" * 6)
        await writer.drain()
        await _bridge(reader, writer, host, port)

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    seen["url"] = f"socks5://bot:s3cret@127.0.0.1:{server.sockets[0].getsockname()[1]}"
    yield seen
    server.close()


@pytest.fixture
async def http_proxy():
    seen = {"targets": []}

    async def handle(reader, writer):
        line = (await reader.readline()).decode()
        while (await reader.readline()) not in (b"\r\n", b""):
            pass
        _, target, _ = line.split()
        host, port = target.rsplit(":", 1)
        seen["targets"].append((host, int(port)))
        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await writer.drain()
        await _bridge(reader, writer, host, int(port))

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    seen["url"] = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
    yield seen
    server.close()


@pytest.fixture
async def yandex():
    async def status(request):
        account = {"uid": 7, "login": "me", "now": "2026-09-26T00:00:00+00:00", "serviceAvailable": True}
        plus = {"hasPlus": True, "isTutorialCompleted": True}
        return web.json_response({"result": {"account": account, "plus": plus}})

    app = web.Application()
    app.router.add_get("/account/status", status)
    server = TestServer(app)
    await server.start_server()
    yield f"http://localhost:{server.port}"
    await close_api_session()
    await server.close()


@pytest.fixture
def proxied():
    def use(url):
        net.configure(url)

    yield use
    net.configure(None)


async def test_api_goes_through_socks5_with_remote_dns(socks5, yandex, proxied):
    proxied(socks5["url"])
    client = await ClientAsync("tok", base_url=yandex, request=YandexRequest()).init()
    for _ in range(3):
        await client.account_status()
    port = int(yandex.rsplit(":", 1)[1])
    assert client.me.account.login == "me"
    assert socks5["targets"] == [("localhost", port)], "имя хоста разрешает прокси; соединение одно на все запросы"


async def test_api_goes_through_http_proxy(http_proxy, yandex, proxied):
    proxied(http_proxy["url"])
    client = await ClientAsync("tok", base_url=yandex, request=YandexRequest()).init()
    assert client.me.account.login == "me" and http_proxy["targets"][0][0] == "localhost"


async def test_dead_proxy_is_a_network_error_without_password(yandex, proxied, monkeypatch):
    monkeypatch.setattr(net, "PROXY_CONNECT_TIMEOUT", 1)
    monkeypatch.setattr(net, "CONNECT_ATTEMPTS", 2)
    with socket.socket() as s:  # свободный порт, на котором никто не слушает
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proxied(f"socks5://bot:s3cret@127.0.0.1:{port}")
    with pytest.raises(NetworkError) as e:
        await ClientAsync("tok", base_url=yandex, request=YandexRequest()).init()
    assert "прокси socks5://127.0.0.1" in str(e.value) and "s3cret" not in str(e.value)


async def test_sessions_pick_the_proxy(proxied):
    proxied("socks5://u:p@10.0.0.1:1080")
    async with net.yandex_session() as s:
        assert isinstance(s.connector, net.RetryingProxyConnector) and not s.trust_env
    async with net.session() as s:
        assert type(s.connector) is net.RetryingConnector, "не-Яндекс (Telegram, Spotify) — напрямую"
    assert net.mask("socks5://u:p@10.0.0.1:1080") == "socks5://10.0.0.1:1080"


@pytest.mark.parametrize(("env", "expected"), [
    ({"YANDEX_PROXY": "socks5h://u:p@1.2.3.4:1080"}, "socks5://u:p@1.2.3.4:1080"),
    ({"YANDEX_PROXY": "http://1.2.3.4:3128"}, "http://1.2.3.4:3128"),
    ({"YANDEX_SSH": "proxy@5.6.7.8"}, "socks5://yandex-tunnel:1080"),
    ({}, None),
])
def test_config(monkeypatch, env, expected):
    for key in ("YANDEX_PROXY", "YANDEX_SSH"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("bot.config.load_dotenv", lambda: None)
    monkeypatch.setenv("BOT_TOKEN", "1:x")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert load_config().yandex_proxy == expected


def test_config_rejects_unknown_scheme(monkeypatch):
    monkeypatch.setattr("bot.config.load_dotenv", lambda: None)
    monkeypatch.setenv("BOT_TOKEN", "1:x")
    monkeypatch.setenv("YANDEX_PROXY", "ftp://1.2.3.4")
    with pytest.raises(ConfigError):
        load_config()


async def test_diag_reports_through_proxy(socks5, yandex, proxied, monkeypatch, capsys):
    from bot import diag_net

    monkeypatch.setattr(diag_net, "TARGETS", (("API", f"{yandex}/account/status"),))
    monkeypatch.setattr("bot.config.load_dotenv", lambda: None)
    monkeypatch.setenv("BOT_TOKEN", "1:x")
    monkeypatch.setenv("YANDEX_PROXY", socks5["url"])
    await diag_net.main()
    out = capsys.readouterr().out
    assert "через прокси socks5://127.0.0.1" in out and "s3cret" not in out and "✅" in out
    assert os.environ.get("YANDEX_PROXY") == socks5["url"]


async def test_websocket_through_proxy(socks5, proxied):
    """Ynison (точный подсчёт) — websocket; он тоже должен ходить через прокси."""
    async def echo(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async for msg in ws:
            await ws.send_str("эхо: " + msg.data)
        return ws

    app = web.Application()
    app.router.add_get("/ws", echo)
    server = TestServer(app)
    await server.start_server()
    proxied(socks5["url"])
    try:
        async with net.yandex_session() as s, s.ws_connect(f"ws://localhost:{server.port}/ws") as ws:
            await ws.send_str("привет")
            assert (await ws.receive()).data == "эхо: привет"
        assert socks5["targets"] == [("localhost", server.port)]
    finally:
        await server.close()
