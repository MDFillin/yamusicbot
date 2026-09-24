"""API админ-панели мини-приложения: /api/admin/*.

Доступ проверяет auth_middleware (bot/web/app.py) до вызова любого обработчика отсюда: подпись Telegram,
Telegram ID из ADMIN_IDS и свежесть подписи (не старше часа). Всем остальным эти адреса отвечают 404.
"""

from __future__ import annotations

from typing import Any

from aiogram.exceptions import TelegramBadRequest
from aiohttp import web

from bot.admin import AUDIENCES, Admin
from bot.web.app import CTX, USER, _json_body

routes = web.RouteTableDef()
USER_ACTIONS = {"ban", "unban", "disconnect", "reset", "message"}


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
    })


@routes.get("/api/admin/users")
async def users(request: web.Request) -> web.Response:
    admin = _admin(request)
    query = request.query.get("q", "").strip()[:100]
    status = request.query.get("status", "all")
    if status not in {"all", "connected", "banned", "blocked"}:
        raise web.HTTPBadRequest(text="Неизвестный фильтр")
    offset = max(_int(request.query.get("offset")), 0)
    items = admin.store.list_users(query, status, offset, 50)
    for user in items:
        user["is_admin"] = admin.is_admin(user["id"])
    return web.json_response({"users": items, "total": admin.store.count_users(query, status), "offset": offset})


def _user_json(admin: Admin, user_id: int) -> dict[str, Any]:
    user = admin.store.get_user(user_id)
    if user is None:
        raise web.HTTPNotFound(text="Пользователь не найден")
    user["is_admin"] = admin.is_admin(user_id)
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
        else:
            await admin.message(actor, user_id, str(body.get("text") or "")[:4000])
    except ValueError as e:
        raise web.HTTPBadRequest(text=str(e)) from e
    return web.json_response(_user_json(admin, user_id))


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
    except TelegramBadRequest as e:
        raise web.HTTPBadRequest(text=f"Telegram не принял файл: {e.message}") from e
    return web.json_response({"size": size})
