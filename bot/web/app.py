"""HTTP API и статика мини-приложения (Telegram Mini App)."""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

import aiohttp
from aiogram import Bot
from aiogram.utils.web_app import WebAppUser
from aiohttp import web
from yandex_music import Album, Artist, Playlist, Track
from yandex_music.exceptions import YandexMusicError

from bot.audio import ConversionError, ffmpeg_available, prepare_for_upload
from bot.config import Config
from bot.handlers.download import start_bulk_download
from bot.sender import TrackSender, TrackTooLargeError
from bot.sources import SourceNotFoundError, TrackSource, load_source, playlist_cover, playlist_ref, resolve
from bot.storage import Storage
from bot.web.auth import AuthError, MediaSigner, user_from_init_data
from bot.ym import (
    TrackUnavailableError,
    UploadError,
    YandexMusic,
    YandexNotReady,
    cover_url,
    tagged_filename,
    track_album,
    track_artists,
    track_title,
)

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
SOURCE_TTL = 120  # сек: страницы одного списка («показать ещё») берём из кэша
LINK_TTL = 600  # сек: прямые ссылки Яндекса на MP3 живут недолго
PAGE_LIMIT = 100
SOURCES = {"likes", "pl", "alb", "art"}


@dataclass
class WebContext:
    config: Config
    bot: Bot
    ym: YandexMusic
    store: Storage
    sender: TrackSender
    signer: MediaSigner
    http: aiohttp.ClientSession | None = None
    sources: dict[tuple[str, str], tuple[float, TrackSource]] = field(default_factory=dict)
    links: dict[str, tuple[float, str]] = field(default_factory=dict)

    async def source(self, src: str, ref: str, fresh: bool) -> TrackSource:
        key = (src, ref)
        cached = self.sources.get(key)
        if cached and not fresh and time.monotonic() - cached[0] < SOURCE_TTL:
            return cached[1]
        source = await load_source(self.ym, src, ref)
        self.sources[key] = (time.monotonic(), source)
        return source

    def invalidate(self) -> None:
        self.sources.clear()

    async def track(self, track_id: str) -> Track:
        track = await self.ym.get_track(track_id)
        if track is None:
            raise web.HTTPNotFound(text="Трек не найден")
        return track

    async def direct_link(self, track_id: str, fresh: bool = False) -> str:
        cached = self.links.get(track_id)
        if cached and not fresh and time.monotonic() - cached[0] < LINK_TTL:
            return cached[1]
        url = await self.ym.direct_link(await self.track(track_id))
        self.links[track_id] = (time.monotonic(), url)
        return url


CTX = web.AppKey("ctx", WebContext)
USER = web.RequestKey("user", WebAppUser)


# ---------- сериализация ----------

def track_json(t: Track, signer: MediaSigner, index: int | None = None) -> dict[str, Any]:
    """Трек для интерфейса; ссылки подписаны заранее, чтобы play() вызывался прямо в обработчике нажатия (iOS)."""
    track_id = str(t.id)
    album = track_album(t)
    artist = t.artists[0] if t.artists else None
    data = {
        "id": track_id,
        "title": track_title(t),
        "artists": track_artists(t),
        "artist_id": str(artist.id) if artist and artist.id else None,
        "album_id": str(album.id) if album and album.id else None,
        "album": album.title if album else None,
        "duration": (t.duration_ms or 0) // 1000,
        "cover": cover_url(t.cover_uri),
        "available": t.available is not False,
        "stream": f"/media/stream/{track_id}?{signer.sign('stream', track_id)}",
        "download": f"/media/download/{track_id}?{signer.sign('download', track_id)}",
        "filename": tagged_filename(t),
    }
    if index is not None:
        data["index"] = index
    return data


def playlist_json(p: Playlist) -> dict[str, Any]:
    return {
        "ref": playlist_ref(p),
        "kind": p.kind,
        "title": p.title,
        "count": p.track_count or 0,
        "cover": cover_url(playlist_cover(p)),
        "owner": (p.owner.name or p.owner.login) if p.owner else None,
    }


def album_json(a: Album) -> dict[str, Any]:
    return {
        "id": str(a.id),
        "title": a.title,
        "artists": ", ".join(x.name for x in a.artists or [] if x.name),
        "year": a.year,
        "cover": cover_url(a.cover_uri),
    }


def artist_json(a: Artist) -> dict[str, Any]:
    return {"id": str(a.id), "name": a.name, "cover": cover_url(a.cover.uri if a.cover else None)}


# ---------- middleware ----------

@web.middleware
async def errors_middleware(request: web.Request, handler):
    try:
        return await handler(request)
    except AuthError as e:
        return web.json_response({"error": str(e)}, status=e.status)
    except YandexNotReady as e:
        return web.json_response({"error": f"Бот не может подключиться к Яндекс Музыке. {e}"}, status=503)
    except web.HTTPException as e:
        if e.status >= 400 and request.path.startswith("/api/"):
            return web.json_response({"error": e.text or e.reason}, status=e.status)
        raise
    except (SourceNotFoundError, TrackUnavailableError) as e:
        return web.json_response({"error": str(e)}, status=404)
    except (UploadError, ConversionError, TrackTooLargeError) as e:
        return web.json_response({"error": str(e)}, status=422)
    except ValueError as e:
        log.info("Некорректный запрос %s: %s", request.path, e)
        return web.json_response({"error": "Некорректный запрос"}, status=400)
    except YandexMusicError as e:
        log.warning("Ошибка Яндекс Музыки на %s: %r", request.path, e)
        return web.json_response({"error": f"Яндекс Музыка: {e}"}, status=502)
    except Exception as e:
        log.exception("Ошибка на %s", request.path)
        return web.json_response({"error": f"{type(e).__name__}: {e}"}, status=500)


@web.middleware
async def auth_middleware(request: web.Request, handler):
    ctx = request.app[CTX]
    if request.path.startswith("/api/"):
        request[USER] = user_from_init_data(
            ctx.config.bot_token, request.headers.get("X-Telegram-Init-Data", ""), ctx.config.allowed_users,
        )
    if request.path.startswith(("/api/", "/media/")):
        await ctx.ym.ensure_started()
    return await handler(request)


# ---------- API ----------

routes = web.RouteTableDef()


def _ctx(request: web.Request) -> WebContext:
    return request.app[CTX]


def _user_id(request: web.Request) -> int:
    return request[USER].id


async def _json_body(request: web.Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except ValueError as e:
        raise web.HTTPBadRequest(text="Ожидался JSON") from e
    if not isinstance(body, dict):
        raise web.HTTPBadRequest(text="Ожидался JSON-объект")
    return body


def _title(body: dict[str, Any]) -> str:
    title = str(body.get("title") or "").strip()[:100]
    if not title:
        raise web.HTTPBadRequest(text="Название не может быть пустым")
    return title


@routes.get("/api/me")
async def api_me(request: web.Request) -> web.Response:
    ctx = _ctx(request)
    return web.json_response({
        "login": ctx.ym.login,
        "has_plus": ctx.ym.has_plus,
        "upload_target": ctx.store.get_upload_target(_user_id(request)),
        "can_convert": ffmpeg_available(),
        "max_upload_mb": ctx.config.web_max_upload_mb,
    })


@routes.get("/api/library")
async def api_library(request: web.Request) -> web.Response:
    ym = _ctx(request).ym
    playlists = await ym.get_my_playlists()
    liked = await ym.get_liked_track_ids()
    return web.json_response({
        "liked_ids": [i.split(":")[0] for i in liked],
        "playlists": [playlist_json(p) for p in playlists],
    })


@routes.get("/api/source/{src}")
async def api_source(request: web.Request) -> web.Response:
    ctx = _ctx(request)
    src = request.match_info["src"]
    if src not in SOURCES:
        raise web.HTTPNotFound(text="Неизвестный список")
    ref = request.query.get("ref", "")
    offset = max(int(request.query.get("offset", 0)), 0)
    limit = min(max(int(request.query.get("limit", 50)), 1), PAGE_LIMIT)
    source = await ctx.source(src, ref, fresh=offset == 0)
    tracks = await resolve(ctx.ym, source.items[offset:offset + limit])
    return web.json_response({
        "src": src,
        "ref": source.ref,
        "title": source.name or source.title,
        "subtitle": source.subtitle,
        "cover": cover_url(source.cover, "400x400"),
        "total": len(source.items),
        "own_kind": source.own_playlist.kind if source.own_playlist is not None else None,
        "offset": offset,
        "tracks": [track_json(t, ctx.signer, offset + i) for i, t in enumerate(tracks)],
    })


@routes.get("/api/search")
async def api_search(request: web.Request) -> web.Response:
    query = request.query.get("q", "").strip()[:200]
    type_ = request.query.get("type", "track")
    if type_ not in {"track", "album", "artist", "playlist"}:
        raise web.HTTPBadRequest(text="Неизвестный тип поиска")
    if not query:
        return web.json_response({"type": type_, "items": []})
    ctx = _ctx(request)
    result = await ctx.ym.search(query, type_)
    items: list[dict[str, Any]] = []
    if result:
        if type_ == "track" and result.tracks:
            items = [track_json(t, ctx.signer) for t in result.tracks.results]
        elif type_ == "album" and result.albums:
            items = [album_json(a) for a in result.albums.results]
        elif type_ == "artist" and result.artists:
            items = [artist_json(a) for a in result.artists.results]
        elif type_ == "playlist" and result.playlists:
            items = [playlist_json(p) for p in result.playlists.results]
    corrected = result.misspell_result if result and result.misspell_corrected else None
    return web.json_response({"type": type_, "items": items, "corrected": corrected})


@routes.post(r"/api/likes/{track_id:[\w.\-]+}")
async def api_like(request: web.Request) -> web.Response:
    ctx = _ctx(request)
    await ctx.ym.like(request.match_info["track_id"])
    ctx.sources.pop(("likes", ""), None)
    return web.json_response({"liked": True})


@routes.delete(r"/api/likes/{track_id:[\w.\-]+}")
async def api_unlike(request: web.Request) -> web.Response:
    ctx = _ctx(request)
    await ctx.ym.unlike(request.match_info["track_id"])
    ctx.sources.pop(("likes", ""), None)
    return web.json_response({"liked": False})


@routes.post("/api/playlists")
async def api_create_playlist(request: web.Request) -> web.Response:
    playlist = await _ctx(request).ym.create_playlist(_title(await _json_body(request)))
    return web.json_response(playlist_json(playlist))


@routes.patch(r"/api/playlists/{kind:\d+}")
async def api_rename_playlist(request: web.Request) -> web.Response:
    ctx = _ctx(request)
    playlist = await ctx.ym.rename_playlist(int(request.match_info["kind"]), _title(await _json_body(request)))
    ctx.invalidate()
    return web.json_response(playlist_json(playlist) if playlist else {"ok": True})


@routes.delete(r"/api/playlists/{kind:\d+}")
async def api_delete_playlist(request: web.Request) -> web.Response:
    ctx = _ctx(request)
    kind = int(request.match_info["kind"])
    await ctx.ym.delete_playlist(kind)
    if ctx.store.get_upload_target(_user_id(request)) == kind:
        ctx.store.set_upload_target(_user_id(request), None)
    ctx.invalidate()
    return web.json_response({"ok": True})


@routes.post(r"/api/playlists/{kind:\d+}/tracks")
async def api_add_track(request: web.Request) -> web.Response:
    ctx = _ctx(request)
    body = await _json_body(request)
    track = await ctx.track(str(body.get("track_id", "")))
    playlist = await ctx.ym.add_to_playlist(int(request.match_info["kind"]), track)
    ctx.invalidate()
    return web.json_response({"ok": True, "title": playlist.title if playlist else None})


@routes.delete(r"/api/playlists/{kind:\d+}/tracks/{index:\d+}")
async def api_remove_track(request: web.Request) -> web.Response:
    ctx = _ctx(request)
    track_id = request.query.get("track_id", "")
    await ctx.ym.remove_from_playlist(int(request.match_info["kind"]), int(request.match_info["index"]), track_id)
    ctx.invalidate()
    return web.json_response({"ok": True})


@routes.put("/api/target")
async def api_set_target(request: web.Request) -> web.Response:
    ctx = _ctx(request)
    kind = (await _json_body(request)).get("kind")
    if kind is not None:
        kind = int(kind)
        if await ctx.ym.get_playlist(kind) is None:
            raise web.HTTPNotFound(text="Плейлист не найден")
    ctx.store.set_upload_target(_user_id(request), kind)
    return web.json_response({"upload_target": kind})


@routes.post(r"/api/tracks/{track_id:[\w.\-]+}/send")
async def api_send_track(request: web.Request) -> web.Response:
    ctx = _ctx(request)
    track = await ctx.track(request.match_info["track_id"])
    await ctx.sender.send(_user_id(request), track)
    return web.json_response({"ok": True})


@routes.post("/api/source/{src}/send")
async def api_send_source(request: web.Request) -> web.Response:
    ctx = _ctx(request)
    src = request.match_info["src"]
    if src not in SOURCES:
        raise web.HTTPNotFound(text="Неизвестный список")
    user_id = _user_id(request)
    started = start_bulk_download(ctx.bot, user_id, user_id, src, request.query.get("ref", ""), ctx.ym, ctx.sender)
    return web.json_response({"started": started})


@routes.post("/api/upload")
async def api_upload(request: web.Request) -> web.Response:
    """Файл с телефона прямо на сервер: лимит Telegram в 20 МБ здесь не действует."""
    ctx = _ctx(request)
    reader = await request.multipart()
    fields: dict[str, str] = {}
    file_name, data = None, b""
    while (part := await reader.next()) is not None:
        if part.name == "file":
            file_name = part.filename or "track.mp3"
            data = bytes(await part.read())
        elif part.name:
            fields[part.name] = (await part.text()).strip()
    if not file_name or not data:
        raise web.HTTPBadRequest(text="Файл не передан")
    if not fields.get("kind", "").isdigit():
        raise web.HTTPBadRequest(text="Не выбран плейлист")
    name, prepared, notes = await prepare_for_upload(
        file_name, data,
        artist=fields.get("artist") or None, title=fields.get("title") or None,
        fallback_artist=fields.get("fallback_artist") or None, fallback_title=fields.get("fallback_title") or None,
    )
    result = await ctx.ym.upload_track(int(fields["kind"]), name, prepared)
    ctx.invalidate()
    return web.json_response({"name": name, "notes": notes, "ugc_track_id": result.ugc_track_id})


# ---------- аудио по подписанным ссылкам ----------

def _check_media(request: web.Request, kind: str) -> str:
    track_id = request.match_info["track_id"]
    if not _ctx(request).signer.verify(kind, track_id, request.query.get("exp"), request.query.get("sig")):
        raise web.HTTPForbidden(text="Ссылка недействительна или устарела")
    return track_id


@routes.get(r"/media/stream/{track_id:[\w.\-]+}")
async def media_stream(request: web.Request) -> web.StreamResponse:
    """Проксирует MP3 из хранилища Яндекса с поддержкой Range — чтобы работала перемотка."""
    ctx = _ctx(request)
    track_id = _check_media(request, "stream")
    headers = {"Range": request.headers["Range"]} if "Range" in request.headers else {}

    upstream = None
    for attempt in range(2):
        url = await ctx.direct_link(track_id, fresh=attempt > 0)
        upstream = await ctx.http.get(url, headers=headers)
        if upstream.status < 400 or attempt == 1:
            break
        upstream.release()  # ссылка протухла — возьмём свежую

    try:
        if upstream.status >= 400:
            raise web.HTTPBadGateway(text=f"Хранилище Яндекса ответило {upstream.status}")
        resp = web.StreamResponse(status=upstream.status)
        for name in ("Content-Length", "Content-Range"):
            if name in upstream.headers:
                resp.headers[name] = upstream.headers[name]
        resp.content_type = "audio/mpeg"
        resp.headers["Accept-Ranges"] = "bytes"
        resp.headers["Cache-Control"] = "private, max-age=3600"
        await resp.prepare(request)
        try:
            async for chunk in upstream.content.iter_chunked(64 * 1024):
                await resp.write(chunk)
            await resp.write_eof()
        except (ConnectionResetError, aiohttp.ClientConnectionError):
            pass  # плеер оборвал запрос при перемотке — это нормально
        return resp
    finally:
        upstream.release()


@routes.get(r"/media/download/{track_id:[\w.\-]+}")
async def media_download(request: web.Request) -> web.Response:
    ctx = _ctx(request)
    track = await ctx.track(_check_media(request, "download"))
    data, _ = await ctx.ym.download_tagged(track)
    name = tagged_filename(track)
    return web.Response(body=data, content_type="audio/mpeg", headers={
        "Content-Disposition": f"attachment; filename=\"track.mp3\"; filename*=UTF-8''{quote(name)}",
        # Telegram Web скачивает файл через fetch со своего домена.
        "Access-Control-Allow-Origin": "https://web.telegram.org",
    })


# ---------- статика ----------

def _static_version() -> str:
    """Меняется при обновлении файлов — чтобы Telegram не держал в кэше старый app.js."""
    stamp = "".join(f"{f.name}{f.stat().st_mtime_ns}" for f in sorted(STATIC_DIR.iterdir()))
    return hashlib.sha1(stamp.encode()).hexdigest()[:10]


@routes.get("/")
async def index(request: web.Request) -> web.Response:
    html = (STATIC_DIR / "index.html").read_text("utf-8").replace("__V__", _static_version())
    return web.Response(text=html, content_type="text/html", headers={"Cache-Control": "no-cache"})


async def _startup(app: web.Application) -> None:
    app[CTX].http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, sock_read=60), trust_env=True)


async def _cleanup(app: web.Application) -> None:
    if app[CTX].http is not None:
        await app[CTX].http.close()


def create_app(config: Config, bot: Bot, ym: YandexMusic, store: Storage, sender: TrackSender) -> web.Application:
    app = web.Application(
        client_max_size=config.web_max_upload_mb * 1024 * 1024,
        middlewares=[errors_middleware, auth_middleware],
    )
    app[CTX] = WebContext(config, bot, ym, store, sender, MediaSigner(config.bot_token))
    app.add_routes(routes)
    app.router.add_static("/static/", STATIC_DIR, append_version=False)
    app.on_startup.append(_startup)
    app.on_cleanup.append(_cleanup)
    return app
