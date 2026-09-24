"""Проверка протокола загрузки своих треков на поддельном сервере Яндекса."""

import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from bot.ym import UploadError, YandexMusic


@pytest.fixture
async def fake_yandex():
    """Поддельный Яндекс: loader/upload-url (как у нового сайта) и приём файла по post-target."""
    received = {"calls": []}

    async def loader(request: web.Request) -> web.Response:
        if request.method != "POST":
            # Так отвечает настоящий api.music.yandex.ru на GET.
            return web.json_response({"message": "HTTP method GET is not supported by this URL",
                                      "url": "/upload-url", "status": "405"}, status=405)
        form = await request.post()
        received["calls"].append("loader")
        received["auth"] = request.headers.get("Authorization")
        received["query"] = dict(request.query)
        received["form"] = dict(form)
        path = form.get("path", "")
        if path == "many.mp3":
            return web.json_response({"result": "TOO_MANY_FILES"})
        body = {"post-target": str(request.url.with_path("/upload/abc").with_query({})), "ugc-track-id": "ugc-123"}
        if path == "plain.mp3":
            return web.json_response(body)  # без обёртки result
        return web.json_response({"invocationInfo": {"req-id": "x"}, "result": body})

    async def upload(request: web.Request) -> web.Response:
        received["upload_auth"] = request.headers.get("Authorization")
        form = await request.post()
        field = form["file"]
        received["filename"] = field.filename
        received["content_type"] = field.content_type
        received["data"] = field.file.read()
        return web.Response(text="CREATED")

    app = web.Application()
    app.router.add_route("*", "/api/loader/upload-url", loader)
    app.router.add_post("/upload/abc", upload)
    server = TestServer(app)
    await server.start_server()
    base = str(server.make_url("")).rstrip("/")
    yield base, received
    await server.close()


def make_ym(apis: list[str]) -> YandexMusic:
    ym = YandexMusic("secret-token", api_base_urls=apis)
    ym.uid = 42
    return ym


async def test_upload_via_loader_post(fake_yandex):
    base, received = fake_yandex
    ym = make_ym([f"{base}/api"])
    try:
        result = await ym.upload_track(1003, "Кино - Кукушка.mp3", b"ID3-mp3-bytes")
    finally:
        await ym.close()

    assert result.ugc_track_id == "ugc-123" and result.server_reply == "CREATED"
    expected = {"uid": "42", "playlist-id": "42:1003", "playlistId": "42:1003", "path": "Кино - Кукушка.mp3"}
    assert received["query"] == expected, "параметры в адресе"
    assert received["form"] == expected, "и в теле формы"
    assert received["auth"] == received["upload_auth"] == "OAuth secret-token"
    assert received["filename"] == "Кино - Кукушка.mp3"
    assert received["content_type"] == "audio/mpeg"
    assert received["data"] == b"ID3-mp3-bytes"


async def test_unwrapped_loader_answer_is_accepted(fake_yandex):
    base, _ = fake_yandex
    ym = make_ym([f"{base}/api"])
    try:
        assert (await ym.upload_track(1003, "plain.mp3", b"x")).ugc_track_id == "ugc-123"
    finally:
        await ym.close()


async def test_falls_back_to_next_api_host(fake_yandex):
    base, received = fake_yandex
    ym = make_ym([f"{base}/dead", f"{base}/api"])
    try:
        assert (await ym.upload_track(1003, "a.mp3", b"x")).ugc_track_id == "ugc-123"
    finally:
        await ym.close()
    assert received["calls"] == ["loader"]


async def test_too_many_files(fake_yandex):
    base, _ = fake_yandex
    ym = make_ym([f"{base}/api"])
    try:
        with pytest.raises(UploadError, match="лимит"):
            await ym.upload_track(1003, "many.mp3", b"x")
    finally:
        await ym.close()


async def test_error_lists_every_attempt(fake_yandex):
    base, _ = fake_yandex
    ym = make_ym([f"{base}/dead", f"{base}/also-dead"])
    try:
        with pytest.raises(UploadError) as err:
            await ym.upload_track(1003, "a.mp3", b"x")
    finally:
        await ym.close()
    text = str(err.value)
    assert "/dead/loader/upload-url: HTTP 404" in text and "/also-dead/loader/upload-url: HTTP 404" in text


async def test_upload_needs_connected_account():
    ym = YandexMusic("t")
    with pytest.raises(UploadError, match="не подключена"):
        await ym.upload_track(1003, "a.mp3", b"x")
    await ym.close()


def test_parse_upload_target_and_describe_body():
    from bot.ym import TOO_MANY_FILES, _describe_body, _parse_upload_target

    assert _parse_upload_target(json.dumps({"result": {"post-target": "u"}})) == {"post-target": "u"}
    assert _parse_upload_target(json.dumps({"post-target": "u", "ugc-track-id": "1"}))["post-target"] == "u"
    assert _parse_upload_target(json.dumps({"result": "too-many-files"})) == TOO_MANY_FILES
    assert _parse_upload_target(json.dumps({"error": {"name": "not-found"}})) is None
    assert _parse_upload_target("<html>") is None
    assert _describe_body('{\n"message":"x",\n"status":"405"\n}') == '{ "message":"x", "status":"405" }'
