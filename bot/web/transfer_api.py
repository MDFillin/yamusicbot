"""API вкладки «Перенос» (bot/transfer.py): своё приложение Spotify, вход, выбор источников, прогресс."""

from __future__ import annotations

import asyncio
import html
import logging
import secrets
import time
from typing import Any

import aiohttp
from aiohttp import web

from bot import net
from bot.handlers.download import start_bulk_download
from bot.spotify import (
    CLIENT_ID_RE,
    Spotify,
    SpotifyAuthError,
    SpotifyError,
    Tokens,
    authorize_url,
    exchange_code,
    pkce_pair,
)
from bot.transfer import SourcePick, Transfers
from bot.web.app import CTX, USER, YM, _json_body, playlist_json

log = logging.getLogger(__name__)
routes = web.RouteTableDef()
TRANSFERS = web.AppKey("transfers", Transfers)
SPOTIFY_HTTP = web.AppKey("spotify_http", aiohttp.ClientSession)
LOGINS = web.AppKey("spotify_logins", dict)  # state → (user_id, verifier, client_id, когда)
LOGIN_TTL = 900  # сек на вход в Spotify
RESUME_DELAY = 30  # сек после запуска — продолжить прерванные переносы
RESUME_TASK = web.AppKey("transfer_resume", asyncio.Task)
MAX_SOURCES = 50


def redirect_uri(request: web.Request) -> str | None:
    url = request.app[CTX].config.webapp_url
    return f"{url}/spotify/callback" if url else None


def _user(request: web.Request) -> int:
    return request[USER].id


def spotify_client(app: web.Application, user_id: int) -> Spotify | None:
    stored = app[CTX].store.get_spotify(user_id)
    if not stored or not stored[1]:
        return None
    client_id, tokens, _ = stored
    store = app[CTX].store
    return Spotify(app[SPOTIFY_HTTP], client_id, Tokens.from_dict(tokens),
                   on_tokens=lambda t: store.set_spotify_tokens(user_id, t.to_dict()))


def _job_json(request: web.Request, user_id: int) -> dict[str, Any] | None:
    running = request.app[TRANSFERS].running(user_id)
    if running:
        return {"id": running.id, "status": "running", **running.state}
    last = request.app[CTX].store.last_transfer_job(user_id)
    if last is None:
        return None
    job_id, _, status, state = last
    return {**state, "id": job_id, "status": status}


@routes.get("/api/transfer")
async def transfer_info(request: web.Request) -> web.Response:
    user_id = _user(request)
    stored = request.app[CTX].store.get_spotify(user_id)
    spotify = None
    if stored:
        spotify = {"client_id": stored[0], "connected": bool(stored[1]), "name": stored[2]}
    return web.json_response({"redirect_uri": redirect_uri(request), "spotify": spotify,
                              "job": _job_json(request, user_id)})


@routes.put("/api/transfer/spotify")
async def set_client(request: web.Request) -> web.Response:
    client_id = str((await _json_body(request)).get("client_id") or "").strip().lower()
    if not CLIENT_ID_RE.fullmatch(client_id):
        raise web.HTTPBadRequest(text="Client ID — 32 символа (цифры и буквы a–f) со страницы приложения Spotify")
    user_id = _user(request)
    if request.app[TRANSFERS].running(user_id):
        raise web.HTTPConflict(text="Дождитесь конца переноса")
    stored = request.app[CTX].store.get_spotify(user_id)
    if not stored or stored[0] != client_id:
        request.app[CTX].store.set_spotify_client(user_id, client_id)
    return web.json_response({"ok": True})


@routes.post("/api/transfer/spotify/login")
async def login(request: web.Request) -> web.Response:
    uri = redirect_uri(request)
    stored = request.app[CTX].store.get_spotify(_user(request))
    if not uri or not stored:
        raise web.HTTPBadRequest(text="Сначала укажите Client ID приложения Spotify")
    logins = request.app[LOGINS]
    now = time.time()
    for key in [k for k, v in logins.items() if now - v[3] > LOGIN_TTL]:
        del logins[key]
    state = secrets.token_urlsafe(24)
    verifier, challenge = pkce_pair()
    logins[state] = (_user(request), verifier, stored[0], now)
    return web.json_response({"url": authorize_url(stored[0], uri, state, challenge)})


@routes.delete("/api/transfer/spotify")
async def logout(request: web.Request) -> web.Response:
    user_id = _user(request)
    if request.app[TRANSFERS].running(user_id):
        raise web.HTTPConflict(text="Дождитесь конца переноса")
    request.app[CTX].store.forget_spotify_login(user_id)
    return web.json_response({"ok": True})


@routes.get("/api/transfer/sources")
async def sources(request: web.Request) -> web.Response:
    ym = request[YM]
    playlists = await ym.get_my_playlists()
    likes = await ym.get_liked_track_ids()
    return web.json_response({"likes": len(likes), "playlists": [playlist_json(p) for p in playlists]})


@routes.post("/api/transfer/start")
async def start(request: web.Request) -> web.Response:
    user_id = _user(request)
    body = await _json_body(request)
    picks = []
    for item in (body.get("sources") or [])[:MAX_SOURCES]:
        if isinstance(item, dict) and item.get("src") == "likes":
            picks.append(SourcePick("likes"))
        elif isinstance(item, dict) and item.get("src") == "pl" and isinstance(item.get("ref"), str) \
                and len(item["ref"]) < 64:
            picks.append(SourcePick("pl", item["ref"]))
    if not picks:
        raise web.HTTPBadRequest(text="Выберите, что переносить")
    sp = spotify_client(request.app, user_id)
    if sp is None:
        raise web.HTTPBadRequest(text="Сначала войдите в Spotify")
    transfers = request.app[TRANSFERS]
    if transfers.running(user_id):
        raise web.HTTPConflict(text="Перенос уже идёт")
    try:
        await sp.me()  # вход ещё действует и приложение пускает — иначе скажем сразу, а не через минуту
    except SpotifyAuthError as e:
        raise web.HTTPForbidden(text=str(e)) from e
    except SpotifyError as e:
        raise web.HTTPBadGateway(text=str(e)) from e
    transfers.start(user_id, request[YM], sp, picks)
    return web.json_response({"job": _job_json(request, user_id)})


@routes.post("/api/transfer/files")
async def send_files(request: web.Request) -> web.Response:
    """Свои загруженные файлы, которых нет в Spotify, — в чат по порядку списков: их можно добавить в Spotify
    на компьютере как локальные файлы (через API Spotify файлы не принимает)."""
    ctx, user_id = request.app[CTX], _user(request)
    last = ctx.store.last_transfer_job(user_id)
    if last is None:
        raise web.HTTPNotFound(text="Переносов ещё не было")
    ids = []
    for source in last[3].get("sources") or []:
        ids += [m["id"] for m in sorted(source.get("missed") or [], key=lambda m: m.get("pos", 0))
                if m.get("ugc") and m.get("id")]
    ids = list(dict.fromkeys(ids))
    if not ids:
        raise web.HTTPBadRequest(text="Ваших файлов среди ненайденного нет")
    started = start_bulk_download(ctx.bot, user_id, user_id, "transfer", "", request[YM], ctx.sender,
                                  items=ids, title="Ваши файлы, которых нет в Spotify")
    return web.json_response({"started": started, "count": len(ids)})


@routes.post("/api/transfer/cancel")
async def cancel(request: web.Request) -> web.Response:
    return web.json_response({"cancelled": request.app[TRANSFERS].cancel(_user(request))})


# ---------- возврат из Spotify после входа (открывается в браузере, без Telegram) ----------

def _page(title: str, text: str, bot_link: str | None, ok: bool) -> web.Response:
    button = (f'<a class="btn" href="{html.escape(bot_link)}">Вернуться в Telegram</a>' if bot_link else "")
    body = f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{html.escape(title)}</title>
<style>
body {{ margin: 0; min-height: 100vh; display: grid; place-items: center; font: 16px/1.45 -apple-system, system-ui,
  sans-serif; background: #121214; color: #f2f2f2; padding: 24px; box-sizing: border-box; text-align: center; }}
.card {{ max-width: 420px; }} .icon {{ font-size: 54px; }} h1 {{ font-size: 22px; margin: 10px 0 8px; }}
p {{ color: #b7b7bd; margin: 0 0 22px; }}
.btn {{ display: inline-block; padding: 13px 22px; border-radius: 14px; background: {'#1ed760' if ok else '#3478f6'};
  color: #000; font-weight: 700; text-decoration: none; }}
</style></head><body><div class="card"><div class="icon">{'✅' if ok else '⚠️'}</div>
<h1>{html.escape(title)}</h1><p>{html.escape(text)}</p>{button}</div></body></html>"""
    return web.Response(text=body, content_type="text/html", headers={"Cache-Control": "no-store"})


@routes.get("/spotify/callback")
async def callback(request: web.Request) -> web.Response:
    ctx = request.app[CTX]
    try:
        me = await ctx.bot.me()
        bot_link = f"https://t.me/{me.username}" if me.username else None
    except Exception:  # noqa: BLE001 — без ссылки страница всё равно нужна
        bot_link = None
    pending = request.app[LOGINS].pop(request.query.get("state", ""), None)
    if pending is None or time.time() - pending[3] > LOGIN_TTL:
        return _page("Ссылка устарела", "Начните вход в Spotify заново из вкладки «Перенос».", bot_link, False)
    user_id, verifier, client_id, _ = pending
    if request.query.get("error"):
        text = ("Вы отказались дать доступ." if request.query["error"] == "access_denied"
                else f"Spotify ответил: {request.query['error'][:100]}")
        return _page("Spotify не подключён", text, bot_link, False)
    code = request.query.get("code", "")
    try:
        tokens = await exchange_code(request.app[SPOTIFY_HTTP], client_id, redirect_uri(request) or "", code, verifier)
        sp = Spotify(request.app[SPOTIFY_HTTP], client_id, tokens)
        me_sp = await sp.me()
    except SpotifyError as e:
        log.info("Вход в Spotify %s не удался: %s", user_id, e)
        return _page("Spotify не подключён", str(e), bot_link, False)
    stored = ctx.store.get_spotify(user_id)
    if not stored or stored[0] != client_id:
        return _page("Spotify не подключён", "Пока вы входили, приложение Spotify поменяли — войдите ещё раз.",
                     bot_link, False)
    name = me_sp.get("display_name") or me_sp.get("id") or "Spotify"
    ctx.store.set_spotify_tokens(user_id, sp.tokens.to_dict(), str(name)[:100])
    return _page("Spotify подключён", f"Аккаунт «{name}» подключён. Вернитесь в Telegram — во вкладке «Перенос» "
                 "можно выбрать, что перенести.", bot_link, True)


async def _startup(app: web.Application) -> None:
    app[SPOTIFY_HTTP] = net.session(timeout=net.timeout(60))

    async def connect(user_id: int):
        ym = await app[CTX].accounts.get(user_id)
        sp = spotify_client(app, user_id)
        return (ym, sp) if ym is not None and sp is not None else None

    async def resume() -> None:
        await asyncio.sleep(RESUME_DELAY)  # пусть бот сначала спокойно поднимется
        await app[TRANSFERS].resume_interrupted(connect)

    app[RESUME_TASK] = asyncio.create_task(resume())


async def _cleanup(app: web.Application) -> None:
    if RESUME_TASK in app:
        app[RESUME_TASK].cancel()
    for job in list(app[TRANSFERS].jobs.values()):
        if job.task and not job.task.done():
            job.task.cancel()
    if SPOTIFY_HTTP in app:
        await app[SPOTIFY_HTTP].close()


def setup(app: web.Application) -> None:
    ctx = app[CTX]

    async def notify(user_id: int, text: str) -> None:
        await ctx.bot.send_message(user_id, text)

    app[TRANSFERS] = Transfers(ctx.store, notify)
    app[LOGINS] = {}
    app.add_routes(routes)
    app.on_startup.append(_startup)
    app.on_cleanup.append(_cleanup)
