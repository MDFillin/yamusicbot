"""Админ-панель мини-приложения: доступ только владельцу, блокировки, техработы, лимиты, рассылка."""

from __future__ import annotations

import asyncio
import logging
import time

import pytest
from aiogram import Bot
from aiogram.methods import DeleteMyCommands, SendDocument, SendMessage, SetMyCommands
from aiohttp.test_utils import TestClient, TestServer
from test_bot_flow import FakeTelegram
from test_web import (
    FRIEND_ID,
    NEWBIE_ID,
    OWNER_ID,
    TOKEN,
    FakeOAuth,
    FakePlacer,
    FakeSender,
    FakeYM,
    as_user,
    init_data,
)

from bot.accounts import Accounts
from bot.admin import Admin
from bot.config import Config
from bot.storage import Storage
from bot.web.app import create_app

ADMIN_ID = OWNER_ID


@pytest.fixture
async def env(tmp_path, upstream):
    fakes = {"tok-owner": FakeYM(upstream), "tok-friend": FakeYM(upstream, uid=77, login="friend", likes=["2:10"])}
    store = Storage(tmp_path / "bot.db")
    store.set_account(OWNER_ID, "tok-owner", "me")
    store.set_account(FRIEND_ID, "tok-friend", "friend")
    accounts = Accounts(store, factory=fakes.__getitem__, oauth=FakeOAuth())
    config = Config(bot_token=TOKEN, data_dir=tmp_path, admin_ids=frozenset({ADMIN_ID}))
    telegram = FakeTelegram()
    bot = Bot(TOKEN, session=telegram)
    admin = Admin(config, store, accounts, bot)
    client = TestClient(TestServer(create_app(config, bot, accounts, store, FakeSender(), FakePlacer(), admin)))
    await client.start_server()
    client.session.headers["X-Telegram-Init-Data"] = init_data(ADMIN_ID)
    yield type("Env", (), {"client": client, "store": store, "admin": admin, "tg": telegram})
    await client.close()
    await accounts.close()
    store.close()


# ---------- кто может войти ----------

async def test_admin_api_is_invisible_to_others(env):
    c = env.client
    for path in ("/api/admin/overview", "/api/admin/users", "/api/admin/nope"):
        r = await c.get(path, headers=as_user(FRIEND_ID))
        assert r.status == 404, path
    r = await c.put("/api/admin/settings", json={"maintenance": True}, headers=as_user(FRIEND_ID))
    assert r.status == 404 and not env.admin.settings.maintenance
    r = await c.post(f"/api/admin/users/{FRIEND_ID}/ban", json={}, headers=as_user(FRIEND_ID))
    assert r.status == 404 and not env.store.is_banned(FRIEND_ID)

    [entry] = [e for e in env.store.audit() if e["action"] == "denied"]
    assert entry["actor"] == FRIEND_ID, "попытка записана в журнал (один раз, без спама)"
    alerts = [m for m in env.tg.calls if isinstance(m, SendMessage) and "🚨" in m.text]
    assert len(alerts) == 1 and alerts[0].chat_id == ADMIN_ID, "владельцу пришло предупреждение"
    me = await (await c.get("/api/me", headers=as_user(FRIEND_ID))).json()
    assert "is_admin" not in me


async def test_admin_needs_valid_and_fresh_signature(env):
    c = env.client
    forged = init_data(ADMIN_ID, token="1:OTHER")
    assert (await c.get("/api/admin/overview", headers={"X-Telegram-Init-Data": forged})).status == 401
    assert (await c.get("/api/admin/overview", headers={"X-Telegram-Init-Data": ""})).status == 401
    stale = init_data(ADMIN_ID, auth_date=int(time.time()) - 2 * 3600)
    r = await c.get("/api/admin/overview", headers={"X-Telegram-Init-Data": stale})
    assert r.status == 401 and (await r.json())["code"] == "stale_session"
    r = await c.get("/api/admin/overview")
    assert r.status == 200 and r.headers["Cache-Control"] == "no-store"
    assert (await (await c.get("/api/me")).json())["is_admin"] is True


async def test_overview(env):
    await env.client.get("/api/library", headers=as_user(FRIEND_ID))
    data = await (await env.client.get("/api/admin/overview")).json()
    assert data["stats"]["users"]["total"] == 2 and data["stats"]["users"]["connected"] == 2
    assert data["stats"]["users"]["active_today"] == 2 and len(data["stats"]["series"]) == 14
    assert data["server"]["owners"] == [ADMIN_ID] and data["server"]["admins"] == []
    assert data["settings"]["maintenance"] is False
    assert data["broadcast"]["state"] == "idle"


# ---------- пользователи ----------

async def test_users_search_and_ban(env):
    c = env.client
    await c.get("/api/library", headers=as_user(FRIEND_ID))
    users = (await (await c.get("/api/admin/users?q=friend")).json())["users"]
    assert [u["id"] for u in users] == [FRIEND_ID] and users[0]["yandex_login"] == "friend"

    r = await c.post(f"/api/admin/users/{FRIEND_ID}/ban", json={"reason": "спам"})
    assert r.status == 200 and (await r.json())["banned"]
    r = await c.get("/api/library", headers=as_user(FRIEND_ID))
    assert r.status == 403 and (await r.json())["code"] == "banned"
    banned = (await (await c.get("/api/admin/users?status=banned")).json())["users"]
    assert [u["ban_reason"] for u in banned] == ["спам"]

    r = await c.post(f"/api/admin/users/{ADMIN_ID}/ban", json={})
    assert r.status == 400 and "Админа" in (await r.json())["error"]

    await c.post(f"/api/admin/users/{FRIEND_ID}/unban")
    assert (await c.get("/api/library", headers=as_user(FRIEND_ID))).status == 200


async def test_banned_user_media_links_stop_working(env):
    c = env.client
    track = (await (await c.get("/api/source/likes", headers=as_user(FRIEND_ID))).json())["tracks"][0]
    assert (await c.get(track["stream"])).status == 200
    await c.post(f"/api/admin/users/{FRIEND_ID}/ban", json={})
    assert (await c.get(track["stream"])).status == 403


async def test_disconnect_and_message(env):
    c = env.client
    r = await c.post(f"/api/admin/users/{FRIEND_ID}/disconnect")
    assert r.status == 200 and not (await r.json())["connected"]
    assert env.store.get_account(FRIEND_ID) is None
    r = await c.post(f"/api/admin/users/{FRIEND_ID}/message", json={"text": "Привет от админа"})
    assert r.status == 200
    assert any(isinstance(m, SendMessage) and m.chat_id == FRIEND_ID and m.text == "Привет от админа"
               for m in env.tg.calls)
    assert [e["action"] for e in env.store.audit()][:2] == ["message", "disconnect"]


# ---------- режимы и лимиты ----------

async def test_maintenance_and_closed_registration(env):
    c = env.client
    r = await c.put("/api/admin/settings", json={"maintenance": True, "maintenance_text": "Обновляемся до 18:00"})
    assert (await r.json())["maintenance"]
    r = await c.get("/api/me", headers=as_user(FRIEND_ID))
    assert r.status == 403 and (await r.json())["error"] == "🛠 Обновляемся до 18:00"
    assert (await c.get("/api/library")).status == 200, "админ работает и во время техработ"

    await c.put("/api/admin/settings", json={"maintenance": False, "closed": True})
    assert (await c.get("/api/me", headers=as_user(FRIEND_ID))).status == 200, "старые пользователи остаются"
    r = await c.get("/api/me", headers=as_user(NEWBIE_ID))
    assert r.status == 403 and (await r.json())["code"] == "closed"
    await c.put("/api/admin/settings", json={"closed": False})
    assert (await c.get("/api/me", headers=as_user(NEWBIE_ID))).status == 200


async def test_limits(env):
    c = env.client
    r = await c.put("/api/admin/settings", json={"download_limit": 1})
    assert (await r.json())["download_limit"] == 1
    assert (await c.put("/api/admin/settings", json={"download_limit": -5})).status == 400
    track = (await (await c.get("/api/source/likes", headers=as_user(FRIEND_ID))).json())["tracks"][0]
    assert (await c.get(track["download"])).status == 200
    r = await c.get(track["download"])
    assert r.status == 429 and "лимит" in (await r.json())["error"]
    user = await (await c.get(f"/api/admin/users/{FRIEND_ID}")).json()
    assert user["downloads"] == 1 and user["downloads_left"] == 0
    await c.post(f"/api/admin/users/{FRIEND_ID}/reset")
    assert (await c.get(track["download"])).status == 200


# ---------- рассылка, журнал, ошибки, бэкап ----------

async def test_broadcast(env):
    c = env.client
    await c.get("/api/me", headers=as_user(FRIEND_ID))
    await c.post(f"/api/admin/users/{NEWBIE_ID}/ban", json={})
    r = await c.post("/api/admin/broadcast", json={"text": "<b>Новость</b>", "test": True})
    assert r.status == 200
    r = await c.post("/api/admin/broadcast", json={"text": "<b>Новость</b>", "audience": "all"})
    body = await r.json()
    assert r.status == 200 and body["total"] == 2, "заблокированным рассылка не уходит"
    for _ in range(100):
        if not env.admin.broadcaster.running:
            break
        await asyncio.sleep(0.02)
    status = await (await c.get("/api/admin/broadcast")).json()
    assert status["state"] == "done" and status["sent"] == 2
    got = sorted(m.chat_id for m in env.tg.calls if isinstance(m, SendMessage) and m.text == "<b>Новость</b>")
    assert got == [ADMIN_ID, ADMIN_ID, FRIEND_ID], "проверка себе + всем"
    assert (await c.post("/api/admin/broadcast", json={"text": " "})).status == 400


async def test_errors_audit_and_backup(env, tmp_path):
    logging.getLogger().addHandler(env.admin.errors)
    try:
        logging.getLogger("bot.test").error("Ошибка на /api/x [c0ffee]")
    finally:
        logging.getLogger().removeHandler(env.admin.errors)
    entries = (await (await env.client.get("/api/admin/errors?q=c0ffee")).json())["entries"]
    assert len(entries) == 1 and entries[0]["level"] == "ERROR"

    r = await env.client.post("/api/admin/backup")
    assert r.status == 200 and (await r.json())["size"] > 0
    [doc] = [m for m in env.tg.calls if isinstance(m, SendDocument)]
    assert doc.chat_id == ADMIN_ID
    assert not list(tmp_path.glob("backup-*"))
    audit = (await (await env.client.get("/api/admin/audit")).json())["entries"]
    assert audit[0]["action"] == "backup" and audit[0]["label"] == "бэкап базы"


# ---------- назначенные админы ----------

async def test_owner_appoints_and_revokes_admin(env, tmp_path):
    c = env.client
    friend = as_user(FRIEND_ID)
    await c.get("/api/me", headers=friend)
    assert (await c.get("/api/admin/overview", headers=friend)).status == 404

    r = await c.post(f"/api/admin/users/{FRIEND_ID}/grant_admin")
    body = await r.json()
    assert r.status == 200 and body["is_admin"] and not body["is_owner"] and body["admin_granted_by"] == ADMIN_ID
    me = await (await c.get("/api/me", headers=friend)).json()
    assert me["is_admin"] is True and me["is_owner"] is False
    assert (await c.get("/api/admin/overview", headers=friend)).status == 200, "права действуют сразу"
    assert any(isinstance(m, SendMessage) and m.chat_id == FRIEND_ID and "права администратора" in m.text
               for m in env.tg.calls)
    assert any(isinstance(m, SetMyCommands) and m.scope.chat_id == FRIEND_ID for m in env.tg.calls)
    team = (await (await c.get("/api/admin/admins")).json())
    assert [(a["id"], a["owner"]) for a in team["admins"]] == [(ADMIN_ID, True), (FRIEND_ID, False)]
    assert team["can_manage"] is True
    listed = (await (await c.get("/api/admin/users?status=admins")).json())["users"]
    assert {u["id"] for u in listed} == {ADMIN_ID, FRIEND_ID}

    # Права сохраняются в базе и переживают перезапуск.
    restarted = Admin(env.admin.config, env.store, env.admin.accounts, env.admin.bot)
    assert restarted.is_admin(FRIEND_ID) and not restarted.is_owner(FRIEND_ID)

    r = await c.post(f"/api/admin/users/{FRIEND_ID}/revoke_admin")
    assert r.status == 200 and not (await r.json())["is_admin"]
    assert (await c.get("/api/admin/overview", headers=friend)).status == 404, "и снимаются сразу"
    assert any(isinstance(m, DeleteMyCommands) and m.scope.chat_id == FRIEND_ID for m in env.tg.calls)
    assert [e["action"] for e in env.store.audit()][:2] == ["revoke_admin", "grant_admin"]


async def test_appointed_admin_limits(env):
    c = env.client
    friend = as_user(FRIEND_ID)
    await c.get("/api/me", headers=friend)
    await c.get("/api/me", headers=as_user(NEWBIE_ID))
    await c.post(f"/api/admin/users/{FRIEND_ID}/grant_admin")

    r = await c.post(f"/api/admin/users/{NEWBIE_ID}/grant_admin", headers=friend)
    assert r.status == 403 and "владелец" in (await r.json())["error"], "назначать может только владелец"
    assert (await c.post(f"/api/admin/users/{ADMIN_ID}/revoke_admin", headers=friend)).status == 403
    assert (await c.post("/api/admin/backup", headers=friend)).status == 403, "бэкап с токенами — только владельцу"
    assert (await c.post(f"/api/admin/users/{ADMIN_ID}/disconnect", headers=friend)).status == 403
    assert env.store.get_account(ADMIN_ID) is not None
    assert (await c.post(f"/api/admin/users/{ADMIN_ID}/ban", json={}, headers=friend)).status == 400
    team = await (await c.get("/api/admin/admins", headers=friend)).json()
    assert team["can_manage"] is False
    # А обычная работа админа доступна.
    assert (await c.post(f"/api/admin/users/{NEWBIE_ID}/ban", json={}, headers=friend)).status == 200


async def test_grant_admin_validation(env):
    c = env.client
    r = await c.post(f"/api/admin/users/{ADMIN_ID}/revoke_admin")
    assert r.status == 400 and ".env" in (await r.json())["error"], "владельца из приложения не снять"
    r = await c.post("/api/admin/users/555/grant_admin")
    assert r.status == 400 and "не знает" in (await r.json())["error"]
    await c.get("/api/me", headers=as_user(NEWBIE_ID))
    await c.post(f"/api/admin/users/{NEWBIE_ID}/ban", json={})
    r = await c.post(f"/api/admin/users/{NEWBIE_ID}/grant_admin")
    assert r.status == 400 and "разблокируйте" in (await r.json())["error"]
    r = await c.post(f"/api/admin/users/{FRIEND_ID}/revoke_admin")
    assert r.status == 400


# ---------- здоровье загрузки в Яндекс ----------

async def _upload(c, headers=None):
    from aiohttp import FormData
    form = FormData()
    form.add_field("kind", "1003")
    form.add_field("file", b"\xff\xfb\x90\x64" + b"\x00" * 400, filename="x.mp3")
    return await c.post("/api/upload", data=form, headers=headers)


async def test_owner_is_alerted_when_yandex_changes_upload(env):
    from bot.ym import UploadEndpointError, UploadResult

    fakes = {u: env.admin.accounts._factory(t) for u, t in ((ADMIN_ID, "tok-owner"), (FRIEND_ID, "tok-friend"))}
    for ym in fakes.values():
        async def broken(kind, filename, data):
            raise UploadEndpointError("Яндекс не выдал адрес для загрузки. Ответы: api.music.yandex.ru: HTTP 404")
        ym.upload_track = broken

    def alerts(word):
        return [m for m in env.tg.calls if isinstance(m, SendMessage) and m.chat_id == ADMIN_ID and word in m.text]

    r = await _upload(env.client)
    assert r.status == 422 and "Яндекс изменил свой сайт" in (await r.json())["error"]
    assert not alerts("перестала работать"), "одна неудача — ещё не повод будить владельца"
    await _upload(env.client, as_user(FRIEND_ID))
    [alert] = alerts("перестала работать")
    assert "HTTP 404" in alert.text and "diag_upload" in alert.text
    await _upload(env.client)
    assert len(alerts("перестала работать")) == 1, "одно уведомление, без спама"
    health = (await (await env.client.get("/api/admin/overview")).json())["upload_health"]
    assert health["broken"] and health["streak"] == 3

    async def works(kind, filename, data):
        return UploadResult("ugc-1", "CREATED")
    fakes[ADMIN_ID].upload_track = works
    assert (await _upload(env.client)).status == 200
    assert alerts("снова работает")
    health = (await (await env.client.get("/api/admin/overview")).json())["upload_health"]
    assert not health["broken"] and health["last_ok"]
