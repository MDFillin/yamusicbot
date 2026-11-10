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
        self.tracks = {t["uri"]: t for ts in self.catalog.values() for t in ts}
        self.searches: list[str] = []
        self.saved: list[str] = []  # «Любимые» по порядку добавления
        self.pls: list[tuple[str, list[str]]] = []
        self.inserts: list[tuple[str, int | None, list[str]]] = []

    def add_known(self, *tracks):
        for t in tracks:
            self.tracks[t["uri"]] = t

    async def me(self):
        return {"id": "me", "display_name": "Андрей"}

    async def liked_tracks(self, on_page=None):
        result = [self.tracks[u] for u in reversed(self.saved)]
        if on_page:
            on_page(len(result), len(result))
        return result

    async def playlists(self):
        return [{"id": f"p{i + 1}", "name": name, "owner": {"id": "me"}} for i, (name, _) in enumerate(self.pls)]

    async def playlist_items(self, playlist_id):
        return [self.tracks[u] for u in self.pls[int(playlist_id[1:]) - 1][1]]

    async def search_tracks(self, query):
        self.searches.append(query)
        return [t for key, tracks in self.catalog.items() if key.lower() in query.lower() for t in tracks]

    async def save_tracks(self, uris, on_saved=None):
        for uri in uris:
            self.saved.append(uri)
            if on_saved and on_saved(uri) is False:
                return


    async def create_playlist(self, name, description=""):
        self.pls.append((name, []))
        return {"id": f"p{len(self.pls)}"}

    async def add_to_playlist(self, playlist_id, uris, position=None):
        self.inserts.append((playlist_id, position, list(uris)))
        items = self.pls[int(playlist_id[1:]) - 1][1]
        items[len(items) if position is None else position:len(items) if position is None else position] = uris


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
    state = {"limited": 1, "retry_after": "0"}
    app = web.Application()

    async def token(request):
        form = await request.post()
        calls.append(("token", dict(form)))
        return web.json_response({"access_token": "A2", "expires_in": 3600, "refresh_token": "R2"})

    async def library(request):
        if state["limited"]:
            state["limited"] -= 1
            return web.Response(status=429, headers={"Retry-After": state["retry_after"]})
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
    old = spotify_module.API, spotify_module.TOKEN_URL, spotify_module.MIN_INTERVAL, spotify_module.LIKE_GAP
    spotify_module.API, spotify_module.TOKEN_URL = base + "/v1", base + "/api/token"
    spotify_module.MIN_INTERVAL, spotify_module.LIKE_GAP = 0, 0
    async with aiohttp.ClientSession() as http:
        yield NS(http=http, calls=calls, state=state)
    spotify_module.API, spotify_module.TOKEN_URL, spotify_module.MIN_INTERVAL, spotify_module.LIKE_GAP = old
    await server.close()


async def test_spotify_refreshes_token_retries_429_and_batches(spotify_api):
    saved = []
    sp = Spotify(spotify_api.http, "c" * 32, Tokens("A1", "R1", time.time() - 1), on_tokens=saved.append)
    uris = [f"spotify:track:{i}" for i in range(85)]
    await sp.save_tracks(uris[:3])
    await sp.add_to_playlist("pl", uris + uris + uris)
    token = [c for c in spotify_api.calls if c[0] == "token"]
    assert token[0][1] == {"grant_type": "refresh_token", "refresh_token": "R1", "client_id": "c" * 32}
    assert len(token) == 1 and saved and saved[0].access == "A2" and saved[0].refresh == "R2"
    library = [c for c in spotify_api.calls if c[0] == "library"]
    assert [c[2] for c in library] == [[u] for u in uris[:3]], "лайки — по одному, по порядку, 429 переждали"
    assert library[0][1] == "Bearer A2"
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
    assert sp.pls == [("Мои записи", ["spotify:track:k", "spotify:track:g"])]
    likes, playlist = job.state["sources"]
    assert (likes["total"], likes["done"], likes["found"]) == (3, 3, 2)
    assert likes["missed"] == [{"title": "Нет в Spotify", "artists": "Кино", "id": "3", "ugc": False, "pos": 3}]
    assert (likes["to_save"], likes["saved"], likes["phase"]) == (2, 2, "done")
    assert playlist["url"] == "https://open.spotify.com/playlist/p1" and playlist["already"] == 2, "пары запомнены"
    assert store.last_transfer_job(1)[2] == "done"
    [(user, text)] = sent
    assert user == 1 and "Мне нравится: в Spotify 2 из 3, не нашлось 1" in text

    searches = len(sp.searches)
    transfers.start(1, ym_with_library(), sp, [SourcePick("likes"), SourcePick("pl", "42.1003")])
    job = await wait_job(transfers, 1)
    assert len(sp.searches) == searches, "второй раз — без поиска: найденное и «не нашли» в кэше"
    assert sp.saved == ["spotify:track:k", "spotify:track:g"], "уже сохранённые лайки не трогаем — порядок цел"
    likes, playlist = job.state["sources"]
    assert (likes["to_save"], likes["in_spotify"], playlist["to_save"], playlist["in_spotify"]) == (0, 2, 0, 2)
    assert len(sp.pls) == 1, "тот же плейлист, а не второй"
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

    transfers.start(2, ym_with_library(), SlowSpotify(), [SourcePick("likes"), SourcePick("pl", "42.1003")])
    release.clear()
    await asyncio.sleep(0.05)
    restarted = Transfers(store)  # бот перезапустился (например, ночная копия)
    assert store.last_transfer_job(2)[2] == "interrupted"
    transfers.jobs[2].task.cancel()
    fresh = FakeSpotify()

    async def connect(user_id):
        return (ym_with_library(), fresh) if user_id == 2 else None

    await restarted.resume_interrupted(connect)
    job = await wait_job(restarted, 2)
    assert job.state["status"] == "done" and [s["src"] for s in job.state["sources"]] == ["likes", "pl"], \
        "продолжен сам, с теми же списками"
    assert restarted.interrupted == {}
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


UGC = "596c86f1-a7c4-42fa-ac0b-bbe0cf95255c"


def ym_with_files() -> FakeYM:
    """Плейлист вперемешку: каталог и загруженные файлы с «грязными» тегами."""
    ym = FakeYM()
    ym.tracks[UGC] = NS(id=UGC, title="BITZ - Письмо", version=None, duration_ms=111_290, available=True,
                        artists=[NS(id=None, name="RioSsa93", cover=None)], albums=[], cover_uri=None)
    ym.tracks["u2"] = NS(id="u2", title="Демо с репетиции", version=None, duration_ms=95_000, available=True,
                         artists=[NS(id=None, name="Я", cover=None)], albums=[], cover_uri=None)
    ym.playlist.tracks = [NS(id=UGC, track=None, track_id=UGC), NS(id=1, track=None, track_id="1:10"),
                          NS(id="u2", track=None, track_id="u2")]
    return ym


async def test_uploaded_files_are_searched_and_kept_in_order(tmp_path):
    store = Storage(tmp_path / "bot.db")
    transfers = Transfers(store)
    sp = FakeSpotify({"письмо": [sp_track("spotify:track:bitz", "Письмо", "BITZ", 112_000)],
                      "Кукушка": [sp_track("spotify:track:k", "Kukushka", "Kino", 200_000)]})
    transfers.start(1, ym_with_files(), sp, [SourcePick("pl", "42.1003")])
    job = await wait_job(transfers, 1)
    st = job.state["sources"][0]
    assert sp.pls == [("Мои записи", ["spotify:track:bitz", "spotify:track:k"])], "порядок как в Яндексе"
    assert st["files_found"] == 1
    assert st["missed"] == [{"title": "Демо с репетиции", "artists": "Я", "id": "u2", "ugc": True, "pos": 3}]
    assert "загруженных файлов нет в Spotify: 1" in summary_text(job.state)
    store.close()


async def test_send_missing_files_to_chat(tenv, monkeypatch):
    calls = []
    monkeypatch.setattr(transfer_api, "start_bulk_download", lambda *a, **kw: calls.append(kw) or True)
    assert (await tenv.client.post("/api/transfer/files")).status == 404
    state = {"sources": [
        {"missed": [{"id": "b", "ugc": True, "pos": 9}, {"id": "a", "ugc": True, "pos": 2}, {"id": "5", "ugc": False}]},
        {"missed": [{"id": "a", "ugc": True, "pos": 1}, {"id": "c", "ugc": True, "pos": 4}]},
    ]}
    job_id = tenv.store.add_transfer_job(1, YM2SP, state)
    tenv.store.update_transfer_job(job_id, "done", state)
    resp = await tenv.client.post("/api/transfer/files")
    assert (await resp.json()) == {"started": True, "count": 3}
    assert calls[0]["items"] == ["a", "b", "c"], "по порядку списков, без каталожных и без повторов"


async def test_retransfer_adds_only_new_and_merges_existing(tmp_path):
    """Год назад перенесли другим сервисом: в «Любимых» уже есть Кукушка (другой версией — другой ID),
    в Spotify уже есть плейлист «Мои записи» с одним треком и с треком, добавленным вручную."""
    store = Storage(tmp_path / "bot.db")
    transfers = Transfers(store)
    old_k = sp_track("spotify:track:k_from_compilation", "Kukushka - Remastered", "KINO", 201_000)
    manual = sp_track("spotify:track:manual", "Своё", "Кто-то", 100_000)
    sp = FakeSpotify({"Кукушка": [sp_track("spotify:track:k", "Kukushka", "Kino", 200_000)],
                      "Группа крови": [sp_track("spotify:track:g", "Gruppa krovi", "Kino", 200_000)]})
    sp.add_known(old_k, manual)
    sp.saved = [old_k["uri"]]
    sp.pls = [("мои записи", [manual["uri"], "spotify:track:g"])]
    ym = ym_with_library()
    ym.playlist.tracks.insert(0, NS(id=3, track=None, track_id="3:10"))  # Нет в Spotify, Кукушка, Группа крови

    transfers.start(1, ym, sp, [SourcePick("likes"), SourcePick("pl", "42.1003")])
    job = await wait_job(transfers, 1)
    likes, playlist = job.state["sources"]
    assert sp.saved == [old_k["uri"], "spotify:track:g"], "Кукушку второй раз не добавили — только новое"
    assert (likes["in_spotify"], likes["saved"]) == (1, 1)
    assert len(sp.pls) == 1, "плейлист с тем же названием дополнен, а не создан второй"
    assert sp.pls[0][1] == [manual["uri"], "spotify:track:k", "spotify:track:g"], \
        "Кукушка вставлена перед Группой крови, ручное — на месте"
    assert (playlist["in_spotify"], playlist["saved"]) == (1, 1)
    assert "1 уже были, добавлено 1" in summary_text(job.state)
    store.close()


@pytest.mark.parametrize(("current", "desired", "result"), [
    ("", "ABC", "ABC"),
    ("AC", "ABCD", "ABCD"),
    ("ABC", "XABC", "XABC"),  # новое сверху — сверху
    ("AXC", "ABC", "AXBC"),  # X добавлен вручную — остаётся, B встаёт перед C
    ("ABC", "ABC", "ABC"),
    ("CBA", "ABCD", "CBAD"),  # переставлено вручную — не трогаем, новое в конец
    ("AB", "AAB", "AB"),  # повтор в Яндексе — один раз
])
def test_merge_plan(current, desired, result):
    from bot.transfer import merge_plan
    items = list(current)
    for pos, uris in merge_plan(list(current), list(desired)):
        items[pos:pos] = uris
    assert "".join(items) == result


async def test_spotify_waits_out_long_ban_and_slows_down(spotify_api, monkeypatch):
    slept, pauses = [], []

    async def fake_sleep(sec):
        slept.append(sec)

    monkeypatch.setattr(spotify_module, "_sleep", fake_sleep)
    spotify_api.state.update(limited=1, retry_after="85000")  # как у пользователя: почти сутки
    sp = Spotify(spotify_api.http, "c" * 32, Tokens("A1", "R1", time.time() + 999))
    sp.on_pause = pauses.append
    await sp.save_tracks(["spotify:track:1"])
    assert slept == [85001], "пережидаем сами, а не падаем"
    assert len(pauses) == 2 and pauses[0] > time.time() + 84000 and pauses[1] is None, "перенос знает, до когда пауза"
    assert sp.interval > 0, "после 429 — медленнее"
    assert [c for c in spotify_api.calls if c[0] == "library"], "после паузы запрос повторён"


async def test_cancel_during_spotify_pause(tmp_path):
    store = Storage(tmp_path / "bot.db")
    transfers = Transfers(store)
    forever = asyncio.Event()

    class BannedSpotify(FakeSpotify):
        async def search_tracks(self, query):
            self.on_pause(time.time() + 85000)
            await forever.wait()

    transfers.start(1, ym_with_library(), BannedSpotify(), [SourcePick("likes")])
    await asyncio.sleep(0.05)
    assert transfers.jobs[1].state["paused_until"] > time.time()
    assert transfers.cancel(1)
    job = await wait_job(transfers, 1)
    assert job.state["status"] == "cancelled" and store.last_transfer_job(1)[2] == "cancelled"
    store.close()


async def test_liked_library_shows_progress_and_stops_at_once(spotify_api, tmp_path):
    pages = [{"items": [{"track": sp_track(f"spotify:track:{p}{i}", "x", "y")} for i in range(50)], "total": 120,
              "next": f"/me/tracks?p={p + 1}" if p < 2 else None} for p in range(3)]
    seen = []

    class PagedSpotify(Spotify):
        async def request(self, method, path, **kw):
            return pages[0] if kw.get("params") else pages[int(path.rsplit("=", 1)[1])]

    sp = PagedSpotify(spotify_api.http, "c" * 32, Tokens("A", "R", time.time() + 999))
    tracks = await sp.liked_tracks(lambda loaded, total: seen.append((loaded, total)))
    assert len(tracks) == 150 and seen == [(50, 120), (100, 120), (150, 120)]
    assert len(await sp.liked_tracks(lambda loaded, total: False)) == 50, "остановили — дальше не грузим"
