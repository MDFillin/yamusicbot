"""Общие фикстуры тестов."""

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from bot import net

FAKE_MP3 = b"\xff\xfb\x90\x64" + bytes(range(256)) * 40


@pytest.fixture(autouse=True)
def yandex_guard(monkeypatch):
    """Свой регулятор запросов к Яндексу на каждый тест — просторный, чтобы не тормозить остальные тесты."""
    guard = net.Guard(rate=1000, burst=1000)
    monkeypatch.setattr(net, "guard", guard)
    return guard


@pytest.fixture
async def upstream(tmp_path):
    """Поддельное хранилище Яндекса: отдаёт MP3 и понимает Range."""
    path = tmp_path / "track.mp3"
    path.write_bytes(FAKE_MP3)
    app = web.Application()

    async def serve(request: web.Request) -> web.FileResponse:
        return web.FileResponse(path)

    app.router.add_get("/file.mp3", serve)
    server = TestServer(app)
    await server.start_server()
    yield str(server.make_url("/file.mp3"))
    await server.close()
