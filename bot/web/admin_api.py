"""API админ-панели мини-приложения: /api/admin/*.

Доступ проверяет auth_middleware (bot/web/app.py) до вызова любого обработчика отсюда: подпись Telegram,
Telegram ID админа (владелец из ADMIN_IDS или назначенный им) и свежесть подписи (не старше часа).
Всем остальным эти адреса отвечают 404. Что доступно только владельцу, проверяет сам Admin (Forbidden -> 403).
"""

from __future__ import annotations

import json
from typing import Any

from aiogram.exceptions import TelegramBadRequest
from aiohttp import web

from bot.admin import AUDIENCES, Admin, Forbidden
from bot.web.app import CTX, USER, _json_body

routes = web.RouteTableDef()
USER_ACTIONS = {"ban", "unban", "disconnect", "reset", "message", "grant_admin", "revoke_admin"}


def _admin(request: web.Request) -> Admin:
    return request.app[CTX].admin


def _actor(request: web.Request) -> int:
    return request[USER].id


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@routes.get("/api/admin/overview")
async def overview(request: web.Request) -> web.Response:
    admin = _admin(request)
    return web.json_response({
        "stats": admin.stats(),
        "server": admin.server_info(),
        "settings": admin.settings_json(),
        "broadcast": admin.broadcaster.status,
        "upload_health": admin.upload_health_json(),
        "listening": {"users": len(admin.store.stats_users()), "health": admin.history_health.json()},
    })


@routes.get("/api/admin/history_raw")
async def history_raw(request: web.Request) -> web.Response:
    """История прослушивания своего аккаунта как её отдаёт Яндекс — чтобы сверить, что бот считает верно."""
    from bot.listening import parse_history

    admin = _admin(request)
    ym = await admin.accounts.get(_actor(request))
    if ym is None:
        raise web.HTTPBadRequest(text="Подключите свой аккаунт Яндекса — показываю только вашу историю")
    raw = await ym.music_history_raw()
    listens = parse_history(await ym.music_history())
    days: dict[str, dict[str, int]] = {}
    for x in listens:
        d = days.setdefault(x.day, {"tracks": 0, "unique": 0})
        d["tracks"] += 1
    for day in days:
        days[day]["unique"] = len({x.track_id for x in listens if x.day == day})
    text = json.dumps(raw, ensure_ascii=False, indent=1)
    return web.json_response({
        "days": [{"date": d, **v} for d, v in sorted(days.items(), reverse=True)],
        "listens": len(listens),
        "raw": text[:60000],
        "raw_truncated": len(text) > 60000,
    })


@routes.get("/api/admin/users")
async def users(request: web.Request) -> web.Response:
    admin = _admin(request)
    query = request.query.get("q", "").strip()[:100]
    status = request.query.get("status", "all")
    if status not in {"all", "connected", "banned", "blocked", "admins"}:
        raise web.HTTPBadRequest(text="Неизвестный фильтр")
    offset = max(_int(request.query.get("offset")), 0)
    if status == "admins":
        items = admin.store.users_by_ids(sorted(admin.all_admin_ids()))
        total, offset = len(items), 0
    else:
        items = admin.store.list_users(query, status, offset, 50)
        total = admin.store.count_users(query, status)
    for user in items:
        _roles(admin, user)
    return web.json_response({"users": items, "total": total, "offset": offset})


def _roles(admin: Admin, user: dict[str, Any]) -> dict[str, Any]:
    user["is_admin"] = admin.is_admin(user["id"])
    user["is_owner"] = admin.is_owner(user["id"])
    return user


def _user_json(admin: Admin, user_id: int) -> dict[str, Any]:
    user = admin.store.get_user(user_id)
    if user is None:
        raise web.HTTPNotFound(text="Пользователь не найден")
    _roles(admin, user)
    granted = admin.store.get_admin(user_id) if user["is_admin"] and not user["is_owner"] else None
    user["admin_granted_by"], user["admin_granted_at"] = granted or (None, None)
    user["downloads_left"] = admin.left(user_id, "download")
    user["uploads_left"] = admin.left(user_id, "upload")
    return user


@routes.get(r"/api/admin/users/{user_id:-?\d+}")
async def user(request: web.Request) -> web.Response:
    return web.json_response(_user_json(_admin(request), int(request.match_info["user_id"])))


@routes.post(r"/api/admin/users/{user_id:-?\d+}/{action}")
async def user_action(request: web.Request) -> web.Response:
    admin = _admin(request)
    actor, user_id = _actor(request), int(request.match_info["user_id"])
    action = request.match_info["action"]
    if action not in USER_ACTIONS:
        raise web.HTTPNotFound(text="Неизвестное действие")
    body = await _json_body(request) if request.can_read_body else {}
    try:
        if action == "ban":
            admin.ban(actor, user_id, str(body.get("reason") or ""))
        elif action == "unban":
            admin.unban(actor, user_id)
        elif action == "disconnect":
            await admin.disconnect(actor, user_id)
            request.app[CTX].invalidate(user_id)
        elif action == "reset":
            admin.reset_limits(actor, user_id)
        elif action == "grant_admin":
            await admin.grant_admin(actor, user_id)
        elif action == "revoke_admin":
            await admin.revoke_admin(actor, user_id)
        else:
            await admin.message(actor, user_id, str(body.get("text") or "")[:4000])
    except Forbidden as e:
        raise web.HTTPForbidden(text=str(e)) from e
    except ValueError as e:
        raise web.HTTPBadRequest(text=str(e)) from e
    return web.json_response(_user_json(admin, user_id))


@routes.get("/api/admin/admins")
async def admins(request: web.Request) -> web.Response:
    admin = _admin(request)
    return web.json_response({"admins": admin.admins(), "can_manage": admin.is_owner(_actor(request))})


@routes.put("/api/admin/settings")
async def settings(request: web.Request) -> web.Response:
    admin = _admin(request)
    body = await _json_body(request)
    allowed = {"maintenance", "maintenance_text", "closed", "download_limit", "upload_limit"}
    try:
        admin.update_settings(_actor(request), {k: v for k, v in body.items() if k in allowed})
    except (TypeError, ValueError) as e:
        raise web.HTTPBadRequest(text=str(e)) from e
    return web.json_response(admin.settings_json())


def _broadcast_json(admin: Admin) -> dict[str, Any]:
    return {**admin.broadcaster.status,
            "audiences": {key: {"label": label, "count": len(admin.store.user_ids(key))}
                          for key, label in AUDIENCES.items()}}


@routes.get("/api/admin/broadcast")
async def broadcast_status(request: web.Request) -> web.Response:
    return web.json_response(_broadcast_json(_admin(request)))


@routes.post("/api/admin/broadcast")
async def broadcast(request: web.Request) -> web.Response:
    admin = _admin(request)
    body = await _json_body(request)
    text = str(body.get("text") or "").strip()
    if body.get("test"):  # сначала себе: проверить, как выглядит и принимает ли Telegram разметку
        if not text:
            raise web.HTTPBadRequest(text="Пустое сообщение")
        try:
            await admin.bot.send_message(_actor(request), text, disable_web_page_preview=True)
        except TelegramBadRequest as e:
            raise web.HTTPBadRequest(text=f"Telegram не принял сообщение: {e.message}") from e
        return web.json_response({"ok": True})
    try:
        admin.start_broadcast(_actor(request), text, str(body.get("audience") or "all"))
    except (RuntimeError, ValueError) as e:
        raise web.HTTPBadRequest(text=str(e)) from e
    return web.json_response(_broadcast_json(admin))


@routes.delete("/api/admin/broadcast")
async def broadcast_cancel(request: web.Request) -> web.Response:
    admin = _admin(request)
    admin.cancel_broadcast(_actor(request))
    return web.json_response(_broadcast_json(admin))


@routes.get("/api/admin/audit")
async def audit(request: web.Request) -> web.Response:
    return web.json_response({"entries": _admin(request).audit_log(200)})


@routes.get("/api/admin/errors")
async def errors(request: web.Request) -> web.Response:
    query = request.query.get("q", "")[:100]
    return web.json_response({"entries": _admin(request).errors.recent(query, 200)})


@routes.post("/api/admin/backup")
async def backup(request: web.Request) -> web.Response:
    """Копия базы уходит админу в личный чат с ботом — скачать её по ссылке нельзя."""
    try:
        size = await _admin(request).backup(_actor(request))
    except Forbidden as e:
        raise web.HTTPForbidden(text=str(e)) from e
    except TelegramBadRequest as e:
        raise web.HTTPBadRequest(text=f"Telegram не принял файл: {e.message}") from e
    return web.json_response({"size": size})
