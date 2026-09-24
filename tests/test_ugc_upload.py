"""Проверка протокола загрузки своих треков на поддельном сервере Яндекса."""

import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from bot.ym import UploadError, YandexMusic


@pytest.fixture
async def fake_yandex():
    received = {}

    async def get_upload_url(request: web.Request) -> web.Response:
        received["auth"] = request.headers.get("Authorization")
        received["query"] = dict(request.query)
        if request.query.get("kind") == "404":
            return web.Response(status=404, text="playlist not found")
        if request.query.get("kind") == "html":
            return web.Response(text="<!DOCTYPE html><html>new site</html>", content_type="text/html")
        target = str(request.url.with_path("/upload/abc").with_query({}))
        return web.Response(text=json.dumps({"post-target": target, "ugc-track-id": "ugc-123"}),
                            content_type="application/json")

    async def upload(request: web.Request) -> web.Response:
        received["upload_auth"] = request.headers.get("Authorization")
        form = await request.post()
        field = form["file"]
        received["filename"] = field.filename
        received["content_type"] = field.content_type
        received["data"] = field.file.read()
        return web.Response(text="CREATED")

    app = web.Application()
    app.router.add_get("/handlers/ugc-upload.jsx", get_upload_url)
    app.router.add_post("/upload/abc", upload)
    server = TestServer(app)
    await server.start_server()
    yield str(server.make_url("")), received
    await server.close()


@pytest.mark.asyncio
async def test_upload_track(fake_yandex):
    base, received = fake_yandex
    ym = YandexMusic("secret-token", web_base_url=base)
    try:
        result = await ym.upload_track(1003, "Кино - Кукушка.mp3", b"ID3-mp3-bytes")
    finally:
        await ym.close()

    assert result.ugc_track_id == "ugc-123"
    assert result.server_reply == "CREATED"
    assert received["auth"] == "OAuth secret-token"
    assert received["upload_auth"] == "OAuth secret-token"
    assert received["query"]["kind"] == "1003"
    assert received["query"]["filename"] == "Кино - Кукушка.mp3"
    assert received["query"]["visibility"] == "private"
    assert received["filename"] == "Кино - Кукушка.mp3"
    assert received["content_type"] == "audio/mpeg"
    assert received["data"] == b"ID3-mp3-bytes"


@pytest.mark.asyncio
async def test_upload_track_error(fake_yandex):
    base, _ = fake_yandex
    ym = YandexMusic("t", web_base_url=base)
    try:
        with pytest.raises(UploadError, match="HTTP 404"):
            await ym.upload_track(404, "a.mp3", b"x")
    finally:
        await ym.close()


@pytest.mark.asyncio
async def test_upload_track_old_endpoint_gone(fake_yandex):
    base, _ = fake_yandex
    ym = YandexMusic("t", web_base_url=base)
    try:
        with pytest.raises(UploadError, match="изменил способ загрузки"):
            await ym.upload_track("html", "a.mp3", b"x")
    finally:
        await ym.close()
