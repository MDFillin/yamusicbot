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
from aiohttp import FormData
from aiohttp.test_utils import TestClient, TestServer

from bot import accounts as accounts_module
from bot.accounts import Accounts
from bot.audio import read_mp3_tags, read_tags, tag_mp3
from bot.config import Config
from bot.storage import Storage
from bot.web.app import create_app
from bot.ym import UploadResult, YandexNotReady

TOKEN = "42:TEST-TOKEN"
OWNER_ID = 1
FRIEND_ID = 2
NEWBIE_ID = 666
FAKE_MP3 = b"\xff\xfb\x90\x64" + bytes(range(256)) * 40
FAKE_JPEG = b"\xff\xd8\xff\xe0" + b"cover" * 40


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

    async def music_history(self):
        from bot.listening import msk_now
        self.calls.append(("music_history",))
        wave = NS(type="wave", data=NS(item_id=NS(seeds=["user:onyourwave"]), full_model=None))
        tracks = [NS(type="track", data=NS(item_id=NS(track_id=t, album_id="10"), full_model=None)) for t in ("1", "2")]
        return NS(history_tabs=[NS(date=msk_now().date().isoformat(), items=[NS(context=wave, tracks=tracks)])])

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

    async def direct_link(self, track, max_bitrate=None):
        self.calls.append(("direct_link", str(track.id), max_bitrate))
        return self.upstream_url

    async def download_tagged(self, track, max_bitrate=None):
        self.calls.append(("download", str(track.id), max_bitrate))
        return tag_mp3(FAKE_MP3, artist="Кино", title=track.title), max_bitrate or 320

    async def upload_track(self, kind, filename, data):
        self.calls.append(("upload", kind, filename, data))
        return UploadResult("ugc-9", "CREATED")


class FakeSender:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str, str]] = []

    async def send(self, chat_id, track, ym):
        self.sent.append((chat_id, str(track.id), ym.login))


class FakePlacer:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def before_upload(self, ym, kind):
        self.calls.append(("before", ym.login, kind))
        return {"known"}

    def after_upload(self, ym, kind, known, ugc_id):
        self.calls.append(("after", ym.login, kind, known, ugc_id))


class FakeOAuth:
    def __init__(self) -> None:
        self.confirmed = False

    async def request_device_code(self, device_name=None):
        return NS(device_code="dev", user_code="WXYZ5678", verification_url="https://ya.ru/device",
                  expires_in=300, interval=0)

    async def poll_device_token(self, device_code):
        return NS(access_token="tok-newbie") if self.confirmed else None


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
    placer = FakePlacer()
    client = TestClient(TestServer(create_app(config, bot, accounts, store, sender, placer)))
    await client.start_server()
    client.session.headers["X-Telegram-Init-Data"] = init_data()
    yield NS(client=client, ym=fakes["tok-owner"], friend=fakes["tok-friend"], store=store, sender=sender,
             accounts=accounts, oauth=oauth, placer=placer)
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
    assert ("direct_link", "1", 320) in env.ym.calls and not any(c[0] == "direct_link" for c in env.friend.calls)


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


async def test_upload_with_editor_meta_and_cover(env):
    tagged = tag_mp3(FAKE_MP3, artist="Из файла", title="Трек", album="Старый альбом", year=2001)
    form = FormData()
    form.add_field("kind", "1003")
    form.add_field("meta", json.dumps({"title": "Новое", "artist": None, "album": "", "year": "2024"}))
    form.add_field("cover", FAKE_JPEG, filename="cover.jpg", content_type="image/jpeg")
    form.add_field("file", tagged, filename="x.mp3", content_type="audio/mpeg")
    r = await env.client.post("/api/upload", data=form)
    body = await r.json()
    assert r.status == 200, body
    assert body["name"] == "Из файла - Новое.mp3" and "новая обложка" in body["notes"]
    [(_, kind, _, data)] = [c for c in env.ym.calls if c[0] == "upload"]
    meta = read_tags(data)
    assert (meta.title, meta.artist, meta.album, meta.year) == ("Новое", "Из файла", None, "2024")
    assert meta.cover == FAKE_JPEG
    assert env.placer.calls == [("before", "me", 1003), ("after", "me", 1003, {"known"}, "ugc-9")], \
        "трек встанет в начало плейлиста"


async def test_upload_can_remove_cover(env):
    form = FormData()
    form.add_field("kind", "1003")
    form.add_field("meta", json.dumps({"remove_cover": True}))
    form.add_field("file", tag_mp3(FAKE_MP3, title="T", cover=FAKE_JPEG), filename="x.mp3")
    assert (await env.client.post("/api/upload", data=form)).status == 200
    [(_, _, _, data)] = [c for c in env.ym.calls if c[0] == "upload"]
    assert read_tags(data).cover is None and read_tags(data).title == "T"


@pytest.mark.parametrize(("field", "value", "error"), [
    ("meta", json.dumps({"year": "давно"}), "Год"),
    ("meta", "не json", "Некорректные данные"),
])
async def test_upload_rejects_bad_meta(env, field, value, error):
    form = FormData()
    form.add_field("kind", "1003")
    form.add_field(field, value)
    form.add_field("file", FAKE_MP3, filename="x.mp3")
    r = await env.client.post("/api/upload", data=form)
    assert r.status == 400 and error in (await r.json())["error"]
    assert not [c for c in env.ym.calls if c[0] == "upload"]


async def test_upload_rejects_non_image_cover(env):
    form = FormData()
    form.add_field("kind", "1003")
    form.add_field("cover", b"%PDF-1.4 not an image", filename="c.jpg")
    form.add_field("file", FAKE_MP3, filename="x.mp3")
    r = await env.client.post("/api/upload", data=form)
    assert r.status == 422 and "картинкой" in (await r.json())["error"]


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


# ---------- настройки ----------

async def test_settings_defaults_and_update(env):
    c = env.client
    me = await (await c.get("/api/me")).json()
    assert me["settings"]["download_quality"] == 320 and me["settings"]["stream_quality"] == 320
    assert [q["kbps"] for q in me["settings"]["qualities"]] == [320, 192, 128, 64]

    r = await c.put("/api/settings", json={"download_quality": 128, "stream_quality": 64})
    assert (await r.json())["download_quality"] == 128 and env.store.get_setting(OWNER_ID, "stream_quality") == "64"
    assert (await c.put("/api/settings", json={"download_quality": 100})).status == 400
    assert (await c.put("/api/settings", json={"stream_quality": "много"})).status == 400
    friend = await (await c.get("/api/settings", headers=as_user(FRIEND_ID))).json()
    assert friend["download_quality"] == 320, "у каждого свои настройки"


async def test_quality_applies_to_player_and_downloads(env):
    await env.client.put("/api/settings", json={"download_quality": 192, "stream_quality": 128})
    t = await _track_links(env)
    assert (await env.client.get(t["stream"])).status == 200
    assert (await env.client.get(t["download"])).status == 200
    assert ("direct_link", "1", 128) in env.ym.calls and ("download", "1", 192) in env.ym.calls


async def test_token_is_shown_only_to_its_owner(env):
    r = await env.client.get("/api/token")
    assert (await r.json()) == {"token": "tok-owner"} and r.headers["Cache-Control"] == "no-store"
    assert (await (await env.client.get("/api/token", headers=as_user(FRIEND_ID))).json())["token"] == "tok-friend"
    r = await env.client.get("/api/token", headers=as_user(NEWBIE_ID))
    assert r.status == 401 and (await r.json())["code"] == "login_required"
    assert (await env.client.get("/api/token", headers={"X-Telegram-Init-Data": ""})).status == 401
    stale = init_data(auth_date=int(time.time()) - 2 * 3600)
    r = await env.client.get("/api/token", headers={"X-Telegram-Init-Data": stale})
    assert r.status == 401 and (await r.json())["code"] == "stale_session", "токен — только свежеоткрытому приложению"
    assert (await env.client.get("/api/library", headers={"X-Telegram-Init-Data": stale})).status == 200


# ---------- защита ----------

async def test_security_headers_everywhere(env):
    stream = (await (await env.client.get("/api/source/likes")).json())["tracks"][0]["stream"]
    for path in ("/", "/api/me", "/static/nope.js", stream, "/media/stream/1?u=1&exp=1&sig=x"):
        r = await env.client.get(path)
        assert r.headers["X-Content-Type-Options"] == "nosniff", path
        csp = r.headers["Content-Security-Policy"]
        assert "script-src 'self' https://telegram.org" in csp and "object-src 'none'" in csp, path


async def test_internal_errors_are_not_shown(env):
    async def broken():
        raise RuntimeError("/srv/secret/path token=abc")

    env.ym.get_my_playlists = broken
    r = await env.client.get("/api/library")
    body = await r.json()
    assert r.status == 500 and "secret" not in body["error"] and "RuntimeError" not in body["error"]
    assert "код" in body["error"]


async def test_json_body_is_limited(env):
    r = await env.client.post("/api/playlists", json={"title": "x" * 100_000})
    assert r.status == 400 and "большой" in (await r.json())["error"]

    async def chunked():  # без Content-Length
        yield b'{"title": "' + b"x" * 100_000 + b'"}'

    r = await env.client.post("/api/playlists", data=chunked(), headers={"Content-Type": "application/json"})
    assert r.status == 400


async def test_upload_size_is_limited(env, tmp_path):
    config = Config(bot_token=TOKEN, data_dir=tmp_path, web_max_upload_mb=1)
    bot = Bot(TOKEN)
    client = TestClient(TestServer(create_app(config, bot, env.accounts, env.store, env.sender, env.placer)))
    await client.start_server()
    try:
        form = FormData()
        form.add_field("kind", "1003")
        form.add_field("file", FAKE_MP3 * 200, filename="big.mp3")  # ~2 МБ
        r = await client.post("/api/upload", data=form, headers=as_user(OWNER_ID))
        assert r.status == 413 and "до 1 МБ" in (await r.json())["error"]

        form = FormData()
        form.add_field("kind", "1003")
        form.add_field("cover", b"\xff\xd8" + b"0" * (11 * 1024 * 1024), filename="c.jpg")
        form.add_field("file", FAKE_MP3, filename="x.mp3")
        r = await env.client.post("/api/upload", data=form)
        assert r.status == 400 and "10 МБ" in (await r.json())["error"]
        assert not [c for c in env.ym.calls if c[0] == "upload"]
    finally:
        await client.close()
        await bot.session.close()


async def test_uploads_are_queued_per_user(env):
    running = {"me": 0, "friend": 0}
    peak = {"me": 0, "friend": 0, "all": 0}

    def slow(ym):
        async def upload_track(kind, filename, data):
            running[ym.login] += 1
            peak[ym.login] = max(peak[ym.login], running[ym.login])
            peak["all"] = max(peak["all"], sum(running.values()))
            await asyncio.sleep(0.05)
            running[ym.login] -= 1
            return UploadResult("ugc", "CREATED")
        return upload_track

    env.ym.upload_track = slow(env.ym)
    env.friend.upload_track = slow(env.friend)

    def post(user_id):
        form = FormData()
        form.add_field("kind", "1003")
        form.add_field("file", FAKE_MP3, filename="x.mp3")
        return env.client.post("/api/upload", data=form, headers=as_user(user_id))

    replies = await asyncio.gather(post(OWNER_ID), post(OWNER_ID), post(FRIEND_ID))
    assert [r.status for r in replies] == [200, 200, 200]
    assert peak["me"] == 1, "свои файлы — по одному"
    assert peak["all"] == 2, "а разные люди загружают одновременно"


# ---------- статистика прослушиваний ----------

async def test_stats_api(env):
    c = env.client
    assert (await (await c.get("/api/stats")).json()) == {
        "enabled": False, "prefs": {"day": False, "week": True, "month": True}}
    r = await c.put("/api/stats", json={"enabled": True})
    assert (await r.json())["enabled"] is True
    data = await (await c.get("/api/stats?kind=week")).json()
    assert data["stats"]["plays"] == 2 and data["stats"]["artists"] == 1
    assert data["period"]["offset"] == 0 and data["period"]["today"]
    assert [t["title"] for t in data["top_tracks"]] == ["Кукушка", "Группа крови"] or \
        {t["title"] for t in data["top_tracks"]} == {"Кукушка", "Группа крови"}
    assert all(t["stream"].startswith("/media/stream/") for t in data["top_tracks"]), "треки сразу можно слушать"
    assert (await c.get("/api/stats?kind=decade")).status == 400

    friend = await (await c.get("/api/stats", headers=as_user(FRIEND_ID))).json()
    assert friend["enabled"] is False, "у каждого своя статистика"

    r = await c.post("/api/stats/sync")
    assert r.status == 429, "только что обновляли"
    for enabled in (False, True, False, True):  # выключить-включить подряд
        await c.put("/api/stats", json={"enabled": enabled})
    assert env.ym.calls.count(("music_history",)) == 1, "повторное включение не дёргает Яндекс чаще раза в минуту"
    r = await c.put("/api/stats", json={"day": True})
    assert (await r.json())["prefs"]["day"] is True

    # Плеер «Медиатеки» сам сообщает о прослушиваниях: Яндекс о них не знает.
    r = await c.post("/api/stats/played", json={"id": "1", "ms": 100_000})
    assert (await r.json())["counted"] is True
    r = await c.post("/api/stats/played", json={"id": "2", "ms": 100_000})
    assert r.status == 429, "100 секунд за мгновение не прослушать"
    for bad in ({"id": "1;x", "ms": 5000}, {"id": "1", "ms": "5000"}, {"id": "1", "ms": 10}, {}):
        assert (await c.post("/api/stats/played", json=bad)).status == 400, bad
    r = await c.post("/api/stats/played", json={"id": "1", "ms": 100_000}, headers=as_user(FRIEND_ID))
    assert (await r.json())["counted"] is False, "у друга статистика выключена — не считаем"
    data = await (await c.get("/api/stats?kind=week")).json()
    assert data["stats"]["plays"] == 3 and data["stats"]["estimated"] == 2
    assert {x["label"] for x in data["stats"]["sources"]} == {"Моя волна", "Плеер «Медиатеки»"}
    assert data["live"] == {"enabled": False, "connected": False}
    assert all("cover" in a and "cover_uri" not in a for a in data["stats"]["top_albums"])

    r = await c.delete("/api/stats")
    assert (await r.json())["deleted"] == 3
    assert (await (await c.get("/api/stats")).json())["enabled"] is False
