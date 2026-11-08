"""Перенос музыки Яндекс → Spotify: подбор пар, клиент Spotify, фоновый перенос и API вкладки «Перенос»."""

from __future__ import annotations

import asyncio
import dataclasses
import time
from types import SimpleNamespace as NS
from urllib.parse import parse_qs, urlsplit

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from test_web import FakeYM, as_user, env, make_track  # noqa: F401 — env: фикстура приложения

from bot import spotify as spotify_module
from bot.spotify import Spotify, SpotifyAuthError, Tokens
from bot.storage import Storage
from bot.transfer import ACCEPT, YM2SP, SourcePick, Transfers, Wanted, find_on_spotify, score, summary_text
from bot.web import transfer_api
from bot.web.app import CTX

# ---------- подбор пар ----------

W = Wanted


@pytest.mark.parametrize(("wanted", "title", "artists", "ms", "match"), [
    (W("1", "Кукушка", ["Кино"], 400_000), "Kukushka", ["Kino"], 401_000, True),
    (W("1", "Почему", ["Баста"], 200_000), "Pochemu", ["Basta"], 200_500, True),
    (W("1", "Lose Yourself", ["Eminem"], 326_000), "Lose Yourself", ["Eminem"], 320_000, True),
    (W("1", "Song 2 (feat. X)", ["Blur"], 120_000), "Song 2 - 2012 Remaster", ["Blur"], 121_000, True),
    (W("1", "Ganja (Slowed)", ["Ooyy"], 230_000), "Ganja - Slowed", ["Ooyy"], 231_000, True),
    (W("1", "Blinding Lights", ["The Weeknd"], 200_000), "Blinding Lights - Remix", ["The Weeknd"], 220_000, False),
    (W("1", "Ganja", ["Ooyy"], 180_000), "Ganja (Slowed + Reverb)", ["Ooyy"], 230_000, False),
    (W("1", "Believer", ["Imagine Dragons"], 204_000), "Believer", ["Kurt Hugo Schneider"], 204_000, False),
    (W("1", "Кукушка", ["Кино"], 400_000), "Звезда по имени Солнце", ["Кино"], 225_000, False),
])
def test_match_score(wanted, title, artists, ms, match):
    assert (score(wanted, title, artists, ms) >= ACCEPT) is match


def sp_track(uri: str, name: str, artist: str, ms: int = 200_000) -> dict:
    return {"uri": uri, "name": name, "artists": [{"name": artist}], "duration_ms": ms}


class FakeSpotify:
    def __init__(self, catalog: dict[str, list[dict]] | None = None) -> None:
        self.catalog = catalog or {}
        self.searches: list[str] = []
        self.saved: list[str] = []
        self.playlists: list[tuple[str, list[str]]] = []

    async def me(self):
        return {"display_name": "Андрей"}

    async def search_tracks(self, query):
        self.searches.append(query)
        return [t for key, tracks in self.catalog.items() if key.lower() in query.lower() for t in tracks]

    async def save_tracks(self, uris):
        self.saved.extend(uris)

    async def create_playlist(self, name, description=""):
        self.playlists.append((name, []))
        return {"id": f"p{len(self.playlists)}", "external_urls": {"spotify": f"https://open.spotify.com/playlist/p{len(self.playlists)}"}}

    async def add_to_playlist(self, playlist_id, uris):
        self.playlists[int(playlist_id[1:]) - 1][1].extend(uris)


async def test_find_tries_translit_and_picks_best():
    sp = FakeSpotify({"kino kukushka": [sp_track("spotify:track:cover", "Kukushka", "Some Band"),
                                        sp_track("spotify:track:ok", "Kukushka", "Kino", 400_000)]})
    assert await find_on_spotify(sp, W("1", "Кукушка", ["Кино"], 400_000)) == "spotify:track:ok"
    assert len(sp.searches) == 3, "точный запрос, обычный, транслит"
    assert await find_on_spotify(FakeSpotify(), W("1", "Нет такого", ["Никто"], 1)) is None


# ---------- клиент Spotify ----------

@pytest.fixture
async def spotify_api():
    calls: list[tuple] = []
    state = {"limited": 1}
    app = web.Application()

    async def token(request):
        form = await request.post()
        calls.append(("token", dict(form)))
        return web.json_response({"access_token": "A2", "expires_in": 3600, "refresh_token": "R2"})

    async def library(request):
        if state["limited"]:
            state["limited"] -= 1
            return web.Response(status=429, headers={"Retry-After": "0"})
        calls.append(("library", request.headers["Authorization"], request.query["uris"].split(",")))
        return web.Response(status=200)

    async def items(request):
        calls.append(("items", request.match_info["id"], (await request.json())["uris"]))
        return web.json_response({"snapshot_id": "s"}, status=201)

    async def me(request):
        if request.headers["Authorization"] == "Bearer BAD":
            return web.json_response({"error": {"status": 403, "message": "User may not be registered."}}, status=403)
        return web.json_response({"id": "u1", "display_name": "Андрей"})

    app.router.add_post("/api/token", token)
    app.router.add_put("/v1/me/library", library)
    app.router.add_post("/v1/playlists/{id}/items", items)
    app.router.add_get("/v1/me", me)
    server = TestServer(app)
    await server.start_server()
    base = str(server.make_url("")).rstrip("/")
    old = spotify_module.API, spotify_module.TOKEN_URL, spotify_module.MIN_INTERVAL
    spotify_module.API, spotify_module.TOKEN_URL, spotify_module.MIN_INTERVAL = base + "/v1", base + "/api/token", 0
    async with aiohttp.ClientSession() as http:
        yield NS(http=http, calls=calls)
    spotify_module.API, spotify_module.TOKEN_URL, spotify_module.MIN_INTERVAL = old
    await server.close()


async def test_spotify_refreshes_token_retries_429_and_batches(spotify_api):
    saved = []
    sp = Spotify(spotify_api.http, "c" * 32, Tokens("A1", "R1", time.time() - 1), on_tokens=saved.append)
    uris = [f"spotify:track:{i}" for i in range(85)]
    await sp.save_tracks(uris)
    await sp.add_to_playlist("pl", uris + uris + uris)
    token = [c for c in spotify_api.calls if c[0] == "token"]
    assert token[0][1] == {"grant_type": "refresh_token", "refresh_token": "R1", "client_id": "c" * 32}
    assert len(token) == 1 and saved and saved[0].access == "A2" and saved[0].refresh == "R2"
    library = [c for c in spotify_api.calls if c[0] == "library"]
    assert [len(c[2]) for c in library] == [40, 40, 5] and library[0][1] == "Bearer A2", "по 40, 429 переждали"
    assert [len(c[2]) for c in spotify_api.calls if c[0] == "items"] == [100, 100, 55]


async def test_spotify_explains_unregistered_user(spotify_api):
    sp = Spotify(spotify_api.http, "c" * 32, Tokens("BAD", "R", time.time() + 999))
    with pytest.raises(SpotifyAuthError, match="User Management"):
        await sp.me()


# ---------- фоновый перенос ----------

def ym_with_library() -> FakeYM:
    ym = FakeYM()
    ym.tracks["3"] = make_track(3, "Нет в Spotify")
    ym.likes = ["2:10", "1:10", "3:10"]  # новые лайки — первыми
    return ym


async def wait_job(transfers: Transfers, user_id: int):
    job = transfers.jobs[user_id]
    await asyncio.wait_for(job.task, 5)
    return job


async def test_transfer_likes_and_playlist(tmp_path):
    store = Storage(tmp_path / "bot.db")
    sent: list[tuple[int, str]] = []

    async def notify(user_id, text):
        sent.append((user_id, text))

    transfers = Transfers(store, notify)
    sp = FakeSpotify({"Кукушка": [sp_track("spotify:track:k", "Kukushka", "Kino", 200_000)],
                      "Группа крови": [sp_track("spotify:track:g", "Gruppa krovi", "Kino", 200_000)]})
    transfers.start(1, ym_with_library(), sp, [SourcePick("likes"), SourcePick("pl", "42.1003")])
    job = await wait_job(transfers, 1)
    assert job.state["status"] == "done"
    assert sp.saved == ["spotify:track:k", "spotify:track:g"], "в Spotify — от старых лайков к новым"
    assert sp.playlists == [("Мои записи", ["spotify:track:k", "spotify:track:g"])]
    likes, playlist = job.state["sources"]
    assert (likes["total"], likes["done"], likes["found"]) == (3, 3, 2)
    assert likes["missed"] == [{"title": "Нет в Spotify", "artists": "Кино"}]
    assert playlist["url"] == "https://open.spotify.com/playlist/p1" and playlist["already"] == 2, "пары запомнены"
    assert store.last_transfer_job(1)[2] == "done"
    [(user, text)] = sent
    assert user == 1 and "перенесено 2 из 3, не нашлось 1" in text

    searches = len(sp.searches)
    transfers.start(1, ym_with_library(), sp, [SourcePick("likes")])
    await wait_job(transfers, 1)
    assert len(sp.searches) == searches, "второй раз — без поиска: найденное и «не нашли» в кэше"
    store.set_match(YM2SP, "3", None)
    store._db.execute("UPDATE transfer_matches SET at = 0 WHERE src_id = '3'")
    transfers.start(1, ym_with_library(), sp, [SourcePick("likes")])
    await wait_job(transfers, 1)
    assert len(sp.searches) > searches, "«не нашли» неделю спустя ищем снова"
    store.close()


async def test_transfer_cancel_and_restart_marks(tmp_path):
    store = Storage(tmp_path / "bot.db")
    transfers = Transfers(store)
    release = asyncio.Event()

    class SlowSpotify(FakeSpotify):
        async def search_tracks(self, query):
            await release.wait()
            return []

    transfers.start(1, ym_with_library(), SlowSpotify(), [SourcePick("likes")])
    await asyncio.sleep(0.05)
    with pytest.raises(RuntimeError):
        transfers.start(1, ym_with_library(), FakeSpotify(), [SourcePick("likes")])
    assert transfers.cancel(1)
    release.set()
    job = await wait_job(transfers, 1)
    assert job.state["status"] == "cancelled" and "остановлен" in summary_text(job.state)

    transfers.start(2, ym_with_library(), SlowSpotify(), [SourcePick("likes")])
    release.clear()
    await asyncio.sleep(0.05)
    assert Transfers(store).store.last_transfer_job(2)[2] == "interrupted", "после перезапуска — прерван"
    transfers.jobs[2].task.cancel()
    store.close()


# ---------- API вкладки ----------

@pytest.fixture
async def tenv(env):  # noqa: F811 — фикстура из test_web
    app = env.client.server.app
    app[CTX].config = dataclasses.replace(app[CTX].config, webapp_url="https://music.example")
    app[transfer_api.TRANSFERS].notify = None  # итог в Telegram тут не отправляем
    return env


async def test_transfer_api_connect_flow(tenv, monkeypatch):
    c = tenv.client
    info = await (await c.get("/api/transfer")).json()
    assert info == {"redirect_uri": "https://music.example/spotify/callback", "spotify": None, "job": None}
    assert (await c.put("/api/transfer/spotify", json={"client_id": "секрет"})).status == 400
    assert (await c.post("/api/transfer/spotify/login")).status == 400, "сначала Client ID"
    client_id = "0123456789abcdef0123456789ABCDEF"
    assert (await c.put("/api/transfer/spotify", json={"client_id": client_id})).status == 200
    url = (await (await c.post("/api/transfer/spotify/login")).json())["url"]
    q = parse_qs(urlsplit(url).query)
    assert q["client_id"] == [client_id.lower()] and q["code_challenge_method"] == ["S256"]
    assert q["redirect_uri"] == ["https://music.example/spotify/callback"]
    assert "playlist-modify-private" in q["scope"][0]
    assert (await c.post("/api/transfer/start", json={"sources": [{"src": "likes"}]})).status == 400, "без входа"

    async def exchange(http, cid, redirect, code, verifier):
        assert (cid, redirect, code) == (client_id.lower(), "https://music.example/spotify/callback", "CODE")
        return Tokens("A", "R", time.time() + 3600)

    async def me(self):
        return {"id": "u1", "display_name": "Андрей"}

    monkeypatch.setattr(transfer_api, "exchange_code", exchange)
    monkeypatch.setattr(Spotify, "me", me)
    c.session.headers.pop("X-Telegram-Init-Data")  # Spotify возвращает в обычный браузер, без Telegram
    bad = await c.get("/spotify/callback", params={"state": "чужой", "code": "CODE"})
    assert bad.status == 200 and "Ссылка устарела" in await bad.text()
    page = await c.get("/spotify/callback", params={"state": q["state"][0], "code": "CODE"})
    assert "Spotify подключён" in await page.text() and "Андрей" in await page.text()
    again = await c.get("/spotify/callback", params={"state": q["state"][0], "code": "CODE"})
    assert "Ссылка устарела" in await again.text(), "state одноразовый"

    stored = tenv.store.get_spotify(1)
    assert stored[1]["access"] == "A" and stored[2] == "Андрей"
    raw = tenv.store._one("SELECT tokens FROM spotify_accounts WHERE user_id = 1")[0]
    assert "access" not in raw and "refresh" not in raw, "токены зашифрованы"
    friend = await (await c.get("/api/transfer", headers=as_user(2))).json()
    assert friend["spotify"] is None, "у каждого свой Spotify"


async def test_transfer_api_start_and_progress(tenv, monkeypatch):
    c = tenv.client
    await c.put("/api/transfer/spotify", json={"client_id": "a" * 32})
    tenv.store.set_spotify_tokens(1, Tokens("A", "R", time.time() + 3600).to_dict(), "Андрей")
    fake = FakeSpotify({"Кукушка": [sp_track("spotify:track:k", "Kukushka", "Kino", 200_000)]})
    monkeypatch.setattr(transfer_api, "spotify_client", lambda app, user_id: fake)
    sources = await (await c.get("/api/transfer/sources")).json()
    assert sources["likes"] == 2 and sources["playlists"][0]["ref"] == "42.1003"
    assert (await c.post("/api/transfer/start", json={"sources": [{"src": "evil"}]})).status == 400
    resp = await c.post("/api/transfer/start", json={"sources": [{"src": "likes"}, {"src": "pl", "ref": "42.1003"}]})
    assert resp.status == 200 and (await resp.json())["job"]["status"] == "running"
    await asyncio.wait_for(c.server.app[transfer_api.TRANSFERS].jobs[1].task, 5)
    job = (await (await c.get("/api/transfer")).json())["job"]
    assert job["status"] == "done" and [s["found"] for s in job["sources"]] == [1, 1]
    assert fake.saved == ["spotify:track:k"] and tenv.sender is not None
