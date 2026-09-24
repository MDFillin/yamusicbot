"""API мини-приложения: авторизация Telegram, свой аккаунт Яндекса у каждого, плеер, скачивание и загрузка."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from types import SimpleNamespace as NS
from urllib.parse import urlencode

import pytest
from aiogram import Bot
from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer

from bot import accounts as accounts_module
from bot.accounts import Accounts
from bot.audio import read_mp3_tags, tag_mp3
from bot.config import Config
from bot.storage import Storage
from bot.web.app import create_app
from bot.ym import UploadResult, YandexNotReady

TOKEN = "42:TEST-TOKEN"
OWNER_ID = 1
FRIEND_ID = 2
NEWBIE_ID = 666
FAKE_MP3 = b"\xff\xfb\x90\x64" + bytes(range(256)) * 40


def init_data(user_id: int = OWNER_ID, token: str = TOKEN, auth_date: int | None = None) -> str:
    """initData так, как его подписывает Telegram."""
    fields = {
        "auth_date": str(auth_date or int(time.time())),
        "query_id": "AAE",
        "user": json.dumps({"id": user_id, "first_name": "User"}, separators=(",", ":")),
    }
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def as_user(user_id: int) -> dict[str, str]:
    return {"X-Telegram-Init-Data": init_data(user_id)}


def make_track(track_id: int, title: str) -> NS:
    return NS(
        id=track_id, title=title, version=None, duration_ms=200_000, available=True,
        artists=[NS(id=7, name="Кино", cover=None)], albums=[NS(id=10, title="Альбом", year=1988)],
        cover_uri="avatars.yandex.net/get-music-content/abc/%%",
    )


class FakeYM:
    has_plus = True
    start_error = None

    def __init__(self, upstream_url: str = "", uid: int = 42, login: str = "me", likes=("1:10", "2:10")) -> None:
        self.upstream_url = upstream_url
        self.uid, self.login = uid, login
        self.likes = list(likes)
        self.tracks = {"1": make_track(1, "Кукушка"), "2": make_track(2, "Группа крови")}
        self.playlist = NS(
            kind=1003, title="Мои записи", track_count=2, uid=uid, revision=5, og_image=None,
            cover=NS(uri=None, items_uri=["avatars.yandex.net/mosaic/%%"]), owner=NS(uid=uid, name="Я", login=login),
            tracks=[NS(id=1, track=None, track_id="1:10"), NS(id=2, track=None, track_id="2:10")],
        )
        self.calls: list[tuple] = []

    @property
    def ready(self) -> bool:
        return self.start_error is None

    async def start(self):
        pass

    async def ensure_started(self):
        if self.start_error:
            raise YandexNotReady(self.start_error)

    async def close(self):
        pass

    async def get_my_playlists(self):
        return [self.playlist]

    async def get_liked_track_ids(self):
        self.calls.append(("likes",))
        return self.likes

    async def get_playlist(self, kind, owner=None):
        self.calls.append(("get_playlist", int(kind)))
        return self.playlist if int(kind) == self.playlist.kind else None

    async def get_tracks(self, ids):
        return [self.tracks[i.split(":")[0]] for i in ids]

    async def get_track(self, track_id):
        return self.tracks.get(track_id)

    async def like(self, track_id):
        self.calls.append(("like", track_id))
        return True

    async def unlike(self, track_id):
        self.calls.append(("unlike", track_id))
        return True

    async def create_playlist(self, title):
        return NS(kind=1010, title=title, track_count=0, uid=42, og_image=None, cover=None, owner=NS(uid=42, name="Я"))

    async def rename_playlist(self, kind, title):
        self.calls.append(("rename", kind, title))
        return None

    async def delete_playlist(self, kind):
        self.calls.append(("delete", kind))
        return True

    async def remove_from_playlist(self, kind, index, track_id):
        self.calls.append(("remove", kind, index, track_id))

    async def direct_link(self, track):
        self.calls.append(("direct_link", str(track.id)))
        return self.upstream_url

    async def download_tagged(self, track):
        return tag_mp3(FAKE_MP3, artist="Кино", title=track.title), 320

    async def upload_track(self, kind, filename, data):
        self.calls.append(("upload", kind, filename, data))
        return UploadResult("ugc-9", "CREATED")


class FakeSender:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str, str]] = []

    async def send(self, chat_id, track, ym):
        self.sent.append((chat_id, str(track.id), ym.login))


class FakeOAuth:
    def __init__(self) -> None:
        self.confirmed = False

    async def request_device_code(self, device_name=None):
        return NS(device_code="dev", user_code="WXYZ5678", verification_url="https://ya.ru/device",
                  expires_in=300, interval=0)

    async def poll_device_token(self, device_code):
        return NS(access_token="tok-newbie") if self.confirmed else None


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


@pytest.fixture
async def env(tmp_path, upstream, monkeypatch):
    monkeypatch.setattr(accounts_module, "MIN_POLL_INTERVAL", 0.01)
    fakes = {
        "tok-owner": FakeYM(upstream),
        "tok-friend": FakeYM(upstream, uid=77, login="friend", likes=["2:10"]),
        "tok-newbie": FakeYM(upstream, uid=99, login="newbie", likes=[]),
    }
    store = Storage(tmp_path / "bot.db")
    store.set_account(OWNER_ID, "tok-owner", "me")
    store.set_account(FRIEND_ID, "tok-friend", "friend")
    oauth = FakeOAuth()
    accounts = Accounts(store, factory=fakes.__getitem__, oauth=oauth)
    sender = FakeSender()
    config = Config(bot_token=TOKEN, data_dir=tmp_path)
    bot = Bot(TOKEN)
    client = TestClient(TestServer(create_app(config, bot, accounts, store, sender)))
    await client.start_server()
    client.session.headers["X-Telegram-Init-Data"] = init_data()
    yield NS(client=client, ym=fakes["tok-owner"], friend=fakes["tok-friend"], store=store, sender=sender,
             accounts=accounts, oauth=oauth)
    await client.close()
    await bot.session.close()
    await accounts.close()
    store.close()


# ---------- авторизация и аккаунты ----------

async def test_api_requires_valid_telegram_signature(env):
    c = env.client
    r = await c.get("/api/me", headers={"X-Telegram-Init-Data": ""})
    assert r.status == 401
    r = await c.get("/api/me", headers={"X-Telegram-Init-Data": init_data(token="1:OTHER")})
    assert r.status == 401 and "подпись" in (await r.json())["error"]
    r = await c.get("/api/me", headers={"X-Telegram-Init-Data": init_data(auth_date=int(time.time()) - 3 * 86400)})
    assert r.status == 401
    me = await (await c.get("/api/me")).json()
    assert me["connected"] and me["login"] == "me" and me["has_plus"]


async def test_anyone_can_open_app_but_needs_own_yandex(env):
    c = env.client
    me = await (await c.get("/api/me", headers=as_user(NEWBIE_ID))).json()
    assert me["connected"] is False and "login" not in me
    r = await c.get("/api/library", headers=as_user(NEWBIE_ID))
    assert r.status == 401 and (await r.json())["code"] == "login_required"


async def test_login_and_logout_in_app(env):
    c = env.client
    newbie = as_user(NEWBIE_ID)
    assert (await (await c.get("/api/login", headers=newbie)).json())["status"] == "none"
    started = await (await c.post("/api/login", headers=newbie)).json()
    assert started["status"] == "pending" and started["code"] == "WXYZ5678" and started["url"] == "https://ya.ru/device"
    assert (await (await c.post("/api/login", headers=newbie)).json())["code"] == "WXYZ5678", "повторно — тот же код"

    env.oauth.confirmed = True
    for _ in range(100):
        status = await (await c.get("/api/login", headers=newbie)).json()
        if status["status"] != "pending":
            break
        await asyncio.sleep(0.02)
    assert status["status"] == "done" and status["connected"]
    me = await (await c.get("/api/me", headers=newbie)).json()
    assert me["connected"] and me["login"] == "newbie"
    assert (await (await c.get("/api/library", headers=newbie)).json())["liked_ids"] == []

    assert (await c.post("/api/logout", headers=newbie)).status == 200
    assert (await (await c.get("/api/me", headers=newbie)).json())["connected"] is False
    assert (await c.get("/api/me")).status == 200 and env.accounts.is_connected(OWNER_ID), "других не трогает"


async def test_login_can_be_cancelled(env):
    newbie = as_user(NEWBIE_ID)
    await env.client.post("/api/login", headers=newbie)
    assert (await env.client.delete("/api/login", headers=newbie)).status == 200
    assert (await (await env.client.get("/api/login", headers=newbie)).json())["status"] == "none"


async def test_each_user_sees_own_library(env):
    mine = await (await env.client.get("/api/library")).json()
    friends = await (await env.client.get("/api/library", headers=as_user(FRIEND_ID))).json()
    assert mine["liked_ids"] == ["1", "2"] and friends["liked_ids"] == ["2"]

    await env.client.get("/api/source/likes")
    friend_likes = await (await env.client.get("/api/source/likes", headers=as_user(FRIEND_ID))).json()
    assert [t["id"] for t in friend_likes["tracks"]] == ["2"], "кэш списков у каждого свой"


async def test_index_and_static_are_public_compressed_and_cached(env):
    r = await env.client.get("/", headers={"X-Telegram-Init-Data": "", "Accept-Encoding": "gzip"})
    html = await r.text()
    assert r.status == 200 and "/static/app.js?v=" in html and "__V__" not in html
    assert r.headers["Content-Encoding"] == "gzip" and r.headers["Cache-Control"] == "no-cache"
    version = html.split("/static/app.js?v=")[1].split('"')[0]

    r = await env.client.get(f"/static/app.js?v={version}", headers={"Accept-Encoding": "gzip"})
    assert r.status == 200 and "Telegram.WebApp" in await r.text()
    assert r.headers["Content-Encoding"] == "gzip" and "immutable" in r.headers["Cache-Control"]
    assert "all rights reserved by" in await r.text()

    r = await env.client.get("/static/app.js?v=old")
    assert r.headers["Cache-Control"] == "no-cache", "старую версию не кэшируем навсегда"
    assert (await env.client.get("/static/nope.js")).status == 404


async def test_big_json_is_gzipped(env):
    env.ym.likes = [f"{i % 2 + 1}:10" for i in range(300)]
    r = await env.client.get("/api/library", headers={"Accept-Encoding": "gzip"})
    assert r.headers.get("Content-Encoding") == "gzip" and len((await r.json())["liked_ids"]) == 300


# ---------- медиатека ----------

async def test_library(env):
    data = await (await env.client.get("/api/library")).json()
    assert data["liked_ids"] == ["1", "2"]
    [pl] = data["playlists"]
    assert pl == {"ref": "42.1003", "kind": 1003, "title": "Мои записи", "count": 2,
                  "cover": "https://avatars.yandex.net/mosaic/200x200", "owner": "Я"}


async def test_source_pages_use_cache_and_carry_signed_links(env):
    r = await env.client.get("/api/source/pl", params={"ref": "42.1003", "offset": 0, "limit": 1})
    first = await r.json()
    assert first["title"] == "Мои записи" and first["own_kind"] == 1003 and first["total"] == 2
    [t] = first["tracks"]
    assert t["id"] == "1" and t["index"] == 0 and t["artists"] == "Кино" and t["album_id"] == "10"
    assert t["cover"] == "https://avatars.yandex.net/get-music-content/abc/200x200"
    assert t["stream"].startswith(f"/media/stream/1?u={OWNER_ID}&exp=") and t["filename"] == "Кино - Кукушка.mp3"

    second = await (await env.client.get("/api/source/pl", params={"ref": "42.1003", "offset": 1})).json()
    assert [x["index"] for x in second["tracks"]] == [1]
    assert env.ym.calls.count(("get_playlist", 1003)) == 1, "следующая страница берётся из кэша"


async def test_unknown_source_is_404(env):
    r = await env.client.get("/api/source/evil")
    assert r.status == 404


async def test_bad_numbers_are_400(env):
    r = await env.client.get("/api/source/likes", params={"offset": "abc"})
    assert r.status == 400 and (await r.json())["error"] == "Некорректный запрос"
    assert (await env.client.put("/api/target", json={"kind": "x"})).status == 400


# ---------- управление ----------

async def test_like_unlike_and_playlist_management(env):
    c = env.client
    assert (await (await c.post("/api/likes/1")).json()) == {"liked": True}
    assert (await (await c.delete("/api/likes/1")).json()) == {"liked": False}

    created = await (await c.post("/api/playlists", json={"title": "  Новый  "})).json()
    assert created["kind"] == 1010 and created["title"] == "Новый"
    assert (await c.post("/api/playlists", json={"title": " "})).status == 400

    assert (await c.patch("/api/playlists/1003", json={"title": "Демо"})).status == 200
    assert (await c.delete("/api/playlists/1003/tracks/1?track_id=2")).status == 200

    assert (await (await c.put("/api/target", json={"kind": 1003})).json()) == {"upload_target": 1003}
    assert env.store.get_upload_target(OWNER_ID) == 1003
    assert (await c.put("/api/target", json={"kind": 777})).status == 404
    assert (await c.delete("/api/playlists/1003")).status == 200
    assert env.store.get_upload_target(OWNER_ID) is None, "удалённый плейлист перестаёт быть целью"

    assert env.ym.calls[:2] == [("like", "1"), ("unlike", "1")]
    assert ("rename", 1003, "Демо") in env.ym.calls
    assert ("remove", 1003, 1, "2") in env.ym.calls
    assert ("delete", 1003) in env.ym.calls
    assert not [c for c in env.friend.calls if c[0] != "likes"], "аккаунт друга не затронут"


async def test_send_track_to_chat(env):
    assert (await env.client.post("/api/tracks/2/send")).status == 200
    assert (await env.client.post("/api/tracks/2/send", headers=as_user(FRIEND_ID))).status == 200
    assert env.sender.sent == [(OWNER_ID, "2", "me"), (FRIEND_ID, "2", "friend")]
    assert (await env.client.post("/api/tracks/404/send")).status == 404


# ---------- плеер и скачивание ----------

async def _track_links(env, user_id: int = OWNER_ID) -> dict:
    data = await (await env.client.get("/api/source/likes", headers=as_user(user_id))).json()
    return data["tracks"][0]


async def test_stream_proxies_audio_with_range(env):
    t = await _track_links(env)
    r = await env.client.get(t["stream"], headers={"Range": "bytes=4-9", "X-Telegram-Init-Data": ""})
    assert r.status == 206
    assert r.headers["Content-Range"] == f"bytes 4-9/{len(FAKE_MP3)}"
    assert r.content_type == "audio/mpeg"
    assert await r.read() == FAKE_MP3[4:10]

    r = await env.client.get(t["stream"], headers={"X-Telegram-Init-Data": ""})
    assert r.status == 200 and await r.read() == FAKE_MP3
    assert ("direct_link", "1") in env.ym.calls and not any(c[0] == "direct_link" for c in env.friend.calls)


async def test_media_links_are_signed_per_user(env):
    t = await _track_links(env)
    forged = t["stream"].replace("/stream/1?", "/stream/2?")
    assert (await env.client.get(forged)).status == 403
    assert (await env.client.get(t["stream"].replace(f"u={OWNER_ID}", f"u={FRIEND_ID}"))).status == 403, \
        "чужую ссылку не переписать на другой аккаунт"
    assert (await env.client.get("/media/stream/1?u=1&exp=9999999999&sig=00")).status == 403
    assert (await env.client.get(t["download"].replace("download", "stream"))).status == 403

    await env.accounts.logout(OWNER_ID)
    assert (await env.client.get(t["stream"])).status == 403, "после выхода ссылки не работают"


async def test_download_returns_tagged_attachment(env):
    t = await _track_links(env)
    r = await env.client.get(t["download"], headers={"X-Telegram-Init-Data": ""})
    assert r.status == 200
    assert "filename*=UTF-8''%D0%9A%D0%B8%D0%BD%D0%BE%20-%20" in r.headers["Content-Disposition"]
    assert r.headers["Access-Control-Allow-Origin"] == "https://web.telegram.org"
    assert read_mp3_tags(await r.read()) == ("Кино", "Кукушка")


# ---------- загрузка ----------

async def test_upload_file_with_manual_tags(env):
    form = FormData()
    form.add_field("kind", "1003")
    form.add_field("artist", "Кино")
    form.add_field("title", "Звезда")
    form.add_field("fallback_artist", "")
    form.add_field("fallback_title", "rec_01")
    form.add_field("file", FAKE_MP3, filename="rec_01.mp3", content_type="audio/mpeg")
    r = await env.client.post("/api/upload", data=form)
    body = await r.json()
    assert r.status == 200, body
    assert body["name"] == "Кино - Звезда.mp3" and body["ugc_track_id"] == "ugc-9"
    [(_, kind, name, data)] = [c for c in env.ym.calls if c[0] == "upload"]
    assert (kind, name) == (1003, "Кино - Звезда.mp3")
    assert read_mp3_tags(data) == ("Кино", "Звезда")


async def test_upload_uses_filename_guess_only_without_tags(env):
    form = FormData()
    form.add_field("kind", "1003")
    form.add_field("fallback_artist", "Из имени")
    form.add_field("fallback_title", "Файла")
    tagged = tag_mp3(FAKE_MP3, artist="Из тегов", title="Файла")
    form.add_field("file", tagged, filename="Из имени - Файла.mp3", content_type="audio/mpeg")
    assert (await env.client.post("/api/upload", data=form)).status == 200
    [(_, _, _, data)] = [c for c in env.ym.calls if c[0] == "upload"]
    assert read_mp3_tags(data) == ("Из тегов", "Файла"), "свои теги файла важнее догадки по имени"


async def test_upload_validation(env):
    form = FormData()
    form.add_field("file", FAKE_MP3, filename="a.mp3")
    r = await env.client.post("/api/upload", data=form)
    assert r.status == 400 and "плейлист" in (await r.json())["error"]


async def test_api_reports_yandex_problem(env):
    env.ym.start_error = "вход устарел"
    r = await env.client.get("/api/me")
    body = await r.json()
    assert r.status == 503 and body["code"] == "yandex_unavailable" and "вход устарел" in body["error"]
    assert (await env.client.get("/api/me", headers=as_user(FRIEND_ID))).status == 200, "у других всё работает"


async def test_revoked_login_resets_client(env):
    from yandex_music.exceptions import UnauthorizedError

    async def revoked():
        raise UnauthorizedError("401")

    env.ym.get_my_playlists = revoked
    r = await env.client.get("/api/library")
    assert r.status == 503 and (await r.json())["code"] == "yandex_unavailable"
    assert OWNER_ID not in env.accounts._clients
