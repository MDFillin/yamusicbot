"""API статистики прослушиваний для мини-приложения: /api/stats."""

from __future__ import annotations

import re
import time
from typing import Any

from aiohttp import web

from bot.listening import APP_CONTEXT, MANUAL_SYNC_INTERVAL, Listening, msk_now, period_at
from bot.live import msk_day
from bot.web.app import CTX, USER, YM, _json_body, cover_url, track_json

routes = web.RouteTableDef()
KINDS = ("day", "week", "month", "year")
_TRACK_ID = re.compile(r"^\d{1,20}$")
APP_PLAY_GAP = 10  # сек: чаще плеер «Медиатеки» прослушивания не присылает (порог — от 24 секунд трека)


def _listening(request: web.Request) -> Listening:
    return request.app[CTX].listening


def _user(request: web.Request) -> int:
    return request[USER].id


@routes.get("/api/stats")
async def stats(request: web.Request) -> web.Response:
    listening, user_id = _listening(request), _user(request)
    body: dict[str, Any] = {"enabled": listening.enabled(user_id), "prefs": listening.prefs(user_id)}
    if not body["enabled"]:
        return web.json_response(body)
    live = listening.live
    body["live"] = {"enabled": bool(live and live.enabled),
                    "connected": bool(live and (c := live.conns.get(user_id)) and c.connected)}
    kind = request.query.get("kind", "week")
    if kind not in KINDS:
        raise web.HTTPBadRequest(text="Неизвестный период")
    try:
        offset = max(min(int(request.query.get("offset", 0)), 0), -520)
    except ValueError as e:
        raise web.HTTPBadRequest(text="Неверный сдвиг периода") from e
    p = period_at(kind, msk_now().date(), offset)
    st = listening.stats(user_id, p)
    # Лучшие треки — полными карточками: их можно сразу включить в плеере.
    tracks = []
    top = st["top_tracks"][:10]
    if top:
        ym = request[YM]
        ctx = request.app[CTX]
        by_id = {str(t.id): t for t in await ym.get_tracks([t["id"] for t in top])}
        for t in top:
            if (full := by_id.get(t["id"])) is not None:
                tracks.append({**track_json(full, ctx.signer, user_id), "plays": t["plays"]})
    for album in st["top_albums"]:
        album["cover"] = cover_url(album.pop("cover_uri", None))
    body.update(period={"kind": kind, "offset": offset, "title": p.title, "start": p.start.isoformat(),
                        "end": p.end.isoformat(), "today": msk_now().date().isoformat()}, stats=st, top_tracks=tracks)
    return web.json_response(body)


@routes.post("/api/stats/played")
async def played(request: web.Request) -> web.Response:
    """Плеер «Медиатеки» дослушал трек до порога — засчитать (Яндекс о таком прослушивании не знает)."""
    listening, user_id = _listening(request), _user(request)
    if not listening.enabled(user_id):
        return web.json_response({"counted": False})
    body = await _json_body(request)
    track_id, ms = str(body.get("id") or ""), body.get("ms")
    if not _TRACK_ID.match(track_id) or not isinstance(ms, int) or not 1_000 <= ms <= 4 * 3600 * 1000:
        raise web.HTTPBadRequest(text="Неверные данные прослушивания")
    # Прослушать ms за время, прошедшее с прошлого сообщения, физически нельзя быстрее, чем за ms.
    last = listening.app_reports.get(user_id)
    if last is not None and time.monotonic() - last < max(APP_PLAY_GAP, ms / 1000 * 0.8):
        raise web.HTTPTooManyRequests(text="Слишком часто")
    listening.app_reports[user_id] = time.monotonic()
    now = time.time()
    listening.store.add_play(user_id, int(now - ms / 1000), msk_day(now - ms / 1000), track_id, APP_CONTEXT, ms,
                             "app")
    await listening.fill_meta(request[YM], [track_id])
    await listening.fill_contexts(request[YM], {APP_CONTEXT: "bot"})
    return web.json_response({"counted": True})


@routes.put("/api/stats")
async def update(request: web.Request) -> web.Response:
    listening, user_id = _listening(request), _user(request)
    body = await _json_body(request)
    if "enabled" in body:
        if body["enabled"]:
            listening.enable(user_id)
            if listening.can_sync_now(user_id):  # первая выгрузка сразу — чтобы было что показать
                await listening.sync(user_id, request[YM])
        else:
            listening.disable(user_id)
    for kind in ("day", "week", "month"):
        if kind in body:
            listening.set_pref(user_id, kind, bool(body[kind]))
    return web.json_response({"enabled": listening.enabled(user_id), "prefs": listening.prefs(user_id)})


@routes.post("/api/stats/sync")
async def sync(request: web.Request) -> web.Response:
    listening, user_id = _listening(request), _user(request)
    if not listening.enabled(user_id):
        raise web.HTTPBadRequest(text="Статистика выключена")
    if not listening.can_sync_now(user_id):
        raise web.HTTPTooManyRequests(text=f"Обновлять можно раз в {MANUAL_SYNC_INTERVAL} секунд")
    added = await listening.sync(user_id, request[YM])
    if added is None:
        raise web.HTTPBadGateway(text="Яндекс не отдал историю — попробуйте позже")
    return web.json_response({"added": added})


@routes.delete("/api/stats")
async def delete(request: web.Request) -> web.Response:
    deleted = _listening(request).disable(_user(request), delete=True)
    return web.json_response({"enabled": False, "deleted": deleted})
