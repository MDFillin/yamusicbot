"""HTTP API и статика мини-приложения (Telegram Mini App)."""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import logging
import secrets
import time
from collections import defaultdict
from collections.abc import Callable, Hashable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

import aiohttp
from aiogram import Bot
from aiogram.utils.web_app import WebAppInitData, WebAppUser
from aiohttp import web
from yandex_music import Album, Artist, Playlist, Track
from yandex_music.exceptions import UnauthorizedError, YandexMusicError

from bot import net
from bot.accounts import Accounts, LoginError, LoginSession
from bot.admin import ADMIN_MAX_AGE, Admin, LimitReached
from bot.audio import (
    MAX_COVER_BYTES,
    ConversionError,
    TrackMeta,
    clean_year,
    ffmpeg_available,
    normalize_cover,
    prepare_for_upload,
)
from bot.config import Config
from bot.handlers.download import start_bulk_download
from bot.listening import Listening
from bot.placer import TopPlacer
from bot.sender import PARALLEL_DOWNLOADS, TrackSender, TrackTooLargeError
from bot.settings import QUALITY_LABELS, available_qualities, get_quality, set_quality
from bot.sources import SourceNotFoundError, TrackSource, load_source, playlist_cover, playlist_ref, resolve
from bot.storage import Storage
from bot.web.auth import AuthError, MediaSigner, init_data_age, verify_init_data
from bot.ym import (
    YANDEX_UNREACHABLE,
    TrackUnavailableError,
    UploadEndpointError,
    UploadError,
    YandexMusic,
    YandexNotReady,
    cover_url,
    is_network_error,
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
# Эти адреса нужны и тем, кто ещё не подключил Яндекс Музыку.
PUBLIC_API = {"/api/me", "/api/login", "/api/logout"}
REVOKED = "Яндекс Музыка не приняла ваш вход — возможно, он устарел или отозван. Подключите аккаунт заново."
COMPRESS_TYPES = {"application/json", "text/html", "text/css", "application/javascript", "image/svg+xml"}
MAX_JSON_BYTES = 64 * 1024  # запросы с JSON крошечные; большие тела нужны только /api/upload
UPLOAD_SLOTS = 3  # файлов, которые сервер принимает одновременно (каждый держит в памяти до WEB_MAX_UPLOAD_MB)
TOKEN_MAX_AGE = 3600  # сек: токен показываем только недавно открытому приложению
# Страница берёт скрипты только у себя и у Telegram: даже если в данных окажется разметка, чужой код не запустится.
CSP = "; ".join((
    "default-src 'self'",
    "script-src 'self' https://telegram.org",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' https: data: blob:",
    "media-src 'self' blob:",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'none'",
))


class TTLCache:
    """Небольшой кэш с временем жизни и ограничением размера (у каждого пользователя свои ключи)."""

    def __init__(self, ttl: float, max_size: int) -> None:
        self.ttl = ttl
        self.max_size = max_size
        self._data: dict[Hashable, tuple[float, Any]] = {}

    def get(self, key: Hashable) -> Any | None:
        item = self._data.get(key)
        if item is None or time.monotonic() - item[0] >= self.ttl:
            return None
        return item[1]

    def set(self, key: Hashable, value: Any) -> None:
        self._data.pop(key, None)
        self._data[key] = (time.monotonic(), value)
        if len(self._data) > self.max_size:
            now = time.monotonic()
            for k in [k for k, (t, _) in self._data.items() if now - t >= self.ttl]:
                del self._data[k]
            while len(self._data) > self.max_size:
                del self._data[next(iter(self._data))]  # самый старый

    def pop(self, key: Hashable) -> None:
        self._data.pop(key, None)

    def drop(self, predicate: Callable[[Hashable], bool]) -> None:
        for k in [k for k in self._data if predicate(k)]:
            del self._data[k]


@dataclass
class WebContext:
    config: Config
    bot: Bot
    accounts: Accounts
    store: Storage
    sender: TrackSender
    signer: MediaSigner
    static: StaticFiles
    placer: TopPlacer = field(default_factory=TopPlacer)
    admin: Admin | None = None  # задаётся в create_app
    listening: Listening | None = None
    http: aiohttp.ClientSession | None = None
    sources: TTLCache = field(default_factory=lambda: TTLCache(SOURCE_TTL, 300))
    links: TTLCache = field(default_factory=lambda: TTLCache(LINK_TTL, 2000))
    upload_slots: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(UPLOAD_SLOTS))
    upload_locks: defaultdict[int, asyncio.Lock] = field(default_factory=lambda: defaultdict(asyncio.Lock))
    download_slots: asyncio.Semaphore = field(default_factory=lambda: asyncio.Semaphore(PARALLEL_DOWNLOADS))

    async def source(self, user_id: int, ym: YandexMusic, src: str, ref: str, fresh: bool) -> TrackSource:
        key = (user_id, src, ref)
        cached = None if fresh else self.sources.get(key)
        if cached is not None:
            return cached
        source = await load_source(ym, src, ref)
        self.sources.set(key, source)
        return source

    def invalidate(self, user_id: int) -> None:
        self.sources.drop(lambda key: key[0] == user_id)

    async def direct_link(self, user_id: int, ym: YandexMusic, track_id: str, fresh: bool = False) -> str:
        quality = get_quality(self.store, self.config, user_id, "stream")
        key = (user_id, track_id, quality)  # ссылка зависит от подписки аккаунта — не делимся ей между людьми
        cached = None if fresh else self.links.get(key)
        if cached is not None:
            return cached
        url = await ym.direct_link(await get_track(ym, track_id), quality)
        self.links.set(key, url)
        return url


async def get_track(ym: YandexMusic, track_id: str) -> Track:
    track = await ym.get_track(track_id)
    if track is None:
        raise web.HTTPNotFound(text="Трек не найден")
    return track


CTX = web.AppKey("ctx", WebContext)
USER = web.RequestKey("user", WebAppUser)
INIT_DATA = web.RequestKey("init_data", WebAppInitData)
YM = web.RequestKey("ym", YandexMusic)


# ---------- сериализация ----------

def track_json(t: Track, signer: MediaSigner, user_id: int, index: int | None = None) -> dict[str, Any]:
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
        "stream": f"/media/stream/{track_id}?{signer.sign('stream', user_id, track_id)}",
        "download": f"/media/download/{track_id}?{signer.sign('download', user_id, track_id)}",
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


def login_json(session: LoginSession | None, connected: bool) -> dict[str, Any]:
    if session is None:
        return {"status": "none", "connected": connected}
    return {
        "status": session.status,
        "code": session.code,
        "url": session.url,
        "expires_in": session.expires_in,
        "error": session.error,
        "connected": connected,
    }


# ---------- middleware ----------

async def security_headers(request: web.Request, resp: web.StreamResponse) -> None:
    """Заголовки безопасности на всех ответах, включая ошибки и потоковые (вызывается перед отправкой)."""
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Content-Security-Policy", CSP)
    if request.path.startswith("/api/admin/"):
        resp.headers["Cache-Control"] = "no-store"


@web.middleware
async def compress_middleware(request: web.Request, handler):
    """gzip для JSON и текста: через туннель страница и списки грузятся заметно быстрее."""
    resp = await handler(request)
    if (
        isinstance(resp, web.Response)
        and resp.content_type in COMPRESS_TYPES
        and isinstance(resp.body, bytes)
        and len(resp.body) > 1024
        and "Content-Encoding" not in resp.headers
    ):
        resp.enable_compression()
    return resp


def _error(message: str, status: int, code: str | None = None) -> web.Response:
    body = {"error": message}
    if code:
        body["code"] = code
    return web.json_response(body, status=status)


@web.middleware
async def errors_middleware(request: web.Request, handler):
    try:
        return await handler(request)
    except AuthError as e:
        return _error(str(e), e.status, e.code)
    except YandexNotReady as e:
        return _error(str(e), 503, "yandex_network" if e.network else "yandex_unavailable")
    except LoginError as e:
        return _error(str(e), 422)
    except web.HTTPException as e:
        if e.status == 413:  # aiohttp отвечает по-английски
            limit = request.app[CTX].config.web_max_upload_mb
            return _error(f"Файл слишком большой: сервер принимает до {limit} МБ", 413)
        if e.status >= 400 and request.path.startswith("/api/"):
            return _error(e.text or e.reason, e.status)
        raise
    except (SourceNotFoundError, TrackUnavailableError) as e:
        return _error(str(e), 404)
    except (UploadError, ConversionError, TrackTooLargeError) as e:
        return _error(str(e), 422)
    except LimitReached as e:
        return _error(str(e), 429, "limit")
    except ValueError as e:
        log.info("Некорректный запрос %s: %s", request.path, e)
        return _error("Некорректный запрос", 400)
    except UnauthorizedError:
        # Вход отозвали уже после подключения: забываем клиент, при следующем запросе объясним подробно.
        if (user := request.get(USER)) is not None:
            await request.app[CTX].accounts.reset(user.id)
        return _error(REVOKED, 503, "yandex_unavailable")
    except YandexMusicError as e:
        log.warning("Ошибка Яндекс Музыки на %s: %r", request.path, e)
        if is_network_error(e):
            return _error(YANDEX_UNREACHABLE, 503, "yandex_network")
        return _error(f"Яндекс Музыка: {str(e)[:300]}", 502)
    except Exception:
        # Подробности — только в лог сервера: пользователю незачем видеть внутренности бота.
        ref = secrets.token_hex(3)
        log.exception("Ошибка на %s [%s]", request.path, ref)
        return _error(f"Что-то пошло не так на сервере (код {ref}). Попробуйте ещё раз", 500)


@web.middleware
async def auth_middleware(request: web.Request, handler):
    """Кто открыл приложение (подпись Telegram) и его аккаунт Яндекса."""
    if request.path.startswith("/api/"):
        ctx = request.app[CTX]
        data = verify_init_data(ctx.config.bot_token, request.headers.get("X-Telegram-Init-Data", ""))
        request[INIT_DATA] = data
        request[USER] = user = data.user
        admin = ctx.admin
        if request.path.startswith("/api/admin/"):
            # Для всех, кроме ADMIN_IDS, админки не существует (404), а попытка попадает в журнал.
            if not admin.is_admin(user.id):
                await admin.probe(user, f"{request.method} {request.path}")
                raise web.HTTPNotFound()
            if init_data_age(data) > ADMIN_MAX_AGE:
                raise AuthError(401, "Для безопасности закройте и снова откройте приложение", code="stale_session")
            admin.touch(user)
            return await handler(request)
        admin.touch(user)
        if (reason := admin.check(user.id)) is not None:
            raise AuthError(403, admin.reason_text(reason), code=reason)
        if request.path not in PUBLIC_API:
            ym = await ctx.accounts.get(user.id)
            if ym is None:
                raise AuthError(401, "Сначала подключите Яндекс Музыку", code="login_required")
            request[YM] = ym
    return await handler(request)


# ---------- API ----------

routes = web.RouteTableDef()


def _ctx(request: web.Request) -> WebContext:
    return request.app[CTX]


def _user_id(request: web.Request) -> int:
    return request[USER].id


async def _read_limited(chunks, limit: int, what: str) -> bytes:
    """Читает поток по кусочкам и прерывает, как только он превысил limit (не дожидаясь конца)."""
    buf = bytearray()
    async for chunk in chunks:
        buf += chunk
        if len(buf) > limit:
            raise web.HTTPRequestEntityTooLarge(limit, len(buf), text=f"{what} больше {limit // 1024} КБ")
    return bytes(buf)


async def _part_chunks(part):
    while chunk := await part.read_chunk(256 * 1024):
        yield chunk


async def _json_body(request: web.Request) -> dict[str, Any]:
    if (request.content_length or 0) > MAX_JSON_BYTES:
        raise web.HTTPBadRequest(text="Слишком большой запрос")
    try:
        body = json.loads(await _read_limited(request.content.iter_chunked(64 * 1024), MAX_JSON_BYTES, "Запрос"))
    except web.HTTPRequestEntityTooLarge as e:
        raise web.HTTPBadRequest(text="Слишком большой запрос") from e
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
    user_id = _user_id(request)
    info: dict[str, Any] = {
        "connected": False,
        "can_convert": ffmpeg_available(),
        "max_upload_mb": ctx.config.web_max_upload_mb,
    }
    if ctx.admin.is_admin(user_id):
        info["is_admin"] = True
        info["is_owner"] = ctx.admin.is_owner(user_id)
    ym = await ctx.accounts.get(user_id)  # не подключиться — 503 с объяснением
    if ym is not None:
        info.update(connected=True, login=ym.login, has_plus=ym.has_plus,
                    upload_target=ctx.store.get_upload_target(user_id), settings=settings_json(ctx, user_id))
    return web.json_response(info)


def settings_json(ctx: WebContext, user_id: int) -> dict[str, Any]:
    return {
        "download_quality": get_quality(ctx.store, ctx.config, user_id, "download"),
        "stream_quality": get_quality(ctx.store, ctx.config, user_id, "stream"),
        "qualities": [{"kbps": q, "label": QUALITY_LABELS[q]} for q in available_qualities(ctx.config)],
    }


@routes.get("/api/settings")
async def api_settings(request: web.Request) -> web.Response:
    return web.json_response(settings_json(_ctx(request), _user_id(request)))


@routes.put("/api/settings")
async def api_update_settings(request: web.Request) -> web.Response:
    ctx = _ctx(request)
    user_id = _user_id(request)
    body = await _json_body(request)
    for kind in ("download", "stream"):
        value = body.get(f"{kind}_quality")
        if value is not None:
            try:
                set_quality(ctx.store, ctx.config, user_id, kind, int(value))
            except (TypeError, ValueError) as e:
                raise web.HTTPBadRequest(text=str(e)) from e
    ctx.links.drop(lambda key: key[0] == user_id)  # плеер возьмёт ссылки в новом качестве
    return web.json_response(settings_json(ctx, user_id))


@routes.get("/api/token")
async def api_token(request: web.Request) -> web.Response:
    """Свой OAuth-токен Яндекс Музыки — только его владельцу (подпись Telegram проверена), без кэширования.

    Остальные запросы принимают подпись до суток, а здесь нужна свежая: если кто-то добыл ссылку на открытое
    приложение (в ней подпись), вечный токен он с неё уже не получит.
    """
    if init_data_age(request[INIT_DATA]) > TOKEN_MAX_AGE:
        raise AuthError(401, "Для безопасности закройте и снова откройте приложение — тогда токен можно показать",
                        code="stale_session")
    account = _ctx(request).store.get_account(_user_id(request))
    if account is None:
        raise AuthError(401, "Сначала подключите Яндекс Музыку", code="login_required")
    return web.json_response({"token": account[0]}, headers={"Cache-Control": "no-store"})


@routes.post("/api/login")
async def api_login_start(request: web.Request) -> web.Response:
    ctx = _ctx(request)
    session = await ctx.accounts.start_login(_user_id(request))
    return web.json_response(login_json(session, ctx.accounts.is_connected(_user_id(request))))


@routes.get("/api/login")
async def api_login_status(request: web.Request) -> web.Response:
    ctx = _ctx(request)
    user_id = _user_id(request)
    return web.json_response(login_json(ctx.accounts.login_session(user_id), ctx.accounts.is_connected(user_id)))


@routes.delete("/api/login")
async def api_login_cancel(request: web.Request) -> web.Response:
    _ctx(request).accounts.cancel_login(_user_id(request))
    return web.json_response({"ok": True})


@routes.post("/api/logout")
async def api_logout(request: web.Request) -> web.Response:
    ctx = _ctx(request)
    user_id = _user_id(request)
    await ctx.accounts.logout(user_id)
    ctx.invalidate(user_id)
    ctx.links.drop(lambda key: key[0] == user_id)
    return web.json_response({"ok": True})


@routes.get("/api/library")
async def api_library(request: web.Request) -> web.Response:
    ym = request[YM]
    playlists, liked = await asyncio.gather(ym.get_my_playlists(), ym.get_liked_track_ids())
    return web.json_response({
        "liked_ids": [i.split(":")[0] for i in liked],
        "playlists": [playlist_json(p) for p in playlists],
    })


@routes.get("/api/source/{src}")
async def api_source(request: web.Request) -> web.Response:
    ctx = _ctx(request)
    ym = request[YM]
    user_id = _user_id(request)
    src = request.match_info["src"]
    if src not in SOURCES:
        raise web.HTTPNotFound(text="Неизвестный список")
    ref = request.query.get("ref", "")
    offset = max(int(request.query.get("offset", 0)), 0)
    limit = min(max(int(request.query.get("limit", 50)), 1), PAGE_LIMIT)
    source = await ctx.source(user_id, ym, src, ref, fresh=offset == 0)
    tracks = await resolve(ym, source.items[offset:offset + limit])
    return web.json_response({
        "src": src,
        "ref": source.ref,
        "title": source.name or source.title,
        "subtitle": source.subtitle,
        "cover": cover_url(source.cover, "400x400"),
        "total": len(source.items),
        "own_kind": source.own_playlist.kind if source.own_playlist is not None else None,
        "offset": offset,
        "tracks": [track_json(t, ctx.signer, user_id, offset + i) for i, t in enumerate(tracks)],
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
    result = await request[YM].search(query, type_)
    items: list[dict[str, Any]] = []
    if result:
        if type_ == "track" and result.tracks:
            items = [track_json(t, ctx.signer, _user_id(request)) for t in result.tracks.results]
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
    await request[YM].like(request.match_info["track_id"])
    _ctx(request).sources.pop((_user_id(request), "likes", ""))
    return web.json_response({"liked": True})


@routes.delete(r"/api/likes/{track_id:[\w.\-]+}")
async def api_unlike(request: web.Request) -> web.Response:
    await request[YM].unlike(request.match_info["track_id"])
    _ctx(request).sources.pop((_user_id(request), "likes", ""))
    return web.json_response({"liked": False})


@routes.post("/api/playlists")
async def api_create_playlist(request: web.Request) -> web.Response:
    playlist = await request[YM].create_playlist(_title(await _json_body(request)))
    return web.json_response(playlist_json(playlist))


@routes.patch(r"/api/playlists/{kind:\d+}")
async def api_rename_playlist(request: web.Request) -> web.Response:
    playlist = await request[YM].rename_playlist(int(request.match_info["kind"]), _title(await _json_body(request)))
    _ctx(request).invalidate(_user_id(request))
    return web.json_response(playlist_json(playlist) if playlist else {"ok": True})


@routes.delete(r"/api/playlists/{kind:\d+}")
async def api_delete_playlist(request: web.Request) -> web.Response:
    ctx = _ctx(request)
    user_id = _user_id(request)
    kind = int(request.match_info["kind"])
    await request[YM].delete_playlist(kind)
    if ctx.store.get_upload_target(user_id) == kind:
        ctx.store.set_upload_target(user_id, None)
    ctx.invalidate(user_id)
    return web.json_response({"ok": True})


@routes.post(r"/api/playlists/{kind:\d+}/tracks")
async def api_add_track(request: web.Request) -> web.Response:
    ym = request[YM]
    body = await _json_body(request)
    track = await get_track(ym, str(body.get("track_id", "")))
    playlist = await ym.add_to_playlist(int(request.match_info["kind"]), track)
    _ctx(request).invalidate(_user_id(request))
    return web.json_response({"ok": True, "title": playlist.title if playlist else None})


@routes.delete(r"/api/playlists/{kind:\d+}/tracks/{index:\d+}")
async def api_remove_track(request: web.Request) -> web.Response:
    track_id = request.query.get("track_id", "")
    await request[YM].remove_from_playlist(int(request.match_info["kind"]), int(request.match_info["index"]), track_id)
    _ctx(request).invalidate(_user_id(request))
    return web.json_response({"ok": True})


@routes.put("/api/target")
async def api_set_target(request: web.Request) -> web.Response:
    ctx = _ctx(request)
    kind = (await _json_body(request)).get("kind")
    if kind is not None:
        kind = int(kind)
        if await request[YM].get_playlist(kind) is None:
            raise web.HTTPNotFound(text="Плейлист не найден")
    ctx.store.set_upload_target(_user_id(request), kind)
    return web.json_response({"upload_target": kind})


@routes.post(r"/api/tracks/{track_id:[\w.\-]+}/send")
async def api_send_track(request: web.Request) -> web.Response:
    ym = request[YM]
    track = await get_track(ym, request.match_info["track_id"])
    await _ctx(request).sender.send(_user_id(request), track, ym)
    return web.json_response({"ok": True})


@routes.post("/api/source/{src}/send")
async def api_send_source(request: web.Request) -> web.Response:
    ctx = _ctx(request)
    src = request.match_info["src"]
    if src not in SOURCES:
        raise web.HTTPNotFound(text="Неизвестный список")
    user_id = _user_id(request)
    started = start_bulk_download(
        ctx.bot, user_id, user_id, src, request.query.get("ref", ""), request[YM], ctx.sender,
    )
    return web.json_response({"started": started})


def parse_meta(fields: dict[str, str], cover: bytes | None) -> TrackMeta:
    """Правки данных трека из формы: JSON-поле meta (null — как в файле, "" — очистить) и файл cover.

    Старые поля artist/title (без meta) тоже понимаем: непустые — правка, пустые — как в файле.
    """
    try:
        raw = json.loads(fields.get("meta") or "{}")
    except ValueError as e:
        raise web.HTTPBadRequest(text="Некорректные данные трека") from e
    if not isinstance(raw, dict):
        raise web.HTTPBadRequest(text="Некорректные данные трека")
    for legacy in ("artist", "title"):
        if legacy not in raw and fields.get(legacy):
            raw[legacy] = fields[legacy]

    def text(name: str) -> str | None:
        value = raw.get(name)
        if value is None:
            return None
        if not isinstance(value, str | int):
            raise web.HTTPBadRequest(text="Некорректные данные трека")
        value = str(value).strip()[:200]
        return value if value or name in ("album", "year") else None  # пустые название и исполнитель — из файла

    try:
        year = clean_year(text("year"))
    except ValueError as e:
        raise web.HTTPBadRequest(text=str(e)) from e
    return TrackMeta(title=text("title"), artist=text("artist"), album=text("album"), year=year,
                     cover=cover, remove_cover=bool(raw.get("remove_cover")) and cover is None)


@routes.post("/api/upload")
async def api_upload(request: web.Request) -> web.Response:
    """Файл с телефона прямо на сервер: лимит Telegram в 20 МБ здесь не действует."""
    ctx = _ctx(request)
    if (request.content_length or 0) > request.client_max_size:
        raise web.HTTPRequestEntityTooLarge(request.client_max_size, request.content_length)
    ctx.admin.check_limit(_user_id(request), "upload")
    # Файл целиком лежит в памяти, пока идёт в Яндекс, поэтому принимаем по одному на человека и немного сразу:
    # тело запроса читаем, только когда подошла очередь.
    async with ctx.upload_locks[_user_id(request)], ctx.upload_slots:
        return await _upload(request, ctx)


async def _upload(request: web.Request, ctx: WebContext) -> web.Response:
    ym = request[YM]
    reader = await request.multipart()
    fields: dict[str, str] = {}
    file_name, data, cover = None, b"", None
    while (part := await reader.next()) is not None:
        if part.name == "file":
            file_name = part.filename or "track.mp3"
            data = await _read_limited(_part_chunks(part), request.client_max_size, "Файл")
        elif part.name == "cover":
            try:
                cover = await _read_limited(_part_chunks(part), MAX_COVER_BYTES, "Обложка")
            except web.HTTPRequestEntityTooLarge as e:
                raise web.HTTPBadRequest(text="Обложка больше 10 МБ") from e
        elif part.name:
            if len(fields) >= 20:
                raise web.HTTPBadRequest(text="Слишком много полей")
            raw = await _read_limited(_part_chunks(part), MAX_JSON_BYTES, "Поле")
            fields[part.name] = raw.decode("utf-8", errors="replace").strip()
    if not file_name or not data:
        raise web.HTTPBadRequest(text="Файл не передан")
    if not fields.get("kind", "").isdigit():
        raise web.HTTPBadRequest(text="Не выбран плейлист")
    kind = int(fields["kind"])
    edit = parse_meta(fields, await normalize_cover(cover) if cover else None)
    name, prepared, notes = await prepare_for_upload(
        file_name, data, edit=edit,
        fallback_artist=fields.get("fallback_artist") or None, fallback_title=fields.get("fallback_title") or None,
    )
    known = await ctx.placer.before_upload(ym, kind)
    try:
        result = await ym.upload_track(kind, name, prepared)
    except UploadEndpointError as e:  # неофициальный API изменился — владелец узнает сразу
        await ctx.admin.upload_failed(_user_id(request), e.details)
        raise
    await ctx.admin.upload_succeeded()
    ctx.admin.count(_user_id(request), "upload")
    ctx.placer.after_upload(ym, kind, known, result.ugc_track_id)  # встанет в начало, когда Яндекс обработает
    ctx.invalidate(_user_id(request))
    if result.note:
        notes.append(result.note)
    return web.json_response({"name": name, "notes": notes, "ugc_track_id": result.ugc_track_id})


# ---------- аудио по подписанным ссылкам ----------

async def _media_account(request: web.Request, kind: str) -> tuple[int, YandexMusic, str]:
    """Проверяет подпись ссылки; возвращает (Telegram ID, его аккаунт Яндекса, id трека)."""
    ctx = _ctx(request)
    track_id = request.match_info["track_id"]
    user = request.query.get("u", "")
    if not user.isdigit() or not ctx.signer.verify(kind, int(user), track_id,
                                                   request.query.get("exp"), request.query.get("sig")):
        raise web.HTTPForbidden(text="Ссылка недействительна или устарела")
    if ctx.admin.check(int(user)) is not None:  # заблокирован или техработы — ссылки тоже не работают
        raise web.HTTPForbidden(text="Доступ закрыт")
    ym = await ctx.accounts.get(int(user))
    if ym is None:
        raise web.HTTPForbidden(text="Аккаунт Яндекса отключён")
    return int(user), ym, track_id


@routes.get(r"/media/stream/{track_id:[\w.\-]+}")
async def media_stream(request: web.Request) -> web.StreamResponse:
    """Проксирует MP3 из хранилища Яндекса с поддержкой Range — чтобы работала перемотка."""
    ctx = _ctx(request)
    user_id, ym, track_id = await _media_account(request, "stream")
    headers = {"Range": request.headers["Range"]} if "Range" in request.headers else {}

    upstream = None
    for attempt in range(2):
        url = await ctx.direct_link(user_id, ym, track_id, fresh=attempt > 0)
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
    user_id, ym, track_id = await _media_account(request, "download")
    track = await get_track(ym, track_id)
    ctx.admin.check_limit(user_id, "download")
    async with ctx.download_slots:  # файл собирается в памяти целиком — не больше нескольких сразу
        data, _ = await ym.download_tagged(track, get_quality(ctx.store, ctx.config, user_id, "download"))
    ctx.admin.count(user_id, "download")
    name = tagged_filename(track)
    return web.Response(body=data, content_type="audio/mpeg", headers={
        "Content-Disposition": f"attachment; filename=\"track.mp3\"; filename*=UTF-8''{quote(name)}",
        # Telegram Web скачивает файл через fetch со своего домена.
        "Access-Control-Allow-Origin": "https://web.telegram.org",
    })


# ---------- статика ----------

class StaticFiles:
    """Статика из памяти, заранее сжатая gzip.

    Адреса файлов содержат версию (хеш содержимого), поэтому браузер Telegram может хранить их в кэше
    сколько угодно — после обновления бота изменится адрес, и он скачает новые.
    """

    TYPES = {".js": "application/javascript", ".css": "text/css", ".html": "text/html", ".svg": "image/svg+xml",
             ".png": "image/png", ".ico": "image/x-icon", ".webp": "image/webp"}

    def __init__(self, directory: Path) -> None:
        files = sorted(f for f in directory.iterdir() if f.is_file() and f.suffix in self.TYPES)
        digest = hashlib.sha1()
        for f in files:
            digest.update(f.name.encode() + f.read_bytes())
        self.version = digest.hexdigest()[:10]
        self.files: dict[str, tuple[str, bytes, bytes | None]] = {}
        for f in files:
            raw = f.read_bytes()
            if f.name == "index.html":
                raw = raw.replace(b"__V__", self.version.encode())
            content_type = self.TYPES[f.suffix]
            packed = gzip.compress(raw, 9) if content_type in COMPRESS_TYPES else None
            self.files[f.name] = (content_type, raw, packed)

    def response(self, request: web.Request, name: str, cache: str) -> web.Response:
        item = self.files.get(name)
        if item is None:
            raise web.HTTPNotFound()
        content_type, raw, packed = item
        headers = {"Cache-Control": cache, "Vary": "Accept-Encoding"}
        body = raw
        if packed is not None and "gzip" in request.headers.get("Accept-Encoding", ""):
            body = packed
            headers["Content-Encoding"] = "gzip"
        return web.Response(body=body, content_type=content_type, headers=headers)


@routes.get("/static/{name}")
async def static_file(request: web.Request) -> web.Response:
    static = _ctx(request).static
    versioned = request.query.get("v") == static.version
    cache = "public, max-age=31536000, immutable" if versioned else "no-cache"
    return static.response(request, request.match_info["name"], cache)


@routes.get("/")
async def index(request: web.Request) -> web.Response:
    return _ctx(request).static.response(request, "index.html", "no-cache")


async def _startup(app: web.Application) -> None:
    app[CTX].http = net.yandex_session(timeout=net.timeout(None, read=60), trust_env=True)


async def _cleanup(app: web.Application) -> None:
    if app[CTX].http is not None:
        await app[CTX].http.close()


def create_app(config: Config, bot: Bot, accounts: Accounts, store: Storage, sender: TrackSender,
               placer: TopPlacer | None = None, admin: Admin | None = None,
               listening: Listening | None = None) -> web.Application:
    from bot.web import admin_api, stats_api  # модули берут CTX и USER отсюда
    app = web.Application(
        client_max_size=config.web_max_upload_mb * 1024 * 1024,
        middlewares=[compress_middleware, errors_middleware, auth_middleware],
    )
    admin = admin or Admin(config, store, accounts, bot)
    app[CTX] = WebContext(config, bot, accounts, store, sender, MediaSigner(config.bot_token), StaticFiles(STATIC_DIR),
                          placer or TopPlacer(), admin, listening or Listening(store, accounts, admin, bot, config))
    app.add_routes(routes)
    app.add_routes(admin_api.routes)
    app.add_routes(stats_api.routes)
    app.on_response_prepare.append(security_headers)
    app.on_startup.append(_startup)
    app.on_cleanup.append(_cleanup)
    return app
